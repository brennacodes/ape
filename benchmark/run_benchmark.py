#!/usr/bin/env python3
"""
Run the benchmark suite end-to-end.

Tests how workflow instructions in different formats perform against
real apps with realistic prompts. Every case runs in an isolated
workspace with a scrubbed environment.

Usage:
    python3 benchmark/run_benchmark.py                          # run all
    python3 benchmark/run_benchmark.py --dry-run                # show cases
    python3 benchmark/run_benchmark.py --model claude-opus-4-20250514
    python3 benchmark/run_benchmark.py --timeout 20    # 20 minutes
    python3 benchmark/run_benchmark.py --max-turns 25
    python3 benchmark/run_benchmark.py --workers 1 --delay 5   # sequential
    python3 benchmark/run_benchmark.py --no-enrich-tokens
    python3 benchmark/run_benchmark.py --legacy-output
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import os
from datetime import datetime
from pathlib import Path

import json as _json
import threading

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.live import Live
from rich.table import Table
from rich.text import Text

console = Console()
logger = logging.getLogger("bench")

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(_HERE, "scripts")
for subdir in ("coordinator", "runner", "evaluator", "results"):
    p = os.path.join(_SCRIPTS, subdir)
    if p not in sys.path:
        sys.path.insert(0, p)

from coordinator import (
    discover_apps, discover_workflows, discover_test_configs,
    discover_prompts, discover_app_configs, match_cases,
)
from runner import (
    run_case, run_all, run_parallel, DEFAULT_MODEL, CaseResult, shutdown_all,
    is_auth_error,
)
from environment import BenchmarkEnvironment, BaselineCache, BaselineMetrics
from results import format_run_summary, write_json
from recorder import Recorder, RunRecord


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bench",
        description="Run the benchmark suite against Claude Code.",
    )
    parser.add_argument(
        "--benchmark-root", type=Path, default=Path(_HERE),
        help="Root of the benchmark directory.",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Model to use (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--output", type=Path, default=Path(_HERE) / "output" / "summaries",
        help="Directory for legacy JSON results.",
    )
    parser.add_argument(
        "--results-dir", type=Path, default=Path(_HERE) / "output",
        help="Directory for structured result storage.",
    )
    parser.add_argument("--timeout", type=float, default=45, help="Per-case timeout in minutes (default: 45).")
    parser.add_argument("--max-turns", type=int, default=None, help="Max CLI turns.")
    parser.add_argument("--dry-run", action="store_true", help="Show cases without executing.")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (1=sequential).")
    parser.add_argument("--delay", type=float, default=0, help="Rate-limit delay (seconds).")
    parser.add_argument("--enrich-tokens", action="store_true", default=False, dest="enrich_tokens")
    parser.add_argument("--no-enrich-tokens", action="store_false", dest="enrich_tokens")
    parser.add_argument("--legacy-output", action="store_true")
    parser.add_argument("--refresh-baselines", action="store_true",
                        help="Force recomputation of fixture baselines (ignores cache). "
                             "Use with --baselines-only to refresh without running the benchmark.")
    parser.add_argument("--baselines-only", action="store_true",
                        help="Compute/refresh baselines and exit (no benchmark run).")
    parser.add_argument("--skip-baseline", action="store_true",
                        help="Skip baseline capture entirely.")
    parser.add_argument("-v", "--verbose", action="store_true", default=True)

    # Dimension filters — any combination narrows the case matrix
    parser.add_argument("--app", default=None, help="Filter by app name.")
    parser.add_argument("--category", default=None, help="Filter by prompt category (e.g. bugs).")
    parser.add_argument("--item", default=None, help="Filter by app-config item ID.")
    parser.add_argument("--format-filter", default=None, help="Filter by workflow format (e.g. plain-text).")
    parser.add_argument("--workflow", default=None, help="Filter by workflow stem (e.g. centminmod).")

    return parser.parse_args(argv)


def _format_duration(ms: float) -> str:
    if ms <= 0:
        return ""
    secs = ms / 1000
    return f"({secs / 60:.1f}m)" if secs >= 60 else f"({secs:.1f}s)"


def _build_filters(args: argparse.Namespace) -> dict[str, str]:
    """Build dimension filter dict from CLI args."""
    filters = {}
    if args.app:
        filters["app"] = args.app
    if args.category:
        filters["category"] = args.category
    if args.item:
        filters["item"] = args.item
    if args.format_filter:
        filters["format"] = args.format_filter
    if args.workflow:
        filters["workflow"] = args.workflow
    return filters


def _summarize_stream_line(line: str) -> str | None:
    """Extract a short human-readable status from a stream-json JSONL line.

    Returns None for lines that don't carry useful progress info.
    """
    line = line.strip()
    if not line:
        return None
    try:
        obj = _json.loads(line)
    except _json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    msg = obj.get("message", {})
    content = msg.get("content", [])
    if isinstance(content, str):
        snippet = content[:80]
        return f"prompt: {snippet}..." if len(content) > 80 else f"prompt: {snippet}"

    if not isinstance(content, list):
        return None

    for block in content:
        btype = block.get("type")
        if btype == "tool_use":
            name = block.get("name", "?")
            inp = block.get("input", {})
            if name == "Bash":
                cmd = inp.get("command", "")
                if len(cmd) > 60:
                    cmd = cmd[:57] + "..."
                return f"Bash: {cmd}"
            elif name in ("Read", "Write", "Edit"):
                path = inp.get("file_path", "")
                if path:
                    # Show just the filename or last 2 path components
                    parts = path.rsplit("/", 2)
                    short = "/".join(parts[-2:]) if len(parts) > 1 else path
                    return f"{name}: {short}"
                return name
            elif name in ("Grep", "Glob"):
                pattern = inp.get("pattern", "")
                return f"{name}: {pattern[:50]}"
            else:
                return f"{name}"
        elif btype == "text":
            text = block.get("text", "").strip()
            if text:
                if len(text) > 70:
                    text = text[:67] + "..."
                return f"text: {text}"
        elif btype == "thinking":
            return "thinking..."

    return None


class LiveProgress:
    """Thread-safe live progress display for benchmark cases.

    Shows one status line per active case (parallel) or a single
    status line (sequential), replacing in-place using Rich Live.
    """

    def __init__(self, console: Console, total: int, parallel: bool = False):
        self._console = console
        self._total = total
        self._parallel = parallel
        self._lock = threading.Lock()
        # case_id -> latest status string
        self._statuses: dict[str, str] = {}
        self._completed = 0
        self._live: Live | None = None

    def start(self) -> None:
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=4,
            transient=True,
        )
        self._live.start()

    def stop(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None

    def set_active(self, case_id: str) -> None:
        with self._lock:
            self._statuses[case_id] = "starting..."
        self._refresh()

    def update(self, case_id: str, line: str) -> None:
        summary = _summarize_stream_line(line)
        if summary is None:
            return
        with self._lock:
            self._statuses[case_id] = summary
        self._refresh()

    def mark_done(self, case_id: str, result_line: str) -> None:
        with self._lock:
            self._statuses.pop(case_id, None)
            self._completed += 1
        self._refresh()

    def _render(self) -> Table:
        table = Table.grid(padding=(0, 1))
        table.add_column(style="dim", width=12)
        table.add_column()
        with self._lock:
            progress = f"[{self._completed}/{self._total}]"
            if not self._statuses:
                table.add_row(progress, "waiting...")
            else:
                for i, (cid, status) in enumerate(self._statuses.items()):
                    # Shorten case_id for display
                    short_id = cid if len(cid) <= 40 else "..." + cid[-37:]
                    prefix = progress if i == 0 else ""
                    table.add_row(prefix, Text(f"{short_id}  {status}", style="cyan"))
        return table

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._render())

    def make_callback(self, case_id: str):
        """Return an on_output callback bound to a specific case_id."""
        def _cb(line: str) -> None:
            self.update(case_id, line)
        return _cb


def preflight_auth_check() -> tuple[bool, str]:
    """Run a lightweight CLI command to verify authentication works.

    Returns (ok, message) — ok is True if auth is valid, False otherwise.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["claude", "-p", "Say OK", "--max-turns", "1",
             "--output-format", "stream-json", "--verbose",
             "--include-partial-messages",
             "--allowedTools", "Bash,Read,Write,Edit,Glob,Grep"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return True, "Authentication OK"

        # Parse stream-json output for the actual error
        for line in (result.stdout or "").strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                if obj.get("type") == "result" and obj.get("is_error"):
                    return False, obj.get("result", "Unknown auth error")
                if obj.get("error"):
                    msg = obj.get("message", {})
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                return False, block.get("text", "Unknown auth error")

        stderr_msg = result.stderr.strip().splitlines()[0] if result.stderr.strip() else ""
        return False, stderr_msg or f"CLI exited with code {result.returncode}"
    except FileNotFoundError:
        return False, "'claude' command not found — is Claude Code installed and on PATH?"
    except subprocess.TimeoutExpired:
        return False, "Auth check timed out after 30s"
    except Exception as exc:
        return False, f"Auth check failed: {exc}"


def discover(benchmark_root: Path, filters: dict[str, str] | None = None):
    """Discover and match all benchmark cases, optionally filtered by dimensions."""
    apps = discover_apps(benchmark_root)
    workflows = discover_workflows(benchmark_root)
    configs = discover_test_configs(benchmark_root)
    prompts = discover_prompts(benchmark_root)
    app_configs = discover_app_configs(benchmark_root)
    cases = match_cases(apps, workflows, configs, prompts, app_configs)

    if filters:
        cases = [c for c in cases if c.matches_filter(**filters)]

    # Sort so each test scenario runs across all formats before moving on.
    # This lets you assess format differences per-category without waiting
    # for the entire suite to finish.
    cases.sort(key=lambda c: (c.app.name, c.category, c.item_id, c.prompt.prompt_id, c.workflow.stem, c.workflow.format))

    return apps, workflows, configs, prompts, app_configs, cases


def dry_run(args: argparse.Namespace) -> int:
    filters = _build_filters(args)
    apps, workflows, configs, prompts, app_configs, cases = discover(args.benchmark_root, filters)

    filter_line = ""
    if filters:
        filter_line = f"\n  Filters:    {', '.join(f'{k}={v}' for k, v in filters.items())}"

    console.print(Panel.fit(
        f"[bold]Benchmark Discovery[/bold]\n"
        f"  Root:       {args.benchmark_root}\n"
        f"  Apps:       {len(apps)}\n"
        f"  Workflows:  {len(workflows)}\n"
        f"  Configs:    {len(configs)}\n"
        f"  Prompts:    {len(prompts)}\n"
        f"  App configs:{len(app_configs)}\n"
        f"  Cases:      {len(cases)}{filter_line}",
        title="bench",
        border_style="blue",
    ))

    if not cases:
        console.print(
            "\n[yellow]Warning:[/yellow] No cases matched. "
            "Check that apps, workflow fixtures, test-configs, and prompts exist."
        )
        if not apps:
            console.print("  [dim]No apps found in fixtures/apps/[/dim]")
        if not workflows:
            console.print("  [dim]No workflows found in fixtures/{format}/[/dim]")
        if not prompts:
            console.print("  [dim]No prompts found in prompts/[/dim]")
        return 1

    console.print("\n[bold yellow]Dry-run:[/bold yellow] listing discovered cases.\n")
    for i, case in enumerate(cases, 1):
        console.print(f"  [dim]#{i:>3}[/dim]  [cyan]{case.case_id}[/cyan]")
        console.print(f"        app:      [green]{case.app.name}[/green]")
        console.print(f"        workflow:  [green]{case.workflow.path}[/green]")
        console.print(f"        config:   [green]{case.test_config.path}[/green]")
        console.print(f"        prompt:   [green]{case.prompt.path}[/green]")

    console.print(f"\n[bold]{len(cases)}[/bold] total cases would be executed.")
    return 0


def _case_identity(case) -> tuple[str, str, str]:
    """Return (fixture_id, format, prompt_id) for a TestCase."""
    fixture_id = case.app.name
    fmt = case.workflow.format
    if case.category and case.item_id:
        prompt_id = f"{case.category}/{case.item_id}"
    else:
        prompt_id = case.prompt.prompt_id
    return fixture_id, fmt, prompt_id


def _make_state_callback(recorder: Recorder, fixture_id: str, fmt: str, prompt_id: str, run_id: int):
    """Return an on_state callback that writes state.json incrementally."""
    def _cb(state: dict) -> None:
        recorder.write_state(fixture_id, fmt, prompt_id, run_id, state)
    return _cb


def _save_result(
    result: CaseResult,
    recorder: Recorder,
    args: argparse.Namespace,
    run_id: int | None = None,
) -> RunRecord | None:
    """Persist a single CaseResult to disk immediately. Returns the RunRecord or None."""
    fixture_id, fmt, prompt_id = _case_identity(result.case)

    if run_id is None:
        run_id = recorder.next_run_id(fixture_id, fmt, prompt_id)
    completed_at = datetime.now().isoformat()
    started_at = getattr(result, "started_at", "") or ""

    if result.summary:
        record = RunRecord.from_run_summary(
            result.summary, run_id,
            wall_clock_ms=result.wall_clock_ms,
            exit_code=result.exit_code,
            stderr=result.stderr,
            workspace_state=result.workspace_state,
            max_turns_configured=args.max_turns or 0,
            prompt_text=result.prompt_text,
            eval_conditions=result.eval_conditions,
            eval_variables=result.eval_variables,
            started_at=started_at,
            completed_at=completed_at,
            ape_version=result.ape_version,
            workflow_hash=result.workflow_hash,
        )
    else:
        # Error case — no summary but still capture everything we have.
        record = RunRecord(
            fixture_id=fixture_id,
            format=fmt,
            prompt_id=prompt_id,
            run_id=run_id,
            error=result.error or "",
            exit_code=result.exit_code,
            stderr=result.stderr,
            wall_clock_ms=result.wall_clock_ms,
            workspace_state=result.workspace_state,
            model=args.model,
            max_turns_configured=args.max_turns or 0,
            timestamp=datetime.now().isoformat(),
            prompt_text=result.prompt_text,
            eval_conditions=result.eval_conditions,
            eval_variables=result.eval_variables,
            started_at=started_at,
            completed_at=completed_at,
            ape_version=result.ape_version,
            workflow_hash=result.workflow_hash,
        )

    recorder.save_run(record, stream_path=result.stream_path, raw_output=result.raw_output)

    return record


def _enrich_with_tokens(recorder: Recorder, records: list[RunRecord]) -> None:
    """Enrich records with token data from session logs (legacy fallback).

    Token data is now extracted from the stream result event during save_run(),
    so this is only needed for records that weren't saved with stream data.
    """
    # Skip if all records already have token data from stream extraction
    needs_enrichment = [r for r in records if r.session_id and r.input_tokens == 0]
    if not needs_enrichment:
        logger.info("All %d records already have token data from stream", len(records))
        return

    try:
        _runner_dir = os.path.join(_SCRIPTS, "runner")
        if _runner_dir not in sys.path:
            sys.path.insert(0, _runner_dir)
        from session_logs import SessionLogParser
    except ImportError:
        logger.warning("session_logs module not available — skipping token enrichment")
        return

    parser = SessionLogParser()
    enriched = 0
    for record in needs_enrichment:
        summary = parser.parse_session(record.session_id)
        if summary is None:
            continue
        record.input_tokens = summary.input_tokens
        record.output_tokens = summary.output_tokens
        record.cache_creation_tokens = summary.cache_creation_tokens
        record.cache_read_tokens = summary.cache_read_tokens
        record.cost_usd = summary.cost_usd
        record.num_turns = summary.num_turns
        if record.max_turns_configured and record.num_turns >= record.max_turns_configured - 2:
            record.hit_turn_limit = True
        recorder.update_run(record)
        enriched += 1

    logger.info("Enriched %d/%d records with token data", enriched, len(records))


def warm_baselines(
    cases: list,
    refresh: bool = False,
    output_dir: Path | None = None,
) -> dict[str, BaselineMetrics]:
    """Pre-compute or load cached baselines for all unique fixtures.

    Returns a dict mapping app name -> BaselineMetrics.  Results are
    deterministic and identical across workflow formats since baselines
    depend only on the fixture's source code.
    """
    cache = BaselineCache(output_dir=output_dir)

    # Collect unique fixtures
    fixtures: dict[str, Path] = {}
    for case in cases:
        if case.app.name not in fixtures:
            fixtures[case.app.name] = case.app.path

    baselines: dict[str, BaselineMetrics] = {}
    for name, path in fixtures.items():
        if not (path / "Cargo.toml").is_file():
            logger.info("Skipping baseline for %s (no Cargo.toml)", name)
            continue

        if refresh:
            logger.info("Refreshing baseline for %s (--refresh-baselines)", name)
            result = cache.compute(path)
        else:
            result = cache.load_or_compute(path)

        if result is not None:
            baselines[name] = result
            console.print(
                f"  [green]{name}[/green]: tests={result.test_count} "
                f"(exit {result.cargo_test_exit_code}), "
                f"coverage={result.coverage_pct}"
            )
        else:
            console.print(f"  [yellow]{name}[/yellow]: no baseline (compute failed)")

    return baselines


def run(args: argparse.Namespace) -> int:
    filters = _build_filters(args)
    _, _, _, _, _, cases = discover(args.benchmark_root, filters)

    if not cases:
        console.print("[red]No cases discovered.[/red] Nothing to run.")
        return 1

    # Pre-flight: verify authentication before running any cases
    console.print("[dim]Verifying authentication...[/dim]", end=" ")
    auth_ok, auth_msg = preflight_auth_check()
    if not auth_ok:
        console.print("[red]FAILED[/red]")
        console.print(f"\n[red bold]Authentication error:[/red bold] {auth_msg}")
        console.print("\n[yellow]Hint:[/yellow] Run [bold]claude[/bold] in a terminal to re-authenticate, then retry.")
        return 1
    console.print("[green]OK[/green]")

    # Pre-compute baselines once per fixture — shared across all variants.
    precomputed: dict[str, BaselineMetrics] = {}
    if args.skip_baseline:
        console.print("[dim]Skipping baseline capture (--skip-baseline)[/dim]")
    else:
        console.print("[bold]Fixture baselines[/bold]")
        precomputed = warm_baselines(cases, refresh=args.refresh_baselines, output_dir=args.results_dir)
        if precomputed:
            console.print(
                f"  [dim]{len(precomputed)} fixture(s) baselined — "
                f"shared across all {len(cases)} cases[/dim]"
            )

    environment = BenchmarkEnvironment(
        skip_baseline=args.skip_baseline,
        precomputed_baselines=precomputed,
    )

    console.print(Panel.fit(
        f"[bold]Benchmark Run[/bold]\n"
        f"  Cases:     {len(cases)}\n"
        f"  Model:     {args.model}\n"
        f"  Timeout:   {args.timeout}m\n"
        f"  Workers:   {args.workers}\n"
        f"  Max turns: {args.max_turns or 'unlimited'}\n"
        f"  Isolation: on",
        title="bench",
        border_style="blue",
    ))

    timeout_seconds = int(args.timeout * 60)

    recorder = Recorder(args.results_dir)
    results: list[CaseResult] = []
    records: list[RunRecord] = []

    # Map case_id -> pre-allocated run_id for incremental state writes
    _allocated_run_ids: dict[str, int] = {}
    _run_id_lock = threading.Lock()

    def _on_result(result: CaseResult) -> None:
        """Save a result to disk immediately and collect it."""
        results.append(result)
        if not args.legacy_output:
            pre_run_id = _allocated_run_ids.pop(result.case.case_id, None)
            record = _save_result(result, recorder, args, run_id=pre_run_id)
            if record:
                records.append(record)

    progress = LiveProgress(console, len(cases), parallel=args.workers > 1)
    progress.start()

    try:
        if args.workers > 1:
            def _make_cb(case):
                progress.set_active(case.case_id)
                return progress.make_callback(case.case_id)

            def _make_state_cb(case):
                if args.legacy_output:
                    return None
                fid, fmt, pid = _case_identity(case)
                with _run_id_lock:
                    rid = recorder.next_run_id(fid, fmt, pid)
                    _allocated_run_ids[case.case_id] = rid
                    recorder.init_run_dir(fid, fmt, pid, rid)
                return _make_state_callback(recorder, fid, fmt, pid, rid)

            for result in run_parallel(
                cases,
                model=args.model,
                timeout=timeout_seconds,
                max_turns=args.max_turns,
                workers=args.workers,
                delay_s=args.delay,
                environment=environment,
                on_output_factory=_make_cb,
                on_state_factory=_make_state_cb if not args.legacy_output else None,
            ):
                cid = result.case.case_id
                if result.error:
                    progress.mark_done(cid, f"ERROR: {result.error}")
                else:
                    s = result.summary
                    progress.mark_done(cid, f"pass={s.passed}/{s.total}")
                _on_result(result)
        else:
            for i, case in enumerate(cases, 1):
                progress.set_active(case.case_id)
                logger.info("[%d/%d] [cyan]%s[/cyan] ...", i, len(cases), case.case_id)

                # Allocate run_id upfront so state can be written incrementally
                state_cb = None
                if not args.legacy_output:
                    fid, fmt, pid = _case_identity(case)
                    rid = recorder.next_run_id(fid, fmt, pid)
                    _allocated_run_ids[case.case_id] = rid
                    recorder.init_run_dir(fid, fmt, pid, rid)
                    state_cb = _make_state_callback(recorder, fid, fmt, pid, rid)

                result = run_case(
                    case,
                    model=args.model,
                    timeout=timeout_seconds,
                    max_turns=args.max_turns,
                    environment=environment,
                    on_output=progress.make_callback(case.case_id),
                    on_state=state_cb,
                )
                progress.mark_done(case.case_id, "")
                _on_result(result)
                if result.error:
                    logger.info("[%d/%d] DONE %s  [red]ERROR[/red]: %s", i, len(cases), case.case_id, result.error)
                else:
                    s = result.summary
                    t = _format_duration(result.wall_clock_ms)
                    warn = " [yellow](stale trace)[/yellow]" if result.stale_trace else ""
                    logger.info(
                        "[%d/%d] DONE %s  pass=%d fail=%d skip=%d rate=%.0f%% %s%s",
                        i, len(cases), case.case_id,
                        s.passed, s.failed, s.skipped, s.pass_rate * 100, t, warn,
                    )
    finally:
        progress.stop()

    if args.enrich_tokens and records and not args.legacy_output:
        _enrich_with_tokens(recorder, records)

    # Print results
    record_by_case = {}
    for rec in records:
        record_by_case[f"{rec.fixture_id}/{rec.format}/{rec.prompt_id}"] = rec

    console.print()
    console.rule("[bold]RESULTS[/bold]", style="blue")
    console.print()

    for result in results:
        if result.summary:
            key = f"{result.summary.metadata.fixture_id}/{result.summary.metadata.format}/{result.summary.metadata.prompt_id}"
            console.print(format_run_summary(result.summary, record=record_by_case.get(key)))
            console.print()
        else:
            console.print(f"[red]FAILED:[/red] {result.case.case_id} -- {result.error}")
            console.print()

    if args.legacy_output:
        args.output.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for result in results:
            if result.summary:
                filename = f"{result.case.case_id.replace('/', '_')}_{timestamp}.json"
                write_json(result.summary, args.output / filename)

    succeeded = sum(1 for r in results if r.error is None)
    failed = sum(1 for r in results if r.error is not None)

    # Detect if all failures are auth-related
    auth_failures = [r for r in results if r.error and is_auth_error(r.error)]

    console.rule(style="dim")
    if failed == 0:
        console.print(f"[bold green]Done.[/bold green] {succeeded} succeeded, {failed} failed.")
    elif auth_failures and len(auth_failures) == failed:
        console.print(
            f"[bold red]Done.[/bold red] All {failed} cases failed due to authentication errors.\n"
            f"[yellow]Hint:[/yellow] Run [bold]claude[/bold] in a terminal to re-authenticate, then retry."
        )
    else:
        console.print(f"[bold yellow]Done.[/bold yellow] {succeeded} succeeded, [red]{failed} failed[/red].")

    if succeeded and not args.legacy_output:
        console.print(f"Structured results written to [green]{args.results_dir}/[/green]")

    return 0 if failed == 0 else 1


def _install_signal_handlers() -> None:
    """Install signal handlers that kill all child processes on Ctrl+C / SIGTERM."""
    def _handle(signum, frame):
        shutdown_all()
        # Restore default handler and re-raise so Python exits normally
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)


def baselines_only(args: argparse.Namespace) -> int:
    """Compute or refresh baselines for all fixtures and exit."""
    filters = _build_filters(args)
    _, _, _, _, _, cases = discover(args.benchmark_root, filters)

    if not cases:
        console.print("[red]No cases discovered.[/red] No fixtures to baseline.")
        return 1

    console.print("[bold]Fixture baselines[/bold]")
    baselines = warm_baselines(cases, refresh=args.refresh_baselines, output_dir=args.results_dir)
    console.print(
        f"\n[bold green]Done.[/bold green] {len(baselines)} fixture(s) baselined."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    _install_signal_handlers()
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(
            console=console, rich_tracebacks=True, markup=True, show_path=False,
        )],
    )
    if args.dry_run:
        return dry_run(args)
    if args.baselines_only:
        return baselines_only(args)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())

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
    python3 benchmark/run_benchmark.py --workers 4 --delay 5
    python3 benchmark/run_benchmark.py --no-enrich-tokens
    python3 benchmark/run_benchmark.py --legacy-output
"""

from __future__ import annotations

import argparse
import logging
import sys
import os
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel

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
    run_case, run_all, run_parallel, DEFAULT_MODEL, CaseResult,
)
from environment import BenchmarkEnvironment
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
    parser.add_argument("--timeout", type=float, default=15, help="Per-case timeout in minutes (default: 15).")
    parser.add_argument("--max-turns", type=int, default=None, help="Max CLI turns.")
    parser.add_argument("--dry-run", action="store_true", help="Show cases without executing.")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers (1=sequential).")
    parser.add_argument("--delay", type=float, default=10.0, help="Rate-limit delay (seconds).")
    parser.add_argument("--enrich-tokens", action="store_true", default=True, dest="enrich_tokens")
    parser.add_argument("--no-enrich-tokens", action="store_false", dest="enrich_tokens")
    parser.add_argument("--legacy-output", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true", default=False)

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


def _enrich_with_tokens(recorder: Recorder, records: list[RunRecord]) -> None:
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
    for record in records:
        if not record.session_id:
            continue
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


def run(args: argparse.Namespace) -> int:
    filters = _build_filters(args)
    _, _, _, _, _, cases = discover(args.benchmark_root, filters)

    if not cases:
        console.print("[red]No cases discovered.[/red] Nothing to run.")
        return 1

    environment = BenchmarkEnvironment()

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

    if args.workers > 1:
        results = run_parallel(
            cases,
            model=args.model,
            timeout=timeout_seconds,
            max_turns=args.max_turns,
            workers=args.workers,
            delay_s=args.delay,
            environment=environment,
        )
    else:
        for i, case in enumerate(cases, 1):
            logger.info("[%d/%d] [cyan]%s[/cyan] ...", i, len(cases), case.case_id)
            result = run_case(
                case,
                model=args.model,
                timeout=timeout_seconds,
                max_turns=args.max_turns,
                environment=environment,
            )
            results.append(result)
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

    # Build RunRecords and save
    if not args.legacy_output:
        for result in results:
            if result.summary:
                run_id = recorder.next_run_id(
                    result.summary.metadata.fixture_id,
                    result.summary.metadata.format,
                    result.summary.metadata.prompt_id,
                )
                record = RunRecord.from_run_summary(
                    result.summary, run_id,
                    wall_clock_ms=result.wall_clock_ms,
                    raw_output=result.raw_output,
                    exit_code=result.exit_code,
                    stderr=result.stderr,
                    workspace_state=result.workspace_state,
                    max_turns_configured=args.max_turns or 0,
                )
                recorder.save_run(record)
                records.append(record)

                if result.trace_path and result.trace_path.exists():
                    try:
                        import json
                        with open(result.trace_path, "r", encoding="utf-8") as f:
                            trace_data = [json.loads(line) for line in f if line.strip()]
                        recorder.save_trace(record, trace_data)
                    except Exception:
                        logger.debug("Failed to save trace for %s", result.case.case_id)

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

    console.rule(style="dim")
    if failed == 0:
        console.print(f"[bold green]Done.[/bold green] {succeeded} succeeded, {failed} failed.")
    else:
        console.print(f"[bold yellow]Done.[/bold yellow] {succeeded} succeeded, [red]{failed} failed[/red].")

    if succeeded and not args.legacy_output:
        console.print(f"Structured results written to [green]{args.results_dir}/[/green]")

    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
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
    return run(args)


if __name__ == "__main__":
    sys.exit(main())

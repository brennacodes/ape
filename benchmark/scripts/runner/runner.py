"""
Execute benchmark test cases in isolated workspaces.

Each case runs in its own temp directory containing a copy of the app
fixture, with the workflow document injected based on format. The
environment is scrubbed to prevent context leakage.

Public API
----------
build_command(prompt_text, model, max_turns) -> list[str]
run_case(case, model, timeout, max_turns, environment) -> CaseResult
run_all(cases, ...) -> list[CaseResult]
run_parallel(cases, ...) -> list[CaseResult]
"""

from __future__ import annotations

import logging
import subprocess
import sys
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("bench.runner")

# Wire up sibling modules
_HERE = os.path.dirname(os.path.abspath(__file__))
_EVAL = os.path.join(_HERE, "..", "evaluator")
_COORD = os.path.join(_HERE, "..", "coordinator")
_RESULTS = os.path.join(_HERE, "..", "results")
for _dir in (_EVAL, _COORD, _RESULTS):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

from trace import Trace, load_trace, parse_trace_jsonl  # noqa: E402
from evaluator import evaluate, CheckResult  # noqa: E402
from coordinator import (  # noqa: E402
    TestCase, load_test_config, load_prompt, load_app_config,
    build_context, get_app_config_variables, interpolate_prompt,
)
from results import (  # noqa: E402
    RunMetadata, RunSummary, CheckOutcome,
    make_outcome, summarize_run, write_json,
)
from environment import BenchmarkEnvironment  # noqa: E402


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    """Result of running one benchmark case."""
    case: TestCase
    summary: Optional[RunSummary]
    trace_path: Optional[Path]
    error: Optional[str]
    wall_clock_ms: float = 0.0
    raw_output: str = ""
    exit_code: int = 0
    stderr: str = ""
    workspace_state: dict = field(default_factory=dict)
    run_id: int = 0
    stale_trace: bool = False


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "claude-sonnet-4-20250514"


def build_command(
    prompt_text: str,
    model: str = DEFAULT_MODEL,
    max_turns: int | None = None,
) -> list[str]:
    """Build the CLI args to invoke Claude Code with a prompt."""
    cmd = [
        "claude",
        "-p", prompt_text,
        "--model", model,
        "--output-format", "stream-json",
        "--verbose",
    ]
    if max_turns is not None:
        cmd.extend(["--max-turns", str(max_turns)])
    return cmd


# ---------------------------------------------------------------------------
# Trace discovery
# ---------------------------------------------------------------------------

def find_latest_trace(session_dir: Path) -> Optional[Path]:
    """Find the most recently modified JSONL file in a session directory."""
    if not session_dir.is_dir():
        return None
    jsonl_files = list(session_dir.glob("*.jsonl"))
    if not jsonl_files:
        return None
    return max(jsonl_files, key=lambda f: f.stat().st_mtime)


# ---------------------------------------------------------------------------
# Case execution
# ---------------------------------------------------------------------------

def execute_cli(
    command: list[str],
    timeout: int = 900,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a Claude Code CLI command as a subprocess."""
    if env is None:
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        env=env,
    )


def check_results_to_outcomes(results: list[CheckResult]) -> list[CheckOutcome]:
    """Convert evaluator CheckResults to results module CheckOutcomes."""
    return [
        make_outcome(
            check_id=r.check_id,
            phase=r.phase,
            passed=r.passed,
            skip_reason=r.skip_reason,
            detail=r.detail,
        )
        for r in results
    ]


def _run_in_workspace(
    case: TestCase,
    workspace_path: Path,
    env: BenchmarkEnvironment,
    executor: Any,
    prompt_text: str,
    checks: list,
    context: dict,
    model: str,
    timeout: int,
    max_turns: int | None,
) -> CaseResult:
    """Execute a test case inside an already-created workspace.

    Called by run_case() inside a try/finally that guarantees teardown.
    """
    workspace_state: dict = {}

    # 3. Build command — prepend workflow content for plain-text format
    workflow_content = env.get_workflow_content(
        case.workflow.path, case.workflow.format,
    )
    if workflow_content:
        full_prompt = f"{workflow_content}\n\n---\n\nUser task:\n{prompt_text}"
        logger.info(
            "%s: workflow prepended (%d chars) + prompt (%d chars) = %d chars total",
            case.case_id, len(workflow_content), len(prompt_text), len(full_prompt),
        )
    else:
        full_prompt = prompt_text
        logger.info(
            "%s: workflow placed as CLAUDE.md (format=%s), prompt only (%d chars)",
            case.case_id, case.workflow.format, len(full_prompt),
        )
    command = build_command(full_prompt, model, max_turns=max_turns)

    logger.debug("%s: cwd=%s", case.case_id, workspace_path)

    # 4. Execute CLI in isolated workspace
    cli_start = time.time()
    t0 = time.perf_counter_ns()
    cli_env = env.build_env(workspace_path)
    try:
        result = executor(command, timeout, cwd=workspace_path, env=cli_env)
    except subprocess.TimeoutExpired:
        logger.warning("CLI timeout for %s after %ds", case.case_id, timeout)
        return CaseResult(case=case, summary=None, trace_path=None, error="CLI timeout")
    except Exception as exc:
        logger.warning("CLI error for %s: %s", case.case_id, exc)
        return CaseResult(case=case, summary=None, trace_path=None, error=f"CLI error: {exc}")
    finally:
        wall_clock_ns = time.perf_counter_ns() - t0

    wall_clock_ms = wall_clock_ns / 1_000_000
    raw_output = getattr(result, "stdout", "") or ""
    exit_code = getattr(result, "returncode", 0) or 0
    stderr = getattr(result, "stderr", "") or ""

    # 5. Capture workspace state
    try:
        ws = env.capture_state(workspace_path)
        workspace_state = {
            "git_log": ws.git_log,
            "modified_files": ws.modified_files,
            "git_status": ws.git_status,
            "committed_files": ws.committed_files,
        }
    except Exception:
        pass

    if exit_code != 0:
        first_line = stderr.strip().splitlines()[0] if stderr.strip() else raw_output[:200] or "no output"
        logger.warning("CLI exited %d for %s: %s", exit_code, case.case_id, first_line)
        return CaseResult(
            case=case, summary=None, trace_path=None,
            error=f"CLI exited with code {exit_code}: {first_line}",
            wall_clock_ms=wall_clock_ms, raw_output=raw_output,
            exit_code=exit_code, stderr=stderr,
            workspace_state=workspace_state,
        )

    # 6. Parse trace — try stdout first, then session file in workspace
    trace = None
    trace_path = None
    stale_trace = False

    if raw_output.strip():
        try:
            trace = parse_trace_jsonl(raw_output)
            logger.debug(
                "Parsed trace from CLI stdout (%d events) for %s",
                len(trace.events), case.case_id,
            )
        except (ValueError, Exception):
            logger.debug("Stdout not parseable as trace for %s, trying session file", case.case_id)

    if trace is None:
        session_dir = env.get_session_dir(workspace_path)
        trace_path = find_latest_trace(session_dir)

        if trace_path is None:
            msg = f"No session trace found in {session_dir}"
            if not session_dir.is_dir():
                msg += " (directory does not exist)"
            logger.warning("%s for %s", msg, case.case_id)
            return CaseResult(
                case=case, summary=None, trace_path=None,
                error=msg,
                wall_clock_ms=wall_clock_ms, raw_output=raw_output,
                exit_code=exit_code, stderr=stderr,
                workspace_state=workspace_state,
            )

        if trace_path.stat().st_mtime < cli_start:
            stale_trace = True
            logger.warning("Stale trace for %s — trace predates CLI invocation", case.case_id)

        try:
            trace = load_trace(trace_path)
        except Exception as exc:
            return CaseResult(
                case=case, summary=None, trace_path=trace_path,
                error=f"Trace parse error: {exc}",
                wall_clock_ms=wall_clock_ms, raw_output=raw_output,
                exit_code=exit_code, stderr=stderr,
                workspace_state=workspace_state,
            )

    # 7. Evaluate
    context["workspace_state"] = workspace_state
    try:
        check_results = evaluate(trace, checks, context)
    except Exception as exc:
        return CaseResult(
            case=case, summary=None, trace_path=trace_path,
            error=f"Evaluation error: {exc}",
            wall_clock_ms=wall_clock_ms, raw_output=raw_output,
            exit_code=exit_code, stderr=stderr,
            workspace_state=workspace_state,
        )

    # 8. Summarize
    outcomes = check_results_to_outcomes(check_results)
    # For template cases, prompt_id encodes category/item for unique identification
    if case.category and case.item_id:
        effective_prompt_id = f"{case.category}/{case.item_id}"
    else:
        effective_prompt_id = case.prompt.prompt_id

    metadata = RunMetadata(
        fixture_id=case.app.name,
        format=case.workflow.format,
        prompt_id=effective_prompt_id,
        model=model,
        session_id=trace.session_id,
    )
    summary = summarize_run(outcomes, metadata)

    return CaseResult(
        case=case, summary=summary, trace_path=trace_path, error=None,
        wall_clock_ms=wall_clock_ms, raw_output=raw_output,
        exit_code=exit_code, stderr=stderr,
        workspace_state=workspace_state,
        stale_trace=stale_trace,
    )


def run_case(
    case: TestCase,
    model: str = DEFAULT_MODEL,
    timeout: int = 900,
    max_turns: int | None = None,
    environment: BenchmarkEnvironment | None = None,
    _execute: Any = None,
) -> CaseResult:
    """Execute one TestCase end-to-end in an isolated workspace.

    Steps:
    1. Load prompt and test-config.
    2. Set up isolated workspace (copy app, inject workflow, git init, scrub env).
    3. Build CLI command — prepend workflow for plain-text, otherwise just the prompt.
    4. Execute CLI with cwd=workspace and scrubbed env.
    5. Capture workspace state.
    6. Find and parse the session trace.
    7. Evaluate against test-config checks.
    8. Summarize and tear down.
    """
    executor = _execute or execute_cli
    env = environment or BenchmarkEnvironment()
    workspace_path = None
    workspace_state = {}

    # 1. Load prompt and test-config
    try:
        prompt_data = load_prompt(case.prompt.path)
        config_data = load_test_config(case.test_config.path)
    except Exception as exc:
        logger.warning("Load error for %s: %s", case.case_id, exc)
        return CaseResult(case=case, summary=None, trace_path=None, error=f"Load error: {exc}")

    prompt_text = prompt_data.get("prompt", "")
    checks = config_data.get("checks", [])

    # For template cases, load app-config and interpolate prompt + context
    app_config_variables = None
    if case.category and case.item_id and case.app_config_path:
        try:
            ac_data = load_app_config(case.app_config_path)
            app_config_variables = get_app_config_variables(
                ac_data, case.category, case.item_id,
            )
            prompt_text = interpolate_prompt(prompt_text, app_config_variables)
            logger.debug(
                "%s: interpolated prompt for %s/%s (%d vars)",
                case.case_id, case.category, case.item_id,
                len(app_config_variables),
            )
        except Exception as exc:
            logger.warning("App-config interpolation error for %s: %s", case.case_id, exc)

    context = build_context(
        prompt_data, config_data,
        app_config_variables=app_config_variables,
    )

    # 2. Set up isolated workspace
    try:
        workspace_path = env.setup(
            case.app.path,
            case.workflow.path,
            case.workflow.format,
        )
    except Exception as exc:
        return CaseResult(case=case, summary=None, trace_path=None, error=f"Workspace setup error: {exc}")

    # Wrap entire workspace lifecycle in try/finally so teardown is
    # guaranteed even on unexpected exceptions.
    try:
        return _run_in_workspace(
            case, workspace_path, env, executor,
            prompt_text, checks, context, model, timeout, max_turns,
        )
    finally:
        env.teardown(workspace_path)


def run_all(
    cases: list[TestCase],
    model: str = DEFAULT_MODEL,
    timeout: int = 900,
    max_turns: int | None = None,
    environment: BenchmarkEnvironment | None = None,
    _execute: Any = None,
) -> list[CaseResult]:
    """Run multiple cases sequentially."""
    return [
        run_case(case, model, timeout, max_turns, environment, _execute)
        for case in cases
    ]


def run_parallel(
    cases: list[TestCase],
    model: str = DEFAULT_MODEL,
    timeout: int = 900,
    max_turns: int | None = None,
    workers: int = 1,
    delay_s: float = 10.0,
    environment: BenchmarkEnvironment | None = None,
    _execute: Any = None,
) -> list[CaseResult]:
    """Run multiple cases concurrently with rate limiting."""
    if workers <= 1:
        return run_all(cases, model, timeout, max_turns, environment, _execute)

    logger.info(
        "Starting parallel execution: %d cases, %d workers, %.1fs delay",
        len(cases), workers, delay_s,
    )

    rate_lock = threading.Lock()
    last_launch = [0.0]

    def _run_one(case: TestCase) -> CaseResult:
        with rate_lock:
            now = time.monotonic()
            wait = delay_s - (now - last_launch[0])
            if wait > 0:
                time.sleep(wait)
            last_launch[0] = time.monotonic()
        return run_case(case, model, timeout, max_turns, environment, _execute)

    results: list[CaseResult | None] = [None] * len(cases)
    completed = 0
    total = len(cases)

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {
                pool.submit(_run_one, case): i
                for i, case in enumerate(cases)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                completed += 1
                try:
                    result = future.result()
                    results[idx] = result
                    if result.error:
                        status = f"ERROR: {result.error}"
                    elif result.summary:
                        s = result.summary
                        status = f"pass={s.passed}/{s.total} rate={s.pass_rate:.0%}"
                    else:
                        status = "no summary"
                    logger.info(
                        "[%d/%d] DONE %s  %s",
                        completed, total, cases[idx].case_id, status,
                    )
                except Exception as exc:
                    logger.exception(
                        "[%d/%d] FAILED %s",
                        completed, total, cases[idx].case_id,
                    )
                    results[idx] = CaseResult(
                        case=cases[idx], summary=None, trace_path=None,
                        error=f"Parallel execution error: {exc}",
                    )
    except KeyboardInterrupt:
        logger.info("Interrupted — cancelling %d pending cases", total - completed)
        for i, r in enumerate(results):
            if r is None:
                results[i] = CaseResult(
                    case=cases[i], summary=None, trace_path=None,
                    error="Cancelled by user",
                )

    return results  # type: ignore[return-value]

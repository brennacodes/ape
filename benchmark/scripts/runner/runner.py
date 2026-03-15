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
run_parallel(cases, ...) -> Iterator[CaseResult]
"""

from __future__ import annotations

import logging
import signal
import subprocess
import sys
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger("bench.runner")

# ---------------------------------------------------------------------------
# Subprocess tracking for graceful shutdown
# ---------------------------------------------------------------------------

_active_processes: set[subprocess.Popen] = set()
_process_lock = threading.Lock()
_shutting_down = False


def _kill_process_group(proc: subprocess.Popen) -> None:
    """Send SIGTERM to a process's entire process group."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass


def shutdown_all() -> None:
    """Kill all running subprocess trees. Safe to call from signal handlers."""
    global _shutting_down
    _shutting_down = True
    with _process_lock:
        procs = list(_active_processes)
    for proc in procs:
        _kill_process_group(proc)

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
from environment import BenchmarkEnvironment, SetupSnapshot  # noqa: E402


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
    prompt_text: str = ""
    eval_conditions: dict = field(default_factory=dict)
    eval_variables: dict = field(default_factory=dict)
    started_at: str = ""
    stream_path: Optional[Path] = None


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "claude-opus-4-6"


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
    on_output: Any = None,
    stream_path: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run a Claude Code CLI command as a subprocess.

    Uses Popen with start_new_session=True so each child gets its own
    process group, allowing clean shutdown of the entire tree on
    Ctrl+C / SIGTERM.

    If *on_output* is provided it is called with each stdout line as it
    arrives, enabling live progress display.

    If *stream_path* is provided, each stdout line is tee'd to that file
    as it arrives (JSONL).  After the process exits the file is converted
    to a valid JSON array via ``jq -s '.'``.
    """
    if _shutting_down:
        raise KeyboardInterrupt("Shutdown in progress")
    if env is None:
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

    stream_file = None
    if stream_path is not None:
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        stream_file = open(stream_path, "w", encoding="utf-8")

    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    with _process_lock:
        _active_processes.add(proc)
    try:
        # Always stream line-by-line so we can tee to file + on_output.
        stderr_chunks: list[str] = []

        def _drain_stderr():
            for line in proc.stderr:
                stderr_chunks.append(line)

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        stdout_lines: list[str] = []
        deadline = time.monotonic() + timeout
        for line in proc.stdout:
            if time.monotonic() > deadline:
                _kill_process_group(proc)
                proc.wait()
                raise subprocess.TimeoutExpired(command, timeout)
            stdout_lines.append(line)
            if stream_file is not None:
                stream_file.write(line)
                stream_file.flush()
            if on_output is not None:
                try:
                    on_output(line)
                except Exception:
                    pass

        proc.wait()
        stderr_thread.join(timeout=5)
        return subprocess.CompletedProcess(
            args=command,
            returncode=proc.returncode,
            stdout="".join(stdout_lines),
            stderr="".join(stderr_chunks),
        )
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        proc.wait()
        raise
    except KeyboardInterrupt:
        _kill_process_group(proc)
        proc.wait()
        raise
    finally:
        with _process_lock:
            _active_processes.discard(proc)
        if stream_file is not None:
            stream_file.close()
        if stream_path is not None and stream_path.exists():
            _jsonl_to_json_array(stream_path)


def _jsonl_to_json_array(path: Path) -> None:
    """Convert a JSONL file to a JSON array in-place using jq."""
    tmp = path.with_suffix(".tmp")
    try:
        with open(path, "r") as fin, open(tmp, "w") as fout:
            result = subprocess.run(
                ["jq", "-s", "."],
                stdin=fin,
                stdout=fout,
                timeout=30,
            )
        if result.returncode == 0:
            tmp.rename(path)
        else:
            tmp.unlink(missing_ok=True)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # jq not available or failed — fall back to Python
        tmp.unlink(missing_ok=True)
        _jsonl_to_json_array_python(path)


def _jsonl_to_json_array_python(path: Path) -> None:
    """Fallback: convert JSONL to JSON array using Python (when jq is unavailable)."""
    import json as _json
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(_json.loads(line))
            except _json.JSONDecodeError:
                continue
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(events, f, indent=2)


def is_auth_error(error_message: str) -> bool:
    """Return True if the error message indicates an authentication failure."""
    auth_indicators = [
        "authentication_error",
        "authentication_failed",
        "OAuth token has expired",
        "invalid_api_key",
        "api key",
        "unauthorized",
        "401",
    ]
    lower = error_message.lower()
    return any(indicator.lower() in lower for indicator in auth_indicators)


def _extract_cli_error(raw_output: str, stderr: str, exit_code: int) -> str:
    """Extract a human-readable error from CLI output when it exits non-zero.

    Parses the stream-json output to find the actual error message rather
    than naively truncating the first line (which is usually the init JSON).
    """
    import json as _json

    # Prefer stderr if available
    if stderr.strip():
        return f"CLI exited with code {exit_code}: {stderr.strip().splitlines()[0]}"

    # Parse stream-json lines looking for error information
    if raw_output.strip():
        for line in raw_output.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue

            # Check for result messages with errors
            if obj.get("type") == "result" and obj.get("is_error"):
                result_text = obj.get("result", "")
                if result_text:
                    return f"CLI exited with code {exit_code}: {result_text}"

            # Check for assistant messages with error field
            if obj.get("error"):
                msg = obj.get("message", {})
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            return f"CLI exited with code {exit_code}: {block.get('text', '')}"
                error_type = obj.get("error", "")
                return f"CLI exited with code {exit_code}: {error_type}"

    # Fallback: first 200 chars of raw output
    fallback = raw_output[:200] or "no output"
    return f"CLI exited with code {exit_code}: {fallback}"


def check_results_to_outcomes(
    results: list[CheckResult],
    category: str = "",
) -> list[CheckOutcome]:
    """Convert evaluator CheckResults to results module CheckOutcomes."""
    return [
        make_outcome(
            check_id=r.check_id,
            phase=r.phase,
            passed=r.passed,
            skip_reason=r.skip_reason,
            detail=r.detail,
            category=category or None,
            metric_value=r.metric_value,
            target_value=r.target_value,
            operator=r.operator,
            eval_trace=r.eval_trace,
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
    on_output: Any = None,
    on_state: Any = None,
) -> CaseResult:
    """Execute a test case inside an already-created workspace.

    Called by run_case() inside a try/finally that guarantees teardown.

    Captures setup state here — right before the CLI command is built —
    so the snapshot reflects the true workspace after ALL setup (including
    baseline capture) and before the prompt is submitted.

    *on_state*, when provided, is called with the workspace_state dict
    each time it is updated so the caller can persist it incrementally.
    """
    workspace_state: dict = {}

    # Capture setup state NOW — after all setup, before prompt submission.
    setup_snapshot: SetupSnapshot | None = None
    try:
        setup_snapshot = env.capture_setup_state(workspace_path, case_id=case.case_id)
    except Exception:
        logger.warning("%s: failed to capture setup state", case.case_id, exc_info=True)

    # Write setup snapshot to disk immediately so it's visible during the run
    if on_state is not None and setup_snapshot is not None:
        from dataclasses import asdict as _asdict
        before_dict: dict[str, Any] = {
            "file_list": list(setup_snapshot.file_list),
            "git_log": setup_snapshot.git_log,
            "git_status": setup_snapshot.git_status,
            "claude_md_content": setup_snapshot.claude_md_content,
        }
        if setup_snapshot.baseline is not None:
            before_dict["baseline"] = _asdict(setup_snapshot.baseline)
        workspace_state["before"] = before_dict
        on_state(workspace_state)

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
    logger.info("%s: executing CLI (timeout=%ds)", case.case_id, timeout)
    import tempfile
    from datetime import datetime as _dt
    started_at = _dt.now().isoformat()
    cli_start = time.time()
    t0 = time.perf_counter_ns()
    cli_env = env.build_env(workspace_path)
    # Stream file goes outside the workspace so it survives teardown
    stream_fd, stream_tmp = tempfile.mkstemp(suffix=".json", prefix="bench-stream-")
    os.close(stream_fd)
    stream_file = Path(stream_tmp)
    try:
        kwargs: dict[str, Any] = dict(cwd=workspace_path, env=cli_env)
        if on_output is not None:
            kwargs["on_output"] = on_output
        kwargs["stream_path"] = stream_file
        result = executor(command, timeout, **kwargs)
    except subprocess.TimeoutExpired:
        logger.warning("CLI timeout for %s after %ds", case.case_id, timeout)
        return CaseResult(case=case, summary=None, trace_path=None, error="CLI timeout", prompt_text=full_prompt, started_at=started_at, stream_path=stream_file)
    except Exception as exc:
        logger.warning("CLI error for %s: %s", case.case_id, exc)
        return CaseResult(case=case, summary=None, trace_path=None, error=f"CLI error: {exc}", prompt_text=full_prompt, started_at=started_at, stream_path=stream_file)
    finally:
        wall_clock_ns = time.perf_counter_ns() - t0

    wall_clock_ms = wall_clock_ns / 1_000_000
    raw_output = getattr(result, "stdout", "") or ""
    exit_code = getattr(result, "returncode", 0) or 0
    stderr = getattr(result, "stderr", "") or ""
    logger.info(
        "%s: CLI finished in %.1fs (exit %d)",
        case.case_id, wall_clock_ms / 1000, exit_code,
    )

    # 5. Capture workspace state
    logger.info("%s: capturing post-run workspace state", case.case_id)
    try:
        ws = env.capture_state(workspace_path, setup_snapshot=setup_snapshot, case_id=case.case_id)
        workspace_state = {
            "git_log": ws.git_log,
            "modified_files": ws.modified_files,
            "git_status": ws.git_status,
            "committed_files": ws.committed_files,
            "full_diff": ws.full_diff,
        }
        if ws.before is not None:
            before_dict: dict[str, Any] = {
                "file_list": list(ws.before.file_list),
                "git_log": ws.before.git_log,
                "git_status": ws.before.git_status,
                "claude_md_content": ws.before.claude_md_content,
            }
            if ws.before.baseline is not None:
                from dataclasses import asdict
                before_dict["baseline"] = asdict(ws.before.baseline)
            workspace_state["before"] = before_dict
    except Exception:
        pass

    # 5b. Check memory isolation — flag if any memory files were created
    try:
        memory_leak = env.check_memory_leak(workspace_path)
        if memory_leak:
            workspace_state["memory_leak"] = memory_leak
            logger.warning(
                "Memory isolation failure for %s: %s",
                case.case_id, memory_leak,
            )
    except Exception:
        pass

    if on_state is not None:
        on_state(workspace_state)

    if exit_code != 0:
        error_detail = _extract_cli_error(raw_output, stderr, exit_code)
        logger.warning("CLI exited %d for %s: %s", exit_code, case.case_id, error_detail)
        return CaseResult(
            case=case, summary=None, trace_path=None,
            error=error_detail,
            wall_clock_ms=wall_clock_ms, raw_output=raw_output,
            exit_code=exit_code, stderr=stderr,
            workspace_state=workspace_state,
            prompt_text=full_prompt,
            started_at=started_at,
            stream_path=stream_file,
        )

    # 6. Parse trace — try stdout first, then session file in workspace
    logger.info("%s: parsing session trace", case.case_id)
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
                prompt_text=full_prompt,
                started_at=started_at,
                stream_path=stream_file,
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
                prompt_text=full_prompt,
                started_at=started_at,
                stream_path=stream_file,
            )

    # 7. Evaluate
    logger.info("%s: evaluating %d checks", case.case_id, len(checks))
    context["workspace_state"] = workspace_state
    context["workspace_path"] = str(workspace_path)
    # Capture evaluation context for re-evaluation reproducibility
    eval_conditions = dict(context.get("conditions", {}))
    eval_variables = dict(context.get("variables", {}))
    try:
        check_results = evaluate(trace, checks, context)
    except Exception as exc:
        return CaseResult(
            case=case, summary=None, trace_path=trace_path,
            error=f"Evaluation error: {exc}",
            wall_clock_ms=wall_clock_ms, raw_output=raw_output,
            exit_code=exit_code, stderr=stderr,
            workspace_state=workspace_state,
            prompt_text=full_prompt,
            eval_conditions=eval_conditions,
            eval_variables=eval_variables,
            started_at=started_at,
            stream_path=stream_file,
        )

    # 8. Summarize
    logger.info("%s: summarizing results", case.case_id)
    outcomes = check_results_to_outcomes(check_results, category=case.category)
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
        prompt_text=full_prompt,
        eval_conditions=eval_conditions,
        eval_variables=eval_variables,
        started_at=started_at,
        stream_path=stream_file,
    )


def run_case(
    case: TestCase,
    model: str = DEFAULT_MODEL,
    timeout: int = 900,
    max_turns: int | None = None,
    environment: BenchmarkEnvironment | None = None,
    _execute: Any = None,
    on_output: Any = None,
    on_state: Any = None,
) -> CaseResult:
    """Execute one TestCase end-to-end in an isolated workspace.

    Steps:
    1. Load prompt and test-config.
    2. Set up isolated workspace (copy app, inject workflow, git init, scrub env).
    3. Build CLI command — prepend workflow for plain-text; otherwise (markdown, adhoc-xml, structured-md, ape) just the prompt.
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
    fixture_workflow_files: list[str] | None = None
    if case.category and case.item_id and case.app_config_path:
        try:
            ac_data = load_app_config(case.app_config_path)
            app_config_variables = get_app_config_variables(
                ac_data, case.category, case.item_id,
            )
            prompt_text = interpolate_prompt(prompt_text, app_config_variables)
            fixture_workflow_files = ac_data.get("workflow_files")
            logger.debug(
                "%s: interpolated prompt for %s/%s (%d vars)",
                case.case_id, case.category, case.item_id,
                len(app_config_variables),
            )
        except Exception as exc:
            logger.warning("App-config interpolation error for %s: %s", case.case_id, exc)
    elif case.app_config_path:
        # Non-template case but app config exists — still load workflow_files
        try:
            ac_data = load_app_config(case.app_config_path)
            fixture_workflow_files = ac_data.get("workflow_files")
        except Exception:
            pass

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
            fixture_workflow_files=fixture_workflow_files,
            case_id=case.case_id,
        )
    except Exception as exc:
        return CaseResult(case=case, summary=None, trace_path=None, error=f"Workspace setup error: {exc}")

    # Wrap entire workspace lifecycle in try/finally so teardown is
    # guaranteed even on unexpected exceptions.
    # NOTE: Setup state capture happens inside _run_in_workspace,
    # right before the CLI command is built, ensuring it reflects the
    # true workspace state after ALL setup and before prompt submission.
    try:
        return _run_in_workspace(
            case, workspace_path, env, executor,
            prompt_text, checks, context, model, timeout, max_turns,
            on_output=on_output,
            on_state=on_state,
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
    on_output: Any = None,
) -> list[CaseResult]:
    """Run multiple cases sequentially."""
    return [
        run_case(case, model, timeout, max_turns, environment, _execute, on_output=on_output)
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
    on_output: Any = None,
    on_output_factory: Any = None,
    on_state_factory: Any = None,
) -> Iterator[CaseResult]:
    """Run multiple cases concurrently with rate limiting.

    Yields each CaseResult as it completes so callers can persist
    results incrementally instead of waiting for the entire suite.

    *on_output_factory*, when provided, is called with a TestCase and
    must return a callback ``(line: str) -> None`` for that case.  This
    is preferred over *on_output* for parallel runs because it produces
    per-case callbacks that know which case each line belongs to.

    *on_state_factory*, when provided, is called with a TestCase and
    must return a callback ``(state: dict) -> None`` for that case.
    """
    if workers <= 1:
        yield from run_all(cases, model, timeout, max_turns, environment, _execute, on_output=on_output)
        return

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
        cb = on_output_factory(case) if on_output_factory else on_output
        state_cb = on_state_factory(case) if on_state_factory else None
        return run_case(case, model, timeout, max_turns, environment, _execute, on_output=cb, on_state=state_cb)

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
                    if result.error:
                        status = f"[red]ERROR[/red]: {result.error}"
                    elif result.summary:
                        s = result.summary
                        status = f"pass={s.passed}/{s.total} rate={s.pass_rate:.0%}"
                    else:
                        status = "no summary"
                    logger.info(
                        "[%d/%d] DONE %s  %s",
                        completed, total, cases[idx].case_id, status,
                    )
                    yield result
                except Exception as exc:
                    logger.exception(
                        "[%d/%d] FAILED %s",
                        completed, total, cases[idx].case_id,
                    )
                    yield CaseResult(
                        case=cases[idx], summary=None, trace_path=None,
                        error=f"Parallel execution error: {exc}",
                    )
    except KeyboardInterrupt:
        logger.info("Interrupted — %d/%d cases completed before cancellation", completed, total)

"""Tests for benchmark/scripts/runner/runner.py.

CLI execution is mocked via the _execute parameter — no real Claude Code
sessions are created. Traces are returned as stdout from mock executors.
"""

import json
import subprocess
import sys
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock

_HERE = os.path.dirname(os.path.abspath(__file__))
_COORD = os.path.join(_HERE, "..", "coordinator")
_RESULTS = os.path.join(_HERE, "..", "results")
_EVAL = os.path.join(_HERE, "..", "evaluator")
for _dir in (_COORD, _RESULTS, _EVAL):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

from runner import (
    CaseResult,
    DEFAULT_MODEL,
    build_command,
    find_latest_trace,
    execute_cli,
    check_results_to_outcomes,
    run_case,
    run_all,
)
from coordinator import AppFixture, WorkflowFixture, TestConfigPath, PromptPath, TestCase
from environment import BenchmarkEnvironment
from evaluator import CheckResult
from results import CheckOutcome


# ===========================================================================
# Helpers
# ===========================================================================

def _write(path: Path, content: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_app(tmp_path: Path) -> Path:
    app_dir = tmp_path / "apps" / "testapp"
    app_dir.mkdir(parents=True)
    _write(app_dir / "main.py", "print('hello')")
    return app_dir


def _make_workflow(tmp_path: Path, content: str = "WORKFLOW CONTENT") -> Path:
    f = tmp_path / "workflows" / "plain-text" / "test.txt"
    _write(f, content)
    return f


def _make_prompt(tmp_path: Path) -> Path:
    f = tmp_path / "prompts" / "test-prompt.yml"
    _write(f, "id: test-prompt\nprompt: fix the bug\nconditions:\n  is_informational: false\nvariables: {}")
    return f


def _make_config(tmp_path: Path) -> Path:
    config = {
        "fixture_id": "test",
        "checks": [
            {
                "id": "no_writes",
                "phase": "intake",
                "description": "No writes",
                "type": "gate",
                "condition": {
                    "metric": "tool_call.file_write",
                    "transform": "count",
                    "operator": "eq",
                    "target": 0,
                },
            }
        ],
    }
    import yaml
    f = tmp_path / "test-configs" / "test.yml"
    _write(f, yaml.dump(config))
    return f


def _make_case(tmp_path: Path) -> TestCase:
    app_path = _make_app(tmp_path)
    workflow_path = _make_workflow(tmp_path)
    config_path = _make_config(tmp_path)
    prompt_path = _make_prompt(tmp_path)
    return TestCase(
        app=AppFixture(app_path, "testapp"),
        workflow=WorkflowFixture(workflow_path, "test", "plain-text"),
        test_config=TestConfigPath(config_path, "test"),
        prompt=PromptPath(prompt_path, "test-prompt"),
    )


def _jsonl_line(type_: str, role: str, content) -> str:
    return json.dumps({
        "type": type_,
        "sessionId": "sess-test",
        "parentUuid": None,
        "message": {"role": role, "content": content},
    })


def _make_trace_stdout() -> str:
    """Build a minimal valid trace as JSONL string (returned as stdout)."""
    lines = [
        _jsonl_line("user", "user", "fix the bug"),
        _jsonl_line("assistant", "assistant", [{"type": "text", "text": "Done."}]),
    ]
    return "\n".join(lines)


def _make_trace_file(session_dir: Path, name: str = "trace.jsonl") -> Path:
    """Create a minimal valid trace JSONL file."""
    trace_file = session_dir / name
    _write(trace_file, _make_trace_stdout())
    return trace_file


def _write_stream_file(stream_path, content):
    """Write JSONL content to stream file and convert to JSON array."""
    if stream_path is None:
        return
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    events = []
    for line in content.splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    stream_path.write_text(json.dumps(events, indent=2), encoding="utf-8")


def _trace_execute(cmd, timeout, cwd=None, env=None, **kwargs):
    """Mock executor that returns a valid trace as stdout."""
    trace = _make_trace_stdout()
    _write_stream_file(kwargs.get("stream_path"), trace)
    return subprocess.CompletedProcess(
        args=cmd, returncode=0, stdout=trace, stderr="",
    )


def _noop_execute(cmd, timeout, cwd=None, env=None, **kwargs):
    """Mock executor that returns empty stdout (no trace)."""
    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


# ===========================================================================
# build_command
# ===========================================================================

class TestBuildCommand:
    def test_returns_list(self):
        cmd = build_command("fix bug")
        assert isinstance(cmd, list)

    def test_starts_with_claude(self):
        cmd = build_command("fix bug")
        assert cmd[0] == "claude"

    def test_includes_prompt_flag(self):
        cmd = build_command("fix bug")
        assert "-p" in cmd
        idx = cmd.index("-p") + 1
        assert cmd[idx] == "fix bug"

    def test_includes_model_flag(self):
        cmd = build_command("fix bug", model="custom-model")
        idx = cmd.index("--model") + 1
        assert cmd[idx] == "custom-model"

    def test_default_model(self):
        cmd = build_command("fix bug")
        idx = cmd.index("--model") + 1
        assert cmd[idx] == DEFAULT_MODEL

    def test_includes_output_format(self):
        cmd = build_command("fix bug")
        assert "--output-format" in cmd
        idx = cmd.index("--output-format") + 1
        assert cmd[idx] == "stream-json"

    def test_max_turns(self):
        cmd = build_command("fix bug", max_turns=10)
        idx = cmd.index("--max-turns") + 1
        assert cmd[idx] == "10"

    def test_no_max_turns_by_default(self):
        cmd = build_command("fix bug")
        assert "--max-turns" not in cmd


# ===========================================================================
# find_latest_trace
# ===========================================================================

class TestFindLatestTrace:
    def test_finds_single_file(self, tmp_path):
        trace = _make_trace_file(tmp_path)
        result = find_latest_trace(tmp_path)
        assert result == trace

    def test_finds_newest_file(self, tmp_path):
        import time
        _make_trace_file(tmp_path, "old.jsonl")
        time.sleep(0.05)
        newest = _make_trace_file(tmp_path, "new.jsonl")
        result = find_latest_trace(tmp_path)
        assert result == newest

    def test_returns_none_for_empty_dir(self, tmp_path):
        assert find_latest_trace(tmp_path) is None

    def test_returns_none_for_nonexistent_dir(self, tmp_path):
        assert find_latest_trace(tmp_path / "nope") is None

    def test_ignores_non_jsonl_files(self, tmp_path):
        _write(tmp_path / "notes.txt", "not a trace")
        assert find_latest_trace(tmp_path) is None

    def test_only_returns_jsonl(self, tmp_path):
        _write(tmp_path / "notes.txt", "not a trace")
        trace = _make_trace_file(tmp_path)
        assert find_latest_trace(tmp_path) == trace


# ===========================================================================
# check_results_to_outcomes
# ===========================================================================

class TestCheckResultsToOutcomes:
    def test_converts_passing(self):
        results = [CheckResult("c1", "p", "desc", True, None)]
        outcomes = check_results_to_outcomes(results)
        assert len(outcomes) == 1
        assert outcomes[0].check_id == "c1"
        assert outcomes[0].passed is True

    def test_converts_failing(self):
        results = [CheckResult("c2", "p", "desc", False, None)]
        outcomes = check_results_to_outcomes(results)
        assert outcomes[0].passed is False

    def test_converts_skipped(self):
        results = [CheckResult("c3", "p", "desc", None, "reason")]
        outcomes = check_results_to_outcomes(results)
        assert outcomes[0].passed is None
        assert outcomes[0].skip_reason == "reason"

    def test_empty_list(self):
        assert check_results_to_outcomes([]) == []

    def test_preserves_order(self):
        results = [
            CheckResult("c1", "p", "d", True, None),
            CheckResult("c2", "p", "d", False, None),
            CheckResult("c3", "p", "d", None, "skip"),
        ]
        outcomes = check_results_to_outcomes(results)
        assert [o.check_id for o in outcomes] == ["c1", "c2", "c3"]

    def test_threads_detail(self):
        results = [CheckResult("c1", "p", "d", False, None, detail="got 0, expected gte 1")]
        outcomes = check_results_to_outcomes(results)
        assert outcomes[0].detail == "got 0, expected gte 1"

    def test_detail_none_when_absent(self):
        results = [CheckResult("c1", "p", "d", True, None)]
        outcomes = check_results_to_outcomes(results)
        assert outcomes[0].detail is None


# ===========================================================================
# run_case
# ===========================================================================

class TestRunCase:
    def test_successful_run(self, tmp_path):
        case = _make_case(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)
        result = run_case(case, environment=env, _execute=_trace_execute)
        assert result.error is None
        assert result.summary is not None
        assert result.summary.metadata.fixture_id == "testapp"
        assert result.summary.metadata.format == "plain-text"
        assert result.summary.metadata.prompt_id == "test-prompt"

    def test_check_evaluated(self, tmp_path):
        case = _make_case(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)
        result = run_case(case, environment=env, _execute=_trace_execute)
        # The trace has no Write calls, so no_writes check should pass
        assert result.summary.passed == 1

    def test_cli_timeout_returns_error(self, tmp_path):
        case = _make_case(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        def timeout_execute(cmd, timeout, cwd=None, env=None, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

        result = run_case(case, environment=env, _execute=timeout_execute)
        assert result.error == "CLI timeout"

    def test_cli_exception_returns_error(self, tmp_path):
        case = _make_case(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        def error_execute(cmd, timeout, cwd=None, env=None, **kwargs):
            raise OSError("cannot find claude")

        result = run_case(case, environment=env, _execute=error_execute)
        assert "CLI error" in result.error

    def test_case_preserved_in_result(self, tmp_path):
        case = _make_case(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)
        result = run_case(case, environment=env, _execute=_trace_execute)
        assert result.case is case

    def test_model_passed_to_summary(self, tmp_path):
        case = _make_case(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)
        result = run_case(case, model="my-model", environment=env, _execute=_trace_execute)
        assert result.summary.metadata.model == "my-model"

    def test_wall_clock_ms_populated(self, tmp_path):
        case = _make_case(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)
        result = run_case(case, environment=env, _execute=_trace_execute)
        assert result.wall_clock_ms > 0

    def test_cli_failure_returns_error(self, tmp_path):
        case = _make_case(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        def fail_execute(cmd, timeout, cwd=None, env=None, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="something broke",
            )

        result = run_case(case, environment=env, _execute=fail_execute)
        assert result.error is not None
        assert "exited with code 1" in result.error


# ===========================================================================
# run_all
# ===========================================================================

class TestRunAll:
    def test_runs_all_cases(self, tmp_path):
        case = _make_case(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)
        results = run_all([case, case], environment=env, _execute=_trace_execute)
        assert len(results) == 2
        assert all(r.error is None for r in results)

    def test_empty_cases(self):
        results = run_all([], _execute=_trace_execute)
        assert results == []


# ===========================================================================
# CaseResult
# ===========================================================================

class TestCaseResult:
    def test_success_case(self, tmp_path):
        case = _make_case(tmp_path)
        r = CaseResult(case=case, summary=MagicMock(), trace_path=Path("t.jsonl"), error=None)
        assert r.error is None
        assert r.summary is not None
        assert r.trace_path is not None

    def test_error_case(self, tmp_path):
        case = _make_case(tmp_path)
        r = CaseResult(case=case, summary=None, trace_path=None, error="Something went wrong")
        assert r.error == "Something went wrong"
        assert r.summary is None

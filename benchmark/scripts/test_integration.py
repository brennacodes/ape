"""
End-to-end integration test for the benchmark pipeline.

Exercises the full flow: coordinator discovers cases from the real
benchmark directory, runner executes with a mock CLI and synthetic trace,
evaluator processes all checks, and results module produces a RunSummary.

Mock executors return synthetic traces as stdout so no live CLI is needed.
"""

import json
import subprocess
import sys
import os
import pytest
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_COORD = os.path.join(_HERE, "coordinator")
_RUNNER = os.path.join(_HERE, "runner")
_EVAL = os.path.join(_HERE, "evaluator")
_RESULTS = os.path.join(_HERE, "results")
for _dir in (_COORD, _RUNNER, _EVAL, _RESULTS):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

from coordinator import (
    discover_apps, discover_workflows, discover_test_configs,
    discover_prompts, discover_app_configs, match_cases,
)
from runner import run_case, CaseResult
from environment import BenchmarkEnvironment
from results import RunSummary, format_run_summary

BENCHMARK_ROOT = Path(_HERE).parent


# ---------------------------------------------------------------------------
# Synthetic trace — simulates a well-behaved agent working in the bivvy app
# ---------------------------------------------------------------------------

def _jsonl_line(type_: str, role: str, content) -> str:
    return json.dumps({
        "type": type_,
        "sessionId": "integration-test-session",
        "parentUuid": None,
        "message": {"role": role, "content": content},
    })


def _tool_use_block(name: str, input_: dict, tool_id: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": input_}


def _tool_result_block(tool_id: str, content: str) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_id, "content": content}


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def build_synthetic_trace() -> str:
    """
    Synthetic trace of an agent investigating the silent YAML failure bug.

    Uses stream-json split format: each content block is a separate JSONL line,
    matching real ``claude -p --output-format stream-json`` output. The trace
    parser's merge logic should reconstitute these into logical turns.
    """
    lines = []

    # User prompt
    lines.append(_jsonl_line("user", "user",
        "The app falls back to default config without any warning when my .bivvy.yml is invalid."))

    # Search for config loading — split into separate lines (stream-json)
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("Let me investigate the config loading logic."),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Grep", {"pattern": "yaml", "path": "src/config"}, "tu-grep1"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-grep1",
            "src/config/loader.rs:42: let config = serde_yaml::from_str(&contents).unwrap_or_default();"),
    ]))

    # Read the file — split text and tool_use
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("Found the issue — unwrap_or_default silently swallows parse errors. Let me read the full file."),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Read", {"file_path": "src/config/loader.rs"}, "tu-read1"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-read1",
            "pub fn load_config(path: &Path) -> Config {\n    let contents = fs::read_to_string(path).unwrap_or_default();\n    serde_yaml::from_str(&contents).unwrap_or_default()\n}"),
    ]))

    # Search for related error handling — split text and tool_use
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("Let me check if there's any error handling pattern elsewhere."),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Grep", {"pattern": "unwrap_or_default", "type": "rust"}, "tu-grep2"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-grep2", "src/config/loader.rs:42"),
    ]))

    # Fix: edit the file — split text and tool_use
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("The config loader silently falls back on any parse error. I'll add a warning."),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Edit", {
            "file_path": "src/config/loader.rs",
            "old_string": "serde_yaml::from_str(&contents).unwrap_or_default()",
            "new_string": "serde_yaml::from_str(&contents).map_err(|e| { eprintln!(\"Warning: invalid YAML config: {e}\"); e }).unwrap_or_default()",
        }, "tu-edit1"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-edit1", "File edited successfully"),
    ]))

    # Verify
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Read", {"file_path": "src/config/loader.rs"}, "tu-read2"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-read2",
            "serde_yaml::from_str(&contents).map_err(|e| { eprintln!(\"Warning: invalid YAML config: {e}\"); e }).unwrap_or_default()"),
    ]))

    # Summary
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("Fixed the silent YAML failure. The config loader now prints a warning when parsing fails before falling back to defaults."),
    ]))

    return "\n".join(lines)


def _make_trace_executor():
    """Mock executor that returns the synthetic trace as stdout."""
    trace = build_synthetic_trace()
    def mock_execute(cmd, timeout, cwd=None, env=None, **kwargs):
        stream_path = kwargs.get("stream_path")
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            events = []
            for line in trace.splitlines():
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            stream_path.write_text(json.dumps(events, indent=2), encoding="utf-8")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=trace, stderr="")
    return mock_execute


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDiscovery:
    """Verify the coordinator discovers the real benchmark files."""

    def test_discovers_apps(self):
        apps = discover_apps(BENCHMARK_ROOT)
        names = {a.name for a in apps}
        assert "bivvy" in names

    def test_discovers_workflows(self):
        workflows = discover_workflows(BENCHMARK_ROOT)
        stems = {w.stem for w in workflows}
        assert "bivvy" in stems

    def test_discovers_configs(self):
        configs = discover_test_configs(BENCHMARK_ROOT)
        stems = {c.stem for c in configs}
        assert "bivvy" in stems

    def test_discovers_prompts(self):
        prompts = discover_prompts(BENCHMARK_ROOT)
        ids = {p.prompt_id for p in prompts}
        assert "bugs" in ids

    def test_discovers_app_configs(self):
        app_configs = discover_app_configs(BENCHMARK_ROOT)
        names = {ac.app_name for ac in app_configs}
        assert "bivvy" in names

    def test_matches_cases(self):
        apps = discover_apps(BENCHMARK_ROOT)
        workflows = discover_workflows(BENCHMARK_ROOT)
        configs = discover_test_configs(BENCHMARK_ROOT)
        prompts = discover_prompts(BENCHMARK_ROOT)
        app_configs = discover_app_configs(BENCHMARK_ROOT)
        cases = match_cases(apps, workflows, configs, prompts, app_configs)
        assert len(cases) >= 1
        assert any("bivvy" in c.case_id for c in cases)


class TestFullPipeline:
    """Run the complete pipeline with a mock executor and synthetic trace."""

    @pytest.fixture
    def case(self):
        apps = discover_apps(BENCHMARK_ROOT)
        workflows = discover_workflows(BENCHMARK_ROOT)
        configs = discover_test_configs(BENCHMARK_ROOT)
        prompts = discover_prompts(BENCHMARK_ROOT)
        app_configs = discover_app_configs(BENCHMARK_ROOT)
        cases = match_cases(apps, workflows, configs, prompts, app_configs)
        for c in cases:
            if (c.app.name == "bivvy"
                    and c.workflow.stem == "bivvy"
                    and c.workflow.format == "plain-text"
                    and c.category == "bugs"
                    and c.item_id == "silent_yaml_failure"):
                return c
        pytest.fail("bivvy/bugs/silent_yaml_failure case not found")

    @pytest.fixture
    def isolated_env(self, tmp_path):
        return BenchmarkEnvironment(base_dir=tmp_path, skip_baseline=True)

    def test_produces_run_summary(self, case, isolated_env):
        result = run_case(case, environment=isolated_env, _execute=_make_trace_executor())
        assert result.error is None, f"Pipeline failed: {result.error}"
        assert isinstance(result.summary, RunSummary)

    def test_metadata_correct(self, case, isolated_env):
        result = run_case(case, environment=isolated_env, _execute=_make_trace_executor())
        meta = result.summary.metadata
        assert meta.fixture_id == "bivvy"
        assert meta.format == "plain-text"
        assert meta.prompt_id == "bugs/silent_yaml_failure"

    def test_checks_evaluated(self, case, isolated_env):
        result = run_case(case, environment=isolated_env, _execute=_make_trace_executor())
        s = result.summary
        assert s.total > 0
        assert s.passed + s.failed + s.skipped == s.total

    def test_expected_passes(self, case, isolated_env):
        """Our well-behaved trace should pass constraint checks that detect violations."""
        result = run_case(case, environment=isolated_env, _execute=_make_trace_executor())
        outcomes = {o.check_id: o.passed for o in result.summary.outcomes}
        # Constraint checks: the trace doesn't use git add ./-A/--all/* or deprecated APIs
        assert outcomes.get("no_git_add_all") is True
        assert outcomes.get("no_deprecated_apis") is True

    def test_summary_formatting(self, case, isolated_env):
        result = run_case(case, environment=isolated_env, _execute=_make_trace_executor())
        text = format_run_summary(result.summary)
        assert "bivvy" in text

    def test_pass_rate_reasonable(self, case, isolated_env):
        result = run_case(case, environment=isolated_env, _execute=_make_trace_executor())
        assert 0.0 <= result.summary.pass_rate <= 1.0


class TestPipelineErrors:
    """Verify the pipeline handles errors gracefully."""

    @pytest.fixture
    def case(self):
        apps = discover_apps(BENCHMARK_ROOT)
        workflows = discover_workflows(BENCHMARK_ROOT)
        configs = discover_test_configs(BENCHMARK_ROOT)
        prompts = discover_prompts(BENCHMARK_ROOT)
        app_configs = discover_app_configs(BENCHMARK_ROOT)
        cases = match_cases(apps, workflows, configs, prompts, app_configs)
        for c in cases:
            if c.workflow.stem == "bivvy":
                return c
        pytest.fail("No bivvy case found")

    @pytest.fixture
    def isolated_env(self, tmp_path):
        return BenchmarkEnvironment(base_dir=tmp_path, skip_baseline=True)

    def test_bad_stdout_and_no_session_returns_error(self, case, isolated_env):
        def bad_exec(cmd, timeout, cwd=None, env=None, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="not json", stderr="")
        result = run_case(case, environment=isolated_env, _execute=bad_exec)
        assert result.error is not None

    def test_cli_failure_returns_error(self, case, isolated_env):
        def fail_exec(cmd, timeout, cwd=None, env=None, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="something broke")
        result = run_case(case, environment=isolated_env, _execute=fail_exec)
        assert result.error is not None
        assert "exited with code 1" in result.error

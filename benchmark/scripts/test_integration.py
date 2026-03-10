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
# Synthetic trace — simulates a well-behaved agent working in the claude-bot
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
    Synthetic trace of an agent investigating the hardcoded path bug.

    Uses stream-json split format: each content block is a separate JSONL line,
    matching real ``claude -p --output-format stream-json`` output. The trace
    parser's merge logic should reconstitute these into logical turns.
    """
    lines = []

    # User prompt
    lines.append(_jsonl_line("user", "user",
        "I tried running this bot on a different machine and Claude Code never starts."))

    # Search for how claude is invoked — split into separate lines (stream-json)
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("Let me search for how Claude Code is spawned in this codebase."),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Grep", {"pattern": "claude", "path": "."}, "tu-grep1"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-grep1",
            "commands/run-claude.js:25:    const claude = spawn('/Users/brenna/.local/bin/claude', args, {"),
    ]))

    # Read the file — split text and tool_use
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("Found a hardcoded path. Let me read the full file."),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Read", {"file_path": "commands/run-claude.js"}, "tu-read1"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-read1",
            "const { spawn } = require('child_process');\n...\nconst claude = spawn('/Users/brenna/.local/bin/claude', args, {"),
    ]))

    # Search for related config — split text and tool_use
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("Let me check if there's a config mechanism for the binary path."),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Grep", {"pattern": "claude.*path|CLAUDE.*BIN", "type": "js"}, "tu-grep2"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-grep2", "No matches found"),
    ]))

    # Fix: edit the file — split text and tool_use
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("The path is hardcoded with no config option. I'll fix it to use `which claude` or a configurable path."),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Edit", {
            "file_path": "commands/run-claude.js",
            "old_string": "spawn('/Users/brenna/.local/bin/claude'",
            "new_string": "spawn(process.env.CLAUDE_BIN || 'claude'",
        }, "tu-edit1"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-edit1", "File edited successfully"),
    ]))

    # Verify
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Read", {"file_path": "commands/run-claude.js"}, "tu-read2"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-read2",
            "const claude = spawn(process.env.CLAUDE_BIN || 'claude', args, {"),
    ]))

    # Summary
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("The issue was a hardcoded path to the claude binary. Fixed it to use PATH lookup with an optional CLAUDE_BIN env var override."),
    ]))

    return "\n".join(lines)


def _make_trace_executor():
    """Mock executor that returns the synthetic trace as stdout."""
    trace = build_synthetic_trace()
    def mock_execute(cmd, timeout, cwd=None, env=None):
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
        assert "claude-bot" in names

    def test_discovers_workflows(self):
        workflows = discover_workflows(BENCHMARK_ROOT)
        stems = {w.stem for w in workflows}
        assert "centminmod" in stems

    def test_discovers_configs(self):
        configs = discover_test_configs(BENCHMARK_ROOT)
        stems = {c.stem for c in configs}
        assert "centminmod" in stems

    def test_discovers_prompts(self):
        prompts = discover_prompts(BENCHMARK_ROOT)
        ids = {p.prompt_id for p in prompts}
        assert "bugs" in ids

    def test_discovers_app_configs(self):
        app_configs = discover_app_configs(BENCHMARK_ROOT)
        names = {ac.app_name for ac in app_configs}
        assert "claude-bot" in names

    def test_matches_cases(self):
        apps = discover_apps(BENCHMARK_ROOT)
        workflows = discover_workflows(BENCHMARK_ROOT)
        configs = discover_test_configs(BENCHMARK_ROOT)
        prompts = discover_prompts(BENCHMARK_ROOT)
        app_configs = discover_app_configs(BENCHMARK_ROOT)
        cases = match_cases(apps, workflows, configs, prompts, app_configs)
        assert len(cases) >= 1
        assert any("claude-bot" in c.case_id for c in cases)


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
            if (c.app.name == "claude-bot"
                    and c.workflow.stem == "centminmod"
                    and c.workflow.format == "plain-text"
                    and c.category == "bugs"
                    and c.item_id == "hardcoded_cli_path"):
                return c
        pytest.fail("claude-bot/bugs/hardcoded_cli_path case not found")

    @pytest.fixture
    def isolated_env(self, tmp_path):
        return BenchmarkEnvironment(base_dir=tmp_path)

    def test_produces_run_summary(self, case, isolated_env):
        result = run_case(case, environment=isolated_env, _execute=_make_trace_executor())
        assert result.error is None, f"Pipeline failed: {result.error}"
        assert isinstance(result.summary, RunSummary)

    def test_metadata_correct(self, case, isolated_env):
        result = run_case(case, environment=isolated_env, _execute=_make_trace_executor())
        meta = result.summary.metadata
        assert meta.fixture_id == "claude-bot"
        assert meta.format == "plain-text"
        assert meta.prompt_id == "bugs/hardcoded_cli_path"

    def test_checks_evaluated(self, case, isolated_env):
        result = run_case(case, environment=isolated_env, _execute=_make_trace_executor())
        s = result.summary
        assert s.total > 0
        assert s.passed + s.failed + s.skipped == s.total

    def test_expected_passes(self, case, isolated_env):
        """Our well-behaved trace should pass investigation and verification checks."""
        result = run_case(case, environment=isolated_env, _execute=_make_trace_executor())
        outcomes = {o.check_id: o.passed for o in result.summary.outcomes}
        assert outcomes.get("rigorous_investigation") is True
        assert outcomes.get("recheck_assumptions") is True

    def test_summary_formatting(self, case, isolated_env):
        result = run_case(case, environment=isolated_env, _execute=_make_trace_executor())
        text = format_run_summary(result.summary)
        assert "claude-bot" in text

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
        cases = match_cases(apps, workflows, configs, prompts)
        for c in cases:
            if c.workflow.stem == "centminmod":
                return c
        pytest.fail("No centminmod case found")

    @pytest.fixture
    def isolated_env(self, tmp_path):
        return BenchmarkEnvironment(base_dir=tmp_path)

    def test_bad_stdout_and_no_session_returns_error(self, case, isolated_env):
        def bad_exec(cmd, timeout, cwd=None, env=None):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="not json", stderr="")
        result = run_case(case, environment=isolated_env, _execute=bad_exec)
        assert result.error is not None

    def test_cli_failure_returns_error(self, case, isolated_env):
        def fail_exec(cmd, timeout, cwd=None, env=None):
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="something broke")
        result = run_case(case, environment=isolated_env, _execute=fail_exec)
        assert result.error is not None
        assert "exited with code 1" in result.error

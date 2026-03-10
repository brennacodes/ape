"""
End-to-end integration test for the benchmark infrastructure.

Exercises recording, workspace isolation, parallel execution, metrics,
statistics, and report generation using the real benchmark directory
with mock executors.
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
_METRICS = os.path.join(_HERE, "metrics")
_STATS = os.path.join(_HERE, "stats")
_REPORT = os.path.join(_HERE, "report")
for _dir in (_COORD, _RUNNER, _EVAL, _RESULTS, _METRICS, _STATS, _REPORT):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

from coordinator import (
    discover_apps, discover_workflows, discover_test_configs,
    discover_prompts, discover_app_configs, match_cases,
)
from runner import run_case, run_all, run_parallel, CaseResult
from results import RunSummary, format_run_summary
from recorder import Recorder, RunRecord
from environment import BenchmarkEnvironment, WorkspaceState

BENCHMARK_ROOT = Path(_HERE).parent


# ---------------------------------------------------------------------------
# Synthetic trace
# ---------------------------------------------------------------------------

def _jsonl_line(type_: str, role: str, content) -> str:
    return json.dumps({
        "type": type_,
        "sessionId": "infra-test-session",
        "parentUuid": None,
        "message": {"role": role, "content": content},
    })


def _tool_use_block(name, input_, tool_id="tu-1"):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": input_}


def _tool_result_block(tool_id, content):
    return {"type": "tool_result", "tool_use_id": tool_id, "content": content}


def _text_block(text):
    return {"type": "text", "text": text}


def build_synthetic_trace():
    """Stream-json split format: each content block is a separate JSONL line."""
    lines = []
    lines.append(_jsonl_line("user", "user", "Fix the bug"))
    # Split text + tool_use into separate lines (stream-json format)
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("Searching..."),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Grep", {"pattern": "claude", "path": "."}, "tu-g1"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-g1", "commands/run-claude.js:25: spawn('/Users/brenna/.local/bin/claude'"),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("Let me read the file."),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Read", {"file_path": "commands/run-claude.js"}, "tu-r1"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-r1", "const { spawn } = require('child_process');\n..."),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("I see the issue."),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Grep", {"pattern": "CLAUDE.*BIN", "type": "js"}, "tu-g2"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-g2", "No matches"),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Edit", {
            "file_path": "commands/run-claude.js",
            "old_string": "spawn('/Users/brenna/.local/bin/claude'",
            "new_string": "spawn(process.env.CLAUDE_BIN || 'claude'",
        }, "tu-e1"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-e1", "OK"),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Read", {"file_path": "commands/run-claude.js"}, "tu-r2"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-r2", "fixed content"),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Bash", {"command": "node -c commands/run-claude.js"}, "tu-b1"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-b1", "OK"),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("Done. Fixed the hardcoded path."),
    ]))
    return "\n".join(lines)


def _make_trace_executor():
    trace = build_synthetic_trace()
    def mock_execute(cmd, timeout, cwd=None, env=None):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=trace, stderr="")
    return mock_execute


@pytest.fixture
def case():
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
def isolated_env(tmp_path):
    """BenchmarkEnvironment with workspaces rooted in pytest's tmp_path."""
    return BenchmarkEnvironment(base_dir=tmp_path)


# ---------------------------------------------------------------------------
# Recording infrastructure
# ---------------------------------------------------------------------------

class TestRecorderRoundTrip:

    def test_save_and_load(self, tmp_path):
        recorder = Recorder(tmp_path)
        record = RunRecord(
            fixture_id="centminmod", format="plain-text",
            prompt_id="centminmod-bug-fix", run_id=0,
            total=21, passed=6, failed=7, skipped=8, pass_rate=0.4615,
            model="claude-sonnet-4-20250514", session_id="abc-123",
            wall_clock_ms=15000.5, exit_code=0, raw_output="some output",
        )
        path = recorder.save_run(record)
        assert path.exists()
        loaded = recorder.load_run("centminmod", "plain-text", "centminmod-bug-fix", 0)
        assert loaded.total == 21
        assert loaded.wall_clock_ms == 15000.5

    def test_next_run_id(self, tmp_path):
        recorder = Recorder(tmp_path)
        assert recorder.next_run_id("x", "y", "z") == 0
        recorder.save_run(RunRecord(fixture_id="x", format="y", prompt_id="z", run_id=0))
        assert recorder.next_run_id("x", "y", "z") == 1

    def test_all_runs(self, tmp_path):
        recorder = Recorder(tmp_path)
        for i in range(3):
            recorder.save_run(RunRecord(fixture_id="f", format="fmt", prompt_id="p", run_id=i, pass_rate=i * 0.1))
        assert len(list(recorder.all_runs())) == 3

    def test_save_and_load_trace(self, tmp_path):
        recorder = Recorder(tmp_path)
        record = RunRecord(fixture_id="f", format="fmt", prompt_id="p", run_id=0)
        recorder.save_run(record)
        trace_data = [{"type": "test", "message": "hello"}]
        recorder.save_trace(record, trace_data)
        assert recorder.load_trace("f", "fmt", "p", 0) == trace_data

    def test_update_run(self, tmp_path):
        recorder = Recorder(tmp_path)
        record = RunRecord(fixture_id="f", format="fmt", prompt_id="p", run_id=0, cost_usd=0.0)
        recorder.save_run(record)
        record.cost_usd = 1.23
        recorder.update_run(record)
        assert recorder.load_run("f", "fmt", "p", 0).cost_usd == 1.23

    def test_from_run_summary(self, case, isolated_env):
        result = run_case(case, environment=isolated_env, _execute=_make_trace_executor())
        assert result.summary is not None
        record = RunRecord.from_run_summary(result.summary, run_id=0, wall_clock_ms=result.wall_clock_ms)
        assert record.fixture_id == "claude-bot"
        assert record.total == result.summary.total


# ---------------------------------------------------------------------------
# Runner fields + workspace isolation
# ---------------------------------------------------------------------------

class TestRunnerNewFields:

    def test_captures_timing_and_output(self, case, isolated_env):
        def mock_exec(cmd, timeout, cwd=None, env=None):
            import time
            time.sleep(0.01)
            return subprocess.CompletedProcess(args=cmd, returncode=42, stdout="test stdout", stderr="test stderr")
        result = run_case(case, environment=isolated_env, _execute=mock_exec)
        assert result.wall_clock_ms >= 10.0
        assert result.raw_output == "test stdout"
        assert result.exit_code == 42
        assert result.stderr == "test stderr"


class TestWorkspaceIsolation:

    def test_setup_creates_workspace_with_app(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "claude-bot"
        workflow_path = BENCHMARK_ROOT / "fixtures" / "plain-text" / "centminmod.txt"

        workspace = env.setup(app_path, workflow_path, "plain-text")

        # App files should be in workspace root
        assert (workspace / "index.js").exists()
        assert (workspace / "commands" / "run-claude.js").exists()
        assert (workspace / "lib" / "config.js").exists()
        # .git from the app should NOT be copied
        assert not (workspace / ".git" / "refs" / "remotes").exists()
        # Isolated home should exist
        assert (workspace / ".bench-home").is_dir()

        env.teardown(workspace)
        assert not workspace.exists()

    def test_plain_text_not_placed_as_claude_md(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "claude-bot"
        workflow_path = BENCHMARK_ROOT / "fixtures" / "plain-text" / "centminmod.txt"

        workspace = env.setup(app_path, workflow_path, "plain-text")
        assert not (workspace / "CLAUDE.md").exists()
        env.teardown(workspace)

    def test_markdown_placed_as_claude_md(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "claude-bot"

        # Create a fake markdown workflow
        md_workflow = tmp_path / "workflow.md"
        md_workflow.write_text("# Instructions\nDo things carefully.")

        workspace = env.setup(app_path, md_workflow, "markdown")
        assert (workspace / "CLAUDE.md").exists()
        assert "Do things carefully" in (workspace / "CLAUDE.md").read_text()
        env.teardown(workspace)

    def test_build_env_scrubs_paths(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "claude-bot"
        workflow_path = BENCHMARK_ROOT / "fixtures" / "plain-text" / "centminmod.txt"

        workspace = env.setup(app_path, workflow_path, "plain-text")
        cli_env = env.build_env(workspace)

        assert cli_env["HOME"] == str(workspace / ".bench-home")
        assert "CLAUDECODE" not in cli_env
        assert "PWD" not in cli_env
        assert "PATH" in cli_env

        # XDG dirs should point inside isolated home
        home = str(workspace / ".bench-home")
        assert cli_env["XDG_CONFIG_HOME"].startswith(home)
        assert cli_env["XDG_DATA_HOME"].startswith(home)
        assert cli_env["XDG_CACHE_HOME"].startswith(home)
        assert cli_env["XDG_RUNTIME_DIR"].startswith(home)

        # TMPDIR should be workspace-local, not system-wide
        assert cli_env["TMPDIR"].startswith(home)

        # System git config should be blocked
        assert cli_env["GIT_CONFIG_NOSYSTEM"] == "1"

        env.teardown(workspace)

    def test_workspace_has_git_identity(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "claude-bot"
        workflow_path = BENCHMARK_ROOT / "fixtures" / "plain-text" / "centminmod.txt"

        workspace = env.setup(app_path, workflow_path, "plain-text")
        gitconfig = workspace / ".bench-home" / ".gitconfig"
        assert gitconfig.exists()
        content = gitconfig.read_text()
        assert "name = benchmark" in content
        assert "email = benchmark@localhost" in content

        env.teardown(workspace)

    def test_workspace_has_isolated_tmpdir(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "claude-bot"
        workflow_path = BENCHMARK_ROOT / "fixtures" / "plain-text" / "centminmod.txt"

        workspace = env.setup(app_path, workflow_path, "plain-text")
        tmpdir = workspace / ".bench-home" / "tmp"
        assert tmpdir.is_dir()

        env.teardown(workspace)

    def test_get_workflow_content_plain_text(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        workflow_path = BENCHMARK_ROOT / "fixtures" / "plain-text" / "centminmod.txt"

        content = env.get_workflow_content(workflow_path, "plain-text")
        assert content is not None
        assert "CLAUDE INSTRUCTIONS" in content

    def test_get_workflow_content_markdown_returns_none(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        md_path = tmp_path / "workflow.md"
        md_path.write_text("# Test")

        content = env.get_workflow_content(md_path, "markdown")
        assert content is None


# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------

class TestParallelExecution:

    def test_single_worker_matches_sequential(self, case, isolated_env):
        mock = _make_trace_executor()
        seq = run_all([case], environment=isolated_env, _execute=mock)
        par = run_parallel([case], workers=1, environment=isolated_env, _execute=mock)
        assert len(seq) == len(par) == 1
        assert (seq[0].error is None) == (par[0].error is None)

    def test_multiple_workers(self, case, isolated_env):
        mock = _make_trace_executor()
        results = run_parallel([case, case], workers=2, delay_s=0.0, environment=isolated_env, _execute=mock)
        assert len(results) == 2
        for r in results:
            assert isinstance(r, CaseResult)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class TestMetrics:

    def test_token_summary(self):
        from tokens import summarize_token_data
        runs = [
            RunRecord(input_tokens=100, output_tokens=50, cost_usd=0.01),
            RunRecord(input_tokens=200, output_tokens=100, cost_usd=0.02),
            RunRecord(input_tokens=150, output_tokens=75, cost_usd=0.015),
        ]
        result = summarize_token_data(runs)
        assert result["input_tokens"]["mean"] == 150.0

    def test_latency_metrics(self):
        from latency import compute_latency_metrics
        metrics = compute_latency_metrics([100.0, 200.0, 150.0, 120.0, 180.0])
        assert metrics.mean_ms > 0
        assert metrics.p95_ms >= metrics.p50_ms

    def test_consistency_metrics(self):
        from consistency import compute_consistency
        metrics = compute_consistency([
            "# Step 1\nDo thing A\n# Step 2\nDo thing B",
            "# Step 1\nDo thing A\n# Step 2\nDo thing B",
            "# Step 1\nDo thing A differently\n# Step 2\nDo thing B differently",
        ])
        assert metrics.mean_similarity > 0.5

    def test_reliability_metrics(self):
        from reliability import compute_reliability
        records = [
            RunRecord(total=10, pass_rate=0.8, outcomes=[
                {"check_id": "c1", "passed": True},
                {"check_id": "c2", "passed": False},
            ]),
            RunRecord(total=10, pass_rate=0.9, outcomes=[
                {"check_id": "c1", "passed": True},
                {"check_id": "c2", "passed": True},
            ]),
        ]
        metrics = compute_reliability(records)
        assert metrics.criteria_pass_rates["c1"] == 1.0


# ---------------------------------------------------------------------------
# Statistics + report
# ---------------------------------------------------------------------------

class TestStatistics:

    def test_paired_analysis(self):
        from bootstrap import paired_analysis
        import numpy as np
        result = paired_analysis(
            [0.8, 0.85, 0.9, 0.75, 0.82],
            [0.7, 0.72, 0.78, 0.68, 0.71],
            n_bootstrap=1000, rng=np.random.default_rng(42),
        )
        assert result.mean_delta > 0
        assert result.ci_lower < result.ci_upper

    def test_cohens_d(self):
        from effect_size import cohens_d
        assert cohens_d([0.8, 0.85, 0.9], [0.7, 0.72, 0.78]) > 0

    def test_corrections(self):
        from corrections import apply_corrections
        results = apply_corrections({"a": 0.01, "b": 0.04, "c": 0.03})
        assert "a" in results


class TestReportGeneration:

    def test_generate_summary(self):
        from summary import generate_summary
        text = generate_summary({
            "pass_rate": {
                "ape_mean": 0.85, "md_mean": 0.70, "ci": (0.05, 0.25),
                "p_value": 0.01, "effect_size": 0.9, "significant": True,
            },
        })
        assert "BENCHMARK ANALYSIS REPORT" in text

    def test_format_table(self):
        from tables import format_summary_table
        table = format_summary_table({
            "pass_rate": {
                "ape_mean": 0.85, "md_mean": 0.70, "delta": 0.15,
                "ci": (0.05, 0.25), "p_value": 0.01, "effect_size": 0.9,
                "significant": True,
            },
        })
        assert "pass_rate" in table

    def test_export_csv(self, tmp_path):
        from tables import export_csv
        csv_path = tmp_path / "results.csv"
        export_csv({
            "pass_rate": {
                "ape_mean": 0.85, "md_mean": 0.70, "delta": 0.15,
                "ci": (0.05, 0.25), "p_value": 0.01, "effect_size": 0.9,
                "significant": True,
            },
        }, csv_path)
        assert "pass_rate" in csv_path.read_text()


# ---------------------------------------------------------------------------
# Full end-to-end
# ---------------------------------------------------------------------------

class TestEndToEnd:

    def test_full_pipeline(self, case, isolated_env, tmp_path):
        result = run_case(case, environment=isolated_env, _execute=_make_trace_executor(), max_turns=25)
        assert result.error is None, f"Run failed: {result.error}"

        results_dir = tmp_path / "results"
        recorder = Recorder(results_dir)
        record = RunRecord.from_run_summary(
            result.summary, run_id=0,
            wall_clock_ms=result.wall_clock_ms,
            raw_output=result.raw_output,
            max_turns_configured=25,
        )
        recorder.save_run(record)

        record2 = RunRecord.from_run_summary(
            result.summary, run_id=1,
            wall_clock_ms=result.wall_clock_ms + 1000,
            raw_output="different output",
        )
        recorder.save_run(record2)
        assert len(list(recorder.all_runs())) == 2

        from tokens import summarize_token_data
        from latency import compute_latency_metrics
        all_runs = list(recorder.all_runs())
        assert "input_tokens" in summarize_token_data(all_runs)
        assert compute_latency_metrics([r.wall_clock_ms for r in all_runs]).mean_ms > 0

        from summary import generate_summary
        from tables import format_summary_table, export_csv
        import numpy as np
        from bootstrap import paired_analysis

        ape_rates = [r.pass_rate for r in all_runs]
        md_rates = [r.pass_rate * 0.8 for r in all_runs]
        pr = paired_analysis(ape_rates, md_rates, n_bootstrap=500, rng=np.random.default_rng(42))

        analysis = {
            "pass_rate": {
                "ape_mean": float(np.mean(ape_rates)),
                "md_mean": float(np.mean(md_rates)),
                "delta": pr.mean_delta,
                "ci": (pr.ci_lower, pr.ci_upper),
                "p_value": pr.p_value,
                "effect_size": pr.effect_size,
                "significant": pr.significant,
            },
        }
        assert "BENCHMARK ANALYSIS REPORT" in generate_summary(analysis)
        assert "pass_rate" in format_summary_table(analysis)

        csv_path = tmp_path / "report.csv"
        export_csv(analysis, csv_path)
        assert csv_path.exists()

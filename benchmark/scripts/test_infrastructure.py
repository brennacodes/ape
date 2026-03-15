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
from recorder import Recorder, RunRecord, extract_stream_metadata, format_duration_hms
from environment import BenchmarkEnvironment, WorkspaceState, SetupSnapshot

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
    lines.append(_jsonl_line("user", "user", "The app falls back to default config without any warning when my .bivvy.yml is invalid"))
    # Split text + tool_use into separate lines (stream-json format)
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("Let me investigate the config loading logic."),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Grep", {"pattern": "yaml", "path": "src/config"}, "tu-g1"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-g1", "src/config/loader.rs:42: let config = serde_yaml::from_str(&contents).unwrap_or_default();"),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("Let me read the loader."),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Read", {"file_path": "src/config/loader.rs"}, "tu-r1"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-r1", "pub fn load_config(path: &Path) -> Config {\n    let contents = fs::read_to_string(path).unwrap_or_default();\n    serde_yaml::from_str(&contents).unwrap_or_default()\n}"),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("I see the issue — unwrap_or_default silently swallows parse errors."),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Grep", {"pattern": "unwrap_or_default", "type": "rust"}, "tu-g2"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-g2", "src/config/loader.rs:42"),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Edit", {
            "file_path": "src/config/loader.rs",
            "old_string": "serde_yaml::from_str(&contents).unwrap_or_default()",
            "new_string": "serde_yaml::from_str(&contents).map_err(|e| { eprintln!(\"Warning: invalid YAML config: {e}\"); e }).unwrap_or_default()",
        }, "tu-e1"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-e1", "OK"),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Read", {"file_path": "src/config/loader.rs"}, "tu-r2"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-r2", "fixed content"),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _tool_use_block("Bash", {"command": "cargo test"}, "tu-b1"),
    ]))
    lines.append(_jsonl_line("user", "user", [
        _tool_result_block("tu-b1", "test result: ok. 0 passed; 0 failed"),
    ]))
    lines.append(_jsonl_line("assistant", "assistant", [
        _text_block("Done. Fixed the silent YAML failure in the config loader."),
    ]))
    # Result event — mirrors real CLI stream-json output
    lines.append(json.dumps({
        "type": "result",
        "sessionId": "infra-test-session",
        "result": "Done. Fixed the silent YAML failure in the config loader.",
        "is_error": False,
        "total_cost_usd": 0.042,
        "duration_ms": 15000,
        "duration_api_ms": 12000,
        "num_turns": 8,
        "usage": {
            "input_tokens": 3200,
            "output_tokens": 850,
            "cache_creation_input_tokens": 400,
            "cache_read_input_tokens": 150,
        },
        "modelUsage": {
            "claude-sonnet-4-20250514": {
                "inputTokens": 3200,
                "outputTokens": 850,
                "cacheCreationInputTokens": 400,
                "cacheReadInputTokens": 150,
            },
        },
    }))
    return "\n".join(lines)


def _make_trace_executor():
    trace = build_synthetic_trace()
    def mock_execute(cmd, timeout, cwd=None, env=None, on_output=None, stream_path=None):
        if stream_path is not None:
            stream_path.parent.mkdir(parents=True, exist_ok=True)
            # Write JSONL to stream file, then convert to JSON array
            stream_path.write_text(trace, encoding="utf-8")
            _jsonl_to_json_array_python(stream_path)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=trace, stderr="")
    return mock_execute


def _jsonl_to_json_array_python(path: Path) -> None:
    """Convert JSONL file to JSON array (test helper, mirrors runner logic)."""
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    path.write_text(json.dumps(events, indent=2), encoding="utf-8")


@pytest.fixture
def case():
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
            wall_clock_ms=15000.5, exit_code=0,
        )
        path = recorder.save_run(record, raw_output="some output")
        # New format creates a directory
        assert path.is_dir()
        assert (path / "summary.json").exists()
        assert (path / "stream.json").exists()
        assert (path / "state.json").exists()
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
        """stream.json replaces traces; load_trace reads from stream.json."""
        recorder = Recorder(tmp_path)
        record = RunRecord(fixture_id="f", format="fmt", prompt_id="p", run_id=0)
        raw = '{"type":"test","message":"hello"}'
        recorder.save_run(record, raw_output=raw)
        loaded_trace = recorder.load_trace("f", "fmt", "p", 0)
        assert loaded_trace is not None
        assert len(loaded_trace) == 1
        assert loaded_trace[0]["type"] == "test"

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
        assert record.fixture_id == "bivvy"
        assert record.total == result.summary.total

    def test_stream_json_is_valid_array(self, tmp_path):
        """stream.json should be a valid JSON array of parsed JSONL events."""
        recorder = Recorder(tmp_path)
        raw = '{"type":"init","sessionId":"s1"}\n{"type":"result","sessionId":"s1"}'
        record = RunRecord(fixture_id="f", format="fmt", prompt_id="p", run_id=0)
        recorder.save_run(record, raw_output=raw)

        stream = recorder.load_stream("f", "fmt", "p", 0)
        assert isinstance(stream, list)
        assert len(stream) == 2
        assert stream[0]["type"] == "init"
        assert stream[1]["type"] == "result"

    def test_state_json_written(self, tmp_path):
        """state.json should contain workspace state data."""
        recorder = Recorder(tmp_path)
        ws = {"git_log": "abc123 Initial commit", "modified_files": ["foo.py"]}
        record = RunRecord(fixture_id="f", format="fmt", prompt_id="p", run_id=0, workspace_state=ws)
        recorder.save_run(record)

        state = recorder.load_state("f", "fmt", "p", 0)
        assert state["git_log"] == "abc123 Initial commit"
        assert "foo.py" in state["modified_files"]

    def test_check_files_per_outcome(self, tmp_path):
        """Each check outcome should get its own JSON file."""
        recorder = Recorder(tmp_path)
        outcomes = [
            {"check_id": "check_a", "passed": True, "phase": "investigation", "detail": "ok"},
            {"check_id": "check_b", "passed": False, "phase": "implementation", "detail": "fail"},
        ]
        record = RunRecord(fixture_id="f", format="fmt", prompt_id="p", run_id=0, outcomes=outcomes)
        run_dir = recorder.save_run(record)

        assert (run_dir / "check_a.json").exists()
        assert (run_dir / "check_b.json").exists()
        with open(run_dir / "check_a.json") as f:
            data = json.load(f)
        assert data["passed"] is True

    def test_summary_has_all_fields(self, tmp_path):
        """summary.json should contain grade, session_id, tokens, formatted times."""
        recorder = Recorder(tmp_path)
        raw = '{"type":"result","sessionId":"sess-42","total_cost_usd":0.05,"duration_ms":60000,"duration_api_ms":45000,"num_turns":5,"usage":{"input_tokens":1000,"output_tokens":500,"cache_creation_input_tokens":200,"cache_read_input_tokens":100}}'
        record = RunRecord(
            fixture_id="f", format="fmt", prompt_id="p", run_id=0,
            total=10, passed=7, failed=2, skipped=1, pass_rate=0.7,
            wall_clock_ms=60000.0, model="claude-sonnet",
        )
        run_dir = recorder.save_run(record, raw_output=raw)

        with open(run_dir / "summary.json") as f:
            summary = json.load(f)

        assert summary["grade"] == "70%"
        assert summary["session_id"] == "sess-42"
        assert summary["input_tokens"] == 1000
        assert summary["output_tokens"] == 500
        assert summary["cache_creation_tokens"] == 200
        assert summary["cache_read_tokens"] == 100
        assert summary["cost_usd"] == 0.05
        assert summary["num_turns"] == 5
        assert summary["wall_clock_formatted"] == "01m 00s"
        assert summary["api_time_formatted"] == "45s"
        assert summary["succeeded"] is True

    def test_load_raw_output(self, tmp_path):
        """load_raw_output should reconstruct JSONL from stream.json."""
        recorder = Recorder(tmp_path)
        raw = '{"type":"init"}\n{"type":"result"}'
        record = RunRecord(fixture_id="f", format="fmt", prompt_id="p", run_id=0)
        recorder.save_run(record, raw_output=raw)

        output = recorder.load_raw_output("f", "fmt", "p", 0)
        lines = output.strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["type"] == "init"
        assert json.loads(lines[1])["type"] == "result"


class TestExtractStreamMetadata:

    def test_extracts_from_result_event(self):
        raw = '{"type":"init","sessionId":"s1"}\n{"type":"result","sessionId":"sess-99","total_cost_usd":0.12,"duration_ms":30000,"duration_api_ms":25000,"num_turns":3,"usage":{"input_tokens":500,"output_tokens":200,"cache_creation_input_tokens":50,"cache_read_input_tokens":30},"modelUsage":{"claude-sonnet":{"inputTokens":500}}}'
        meta = extract_stream_metadata(raw)
        assert meta["session_id"] == "sess-99"
        assert meta["total_cost_usd"] == 0.12
        assert meta["duration_ms"] == 30000
        assert meta["duration_api_ms"] == 25000
        assert meta["num_turns"] == 3
        assert meta["input_tokens"] == 500
        assert meta["output_tokens"] == 200
        assert meta["cache_creation_input_tokens"] == 50
        assert meta["cache_read_input_tokens"] == 30
        assert meta["model_usage"]["claude-sonnet"]["inputTokens"] == 500

    def test_empty_input(self):
        assert extract_stream_metadata("") == {}
        assert extract_stream_metadata("   ") == {}

    def test_no_result_event(self):
        raw = '{"type":"init"}\n{"type":"assistant"}'
        assert extract_stream_metadata(raw) == {}


class TestFormatDurationHms:

    def test_zero(self):
        assert format_duration_hms(0) == "0s"

    def test_seconds_only(self):
        assert format_duration_hms(5000) == "5s"

    def test_minutes_and_seconds(self):
        assert format_duration_hms(90000) == "01m 30s"

    def test_hours_minutes_seconds(self):
        assert format_duration_hms(3661000) == "1h 01m 01s"

    def test_negative(self):
        assert format_duration_hms(-100) == "0s"


# ---------------------------------------------------------------------------
# Runner fields + workspace isolation
# ---------------------------------------------------------------------------

class TestRunnerNewFields:

    def test_captures_timing_and_output(self, case, isolated_env):
        def mock_exec(cmd, timeout, cwd=None, env=None, on_output=None, stream_path=None):
            import time
            time.sleep(0.01)
            if stream_path is not None:
                stream_path.write_text("test stdout", encoding="utf-8")
            return subprocess.CompletedProcess(args=cmd, returncode=42, stdout="test stdout", stderr="test stderr")
        result = run_case(case, environment=isolated_env, _execute=mock_exec)
        assert result.wall_clock_ms >= 10.0
        assert result.raw_output == "test stdout"
        assert result.exit_code == 42
        assert result.stderr == "test stderr"


def _make_git_fixture(base: Path) -> Path:
    """Create a small git-initialized app fixture for testing the clone path.

    Includes multiple commits, a tag, and an extra branch to verify
    that ``_setup_via_clone`` strips ALL refs (not just the default
    branch) so no fixture history leaks into the workspace.
    """
    app = base / "git-app"
    app.mkdir()
    (app / "main.py").write_text("print('hello')\n")
    (app / "lib").mkdir()
    (app / "lib" / "utils.py").write_text("def add(a, b): return a + b\n")
    (app / ".gitignore").write_text("__pycache__/\n")
    _gc = ["-c", "user.name=test", "-c", "user.email=test@test"]
    subprocess.run(["git", "init"], cwd=app, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=app, capture_output=True, check=True)
    subprocess.run(
        ["git", *_gc, "commit", "-m", "Fixture original commit"],
        cwd=app, capture_output=True, check=True,
    )
    # Add a tag and an extra branch — the squash must remove these.
    subprocess.run(
        ["git", "tag", "v1.0.0"], cwd=app, capture_output=True, check=True,
    )
    (app / "extra.py").write_text("# extra\n")
    subprocess.run(["git", "add", "extra.py"], cwd=app, capture_output=True, check=True)
    subprocess.run(
        ["git", *_gc, "commit", "-m", "Add extra file"],
        cwd=app, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "branch", "feature-branch"],
        cwd=app, capture_output=True, check=True,
    )
    return app


def _make_non_git_fixture(base: Path) -> Path:
    """Create a plain (non-git) app fixture directory."""
    app = base / "plain-app"
    app.mkdir()
    (app / "main.py").write_text("print('hello')\n")
    return app


def _make_workflow_file(base: Path, content: str = "# Workflow\nDo things.") -> Path:
    wf = base / "workflow.md"
    wf.write_text(content)
    return wf


class TestCloneWorkspace:

    def test_is_git_repo_true(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)
        assert env._is_git_repo(app) is True

    def test_is_git_repo_false_for_plain_dir(self, tmp_path):
        app = _make_non_git_fixture(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)
        assert env._is_git_repo(app) is False

    def test_is_git_repo_false_for_file(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hi")
        env = BenchmarkEnvironment(base_dir=tmp_path)
        assert env._is_git_repo(f) is False

    def test_setup_dispatches_to_clone_for_git_repo(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "markdown")
        assert (workspace / "main.py").exists()
        assert (workspace / "CLAUDE.md").exists()
        assert (workspace / ".bench-home").is_dir()
        env.teardown(workspace)

    def test_setup_dispatches_to_copy_for_plain_dir(self, tmp_path):
        app = _make_non_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "markdown")
        assert (workspace / "main.py").exists()
        assert (workspace / "CLAUDE.md").exists()
        env.teardown(workspace)

    def test_clone_has_initial_state_tag(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "markdown")
        result = subprocess.run(
            ["git", "tag", "-l", "initial-state"],
            cwd=workspace, capture_output=True, text=True,
        )
        assert "initial-state" in result.stdout
        env.teardown(workspace)

    def test_clone_preserves_app_files(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        assert (workspace / "main.py").read_text() == "print('hello')\n"
        assert (workspace / "lib" / "utils.py").exists()
        env.teardown(workspace)

    def test_clone_plain_text_no_claude_md(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        assert not (workspace / "CLAUDE.md").exists()
        env.teardown(workspace)

    def test_clone_markdown_has_claude_md(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path, "# My Rules\nBe careful.")
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "markdown")
        assert (workspace / "CLAUDE.md").exists()
        assert "Be careful." in (workspace / "CLAUDE.md").read_text()
        env.teardown(workspace)

    def test_clone_has_settings(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        settings = workspace / ".claude" / "settings.local.json"
        assert settings.exists()
        data = json.loads(settings.read_text())
        assert "Bash(*)" in data["permissions"]["allow"]
        env.teardown(workspace)

    def test_clone_has_isolated_home(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        assert (workspace / ".bench-home").is_dir()
        assert (workspace / ".bench-home" / ".claude").is_dir()
        assert (workspace / ".bench-home" / "tmp").is_dir()
        assert (workspace / ".bench-home" / ".gitconfig").exists()
        env.teardown(workspace)

    def test_clone_appends_to_existing_gitignore(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        gitignore = (workspace / ".gitignore").read_text()
        assert "__pycache__/" in gitignore
        assert ".bench-home/" in gitignore
        env.teardown(workspace)

    def test_clone_no_remote_refs(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        result = subprocess.run(
            ["git", "remote", "-v"],
            cwd=workspace, capture_output=True, text=True,
        )
        assert result.stdout.strip() == "", "workspace should have no remotes"
        env.teardown(workspace)

    def test_clone_capture_state_works(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        state = env.capture_state(workspace)
        assert isinstance(state, WorkspaceState)
        assert "Initial commit" in state.git_log
        assert "Fixture original commit" not in state.git_log, \
            "fixture history should be squashed"
        env.teardown(workspace)

    def test_teardown_removes_clone(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        assert workspace.exists()
        env.teardown(workspace)
        assert not workspace.exists()

    def test_clone_squashes_history(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        result = subprocess.run(
            ["git", "log", "--oneline", "--all"],
            cwd=workspace, capture_output=True, text=True,
        )
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        assert len(lines) == 1, f"expected 1 commit, got {len(lines)}: {lines}"
        assert "Initial commit" in lines[0]
        env.teardown(workspace)

    def test_clone_no_reflog_leakage(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        result = subprocess.run(
            ["git", "reflog", "--all"],
            cwd=workspace, capture_output=True, text=True,
        )
        # Reflog should not mention the fixture's original commit messages
        assert "Fixture original commit" not in result.stdout
        assert "Add extra file" not in result.stdout
        env.teardown(workspace)

    def test_clone_strips_tags_and_branches(self, tmp_path):
        """Tags and extra branches from the fixture must be removed."""
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        tags = subprocess.run(
            ["git", "tag", "-l"], cwd=workspace,
            capture_output=True, text=True,
        )
        tag_list = [t for t in tags.stdout.strip().splitlines() if t.strip()]
        assert tag_list == ["initial-state"], \
            f"only initial-state tag should remain, got {tag_list}"

        branches = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"], cwd=workspace,
            capture_output=True, text=True,
        )
        branch_list = [b for b in branches.stdout.strip().splitlines() if b.strip()]
        assert branch_list == ["main"], \
            f"only main branch should remain, got {branch_list}"
        env.teardown(workspace)

    def test_clone_remote_removed(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        result = subprocess.run(
            ["git", "remote", "-v"],
            cwd=workspace, capture_output=True, text=True,
        )
        assert result.stdout.strip() == ""
        env.teardown(workspace)

    def test_committed_files_empty_no_llm_commits(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        state = env.capture_state(workspace)
        assert state.committed_files == [] or state.committed_files == ()

        env.teardown(workspace)

    def test_committed_files_shows_only_llm_work(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        # Simulate LLM creating a file and committing
        (workspace / "fix.py").write_text("print('fix')\n")
        subprocess.run(
            ["git", "add", "fix.py"], cwd=workspace,
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "-c", "user.name=llm", "-c", "user.email=llm@bench",
             "commit", "-m", "LLM fix"],
            cwd=workspace, capture_output=True, check=True,
        )
        state = env.capture_state(workspace)
        assert "fix.py" in state.committed_files
        # Should not include setup artifacts
        assert ".claude/settings.local.json" not in state.committed_files
        assert ".gitignore" not in state.committed_files
        env.teardown(workspace)

    def test_committed_files_excludes_deletions(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        # Simulate LLM deleting a file
        (workspace / "main.py").unlink()
        subprocess.run(
            ["git", "add", "main.py"], cwd=workspace,
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "-c", "user.name=llm", "-c", "user.email=llm@bench",
             "commit", "-m", "LLM delete"],
            cwd=workspace, capture_output=True, check=True,
        )
        state = env.capture_state(workspace)
        assert "main.py" not in state.committed_files
        env.teardown(workspace)

    def test_setup_snapshot_file_list(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        snapshot = env.capture_setup_state(workspace)
        assert isinstance(snapshot, SetupSnapshot)
        assert "main.py" in snapshot.file_list
        assert "lib/utils.py" in snapshot.file_list
        env.teardown(workspace)

    def test_setup_snapshot_clean_status(self, tmp_path):
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        snapshot = env.capture_setup_state(workspace)
        assert snapshot.git_status == "", f"expected clean status, got: {snapshot.git_status!r}"
        env.teardown(workspace)


class TestWorkspaceIsolation:

    def test_setup_creates_workspace_with_app(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "bivvy"
        workflow_path = BENCHMARK_ROOT / "fixtures" / "plain-text" / "bivvy.txt"

        workspace = env.setup(app_path, workflow_path, "plain-text")

        # App files should be in workspace root
        assert (workspace / "Cargo.toml").exists()
        assert (workspace / "src" / "main.rs").exists()
        assert (workspace / "src" / "config" / "loader.rs").exists()
        # Workspace should be a git repo with initial-state tag
        assert (workspace / ".git").is_dir()
        # Isolated home should exist
        assert (workspace / ".bench-home").is_dir()

        env.teardown(workspace)
        assert not workspace.exists()

    def test_plain_text_not_placed_as_claude_md(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "bivvy"
        workflow_path = BENCHMARK_ROOT / "fixtures" / "plain-text" / "bivvy.txt"

        workspace = env.setup(
            app_path, workflow_path, "plain-text",
            fixture_workflow_files=["CLAUDE.md", ".claude/bivvy-dev-workflow.md"],
        )
        assert not (workspace / "CLAUDE.md").exists()
        env.teardown(workspace)

    def test_markdown_placed_as_claude_md(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "bivvy"

        # Create a fake markdown workflow
        md_workflow = tmp_path / "workflow.md"
        md_workflow.write_text("# Instructions\nDo things carefully.")

        workspace = env.setup(app_path, md_workflow, "markdown")
        assert (workspace / "CLAUDE.md").exists()
        assert "Do things carefully" in (workspace / "CLAUDE.md").read_text()
        env.teardown(workspace)

    def test_build_env_scrubs_paths(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "bivvy"
        workflow_path = BENCHMARK_ROOT / "fixtures" / "plain-text" / "bivvy.txt"

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
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "bivvy"
        workflow_path = BENCHMARK_ROOT / "fixtures" / "plain-text" / "bivvy.txt"

        workspace = env.setup(app_path, workflow_path, "plain-text")
        gitconfig = workspace / ".bench-home" / ".gitconfig"
        assert gitconfig.exists()
        content = gitconfig.read_text()
        assert "name = benchmark" in content
        assert "email = benchmark@localhost" in content

        env.teardown(workspace)

    def test_workspace_has_isolated_tmpdir(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "bivvy"
        workflow_path = BENCHMARK_ROOT / "fixtures" / "plain-text" / "bivvy.txt"

        workspace = env.setup(app_path, workflow_path, "plain-text")
        tmpdir = workspace / ".bench-home" / "tmp"
        assert tmpdir.is_dir()

        env.teardown(workspace)

    def test_get_workflow_content_plain_text(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        workflow_path = BENCHMARK_ROOT / "fixtures" / "plain-text" / "bivvy.txt"

        content = env.get_workflow_content(workflow_path, "plain-text")
        assert content is not None

    def test_get_workflow_content_markdown_returns_none(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        md_path = tmp_path / "workflow.md"
        md_path.write_text("# Test")

        content = env.get_workflow_content(md_path, "markdown")
        assert content is None

    def test_structured_md_placed_as_claude_md(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "bivvy"

        workflow_path = tmp_path / "structured-workflow.md"
        workflow_path.write_text("# Structured MD\nDo structured things.")
        workspace = env.setup(app_path, workflow_path, "structured-md")
        assert (workspace / "CLAUDE.md").exists()
        assert (workspace / "CLAUDE.md").read_text() == workflow_path.read_text()
        env.teardown(workspace)

    def test_get_workflow_content_structured_md_returns_none(self, tmp_path):
        env = BenchmarkEnvironment(base_dir=tmp_path)
        md_path = tmp_path / "workflow.md"
        md_path.write_text("# Structured MD content")

        content = env.get_workflow_content(md_path, "structured-md")
        assert content is None

    # --- Isolation hooks & memory (audit item 3) --------------------------

    def test_settings_include_hooks(self, tmp_path):
        """settings.local.json must configure PreToolUse hooks for the guard."""
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        data = json.loads((workspace / ".claude" / "settings.local.json").read_text())
        assert "hooks" in data, "settings.local.json must contain hooks"
        hooks = data["hooks"]
        assert "PreToolUse" in hooks

        # Must have entries for Bash, Read, Grep, Glob
        matchers = {e["matcher"] for e in hooks["PreToolUse"]}
        assert matchers == {"Bash", "Read", "Grep", "Glob"}

        # Each entry must reference the guard script
        for entry in hooks["PreToolUse"]:
            cmds = [h["command"] for h in entry["hooks"]]
            assert any("guard.py" in c for c in cmds), \
                f"hook for {entry['matcher']} must reference guard.py"
        env.teardown(workspace)

    def test_guard_script_exists(self, tmp_path):
        """guard.py must exist in .bench-home/ and be executable."""
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        guard = workspace / ".bench-home" / "guard.py"
        assert guard.exists(), "guard.py must be written to .bench-home/"
        assert os.access(guard, os.X_OK), "guard.py must be executable"
        env.teardown(workspace)

    def test_guard_blocks_git_log(self, tmp_path):
        """Guard must block 'git log' without -1 limit."""
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        guard = workspace / ".bench-home" / "guard.py"

        # Blocked: git log (no limit)
        result = subprocess.run(
            ["python3", str(guard)],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git log --oneline"}}),
            capture_output=True, text=True, cwd=workspace,
        )
        assert result.returncode != 0, "git log without -1 must be blocked"

        # Allowed: git log -1
        result = subprocess.run(
            ["python3", str(guard)],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git log -1 --oneline"}}),
            capture_output=True, text=True, cwd=workspace,
        )
        assert result.returncode == 0, "git log -1 must be allowed"
        env.teardown(workspace)

    def test_guard_blocks_git_show_and_reflog(self, tmp_path):
        """Guard must block 'git show' and 'git reflog'."""
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        guard = workspace / ".bench-home" / "guard.py"

        for cmd in ["git show HEAD", "git reflog"]:
            result = subprocess.run(
                ["python3", str(guard)],
                input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
                capture_output=True, text=True, cwd=workspace,
            )
            assert result.returncode != 0, f"'{cmd}' must be blocked"
        env.teardown(workspace)

    def test_guard_blocks_bench_home_access(self, tmp_path):
        """Guard must block Read/Grep/Glob/Bash access to .bench-home/."""
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        guard = workspace / ".bench-home" / "guard.py"

        cases = [
            {"tool_name": "Read", "tool_input": {"file_path": ".bench-home/guard.py"}},
            {"tool_name": "Bash", "tool_input": {"command": "cat .bench-home/guard.py"}},
            {"tool_name": "Grep", "tool_input": {"pattern": "test", "path": ".bench-home/"}},
            {"tool_name": "Glob", "tool_input": {"pattern": "*.py", "path": ".bench-home/"}},
        ]
        for case in cases:
            result = subprocess.run(
                ["python3", str(guard)],
                input=json.dumps(case),
                capture_output=True, text=True, cwd=workspace,
            )
            assert result.returncode != 0, \
                f".bench-home access via {case['tool_name']} must be blocked"
        env.teardown(workspace)

    def test_guard_blocks_settings_access(self, tmp_path):
        """Guard must block reading .claude/settings files."""
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        guard = workspace / ".bench-home" / "guard.py"

        cases = [
            {"tool_name": "Read", "tool_input": {"file_path": ".claude/settings.local.json"}},
            {"tool_name": "Bash", "tool_input": {"command": "cat .claude/settings.local.json"}},
        ]
        for case in cases:
            result = subprocess.run(
                ["python3", str(guard)],
                input=json.dumps(case),
                capture_output=True, text=True, cwd=workspace,
            )
            assert result.returncode != 0, \
                f"settings access via {case['tool_name']} must be blocked"
        env.teardown(workspace)

    def test_guard_blocks_env_inspection(self, tmp_path):
        """Guard must block 'env' and 'printenv' commands."""
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        guard = workspace / ".bench-home" / "guard.py"

        for cmd in ["env", "printenv", "env | grep HOME"]:
            result = subprocess.run(
                ["python3", str(guard)],
                input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
                capture_output=True, text=True, cwd=workspace,
            )
            assert result.returncode != 0, f"'{cmd}' must be blocked"
        env.teardown(workspace)

    def test_guard_blocks_parent_traversal(self, tmp_path):
        """Guard must block parent directory traversal."""
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        guard = workspace / ".bench-home" / "guard.py"

        cases = [
            {"tool_name": "Bash", "tool_input": {"command": "ls ../"}},
            {"tool_name": "Read", "tool_input": {"file_path": "../other/file.txt"}},
            {"tool_name": "Grep", "tool_input": {"pattern": "test", "path": "../../"}},
        ]
        for case in cases:
            result = subprocess.run(
                ["python3", str(guard)],
                input=json.dumps(case),
                capture_output=True, text=True, cwd=workspace,
            )
            assert result.returncode != 0, \
                f"parent traversal via {case['tool_name']} must be blocked"
        env.teardown(workspace)

    def test_guard_blocks_git_internals(self, tmp_path):
        """Guard must block direct .git/ directory access."""
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        guard = workspace / ".bench-home" / "guard.py"

        cases = [
            {"tool_name": "Read", "tool_input": {"file_path": ".git/config"}},
            {"tool_name": "Bash", "tool_input": {"command": "cat .git/config"}},
        ]
        for case in cases:
            result = subprocess.run(
                ["python3", str(guard)],
                input=json.dumps(case),
                capture_output=True, text=True, cwd=workspace,
            )
            assert result.returncode != 0, \
                f".git/ access via {case['tool_name']} must be blocked"
        env.teardown(workspace)

    def test_guard_allows_normal_operations(self, tmp_path):
        """Guard must allow normal development operations."""
        app = _make_git_fixture(tmp_path)
        wf = _make_workflow_file(tmp_path)
        env = BenchmarkEnvironment(base_dir=tmp_path)

        workspace = env.setup(app, wf, "plain-text")
        guard = workspace / ".bench-home" / "guard.py"

        allowed = [
            {"tool_name": "Bash", "tool_input": {"command": "cargo build"}},
            {"tool_name": "Bash", "tool_input": {"command": "cargo test"}},
            {"tool_name": "Bash", "tool_input": {"command": "git status"}},
            {"tool_name": "Bash", "tool_input": {"command": "git add -A"}},
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'fix bug'"}},
            {"tool_name": "Bash", "tool_input": {"command": "git diff"}},
            {"tool_name": "Bash", "tool_input": {"command": "git diff --staged"}},
            {"tool_name": "Bash", "tool_input": {"command": "git log -1 --oneline"}},
            {"tool_name": "Read", "tool_input": {"file_path": "src/main.rs"}},
            {"tool_name": "Read", "tool_input": {"file_path": "CLAUDE.md"}},
            {"tool_name": "Grep", "tool_input": {"pattern": "fn main", "path": "src/"}},
            {"tool_name": "Glob", "tool_input": {"pattern": "**/*.rs", "path": "."}},
        ]
        for case in allowed:
            result = subprocess.run(
                ["python3", str(guard)],
                input=json.dumps(case),
                capture_output=True, text=True, cwd=workspace,
            )
            assert result.returncode == 0, \
                f"'{case['tool_input']}' should be allowed but was blocked: {result.stderr}"
        env.teardown(workspace)

    def test_build_env_disables_memory(self, tmp_path):
        """build_env() must set CLAUDE_CODE_DISABLE_AUTO_MEMORY=1."""
        env = BenchmarkEnvironment(base_dir=tmp_path)
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "bivvy"
        workflow_path = BENCHMARK_ROOT / "fixtures" / "plain-text" / "bivvy.txt"

        workspace = env.setup(app_path, workflow_path, "plain-text")
        cli_env = env.build_env(workspace)
        assert cli_env.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") == "1", \
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY must be set to '1'"
        env.teardown(workspace)

    def test_check_memory_leak_clean(self, tmp_path):
        """check_memory_leak() returns empty list when no memory files exist."""
        env = BenchmarkEnvironment(base_dir=tmp_path)
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "bivvy"
        workflow_path = BENCHMARK_ROOT / "fixtures" / "plain-text" / "bivvy.txt"

        workspace = env.setup(app_path, workflow_path, "plain-text")
        assert env.check_memory_leak(workspace) == []
        env.teardown(workspace)

    def test_check_memory_leak_detects_files(self, tmp_path):
        """check_memory_leak() detects memory files if they exist."""
        env = BenchmarkEnvironment(base_dir=tmp_path)
        app_path = BENCHMARK_ROOT / "fixtures" / "apps" / "bivvy"
        workflow_path = BENCHMARK_ROOT / "fixtures" / "plain-text" / "bivvy.txt"

        workspace = env.setup(app_path, workflow_path, "plain-text")

        # Simulate a memory file being created
        encoded = str(workspace.resolve()).replace("/", "-").replace(" ", "-")
        mem_dir = workspace / ".bench-home" / ".claude" / "projects" / encoded / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "auto.md").write_text("some memory")

        leaked = env.check_memory_leak(workspace)
        assert len(leaked) > 0, "should detect memory file"
        assert any("memory" in p for p in leaked)
        env.teardown(workspace)


# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------

class TestParallelExecution:

    def test_single_worker_matches_sequential(self, case, isolated_env):
        mock = _make_trace_executor()
        seq = run_all([case], environment=isolated_env, _execute=mock)
        par = list(run_parallel([case], workers=1, environment=isolated_env, _execute=mock))
        assert len(seq) == len(par) == 1
        assert (seq[0].error is None) == (par[0].error is None)

    def test_multiple_workers(self, case, isolated_env):
        mock = _make_trace_executor()
        results = list(run_parallel([case, case], workers=2, delay_s=0.0, environment=isolated_env, _execute=mock))
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
            max_turns_configured=25,
        )
        run_dir = recorder.save_run(record, raw_output=result.raw_output)

        # Verify per-run directory structure
        assert run_dir.is_dir()
        assert (run_dir / "summary.json").exists()
        assert (run_dir / "stream.json").exists()
        assert (run_dir / "state.json").exists()

        # Verify stream.json is a valid JSON array with events
        stream = recorder.load_stream(
            record.fixture_id, record.format, record.prompt_id, record.run_id,
        )
        assert isinstance(stream, list)
        assert len(stream) > 0

        # Verify stream metadata was extracted into summary.json
        with open(run_dir / "summary.json") as f:
            summary_data = json.load(f)
        assert summary_data["session_id"] == "infra-test-session"
        assert summary_data["cost_usd"] == 0.042
        assert summary_data["input_tokens"] == 3200
        assert summary_data["output_tokens"] == 850
        assert summary_data["num_turns"] == 8

        record2 = RunRecord.from_run_summary(
            result.summary, run_id=1,
            wall_clock_ms=result.wall_clock_ms + 1000,
        )
        recorder.save_run(record2, raw_output="different output")
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

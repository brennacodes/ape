"""Integration tests for the evaluator using real benchmark output data.

These tests load actual raw CLI output from stored result JSON files, parse
them with parse_trace_jsonl (the same parser the runner uses), reconstruct
proper evaluation context from YAML configs, and verify that specific checks
produce correct results.

This catches bugs that synthetic traces miss — real traces have hundreds of
events, stream-json splitting, merged assistant turns, and the full complexity
of a live Claude Code session.
"""

import json
import sys
import os
import yaml
import pytest
from pathlib import Path

# Wire up module paths
_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.join(_HERE, "..", "runner")
_COORD = os.path.join(_HERE, "..", "coordinator")
for _dir in (_RUNNER, _COORD):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

from trace import parse_trace_jsonl, Trace
from evaluator import (
    evaluate,
    resolve_metric,
    evaluate_condition,
    _tool_indices_matching,
    _resolve_diff_files_changed,
    _resolve_diff_scope_permitted,
    _normalize_to_relative,
    _detect_phases,
    _count_impl_verify_cycles,
    CheckResult,
)
from coordinator import get_app_config_variables, build_context

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BENCHMARK_ROOT = Path(_HERE).parent.parent
RESULTS_DIR = BENCHMARK_ROOT / "output" / "raw" / "bivvy"
CONFIG_PATH = BENCHMARK_ROOT / "test-configs" / "bivvy.yml"
PROMPTS_DIR = BENCHMARK_ROOT / "prompts"


def _has_real_data():
    """Check if real benchmark result files are available."""
    return RESULTS_DIR.is_dir() and any(RESULTS_DIR.rglob("*.json"))


needs_real_data = pytest.mark.skipif(
    not _has_real_data(),
    reason="No benchmark result files in output/raw/bivvy",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _load_record(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def _build_context(record: dict, config: dict) -> dict:
    """Reconstruct evaluation context from YAML files (same as re_evaluate.py)."""
    stored_conditions = record.get("eval_conditions")
    stored_variables = record.get("eval_variables")
    if stored_conditions is not None and stored_variables is not None:
        return {
            "conditions": stored_conditions,
            "variables": stored_variables,
            "phase_tool_mapping": config.get("phase_tool_mapping", {}),
            "phase_classification": config.get("phase_classification", {}),
        }

    prompt_id = record.get("prompt_id", "")
    fixture_id = record.get("fixture_id", "bivvy")
    parts = prompt_id.split("/", 1) if prompt_id else []
    category = parts[0] if len(parts) >= 1 else ""
    item_id = parts[1] if len(parts) >= 2 else ""

    prompt_path = PROMPTS_DIR / f"{category}.yml"
    if prompt_path.exists():
        prompt_data = yaml.safe_load(open(prompt_path))
    else:
        prompt_data = {"conditions": {}, "variables": {}}

    app_config_path = PROMPTS_DIR / "app-configs" / f"{fixture_id}.yml"
    app_config_variables = None
    if app_config_path.exists() and category and item_id:
        ac_data = yaml.safe_load(open(app_config_path))
        app_config_variables = get_app_config_variables(ac_data, category, item_id)

    return build_context(prompt_data, config, app_config_variables=app_config_variables)


def _evaluate_file(path: Path, config: dict) -> tuple[dict, Trace, list[CheckResult]]:
    """Load a result file, parse its trace, evaluate all checks."""
    record = _load_record(path)
    raw_output = record.get("raw_output", "")
    if not raw_output:
        pytest.skip("No raw_output in record")
    trace = parse_trace_jsonl(raw_output)
    context = _build_context(record, config)
    context["workspace_state"] = record.get("workspace_state", {})
    context["workspace_path"] = trace.workspace_path
    results = evaluate(trace, config["checks"], context)
    return record, trace, results


def _result_map(results: list[CheckResult]) -> dict[str, CheckResult]:
    return {r.check_id: r for r in results}


# ---------------------------------------------------------------------------
# Helper to locate a specific result file
# ---------------------------------------------------------------------------

def _find_result(fmt: str, prompt_id: str, run_id: int = 0) -> Path:
    path = RESULTS_DIR / fmt / prompt_id / f"{run_id:03d}.json"
    if not path.exists():
        pytest.skip(f"Result file not found: {path}")
    return path


# ===========================================================================
# TRACE PARSING — real data
# ===========================================================================

@needs_real_data
class TestTraceParsingRealData:
    """Verify parse_trace_jsonl handles real CLI output correctly."""

    def test_parses_without_error(self, config):
        path = _find_result("ape", "bugs/silent_yaml_failure")
        record = _load_record(path)
        trace = parse_trace_jsonl(record["raw_output"])
        assert len(trace.events) > 0, "Trace should have events"

    def test_extracts_workspace_path(self, config):
        path = _find_result("ape", "bugs/silent_yaml_failure")
        record = _load_record(path)
        trace = parse_trace_jsonl(record["raw_output"])
        assert trace.workspace_path is not None, "workspace_path should be extracted from init event"

    def test_tool_calls_present(self, config):
        path = _find_result("ape", "bugs/silent_yaml_failure")
        record = _load_record(path)
        trace = parse_trace_jsonl(record["raw_output"])
        all_calls = trace.all_tool_calls()
        assert len(all_calls) > 10, f"Expected many tool calls, got {len(all_calls)}"

    def test_bash_commands_extracted(self, config):
        path = _find_result("ape", "bugs/silent_yaml_failure")
        record = _load_record(path)
        trace = parse_trace_jsonl(record["raw_output"])
        bash_cmds = trace.bash_commands()
        assert len(bash_cmds) > 0, "Should have Bash commands"
        assert any("cargo" in cmd for cmd in bash_cmds), "Should have cargo commands"

    def test_merged_events_consistent(self, config):
        """Event merging should produce contiguous indices."""
        path = _find_result("ape", "bugs/silent_yaml_failure")
        record = _load_record(path)
        trace = parse_trace_jsonl(record["raw_output"])
        indices = [ev.index for ev in trace.events]
        assert indices == list(range(len(trace.events))), "Event indices should be sequential after merging"

    def test_tool_call_event_index_matches_parent(self, config):
        """Each ToolCall.event_index should equal its parent event's index."""
        path = _find_result("ape", "bugs/silent_yaml_failure")
        record = _load_record(path)
        trace = parse_trace_jsonl(record["raw_output"])
        for ev in trace.events:
            for tc in ev.tool_calls:
                assert tc.event_index == ev.index, (
                    f"ToolCall {tc.name} has event_index={tc.event_index} "
                    f"but parent event index={ev.index}"
                )


# ===========================================================================
# EVALUATOR — real data ground truth
# ===========================================================================

@needs_real_data
class TestEvaluatorRealDataApe:
    """
    Test evaluator against ape/bugs/silent_yaml_failure/000.json.

    Ground truth established by manual inspection of the trace:
    - cargo clippy was run (1 time) → run_clippy should pass
    - cargo llvm-cov was NOT run → run_coverage should fail
    - cargo test was run (7 times) → verify_tests_after_changes should pass
    - No 'git add .' was used → no_git_add_dot should pass
    - cargo build was run → build_before_commit should pass
    - cargo build --all-targets was run → build_dev should pass
    - cargo build --release was NOT run → build_release should fail
    - impl/test cycles ≤ 3 → max_impl_test_cycles should pass
    """

    @pytest.fixture(scope="class")
    def evaluated(self, config):
        path = _find_result("ape", "bugs/silent_yaml_failure")
        return _evaluate_file(path, config)

    def test_run_coverage_fails(self, evaluated):
        """cargo llvm-cov was never run, so run_coverage correctly fails."""
        _, trace, results = evaluated
        rm = _result_map(results)
        assert rm["run_coverage"].passed is False
        assert "got 0" in rm["run_coverage"].detail

    def test_run_clippy_passes(self, evaluated):
        """cargo clippy was run once."""
        _, trace, results = evaluated
        rm = _result_map(results)
        assert rm["run_clippy"].passed is True

    def test_verify_tests_after_changes_passes(self, evaluated):
        """cargo test was run and appears before task_completed."""
        _, trace, results = evaluated
        rm = _result_map(results)
        assert rm["verify_tests_after_changes"].passed is True

    def test_no_git_add_dot_passes(self, evaluated):
        """No 'git add .' command was used."""
        _, trace, results = evaluated
        rm = _result_map(results)
        assert rm["no_git_add_dot"].passed is True

    def test_build_before_commit_passes(self, evaluated):
        """cargo build precedes git commit (or git commit not present)."""
        _, trace, results = evaluated
        rm = _result_map(results)
        assert rm["build_before_commit"].passed is True

    def test_build_dev_passes(self, evaluated):
        """cargo build --all-targets was run."""
        _, trace, results = evaluated
        rm = _result_map(results)
        assert rm["build_dev"].passed is True

    def test_build_release_fails(self, evaluated):
        """cargo build --release was NOT run."""
        _, trace, results = evaluated
        rm = _result_map(results)
        assert rm["build_release"].passed is False

    def test_max_impl_test_cycles_passes(self, evaluated):
        """Implementation/test cycle count ≤ 3."""
        _, trace, results = evaluated
        rm = _result_map(results)
        assert rm["max_impl_test_cycles"].passed is True

    def test_test_after_commit_passes(self, evaluated):
        _, trace, results = evaluated
        rm = _result_map(results)
        assert rm["test_after_commit"].passed is True

    def test_build_after_commit_passes(self, evaluated):
        _, trace, results = evaluated
        rm = _result_map(results)
        assert rm["build_after_commit"].passed is True

    def test_rerun_after_fix_passes(self, evaluated):
        """Edit → Bash pattern should be present."""
        _, trace, results = evaluated
        rm = _result_map(results)
        assert rm["rerun_after_fix"].passed is True

    def test_scope_to_request_passes(self, evaluated):
        """Modified files should be subset of permitted paths."""
        _, trace, results = evaluated
        rm = _result_map(results)
        assert rm["scope_to_request"].passed is True


@needs_real_data
class TestEvaluatorRealDataNoWorkflow:
    """
    Test evaluator against no-workflow/bugs/silent_yaml_failure/000.json.

    Ground truth — no-workflow runs lack linting, coverage, build commands:
    - cargo clippy was NOT run → run_clippy should fail
    - cargo llvm-cov was NOT run → run_coverage should fail
    - cargo build --all-targets was NOT run → build_dev should fail
    - cargo build --release was NOT run → build_release should fail
    - cargo test WAS run → verify_tests_after_changes should pass
    - No 'git add .' → no_git_add_dot should pass
    """

    @pytest.fixture(scope="class")
    def evaluated(self, config):
        path = _find_result("no-workflow", "bugs/silent_yaml_failure")
        return _evaluate_file(path, config)

    def test_run_clippy_fails(self, evaluated):
        """No-workflow run didn't invoke cargo clippy."""
        _, _, results = evaluated
        rm = _result_map(results)
        assert rm["run_clippy"].passed is False

    def test_run_coverage_fails(self, evaluated):
        """No-workflow run didn't invoke cargo llvm-cov."""
        _, _, results = evaluated
        rm = _result_map(results)
        assert rm["run_coverage"].passed is False

    def test_build_dev_fails(self, evaluated):
        """No-workflow run didn't invoke cargo build --all-targets."""
        _, _, results = evaluated
        rm = _result_map(results)
        assert rm["build_dev"].passed is False

    def test_build_release_fails(self, evaluated):
        """No-workflow run didn't invoke cargo build --release."""
        _, _, results = evaluated
        rm = _result_map(results)
        assert rm["build_release"].passed is False

    def test_verify_tests_passes(self, evaluated):
        """cargo test was run."""
        _, _, results = evaluated
        rm = _result_map(results)
        assert rm["verify_tests_after_changes"].passed is True

    def test_no_git_add_dot_passes(self, evaluated):
        """No 'git add .' was used."""
        _, _, results = evaluated
        rm = _result_map(results)
        assert rm["no_git_add_dot"].passed is True

    def test_rerun_after_fix_passes(self, evaluated):
        _, _, results = evaluated
        rm = _result_map(results)
        assert rm["rerun_after_fix"].passed is True


# ===========================================================================
# METRIC RESOLUTION — real data
# ===========================================================================

@needs_real_data
class TestMetricResolutionRealData:
    """Test metric resolution functions against real traces."""

    @pytest.fixture(scope="class")
    def trace_and_context(self, config):
        path = _find_result("ape", "bugs/silent_yaml_failure")
        record = _load_record(path)
        trace = parse_trace_jsonl(record["raw_output"])
        context = _build_context(record, config)
        context["workspace_state"] = record.get("workspace_state", {})
        context["workspace_path"] = trace.workspace_path
        return trace, context

    def test_tool_indices_matching_cargo_test(self, trace_and_context):
        """_tool_indices_matching should find cargo test commands."""
        trace, _ = trace_and_context
        indices = _tool_indices_matching(trace, "Bash", "cargo test")
        assert len(indices) >= 1, "Should find cargo test commands"

    def test_tool_indices_matching_cargo_llvm_cov(self, trace_and_context):
        """_tool_indices_matching should find zero cargo llvm-cov commands."""
        trace, _ = trace_and_context
        indices = _tool_indices_matching(trace, "Bash", "cargo llvm-cov")
        assert len(indices) == 0, "Should find no cargo llvm-cov commands"

    def test_tool_indices_matching_cargo_clippy(self, trace_and_context):
        """_tool_indices_matching should find cargo clippy commands."""
        trace, _ = trace_and_context
        indices = _tool_indices_matching(trace, "Bash", "cargo clippy")
        assert len(indices) >= 1, "Should find cargo clippy commands"

    def test_diff_files_changed(self, trace_and_context):
        """diff.files_changed should return relative paths of modified files."""
        trace, context = trace_and_context
        changed = _resolve_diff_files_changed(trace, context)
        assert len(changed) > 0, "Should detect file changes"
        # All paths should be relative (no leading /)
        for p in changed:
            assert not p.startswith("/"), f"Path should be relative: {p}"

    def test_diff_scope_permitted_paths(self, trace_and_context):
        """diff.scope.permitted_paths should return relative read paths."""
        trace, context = trace_and_context
        permitted = _resolve_diff_scope_permitted(trace, context)
        assert len(permitted) > 0, "Should have permitted paths"
        for p in permitted:
            assert not p.startswith("/"), f"Path should be relative: {p}"

    def test_changed_subset_of_permitted(self, trace_and_context):
        """Modified files should be a subset of permitted paths."""
        trace, context = trace_and_context
        changed = set(_resolve_diff_files_changed(trace, context))
        permitted = set(_resolve_diff_scope_permitted(trace, context))
        assert changed <= permitted, (
            f"Changed files not in permitted: {changed - permitted}"
        )

    def test_resolve_metric_file_read(self, trace_and_context):
        """resolve_metric for tool_call.file_read should return indices."""
        trace, context = trace_and_context
        indices = resolve_metric("tool_call.file_read", trace, context)
        assert isinstance(indices, list)
        assert len(indices) > 0

    def test_resolve_metric_execute_command(self, trace_and_context):
        """resolve_metric for tool_call.execute_command should return indices."""
        trace, context = trace_and_context
        indices = resolve_metric("tool_call.execute_command", trace, context)
        assert isinstance(indices, list)
        assert len(indices) > 0


# ===========================================================================
# PHASE DETECTION — real data
# ===========================================================================

@needs_real_data
class TestPhaseDetectionRealData:
    """Test phase detection with real trace data."""

    @pytest.fixture(scope="class")
    def trace_and_config(self, config):
        path = _find_result("ape", "bugs/silent_yaml_failure")
        record = _load_record(path)
        trace = parse_trace_jsonl(record["raw_output"])
        return trace, config

    def test_phases_detected(self, trace_and_config):
        """Phase detection should identify at least implementation and testing."""
        trace, config = trace_and_config
        phase_mapping = config.get("phase_tool_mapping", {})
        phase_class = config.get("phase_classification", {})
        order, phase_events = _detect_phases(trace, phase_mapping, phase_class)
        assert "implementation" in order, "Should detect implementation phase"
        assert "testing" in order, "Should detect testing phase"

    def test_cycle_count_reasonable(self, trace_and_config):
        """Impl/test cycle count should be reasonable (1-5)."""
        trace, config = trace_and_config
        phase_mapping = config.get("phase_tool_mapping", {})
        phase_class = config.get("phase_classification", {})
        _, phase_events = _detect_phases(trace, phase_mapping, phase_class)
        count = _count_impl_verify_cycles(phase_events)
        assert 0 <= count <= 5, f"Cycle count {count} seems unreasonable"


# ===========================================================================
# CROSS-FORMAT CONSISTENCY
# ===========================================================================

@needs_real_data
class TestCrossFormatConsistency:
    """Verify that deterministic checks produce consistent results across formats.

    Some checks (like run_coverage, run_clippy) depend only on whether specific
    commands were run, not on context reconstruction. These should be consistent
    with the raw trace data regardless of format.
    """

    def _formats_with_data(self, prompt_id: str) -> list[str]:
        """Find all formats that have data for a given prompt_id."""
        formats = []
        for fmt_dir in sorted(RESULTS_DIR.iterdir()):
            if not fmt_dir.is_dir():
                continue
            result_file = fmt_dir / prompt_id / "000.json"
            if result_file.exists():
                formats.append(fmt_dir.name)
        return formats

    def test_run_coverage_consistent_across_formats(self, config):
        """run_coverage (cargo llvm-cov count) should match trace reality for all formats."""
        prompt_id = "bugs/silent_yaml_failure"
        formats = self._formats_with_data(prompt_id)
        if len(formats) < 2:
            pytest.skip("Need at least 2 formats for cross-format test")

        for fmt in formats:
            path = _find_result(fmt, prompt_id)
            record = _load_record(path)
            raw_output = record.get("raw_output", "")
            if not raw_output:
                continue
            trace = parse_trace_jsonl(raw_output)

            # Directly verify: does the trace contain cargo llvm-cov?
            llvm_cov_cmds = trace.bash_commands_matching("cargo llvm-cov")
            context = _build_context(record, config)
            context["workspace_state"] = record.get("workspace_state", {})
            context["workspace_path"] = trace.workspace_path
            results = evaluate(trace, config["checks"], context)
            rm = _result_map(results)

            if len(llvm_cov_cmds) == 0:
                assert rm["run_coverage"].passed is False, (
                    f"Format {fmt}: no cargo llvm-cov in trace but run_coverage passed"
                )
            else:
                assert rm["run_coverage"].passed is True, (
                    f"Format {fmt}: cargo llvm-cov in trace but run_coverage failed"
                )


# ===========================================================================
# RE-EVALUATION CONSISTENCY
# ===========================================================================

@needs_real_data
class TestReEvaluationConsistency:
    """Test that re-evaluation with correct parser/context produces stable results.

    Evaluating the same trace twice should produce identical results.
    """

    def test_double_evaluation_identical(self, config):
        """Running evaluate twice on the same trace/context yields same outcomes."""
        path = _find_result("ape", "bugs/silent_yaml_failure")
        record = _load_record(path)
        trace = parse_trace_jsonl(record["raw_output"])
        context = _build_context(record, config)
        context["workspace_state"] = record.get("workspace_state", {})
        context["workspace_path"] = trace.workspace_path

        results1 = evaluate(trace, config["checks"], context)
        results2 = evaluate(trace, config["checks"], context)

        for r1, r2 in zip(results1, results2):
            assert r1.check_id == r2.check_id
            assert r1.passed == r2.passed, (
                f"Check {r1.check_id}: first={r1.passed}, second={r2.passed}"
            )
            assert r1.skip_reason == r2.skip_reason

    def test_parse_trace_idempotent(self, config):
        """Parsing the same raw_output twice gives traces with identical structure."""
        path = _find_result("ape", "bugs/silent_yaml_failure")
        record = _load_record(path)
        raw = record["raw_output"]

        t1 = parse_trace_jsonl(raw)
        t2 = parse_trace_jsonl(raw)

        assert len(t1.events) == len(t2.events)
        assert t1.workspace_path == t2.workspace_path
        assert t1.session_id == t2.session_id

        for e1, e2 in zip(t1.events, t2.events):
            assert e1.index == e2.index
            assert len(e1.tool_calls) == len(e2.tool_calls)
            assert len(e1.tool_results) == len(e2.tool_results)


# ===========================================================================
# CONTEXT RECONSTRUCTION
# ===========================================================================

@needs_real_data
class TestContextReconstruction:
    """Test that context is properly reconstructed from YAML files."""

    def test_bugs_category_has_explicit_edit_requested(self, config):
        """bugs.yml should set explicit_edit_requested condition."""
        prompt_data = yaml.safe_load(open(PROMPTS_DIR / "bugs.yml"))
        context = build_context(prompt_data, config)
        conditions = context.get("conditions", {})
        assert conditions.get("explicit_edit_requested") is True

    def test_app_config_variables_loaded(self, config):
        """App config variables should include location for silent_yaml_failure."""
        ac_path = PROMPTS_DIR / "app-configs" / "bivvy.yml"
        ac_data = yaml.safe_load(open(ac_path))
        variables = get_app_config_variables(ac_data, "bugs", "silent_yaml_failure")
        assert variables is not None
        assert "location" in variables
        assert variables["location"] == "src/config/loader.rs"

    def test_context_has_phase_mapping(self, config):
        """Built context should include phase_tool_mapping from config."""
        prompt_data = yaml.safe_load(open(PROMPTS_DIR / "bugs.yml"))
        context = build_context(prompt_data, config)
        assert "phase_tool_mapping" in context
        assert "implementation" in context["phase_tool_mapping"]
        assert "testing" in context["phase_tool_mapping"]

    def test_stored_context_preferred(self, config):
        """When eval_conditions/eval_variables are stored, use them directly."""
        record = {
            "eval_conditions": {"explicit_edit_requested": True, "custom": True},
            "eval_variables": {"file_path": "src/main.rs"},
        }
        context = _build_context(record, config)
        assert context["conditions"]["custom"] is True
        assert context["variables"]["file_path"] == "src/main.rs"


# ===========================================================================
# PATH NORMALIZATION
# ===========================================================================

@needs_real_data
class TestPathNormalization:
    """Test path normalization with real workspace paths."""

    def test_normalize_removes_workspace_prefix(self, config):
        path = _find_result("ape", "bugs/silent_yaml_failure")
        record = _load_record(path)
        trace = parse_trace_jsonl(record["raw_output"])

        workspace = trace.workspace_path
        assert workspace is not None

        # Modified paths are absolute; after normalization they should be relative
        modified_abs = set(trace.all_file_paths_modified())
        assert any(p.startswith("/") for p in modified_abs), "Modified paths should be absolute"

        normalized = _normalize_to_relative(modified_abs, workspace)
        for p in normalized:
            assert not p.startswith("/"), f"Normalized path still absolute: {p}"

    def test_normalize_handles_symlinks(self, config):
        """macOS /var → /private/var symlink should be handled."""
        path = _find_result("ape", "bugs/silent_yaml_failure")
        record = _load_record(path)
        trace = parse_trace_jsonl(record["raw_output"])

        workspace = trace.workspace_path
        modified = set(trace.all_file_paths_modified())

        # Both /var/... and /private/var/... should normalize to the same relative path
        normalized = _normalize_to_relative(modified, workspace)
        assert len(normalized) > 0, "Should produce normalized paths"


# ===========================================================================
# EDGE CASES FROM REAL DATA
# ===========================================================================

@needs_real_data
class TestRealDataEdgeCases:
    """Edge cases discovered from real benchmark runs."""

    def test_metric_args_substring_match(self, config):
        """metric_args 'cargo test' should match 'cargo test --lib ...' etc."""
        path = _find_result("ape", "bugs/silent_yaml_failure")
        record = _load_record(path)
        trace = parse_trace_jsonl(record["raw_output"])

        # These specific commands in the trace contain "cargo test" as substring
        indices = _tool_indices_matching(trace, "Bash", "cargo test")
        # Manual count: there are 7 cargo test commands in this trace
        assert len(indices) >= 5, f"Expected many cargo test matches, got {len(indices)}"

    def test_metric_args_no_false_positives(self, config):
        """metric_args should not match unrelated commands."""
        path = _find_result("ape", "bugs/silent_yaml_failure")
        record = _load_record(path)
        trace = parse_trace_jsonl(record["raw_output"])

        # "cargo llvm-cov" should not match "cargo test" commands
        indices = _tool_indices_matching(trace, "Bash", "cargo llvm-cov")
        assert len(indices) == 0, (
            f"Should not match cargo llvm-cov in trace without it, got {len(indices)}"
        )

    def test_skipped_checks_have_reason(self, config):
        """Checks that skip should always have a skip_reason."""
        path = _find_result("ape", "bugs/silent_yaml_failure")
        _, _, results = _evaluate_file(path, config)
        for r in results:
            if r.passed is None:
                assert r.skip_reason, (
                    f"Check {r.check_id} has passed=None but no skip_reason"
                )

    def test_all_checks_in_config_evaluated(self, config):
        """Every check in the config should produce a result."""
        path = _find_result("ape", "bugs/silent_yaml_failure")
        _, _, results = _evaluate_file(path, config)
        check_ids_in_config = {c["id"] for c in config["checks"]}
        check_ids_in_results = {r.check_id for r in results}
        missing = check_ids_in_config - check_ids_in_results
        assert not missing, f"Checks in config but not in results: {missing}"

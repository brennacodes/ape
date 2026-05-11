"""Tests for benchmark/scripts/evaluator/evaluator.py.

Tests are organized by function. Each test builds a minimal synthetic Trace
(using load_trace_from_string from runner/trace.py) and checks expected outcomes.

Helpers at the top build JSONL lines for common event patterns.
"""

import json
import sys
import os
import pytest

# Wire up runner module so trace.py is importable
_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.join(_HERE, "..", "runner")
if _RUNNER not in sys.path:
    sys.path.insert(0, _RUNNER)

from trace import load_trace_from_string
from evaluator import (
    CheckResult,
    MetricNotResolvable,
    UnknownOperator,
    interpolate,
    evaluate_prompt_condition,
    resolve_metric,
    resolve_target,
    evaluate_condition,
    evaluate_check,
    evaluate,
    TOOL_NAME_MAP,
    MULTI_TOOL_MAP,
    _TASK_COMPLETED,
    _path_index_map,
    _tool_indices,
    _tool_indices_matching,
    _tool_indices_multi,
    _tool_indices_multi_matching,
    _primary_arg,
    _tool_content,
    _apply_operator,
    _evaluate_precedes_per_path,
    _CONTENT_OPERATORS,
    _ALL_METRIC_NAMES,
    _detect_phases,
    _build_activation_timeline,
    _matches_signal,
    _count_impl_verify_cycles,
    _resolve_diff_files_changed,
    _resolve_diff_scope_permitted,
    _resolve_workspace_untracked,
    _resolve_git_committed_files,
    _summarize_value,
    _resolve_position_boundary,
)
from trace import ToolCall


# ===========================================================================
# JSONL Trace Fixtures
# ===========================================================================

SESSION = "sess-0000"


def _ev(type_: str, role: str, content) -> str:
    return json.dumps({
        "type": type_,
        "sessionId": SESSION,
        "parentUuid": None,
        "message": {"role": role, "content": content},
    })


def _user_prompt(text: str) -> str:
    return _ev("user", "user", text)


def _assistant_tool_use(name: str, input_: dict, tool_id: str = "t1") -> str:
    return _ev("assistant", "assistant", [
        {"type": "tool_use", "id": tool_id, "name": name, "input": input_}
    ])


def _assistant_parallel(calls: list[tuple[str, dict, str]]) -> str:
    """Multiple tool_use blocks in one assistant message."""
    blocks = [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}
        for name, inp, tid in calls
    ]
    return _ev("assistant", "assistant", blocks)


def _tool_result(tool_id: str = "t1", content: str = "ok") -> str:
    return _ev("user", "user", [
        {"type": "tool_result", "tool_use_id": tool_id, "content": content}
    ])


def _assistant_text(text: str) -> str:
    return _ev("assistant", "assistant", [{"type": "text", "text": text}])


def _to_jsonl(*lines: str) -> str:
    return "\n".join(lines)


def _simple_trace(*events: str) -> object:
    return load_trace_from_string(_to_jsonl(*events))


# Convenient shortcuts
def _read(path: str, tid: str = "t1"):
    return _assistant_tool_use("Read", {"file_path": path}, tid)

def _write(path: str, tid: str = "t1"):
    return _assistant_tool_use("Write", {"file_path": path}, tid)

def _edit(path: str, tid: str = "t1"):
    return _assistant_tool_use("Edit", {"file_path": path}, tid)

def _bash(cmd: str, tid: str = "t1"):
    return _assistant_tool_use("Bash", {"command": cmd}, tid)

def _grep(pattern: str, extra: dict = None, tid: str = "t1"):
    inp = {"pattern": pattern}
    if extra:
        inp.update(extra)
    return _assistant_tool_use("Grep", inp, tid)

def _skill(name: str, tid: str = "t1"):
    return _assistant_tool_use("Skill", {"skill": name}, tid)

def _ask(question: str, tid: str = "t1"):
    return _assistant_tool_use("AskUserQuestion", {"questions": question}, tid)


# ===========================================================================
# interpolate
# ===========================================================================

class TestInterpolate:
    def test_no_placeholders(self):
        assert interpolate("hello", {}) == "hello"

    def test_single_placeholder(self):
        assert interpolate("${foo}", {"foo": "bar"}) == "bar"

    def test_multiple_placeholders(self):
        assert interpolate("${a} and ${b}", {"a": "x", "b": "y"}) == "x and y"

    def test_non_string_passthrough(self):
        assert interpolate(42, {}) == 42
        assert interpolate(["a"], {}) == ["a"]

    def test_unknown_variable_raises(self):
        with pytest.raises(ValueError, match="Variable"):
            interpolate("${missing}", {})

    def test_integer_variable(self):
        assert interpolate("run ${cmd}", {"cmd": 123}) == "run 123"


# ===========================================================================
# evaluate_prompt_condition
# ===========================================================================

class TestEvaluatePromptCondition:
    def test_true_condition(self):
        assert evaluate_prompt_condition("is_informational", {"is_informational": True}) is True

    def test_false_condition(self):
        assert evaluate_prompt_condition("is_informational", {"is_informational": False}) is False

    def test_missing_defaults_false(self):
        assert evaluate_prompt_condition("is_informational", {}) is False

    def test_negation_of_true(self):
        assert evaluate_prompt_condition("!explicit_feature_requested", {"explicit_feature_requested": True}) is False

    def test_negation_of_false(self):
        assert evaluate_prompt_condition("!explicit_feature_requested", {"explicit_feature_requested": False}) is True

    def test_negation_of_missing(self):
        # Missing → False; negated → True
        assert evaluate_prompt_condition("!unknown", {}) is True


# ===========================================================================
# resolve_metric
# ===========================================================================

class TestResolveMetric:
    def _ctx(self, **variables):
        return {"conditions": {}, "variables": variables}

    def test_file_read_returns_indices(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _read("foo.py", "t1"),
            _tool_result("t1"),
            _read("bar.py", "t2"),
            _tool_result("t2"),
        )
        indices = resolve_metric("tool_call.file_read", trace, self._ctx())
        assert len(indices) == 2

    def test_bash_returns_indices(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("pytest", "t1"),
            _tool_result("t1"),
        )
        indices = resolve_metric("tool_call.execute_command", trace, self._ctx())
        assert len(indices) == 1

    def test_no_calls_returns_empty(self):
        trace = _simple_trace(_user_prompt("task"))
        assert resolve_metric("tool_call.file_write", trace, self._ctx()) == []

    def test_search_ordered_calls(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _grep("err", {}, "t1"),
            _tool_result("t1"),
            _grep("error handling", {}, "t2"),
            _tool_result("t2"),
        )
        calls = resolve_metric("tool_call.search.ordered_calls", trace, self._ctx())
        assert calls == [{"pattern": "err"}, {"pattern": "error handling"}]

    def test_search_args_merged(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _grep("foo", {"type": "py"}, "t1"),
            _tool_result("t1"),
            _grep("bar", {"path": "src/"}, "t2"),
            _tool_result("t2"),
        )
        args = resolve_metric("tool_call.search.args", trace, self._ctx())
        assert "type" in args
        assert "path" in args

    def test_parallel_batches(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _assistant_parallel([
                ("Grep", {"pattern": "foo"}, "t1"),
                ("Grep", {"pattern": "bar"}, "t2"),
            ]),
            _tool_result("t1"),
            _tool_result("t2"),
        )
        batches = resolve_metric("execution.parallel_batch", trace, self._ctx())
        assert len(batches) == 1
        assert batches[0].count("Grep") == 2

    def test_text_response_indices(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _assistant_text("thinking..."),
        )
        indices = resolve_metric("trace.text_response", trace, self._ctx())
        assert len(indices) == 1

    def test_tool_call_batches(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("ls", "t1"),
            _tool_result("t1"),
            _bash("pwd", "t2"),
            _tool_result("t2"),
        )
        batches = resolve_metric("trace.tool_call_batches", trace, self._ctx())
        assert len(batches) == 2

    def test_tool_result_indices(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("ls", "t1"),
            _tool_result("t1"),
        )
        indices = resolve_metric("tool_call.result", trace, self._ctx())
        assert len(indices) == 1

    def test_task_completed_sentinel(self):
        trace = _simple_trace(_user_prompt("task"))
        indices = resolve_metric("task_completed", trace, self._ctx())
        assert indices == [_TASK_COMPLETED]

    def test_phase_execution_order_requires_mapping(self):
        trace = _simple_trace(_user_prompt("task"))
        # Without phase_tool_mapping in context, raises MetricNotResolvable
        with pytest.raises(MetricNotResolvable, match="requires phase_tool_mapping"):
            resolve_metric("phase.execution_order", trace, self._ctx())

    def test_phase_execution_order_with_mapping(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _grep("foo", {}, "t1"),
            _tool_result("t1"),
            _write("bar.py", "t2"),
            _tool_result("t2"),
        )
        ctx = self._ctx()
        ctx["phase_tool_mapping"] = {
            "investigation": {
                "signals": ["tool_call.search"],
                "position": "before_implementation",
            },
            "implementation": {
                "signals": ["tool_call.file_write"],
                "position": "any",
            },
        }
        ctx["phase_classification"] = {
            "ordered": ["investigation", "implementation"],
            "floating": [],
        }
        result = resolve_metric("phase.execution_order", trace, ctx)
        assert result == ["investigation", "implementation"]

    def test_phase_activation_timeline_requires_mapping(self):
        trace = _simple_trace(_user_prompt("task"))
        with pytest.raises(MetricNotResolvable, match="requires phase_tool_mapping"):
            resolve_metric("phase.activation_timeline", trace, self._ctx())

    def test_phase_activation_timeline_collapses_consecutive_duplicates(self):
        # Three consecutive writes (all impl) followed by a bash test should
        # collapse to ["implementation", "verification"].
        trace = _simple_trace(
            _user_prompt("task"),
            _write("a.py", "t1"),
            _tool_result("t1"),
            _write("b.py", "t2"),
            _tool_result("t2"),
            _write("c.py", "t3"),
            _tool_result("t3"),
            _bash("pytest", "t4"),
            _tool_result("t4"),
        )
        ctx = self._ctx()
        ctx["phase_tool_mapping"] = {
            "implementation": {
                "signals": ["tool_call.file_write"],
                "position": "any",
            },
            "verification": {
                "signals": ["tool_call.execute_command"],
                "position": "after_implementation",
            },
        }
        ctx["phase_classification"] = {
            "ordered": ["implementation", "verification"],
            "floating": [],
        }
        result = resolve_metric("phase.activation_timeline", trace, ctx)
        assert result == ["implementation", "verification"]

    def test_phase_activation_timeline_filters_floating_phase(self):
        # A floating phase that shares event indices with an ordered phase
        # should be tie-broken away.
        trace = _simple_trace(
            _user_prompt("task"),
            _write("a.py", "t1"),
            _tool_result("t1"),
            _bash("pytest", "t2"),
            _tool_result("t2"),
        )
        ctx = self._ctx()
        ctx["phase_tool_mapping"] = {
            "implementation": {
                "signals": ["tool_call.file_write"],
                "position": "any",
            },
            "verification": {
                "signals": ["tool_call.execute_command"],
                "position": "after_implementation",
            },
            "failure_recovery": {
                "signals": ["tool_call.file_write", "tool_call.execute_command"],
                "position": "any",
            },
        }
        ctx["phase_classification"] = {
            "ordered": ["implementation", "verification"],
            "floating": ["failure_recovery"],
        }
        result = resolve_metric("phase.activation_timeline", trace, ctx)
        # Both indices are tie-broken to the ordered phase
        assert "failure_recovery" not in result

    def test_phase_cycle_count(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _write("foo.py", "t1"),
            _tool_result("t1"),
            _bash("pytest", "t2"),
            _tool_result("t2"),
            _write("foo.py", "t3"),
            _tool_result("t3"),
            _bash("pytest", "t4"),
            _tool_result("t4"),
        )
        ctx = self._ctx()
        ctx["phase_tool_mapping"] = {
            "implementation": {
                "signals": ["tool_call.file_write"],
                "position": "any",
            },
            "verification": {
                "signals": ["tool_call.execute_command"],
                "position": "after_implementation",
            },
        }
        ctx["phase_classification"] = {"ordered": ["implementation", "verification"], "floating": []}
        result = resolve_metric("phase.cycle_count", trace, ctx)
        assert result == 2

    def test_diff_files_changed_from_trace(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _write("foo.py", "t1"),
            _tool_result("t1"),
        )
        # Without workspace_state, falls back to trace Write/Edit paths
        result = resolve_metric("diff.files_changed", trace, self._ctx())
        assert result == ["foo.py"]

    def test_diff_files_changed_from_workspace_state(self):
        trace = _simple_trace(_user_prompt("task"))
        ctx = self._ctx()
        ctx["workspace_state"] = {"modified_files": ["a.py", "b.py"]}
        result = resolve_metric("diff.files_changed", trace, ctx)
        assert result == ["a.py", "b.py"]

    def test_git_committed_files_from_trace(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("git add foo.py bar.py", "t1"),
            _tool_result("t1"),
            _bash("git commit -m 'fix'", "t2"),
            _tool_result("t2"),
        )
        result = resolve_metric("git.committed_files", trace, self._ctx())
        assert set(result) == {"foo.py", "bar.py"}

    def test_workspace_untracked_paths(self):
        trace = _simple_trace(_user_prompt("task"))
        ctx = self._ctx()
        ctx["workspace_state"] = {"git_status": "?? tmp_file.txt\n?? scratch/\nM  foo.py\n"}
        result = resolve_metric("workspace.git_status.untracked_paths", trace, ctx)
        assert set(result) == {"tmp_file.txt", "scratch/"}

    def test_workspace_untracked_empty_without_state(self):
        trace = _simple_trace(_user_prompt("task"))
        result = resolve_metric("workspace.git_status.untracked_paths", trace, self._ctx())
        assert result == []

    def test_unknown_metric_raises(self):
        trace = _simple_trace(_user_prompt("task"))
        with pytest.raises(MetricNotResolvable, match="Unknown metric"):
            resolve_metric("totally.unknown", trace, self._ctx())


# ===========================================================================
# _path_index_map
# ===========================================================================

class TestPathIndexMap:
    def test_basic(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _read("foo.py", "t1"),
            _tool_result("t1"),
            _read("bar.py", "t2"),
            _tool_result("t2"),
        )
        m = _path_index_map(trace, "Read")
        assert "foo.py" in m
        assert "bar.py" in m
        assert len(m["foo.py"]) == 1

    def test_multiple_reads_same_path(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _read("foo.py", "t1"),
            _tool_result("t1"),
            _read("foo.py", "t2"),
            _tool_result("t2"),
        )
        m = _path_index_map(trace, "Read")
        assert len(m["foo.py"]) == 2

    def test_no_file_path_key_skipped(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("ls", "t1"),
            _tool_result("t1"),
        )
        m = _path_index_map(trace, "Bash")
        assert m == {}


# ===========================================================================
# evaluate_condition
# ===========================================================================

class TestEvaluateCondition:
    def _ctx(self, **variables):
        return {"conditions": {}, "variables": variables}

    # -- eq / count transform -----------------------------------------------
    def test_count_eq_zero_passes_when_no_writes(self):
        trace = _simple_trace(_user_prompt("task"))
        cond = {
            "metric": "tool_call.file_write",
            "transform": "count",
            "operator": "eq",
            "target": 0,
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is True

    def test_count_eq_zero_fails_when_writes_present(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _write("foo.py", "t1"),
            _tool_result("t1"),
        )
        cond = {
            "metric": "tool_call.file_write",
            "transform": "count",
            "operator": "eq",
            "target": 0,
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is False

    # -- gt / count transform -----------------------------------------------
    def test_count_gt_one_batch(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("ls", "t1"),
            _tool_result("t1"),
            _bash("pwd", "t2"),
            _tool_result("t2"),
        )
        cond = {
            "metric": "trace.tool_call_batches",
            "transform": "count",
            "operator": "gt",
            "target": 1,
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is True

    def test_count_gt_one_batch_fails_with_single(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("ls", "t1"),
            _tool_result("t1"),
        )
        cond = {
            "metric": "trace.tool_call_batches",
            "transform": "count",
            "operator": "gt",
            "target": 1,
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is False

    # -- lte ----------------------------------------------------------------
    def test_lte_passes(self):
        trace = _simple_trace(_user_prompt("task"))
        # Zero batches <= 3
        cond = {
            "metric": "trace.tool_call_batches",
            "transform": "count",
            "operator": "lte",
            "target": 3,
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is True

    # -- exists_before -------------------------------------------------------
    def test_exists_before_passes(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _grep("find something", {}, "t1"),
            _tool_result("t1"),
            _write("foo.py", "t2"),
            _tool_result("t2"),
        )
        cond = {
            "metric": "tool_call.search",
            "operator": "exists_before",
            "target": "tool_call.file_write",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is True

    def test_exists_before_fails_when_write_first(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _write("foo.py", "t1"),
            _tool_result("t1"),
            _grep("find", {}, "t2"),
            _tool_result("t2"),
        )
        cond = {
            "metric": "tool_call.search",
            "operator": "exists_before",
            "target": "tool_call.file_write",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is False

    def test_exists_before_vacuous_when_no_writes(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _grep("find", {}, "t1"),
            _tool_result("t1"),
        )
        cond = {
            "metric": "tool_call.search",
            "operator": "exists_before",
            "target": "tool_call.file_write",
        }
        # No writes → vacuously true
        assert evaluate_condition(cond, trace, self._ctx())[0] is True

    # -- exists_after --------------------------------------------------------
    def test_exists_after_passes(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _write("foo.py", "t1"),
            _tool_result("t1"),
            _read("foo.py", "t2"),
            _tool_result("t2"),
        )
        cond = {
            "metric": "tool_call.file_read",
            "operator": "exists_after",
            "target": "tool_call.file_write",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is True

    def test_exists_after_fails_when_read_before_write(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _read("foo.py", "t1"),
            _tool_result("t1"),
            _write("foo.py", "t2"),
            _tool_result("t2"),
        )
        cond = {
            "metric": "tool_call.file_read",
            "operator": "exists_after",
            "target": "tool_call.file_write",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is False

    # -- exists_between ------------------------------------------------------
    def test_exists_between_passes(self):
        # text response between a tool result and a next tool call.
        # A user event separates the text from the next tool call to
        # prevent merge from combining them into one assistant event.
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("ls", "t1"),
            _tool_result("t1"),
            _assistant_text("I found..."),
            _user_prompt("continue"),
            _bash("pwd", "t2"),
            _tool_result("t2"),
        )
        cond = {
            "metric": "trace.text_response",
            "operator": "exists_between",
            "target": ["tool_call.result", "tool_call.next"],
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is True

    def test_exists_between_fails_with_no_text(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("ls", "t1"),
            _tool_result("t1"),
            _bash("pwd", "t2"),
            _tool_result("t2"),
        )
        cond = {
            "metric": "trace.text_response",
            "operator": "exists_between",
            "target": ["tool_call.result", "tool_call.next"],
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is False

    def test_exists_between_target_before_after(self):
        """target_before / target_after are resolved as the (start, end) tuple."""
        trace = _simple_trace(
            _user_prompt("task"),
            _read("test.spec.js", "r1"),        # file_read (start)
            _tool_result("r1"),
            _bash("npm test", "b1"),             # execute_command (metric)
            _tool_result("b1"),
            _write("src/app.js", "w1"),          # file_write (end)
            _tool_result("w1"),
        )
        cond = {
            "metric": "tool_call.execute_command",
            "operator": "exists_between",
            "target_before": "tool_call.file_read",
            "target_after": "tool_call.file_write",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is True

    def test_exists_between_target_before_after_fails(self):
        """exists_between fails when metric is not between the two targets."""
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("npm test", "b1"),             # execute_command (metric) — before start
            _tool_result("b1"),
            _read("test.spec.js", "r1"),         # file_read (start)
            _tool_result("r1"),
            _write("src/app.js", "w1"),          # file_write (end)
            _tool_result("w1"),
        )
        cond = {
            "metric": "tool_call.execute_command",
            "operator": "exists_between",
            "target_before": "tool_call.file_read",
            "target_after": "tool_call.file_write",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is False

    def test_tdd_prove_tests_fail_proper_structure(self):
        """Test TDD check with properly structured test and implementation files."""
        trace = _simple_trace(
            _user_prompt("implement feature"),
            # Test files in tests/ directory (correct structure)
            _write("tests/unit_test.rs", "t1"),
            _tool_result("t1"),
            # Run tests before impl
            _bash("cargo test", "t2"),
            _tool_result("t2"),
            # Implementation files
            _write("src/lib.rs", "t3"),
            _tool_result("t3"),
        )

        cond = {
            "metric": "tool_call.execute_command",
            "metric_args": "cargo test",
            "operator": "exists_between",
            "target_before": "tool_call.file_modify",
            "target_before_args": "tests/",
            "target_after": "tool_call.file_modify",
            "target_after_args": "src/",
        }

        # cargo test (event 3) is between tests/ (event 1) and src/ (event 5)
        passed, detail = evaluate_condition(cond, trace, self._ctx())
        assert passed is True

    def test_tdd_content_based_same_event_parallel_writes(self):
        """Bug from audit item 6: parallel writes in same event had same event_index.

        When the agent writes a test file AND an impl file in the same assistant
        turn (parallel tool calls), they share an event_index. With the old code
        this caused exists_between to get lo=hi (same index in both start and end
        lists), returning False even when cargo test ran between turns.

        The fix uses call_index (per-tool-call granularity) so parallel writes
        get distinct indices. This test reproduces the exact audit scenario:
        exists_between [[134], [134]] → window collapses.
        """
        # Turn 1: parallel writes — test file + impl file in SAME assistant message
        parallel_write = _assistant_parallel([
            ("Write", {"file_path": "tests/unit_test.rs",
                       "content": '#[cfg(test)]\nmod tests {\n    #[test]\n    fn it_works() { assert!(false); }\n}'},
             "t1"),
            ("Write", {"file_path": "src/config.rs",
                       "content": 'pub fn validate() -> bool { true }'},
             "t2"),
        ])
        # Turn 2: run cargo test
        cargo_test = _assistant_tool_use("Bash", {"command": "cargo test"}, "t3")
        # Turn 3: fix implementation
        impl_write = _assistant_tool_use(
            "Write",
            {"file_path": "src/lib.rs", "content": 'pub fn main() {}'},
            "t4",
        )
        trace = _simple_trace(
            _user_prompt("fix the config validation bug"),
            parallel_write,
            _tool_result("t1"),
            _tool_result("t2"),
            cargo_test,
            _tool_result("t3"),
            impl_write,
            _tool_result("t4"),
        )

        # Content-based condition (matches the actual bivvy.yml check)
        cond = {
            "metric": "tool_call.execute_command",
            "metric_args": "cargo test",
            "operator": "exists_between",
            "target_before": "tool_call.file_modify_with_test_content",
            "target_after": "tool_call.file_modify_without_test_content",
        }

        passed, detail = evaluate_condition(cond, trace, self._ctx())
        # With call_index fix: test write (call 0), impl write (call 1),
        # cargo test (call 2), later impl (call 3).
        # Window: lo=0 (test write), hi=max(1,3)=3 (impl writes).
        # cargo test call_index=2 is strictly between 0 and 3 → passes.
        assert passed is True, f"Should pass with call_index fix, got detail: {detail}"

    def test_tdd_content_based_separate_events_cross_file(self):
        """Standard cross-file TDD pattern: test file → cargo test → impl file."""
        trace = _simple_trace(
            _user_prompt("implement feature"),
            # Write test file with #[test] content
            _assistant_tool_use(
                "Write",
                {"file_path": "tests/integration.rs",
                 "content": '#[test]\nfn test_feature() { assert!(false); }'},
                "t1",
            ),
            _tool_result("t1"),
            # Run tests
            _assistant_tool_use("Bash", {"command": "cargo test"}, "t2"),
            _tool_result("t2"),
            # Write implementation (no test markers)
            _assistant_tool_use(
                "Write",
                {"file_path": "src/lib.rs",
                 "content": 'pub fn feature() -> bool { true }'},
                "t3",
            ),
            _tool_result("t3"),
        )

        cond = {
            "metric": "tool_call.execute_command",
            "metric_args": "cargo test",
            "operator": "exists_between",
            "target_before": "tool_call.file_modify_with_test_content",
            "target_after": "tool_call.file_modify_without_test_content",
        }

        passed, detail = evaluate_condition(cond, trace, self._ctx())
        assert passed is True, f"Cross-file TDD should pass, got detail: {detail}"

    def test_tdd_content_based_inline_test_separate_edits(self):
        """Inline Rust tests: Edit adds #[test] to src/ file, then separate Edit adds impl."""
        trace = _simple_trace(
            _user_prompt("implement feature with TDD"),
            # Edit src/lib.rs to add test (contains #[test])
            _assistant_tool_use(
                "Edit",
                {"file_path": "src/lib.rs",
                 "new_string": '#[cfg(test)]\nmod tests {\n    #[test]\n    fn test_new_feature() {\n        assert!(false);\n    }\n}'},
                "t1",
            ),
            _tool_result("t1"),
            # Run cargo test → should fail
            _assistant_tool_use("Bash", {"command": "cargo test"}, "t2"),
            _tool_result("t2"),
            # Edit src/lib.rs to add implementation (no test markers in this edit)
            _assistant_tool_use(
                "Edit",
                {"file_path": "src/lib.rs",
                 "new_string": 'pub fn new_feature() -> bool { true }'},
                "t3",
            ),
            _tool_result("t3"),
        )

        cond = {
            "metric": "tool_call.execute_command",
            "metric_args": "cargo test",
            "operator": "exists_between",
            "target_before": "tool_call.file_modify_with_test_content",
            "target_after": "tool_call.file_modify_without_test_content",
        }

        passed, detail = evaluate_condition(cond, trace, self._ctx())
        assert passed is True, f"Inline TDD with separate edits should pass, got detail: {detail}"

    def test_tdd_content_based_no_test_run_fails(self):
        """No cargo test between test write and impl write → should fail."""
        trace = _simple_trace(
            _user_prompt("implement feature"),
            _assistant_tool_use(
                "Write",
                {"file_path": "tests/test.rs",
                 "content": '#[test]\nfn test_it() {}'},
                "t1",
            ),
            _tool_result("t1"),
            # Directly write implementation without running tests
            _assistant_tool_use(
                "Write",
                {"file_path": "src/lib.rs",
                 "content": 'pub fn it() {}'},
                "t2",
            ),
            _tool_result("t2"),
        )

        cond = {
            "metric": "tool_call.execute_command",
            "metric_args": "cargo test",
            "operator": "exists_between",
            "target_before": "tool_call.file_modify_with_test_content",
            "target_after": "tool_call.file_modify_without_test_content",
        }

        passed, detail = evaluate_condition(cond, trace, self._ctx())
        assert passed is False, "No cargo test between writes should fail"

    def test_tdd_content_result_structuredpatch_detection(self):
        """Test that structuredPatch in tool_use_result detects test content.

        When an Edit's new_string doesn't contain #[test] but the result's
        structuredPatch shows added lines with test declarations, the call
        should be classified as a test-content write.
        """
        # Edit with plain new_string, but result contains structuredPatch with #[test]
        trace = _simple_trace(
            _user_prompt("add tests"),
            _assistant_tool_use(
                "Edit",
                {"file_path": "src/lib.rs",
                 "new_string": 'fn test_helper() { assert!(true); }'},
                "t1",
            ),
            _tool_result("t1", content='structuredPatch: "+    #[test]"\n"+    fn test_helper() {"'),
            _assistant_tool_use("Bash", {"command": "cargo test"}, "t2"),
            _tool_result("t2"),
            _assistant_tool_use(
                "Edit",
                {"file_path": "src/lib.rs",
                 "new_string": 'pub fn real_impl() -> i32 { 42 }'},
                "t3",
            ),
            _tool_result("t3"),
        )

        cond = {
            "metric": "tool_call.execute_command",
            "metric_args": "cargo test",
            "operator": "exists_between",
            "target_before": "tool_call.file_modify_with_test_content",
            "target_after": "tool_call.file_modify_without_test_content",
        }

        passed, detail = evaluate_condition(cond, trace, self._ctx())
        assert passed is True, f"structuredPatch detection should find test content, got: {detail}"

    def test_tdd_tests_before_implementation_substring_no_false_positive(self):
        """Regression: paths containing 'test' as a substring (e.g. latest.json)
        must not satisfy the tdd_tests_before_implementation check.

        Mirrors the bivvy.yml condition shape: content-aware metrics on both
        sides, no path-substring filters. A non-test write whose path happens
        to contain the letters 't-e-s-t' should NOT count as a test edit.
        """
        trace = _simple_trace(
            _user_prompt("ship feature"),
            _assistant_tool_use(
                "Write",
                {"file_path": "config/latest.json", "content": '{"version": 2}'},
                "t1",
            ),
            _tool_result("t1"),
            _assistant_tool_use(
                "Write",
                {"file_path": "src/lib.rs", "content": 'pub fn run() {}'},
                "t2",
            ),
            _tool_result("t2"),
        )

        cond = {
            "metric": "tool_call.file_modify_with_test_content",
            "operator": "exists_before",
            "target": "tool_call.file_modify_without_test_content",
        }

        passed, detail = evaluate_condition(cond, trace, self._ctx())
        assert passed is False, (
            f"latest.json must not be classified as a test edit; got: {detail}"
        )

    def test_tdd_tests_before_implementation_genuine_test_passes(self):
        """Companion to the false-positive regression: a real #[test] write
        before an impl-only src edit should satisfy the check."""
        trace = _simple_trace(
            _user_prompt("ship feature"),
            _assistant_tool_use(
                "Write",
                {"file_path": "src/lib.rs",
                 "content": '#[cfg(test)]\nmod tests {\n    #[test]\n    fn it_works() { assert!(false); }\n}'},
                "t1",
            ),
            _tool_result("t1"),
            _assistant_tool_use(
                "Edit",
                {"file_path": "src/lib.rs", "new_string": 'pub fn run() {}'},
                "t2",
            ),
            _tool_result("t2"),
        )

        cond = {
            "metric": "tool_call.file_modify_with_test_content",
            "operator": "exists_before",
            "target": "tool_call.file_modify_without_test_content",
        }

        passed, detail = evaluate_condition(cond, trace, self._ctx())
        assert passed is True, (
            f"Genuine #[test] write before impl edit should pass; got: {detail}"
        )

    def test_call_index_distinct_within_parallel_batch(self):
        """Verify that parallel tool calls get distinct call_index values."""
        trace = _simple_trace(
            _user_prompt("task"),
            _assistant_parallel([
                ("Write", {"file_path": "a.rs"}, "t1"),
                ("Write", {"file_path": "b.rs"}, "t2"),
                ("Bash", {"command": "cargo test"}, "t3"),
            ]),
            _tool_result("t1"),
            _tool_result("t2"),
            _tool_result("t3"),
        )

        calls = trace.all_tool_calls()
        # All three should share the same event_index (same assistant message)
        assert calls[0].event_index == calls[1].event_index == calls[2].event_index
        # But have distinct call_index values
        assert calls[0].call_index != calls[1].call_index
        assert calls[1].call_index != calls[2].call_index
        assert calls[0].call_index < calls[1].call_index < calls[2].call_index

    # -- strictly_precedes ---------------------------------------------------
    def test_strictly_precedes_passes(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _ask("clarify?", "t1"),
            _tool_result("t1"),
            _write("foo.py", "t2"),
            _tool_result("t2"),
        )
        cond = {
            "metric": "tool_call.ask_user",
            "operator": "strictly_precedes",
            "target": "tool_call.file_write",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is True

    def test_strictly_precedes_fails_when_interleaved(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _write("foo.py", "t1"),
            _tool_result("t1"),
            _ask("clarify?", "t2"),
            _tool_result("t2"),
            _write("bar.py", "t3"),
            _tool_result("t3"),
        )
        cond = {
            "metric": "tool_call.ask_user",
            "operator": "strictly_precedes",
            "target": "tool_call.file_write",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is False

    # -- regex_not_match -----------------------------------------------------
    def test_regex_not_match_no_md_files(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _write("foo.py", "t1"),
            _tool_result("t1"),
        )
        cond = {
            "metric": "tool_call.file_create",
            "operator": "regex_not_match",
            "target": r".*\.md$",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is True

    def test_regex_not_match_md_file_fails(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _write("README.md", "t1"),
            _tool_result("t1"),
        )
        cond = {
            "metric": "tool_call.file_create",
            "operator": "regex_not_match",
            "target": r".*\.md$",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is False

    # -- has_key -------------------------------------------------------------
    def test_has_key_type_in_search_args(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _grep("foo", {"type": "py"}, "t1"),
            _tool_result("t1"),
        )
        cond = {
            "metric": "tool_call.search.args",
            "operator": "has_key",
            "target": "type",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is True

    def test_has_key_type_missing(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _grep("foo", {}, "t1"),
            _tool_result("t1"),
        )
        cond = {
            "metric": "tool_call.search.args",
            "operator": "has_key",
            "target": "type",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is False

    # -- contains ------------------------------------------------------------
    def test_contains_skill_in_skills(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _skill("docs", "t1"),
            _tool_result("t1"),
        )
        cond = {
            "metric": "tool_call.skill",
            "operator": "contains",
            "target": "docs",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is True

    def test_contains_skill_missing(self):
        trace = _simple_trace(_user_prompt("task"))
        cond = {
            "metric": "tool_call.skill",
            "operator": "contains",
            "target": "docs",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is False

    # -- not_contains --------------------------------------------------------
    def test_not_contains_passes(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("git commit -m 'fix'", "t1"),
            _tool_result("t1"),
        )
        cond = {
            "metric": "tool_call.execute_command",
            "operator": "not_contains",
            "target": "docs",  # literal string, not a metric
        }
        # execute_command returns list of event indices, not file paths
        # not_contains checks if target is not in that list; "docs" is not an int
        assert evaluate_condition(cond, trace, self._ctx())[0] is True

    # -- first_search_broader_than_final ------------------------------------
    def test_first_broader_passes(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _grep("err", {}, "t1"),
            _tool_result("t1"),
            _grep("error handling module", {}, "t2"),
            _tool_result("t2"),
        )
        cond = {
            "metric": "tool_call.search.ordered_calls",
            "operator": "first_search_broader_than_final",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is True

    def test_first_narrower_fails(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _grep("very specific long pattern", {}, "t1"),
            _tool_result("t1"),
            _grep("short", {}, "t2"),
            _tool_result("t2"),
        )
        cond = {
            "metric": "tool_call.search.ordered_calls",
            "operator": "first_search_broader_than_final",
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is False

    # -- contains_count_gte -------------------------------------------------
    def test_contains_count_gte_passes(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _assistant_parallel([
                ("Grep", {"pattern": "foo"}, "t1"),
                ("Grep", {"pattern": "bar"}, "t2"),
                ("Read", {"file_path": "x.py"}, "t3"),
            ]),
            _tool_result("t1"),
            _tool_result("t2"),
            _tool_result("t3"),
        )
        cond = {
            "metric": "execution.parallel_batch",
            "operator": "contains_count_gte",
            "target": {"metric": "tool_call.search", "count": 2},
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is True

    def test_contains_count_gte_fails_insufficient(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _assistant_parallel([
                ("Grep", {"pattern": "foo"}, "t1"),
                ("Read", {"file_path": "x.py"}, "t2"),
            ]),
            _tool_result("t1"),
            _tool_result("t2"),
        )
        cond = {
            "metric": "execution.parallel_batch",
            "operator": "contains_count_gte",
            "target": {"metric": "tool_call.search", "count": 2},
        }
        assert evaluate_condition(cond, trace, self._ctx())[0] is False

    # -- variable interpolation in target -----------------------------------
    def test_target_variable_interpolation(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _grep("foo", {}, "t1"),
            _tool_result("t1"),
            _write("foo.py", "t2"),
            _tool_result("t2"),
        )
        # With a variable that references a literal (not a metric), confirm
        # the interpolation path works
        ctx = {"conditions": {}, "variables": {"file_path": "foo.py"}}
        cond = {
            "metric": "tool_call.search",
            "operator": "exists_before",
            "target": "tool_call.file_write",
        }
        assert evaluate_condition(cond, trace, ctx)[0] is True

    # -- unresolvable metric raises -----------------------------------------
    def test_unresolvable_metric_raises(self):
        trace = _simple_trace(_user_prompt("task"))
        cond = {
            "metric": "phase.execution_order",
            "operator": "strictly_ordered_subset",
            "target": ["intake", "commits"],
        }
        # Without phase_tool_mapping, raises MetricNotResolvable
        with pytest.raises(MetricNotResolvable, match="requires phase_tool_mapping"):
            evaluate_condition(cond, trace, {"conditions": {}, "variables": {}})


# ===========================================================================
# evaluate_check
# ===========================================================================

class TestEvaluateCheck:
    def _ctx(self, conditions=None, variables=None):
        return {
            "conditions": conditions or {},
            "variables": variables or {},
        }

    def test_basic_pass(self):
        trace = _simple_trace(_user_prompt("task"))
        check = {
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
        result = evaluate_check(check, trace, self._ctx())
        assert result.check_id == "no_writes"
        assert result.passed is True
        assert result.skip_reason is None

    def test_basic_fail(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _write("foo.py", "t1"),
            _tool_result("t1"),
        )
        check = {
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
        result = evaluate_check(check, trace, self._ctx())
        assert result.passed is False

    def test_prompt_condition_skips_when_false(self):
        trace = _simple_trace(_user_prompt("task"))
        check = {
            "id": "cond_check",
            "phase": "intake",
            "description": "Only for informational",
            "type": "gate",
            "prompt_condition": "is_informational",
            "condition": {
                "metric": "tool_call.file_write",
                "transform": "count",
                "operator": "eq",
                "target": 0,
            },
        }
        ctx = self._ctx(conditions={"is_informational": False})
        result = evaluate_check(check, trace, ctx)
        assert result.passed is None
        assert result.skip_reason is not None

    def test_prompt_condition_runs_when_true(self):
        trace = _simple_trace(_user_prompt("task"))
        check = {
            "id": "cond_check",
            "phase": "intake",
            "description": "Only for informational",
            "type": "gate",
            "prompt_condition": "is_informational",
            "condition": {
                "metric": "tool_call.file_write",
                "transform": "count",
                "operator": "eq",
                "target": 0,
            },
        }
        ctx = self._ctx(conditions={"is_informational": True})
        result = evaluate_check(check, trace, ctx)
        assert result.passed is True

    def test_negated_prompt_condition(self):
        # !explicit_feature_requested: skip when feature WAS requested
        trace = _simple_trace(_user_prompt("task"))
        check = {
            "id": "no_create",
            "phase": "implementation",
            "description": "No unprompted file creation",
            "type": "constraint",
            "prompt_condition": "!explicit_feature_requested",
            "condition": {
                "metric": "tool_call.file_create",
                "transform": "count",
                "operator": "eq",
                "target": 0,
            },
        }
        # Feature was requested → skip
        ctx = self._ctx(conditions={"explicit_feature_requested": True})
        result = evaluate_check(check, trace, ctx)
        assert result.passed is None

        # Feature not requested → run
        ctx = self._ctx(conditions={"explicit_feature_requested": False})
        result = evaluate_check(check, trace, ctx)
        assert result.passed is True

    def test_unresolvable_metric_fails(self):
        """Phase metric without phase_tool_mapping in context fails (not skips).

        MetricNotResolvable means the data the check needs is missing from
        the trace — the agent didn't do the thing the workflow required.
        """
        trace = _simple_trace(_user_prompt("task"))
        check = {
            "id": "phase_order",
            "phase": "workflow",
            "description": "Phase ordering",
            "type": "workflow_order",
            "condition": {
                "metric": "phase.execution_order",
                "operator": "strictly_ordered_subset",
                "target": ["intake", "commits"],
            },
        }
        result = evaluate_check(check, trace, self._ctx())
        assert result.passed is False
        assert result.skip_reason is None
        assert "requires phase_tool_mapping" in result.detail

    def test_phase_ordering_check_passes_with_context(self):
        """Phase ordering check evaluates when phase config is in context."""
        trace = _simple_trace(
            _user_prompt("task"),
            _grep("foo", {}, "t1"),
            _tool_result("t1"),
            _write("bar.py", "t2"),
            _tool_result("t2"),
        )
        check = {
            "id": "phase_order",
            "phase": "workflow",
            "description": "Phase ordering",
            "type": "workflow_order",
            "condition": {
                "metric": "phase.execution_order",
                "operator": "strictly_ordered_subset",
                "target": ["investigation", "implementation"],
            },
        }
        ctx = self._ctx()
        ctx["phase_tool_mapping"] = {
            "investigation": {"signals": ["tool_call.search"], "position": "before_implementation"},
            "implementation": {"signals": ["tool_call.file_write"], "position": "any"},
        }
        ctx["phase_classification"] = {"ordered": ["investigation", "implementation"], "floating": []}
        result = evaluate_check(check, trace, ctx)
        assert result.passed is True

    def test_strict_with_legal_redirects_empty_timeline_fails(self):
        """An empty trace -> empty timeline -> fails coverage."""
        trace = _simple_trace(_user_prompt("task"))
        check = {
            "id": "phase_ordering",
            "phase": "workflow",
            "description": "Strict ordering",
            "type": "workflow_order",
            "condition": {
                "metric": "phase.activation_timeline",
                "operator": "strict_with_legal_redirects",
                "target": ["investigation", "implementation"],
            },
        }
        ctx = self._ctx()
        ctx["phase_tool_mapping"] = {
            "investigation": {"signals": ["tool_call.search"], "position": "before_implementation"},
            "implementation": {"signals": ["tool_call.file_write"], "position": "any"},
        }
        ctx["phase_classification"] = {"ordered": ["investigation", "implementation"], "floating": []}
        result = evaluate_check(check, trace, ctx)
        assert result.passed is False
        assert result.operator == "strict_with_legal_redirects"
        assert "missing phases" in (result.detail or "")

    def test_strict_with_legal_redirects_full_pass(self):
        """All phases fire in canonical order -> passes."""
        trace = _simple_trace(
            _user_prompt("task"),
            _grep("foo", {}, "t1"),
            _tool_result("t1"),
            _write("bar.py", "t2"),
            _tool_result("t2"),
        )
        check = {
            "id": "phase_ordering",
            "phase": "workflow",
            "description": "Strict ordering",
            "type": "workflow_order",
            "condition": {
                "metric": "phase.activation_timeline",
                "operator": "strict_with_legal_redirects",
                "target": ["investigation", "implementation"],
            },
        }
        ctx = self._ctx()
        ctx["phase_tool_mapping"] = {
            "investigation": {
                "signals": ["tool_call.search"],
                "position": "before_implementation",
                "legal_redirect_targets": [],
            },
            "implementation": {
                "signals": ["tool_call.file_write"],
                "position": "any",
                "legal_redirect_targets": [],
            },
        }
        ctx["phase_classification"] = {"ordered": ["investigation", "implementation"], "floating": []}
        result = evaluate_check(check, trace, ctx)
        assert result.passed is True
        assert result.operator == "strict_with_legal_redirects"
        assert result.eval_trace is not None

    def test_strict_with_legal_redirects_eval_trace_records_rules(self):
        """eval_trace records per-rule verdicts including the activation timeline."""
        trace = _simple_trace(
            _user_prompt("task"),
            _write("bar.py", "t1"),
            _tool_result("t1"),
        )
        # impl present but investigation missing -> coverage failure
        check = {
            "id": "phase_ordering",
            "phase": "workflow",
            "description": "Strict ordering",
            "type": "workflow_order",
            "condition": {
                "metric": "phase.activation_timeline",
                "operator": "strict_with_legal_redirects",
                "target": ["investigation", "implementation"],
            },
        }
        ctx = self._ctx()
        ctx["phase_tool_mapping"] = {
            "investigation": {
                "signals": ["tool_call.search"],
                "position": "before_implementation",
                "legal_redirect_targets": [],
            },
            "implementation": {
                "signals": ["tool_call.file_write"],
                "position": "any",
                "legal_redirect_targets": [],
            },
        }
        ctx["phase_classification"] = {"ordered": ["investigation", "implementation"], "floating": []}
        result = evaluate_check(check, trace, ctx)
        assert result.passed is False
        # Find the audit entry that captures rule verdicts
        rule_entries = [
            e for e in result.eval_trace
            if e.get("function") == "_evaluate_strict_with_legal_redirects"
            and e.get("action") == "rules_evaluated"
        ]
        assert len(rule_entries) == 1
        entry = rule_entries[0]
        assert "coverage_passed" in entry
        assert "first_occurrence_passed" in entry
        assert "transitions_passed" in entry
        assert "filtered_timeline" in entry
        assert entry["coverage_passed"] is False
        assert "investigation" in entry["coverage_missing"]

    def test_precedes_per_path_pass(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _read("foo.py", "t1"),
            _tool_result("t1"),
            _write("foo.py", "t2"),
            _tool_result("t2"),
        )
        check = {
            "id": "prefer_edit",
            "phase": "implementation",
            "description": "Read before write per path",
            "type": "workflow_order",
            "condition": {
                "metric": "tool_call.file_read",
                "operator": "precedes_per_path",
                "target": "tool_call.file_create",
            },
        }
        result = evaluate_check(check, trace, self._ctx())
        assert result.passed is True

    def test_precedes_per_path_fail(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _write("foo.py", "t1"),
            _tool_result("t1"),
        )
        check = {
            "id": "prefer_edit",
            "phase": "implementation",
            "description": "Read before write per path",
            "type": "workflow_order",
            "condition": {
                "metric": "tool_call.file_read",
                "operator": "precedes_per_path",
                "target": "tool_call.file_create",
            },
        }
        result = evaluate_check(check, trace, self._ctx())
        assert result.passed is False



# ===========================================================================
# evaluate (top-level)
# ===========================================================================

class TestEvaluate:
    def _ctx(self, conditions=None, variables=None):
        return {
            "conditions": conditions or {},
            "variables": variables or {},
        }

    def test_empty_checks(self):
        trace = _simple_trace(_user_prompt("task"))
        results = evaluate(trace, [], self._ctx())
        assert results == []

    def test_single_check_pass(self):
        trace = _simple_trace(_user_prompt("task"))
        checks = [{
            "id": "c1",
            "phase": "p",
            "description": "d",
            "type": "constraint",
            "condition": {
                "metric": "tool_call.file_write",
                "transform": "count",
                "operator": "eq",
                "target": 0,
            },
        }]
        results = evaluate(trace, checks, self._ctx())
        assert len(results) == 1
        assert results[0].passed is True

    def test_order_preserved(self):
        trace = _simple_trace(_user_prompt("task"))
        checks = [
            {
                "id": f"c{i}",
                "phase": "p",
                "description": "d",
                "type": "constraint",
                "condition": {
                    "metric": "tool_call.file_write",
                    "transform": "count",
                    "operator": "eq",
                    "target": 0,
                },
            }
            for i in range(5)
        ]
        results = evaluate(trace, checks, self._ctx())
        assert [r.check_id for r in results] == ["c0", "c1", "c2", "c3", "c4"]

    def test_mixed_pass_fail_skip(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _write("foo.py", "t1"),
            _tool_result("t1"),
        )
        checks = [
            {
                "id": "passes",
                "phase": "p",
                "description": "d",
                "type": "constraint",
                "condition": {
                    "metric": "tool_call.file_write",
                    "transform": "count",
                    "operator": "gt",
                    "target": 0,
                },
            },
            {
                "id": "fails",
                "phase": "p",
                "description": "d",
                "type": "constraint",
                "condition": {
                    "metric": "tool_call.file_write",
                    "transform": "count",
                    "operator": "eq",
                    "target": 0,
                },
            },
            {
                "id": "skipped",
                "phase": "p",
                "description": "d",
                "type": "constraint",
                # Use prompt_condition for a legitimate skip — this is the
                # only mechanism that should produce skips.
                "prompt_condition": "nonexistent_flag",
                "condition": {
                    "metric": "tool_call.file_write",
                    "transform": "count",
                    "operator": "eq",
                    "target": 0,
                },
            },
        ]
        results = evaluate(trace, checks, self._ctx())
        by_id = {r.check_id: r for r in results}
        assert by_id["passes"].passed is True
        assert by_id["fails"].passed is False
        assert by_id["skipped"].passed is None

    def test_result_fields_populated(self):
        trace = _simple_trace(_user_prompt("task"))
        checks = [{
            "id": "my_check",
            "phase": "implementation",
            "description": "Test description",
            "type": "constraint",
            "condition": {
                "metric": "tool_call.file_write",
                "transform": "count",
                "operator": "eq",
                "target": 0,
            },
        }]
        results = evaluate(trace, checks, self._ctx())
        r = results[0]
        assert r.check_id == "my_check"
        assert r.phase == "implementation"
        assert r.description == "Test description"
        assert r.passed is True
        assert r.skip_reason is None


# ===========================================================================
# _primary_arg
# ===========================================================================

class TestPrimaryArg:
    def test_bash_returns_command(self):
        tc = ToolCall(tool_use_id="t1", name="Bash", input={"command": "ls -la"}, event_index=0)
        assert _primary_arg(tc) == "ls -la"

    def test_grep_returns_pattern(self):
        tc = ToolCall(tool_use_id="t1", name="Grep", input={"pattern": "foo", "path": "src/"}, event_index=0)
        assert _primary_arg(tc) == "foo"

    def test_read_returns_file_path(self):
        tc = ToolCall(tool_use_id="t1", name="Read", input={"file_path": "/a/b.py"}, event_index=0)
        assert _primary_arg(tc) == "/a/b.py"

    def test_write_returns_file_path(self):
        tc = ToolCall(tool_use_id="t1", name="Write", input={"file_path": "/a/b.py", "content": "x"}, event_index=0)
        assert _primary_arg(tc) == "/a/b.py"

    def test_skill_returns_skill_name(self):
        tc = ToolCall(tool_use_id="t1", name="Skill", input={"skill": "docs"}, event_index=0)
        assert _primary_arg(tc) == "docs"

    def test_fallback_joins_string_values(self):
        tc = ToolCall(tool_use_id="t1", name="Custom", input={"a": "hello", "b": "world"}, event_index=0)
        assert _primary_arg(tc) == "hello world"

    def test_priority_order_command_first(self):
        # If input has both 'command' and 'file_path', command wins
        tc = ToolCall(tool_use_id="t1", name="X", input={"command": "run", "file_path": "f.py"}, event_index=0)
        assert _primary_arg(tc) == "run"

    def test_empty_input_returns_empty_string(self):
        tc = ToolCall(tool_use_id="t1", name="X", input={}, event_index=0)
        assert _primary_arg(tc) == ""

    def test_non_string_values_skipped_in_fallback(self):
        tc = ToolCall(tool_use_id="t1", name="X", input={"count": 5, "label": "ok"}, event_index=0)
        assert _primary_arg(tc) == "ok"


# ===========================================================================
# _tool_indices
# ===========================================================================

class TestToolIndices:
    def test_returns_indices_for_matching_tool(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _read("a.py", "t1"),
            _tool_result("t1"),
            _write("b.py", "t2"),
            _tool_result("t2"),
            _read("c.py", "t3"),
            _tool_result("t3"),
        )
        indices = _tool_indices(trace, "Read")
        assert len(indices) == 2

    def test_returns_empty_for_no_matching_tool(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _read("a.py", "t1"),
            _tool_result("t1"),
        )
        assert _tool_indices(trace, "Grep") == []

    def test_returns_empty_for_no_tool_calls(self):
        trace = _simple_trace(_user_prompt("task"))
        assert _tool_indices(trace, "Read") == []


# ===========================================================================
# _tool_indices_matching
# ===========================================================================

class TestToolIndicesMatching:
    def test_filters_by_command_substring(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("pytest tests/", "t1"),
            _tool_result("t1"),
            _bash("ls -la", "t2"),
            _tool_result("t2"),
            _bash("pytest -v", "t3"),
            _tool_result("t3"),
        )
        indices = _tool_indices_matching(trace, "Bash", "pytest")
        assert len(indices) == 2

    def test_no_match_returns_empty(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("ls", "t1"),
            _tool_result("t1"),
        )
        assert _tool_indices_matching(trace, "Bash", "pytest") == []

    def test_filters_by_file_path_substring(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _read("src/main.py", "t1"),
            _tool_result("t1"),
            _read("tests/test.py", "t2"),
            _tool_result("t2"),
        )
        indices = _tool_indices_matching(trace, "Read", "src/")
        assert len(indices) == 1

    def test_no_calls_of_tool_type(self):
        trace = _simple_trace(_user_prompt("task"))
        assert _tool_indices_matching(trace, "Bash", "pytest") == []


# ===========================================================================
# _tool_content
# ===========================================================================

class TestToolContent:
    def test_returns_file_paths_for_write(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _write("foo.py", "t1"),
            _tool_result("t1"),
            _write("bar.py", "t2"),
            _tool_result("t2"),
        )
        content = _tool_content(trace, "Write")
        assert content == ["foo.py", "bar.py"]

    def test_returns_commands_for_bash(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("ls", "t1"),
            _tool_result("t1"),
            _bash("pwd", "t2"),
            _tool_result("t2"),
        )
        content = _tool_content(trace, "Bash")
        assert content == ["ls", "pwd"]

    def test_returns_skill_names(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _skill("docs", "t1"),
            _tool_result("t1"),
        )
        content = _tool_content(trace, "Skill")
        assert content == ["docs"]

    def test_returns_patterns_for_grep(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _grep("error", {}, "t1"),
            _tool_result("t1"),
        )
        content = _tool_content(trace, "Grep")
        assert content == ["error"]

    def test_returns_empty_for_no_calls(self):
        trace = _simple_trace(_user_prompt("task"))
        assert _tool_content(trace, "Write") == []


# ===========================================================================
# resolve_target
# ===========================================================================

class TestResolveTarget:
    def _ctx(self, **variables):
        return {"conditions": {}, "variables": variables}

    def test_literal_string_target(self):
        trace = _simple_trace(_user_prompt("task"))
        result = resolve_target("CLAUDE.md", trace, self._ctx())
        assert result == "CLAUDE.md"

    def test_metric_name_target(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _write("foo.py", "t1"),
            _tool_result("t1"),
        )
        result = resolve_target("tool_call.file_write", trace, self._ctx())
        assert isinstance(result, list)
        assert len(result) == 1

    def test_variable_interpolation_in_target(self):
        trace = _simple_trace(_user_prompt("task"))
        result = resolve_target("${my_var}", trace, self._ctx(my_var="hello"))
        assert result == "hello"

    def test_list_target_for_exists_between(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("ls", "t1"),
            _tool_result("t1"),
        )
        result = resolve_target(
            ["tool_call.result", "tool_call.next"],
            trace,
            self._ctx(),
        )
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_dict_target_passthrough(self):
        trace = _simple_trace(_user_prompt("task"))
        target = {"metric": "tool_call.search", "count": 2}
        result = resolve_target(target, trace, self._ctx())
        assert result == {"metric": "tool_call.search", "count": 2}

    def test_numeric_target_passthrough(self):
        trace = _simple_trace(_user_prompt("task"))
        assert resolve_target(0, trace, self._ctx()) == 0
        assert resolve_target(3, trace, self._ctx()) == 3

    def test_target_args_filters_indices(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("pytest tests/", "t1"),
            _tool_result("t1"),
            _bash("ls -la", "t2"),
            _tool_result("t2"),
        )
        result = resolve_target(
            "tool_call.execute_command",
            trace,
            self._ctx(),
            target_args="pytest",
        )
        # Only the pytest call should survive filtering
        assert len(result) == 1

    def test_task_completed_target(self):
        trace = _simple_trace(_user_prompt("task"))
        result = resolve_target("task_completed", trace, self._ctx())
        assert result == [_TASK_COMPLETED]


# ===========================================================================
# _apply_operator
# ===========================================================================

class TestApplyOperator:
    def test_scalar_eq(self):
        assert _apply_operator("eq", 3, 3, 10, {}) is True
        assert _apply_operator("eq", 3, 4, 10, {}) is False

    def test_scalar_neq(self):
        assert _apply_operator("neq", 3, 4, 10, {}) is True

    def test_scalar_gt(self):
        assert _apply_operator("gt", 5, 3, 10, {}) is True

    def test_scalar_gte(self):
        assert _apply_operator("gte", 3, 3, 10, {}) is True

    def test_scalar_lt(self):
        assert _apply_operator("lt", 2, 3, 10, {}) is True

    def test_scalar_lte(self):
        assert _apply_operator("lte", 3, 3, 10, {}) is True

    def test_exists_before(self):
        assert _apply_operator("exists_before", [1], [5], 10, {}) is True

    def test_exists_after(self):
        assert _apply_operator("exists_after", [5], [1], 10, {}) is True

    def test_exists_between(self):
        assert _apply_operator("exists_between", [3], ([1], [5]), 10, {}) is True

    def test_strictly_precedes(self):
        assert _apply_operator("strictly_precedes", [1, 2], [5, 6], 10, {}) is True

    def test_strictly_ordered_subset(self):
        assert _apply_operator(
            "strictly_ordered_subset",
            ["intake", "commits"],
            ["intake", "investigation", "commits"],
            10, {},
        ) is True

    def test_strictly_ordered_subset_still_available_regression(self):
        """The legacy subset operator must remain registered and behave as
        documented even after strict_with_legal_redirects landed."""
        # Empty observed -> vacuously passes (subset semantics).
        assert _apply_operator(
            "strictly_ordered_subset", [],
            ["spec", "impl", "doc"], 10, {},
        ) is True
        # Sparse but in-order -> passes.
        assert _apply_operator(
            "strictly_ordered_subset",
            ["spec", "doc"],
            ["spec", "impl", "doc"], 10, {},
        ) is True
        # Out-of-order -> fails.
        assert _apply_operator(
            "strictly_ordered_subset",
            ["doc", "spec"],
            ["spec", "impl", "doc"], 10, {},
        ) is False

    def test_subset_of(self):
        assert _apply_operator("subset_of", [1, 2], [1, 2, 3], 10, {}) is True

    def test_not_contains(self):
        assert _apply_operator("not_contains", ["a", "b"], "c", 10, {}) is True

    def test_contains(self):
        assert _apply_operator("contains", ["a", "b"], "a", 10, {}) is True

    def test_regex_not_match(self):
        assert _apply_operator("regex_not_match", ["foo.py"], r"\.md$", 10, {}) is True

    def test_has_key(self):
        assert _apply_operator("has_key", {"type": "py"}, "type", 10, {}) is True

    def test_each_preceded_by_within_n_steps(self):
        assert _apply_operator(
            "each_preceded_by_within_N_steps", [5], [4], 2, {},
        ) is True

    def test_contains_count_gte(self):
        assert _apply_operator(
            "contains_count_gte",
            [["Grep", "Grep"]],
            {"metric": "tool_call.search", "count": 2},
            10, {},
        ) is True

    def test_first_search_broader_than_final(self):
        calls = [{"pattern": "err"}, {"pattern": "error handling"}]
        assert _apply_operator("first_search_broader_than_final", calls, None, 10, {}) is True

    def test_only_via_empty_a_passes(self):
        """No metric occurrences → vacuously true."""
        assert _apply_operator("only_via", [], [1, 2], 10, {}) is True

    def test_only_via_empty_both_passes(self):
        """No metric and no target → vacuously true."""
        assert _apply_operator("only_via", [], [], 10, {}) is True

    def test_only_via_nonempty_a_empty_b_fails(self):
        """Metric occurred but no dispatch → fail."""
        assert _apply_operator("only_via", [3, 5], [], 10, {}) is False

    def test_only_via_all_covered(self):
        """Every write is preceded by a dispatch."""
        assert _apply_operator("only_via", [2, 4], [1, 3], 10, {}) is True

    def test_only_via_first_not_covered(self):
        """First write at index 1, dispatch only at index 3 → uncovered."""
        assert _apply_operator("only_via", [1, 5], [3], 10, {}) is False

    def test_only_via_dispatch_at_same_index(self):
        """Dispatch at same index as write counts as covered."""
        assert _apply_operator("only_via", [2], [2], 10, {}) is True

    def test_precedes_per_path_raises(self):
        with pytest.raises(UnknownOperator, match="precedes_per_path"):
            _apply_operator("precedes_per_path", [1], [2], 10, {})

    def test_unknown_operator_raises(self):
        with pytest.raises(UnknownOperator, match="Unknown operator"):
            _apply_operator("nonexistent_op", 1, 2, 10, {})


# ===========================================================================
# _evaluate_precedes_per_path
# ===========================================================================

class TestEvaluatePrecedesPerPath:
    def _ctx(self):
        return {"conditions": {}, "variables": {}}

    def test_read_before_write_passes(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _read("foo.py", "t1"),
            _tool_result("t1"),
            _write("foo.py", "t2"),
            _tool_result("t2"),
        )
        check = {
            "id": "test_ppp",
            "phase": "implementation",
            "description": "Read before create",
            "type": "workflow_order",
            "condition": {
                "metric": "tool_call.file_read",
                "operator": "precedes_per_path",
                "target": "tool_call.file_create",
            },
        }
        result = _evaluate_precedes_per_path(check, trace, self._ctx())
        assert result.passed is True
        assert result.check_id == "test_ppp"

    def test_write_without_read_fails(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _write("new.py", "t1"),
            _tool_result("t1"),
        )
        check = {
            "id": "test_ppp",
            "phase": "implementation",
            "description": "Read before create",
            "type": "workflow_order",
            "condition": {
                "metric": "tool_call.file_read",
                "operator": "precedes_per_path",
                "target": "tool_call.file_create",
            },
        }
        result = _evaluate_precedes_per_path(check, trace, self._ctx())
        assert result.passed is False

    def test_no_writes_vacuously_passes(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _read("foo.py", "t1"),
            _tool_result("t1"),
        )
        check = {
            "id": "test_ppp",
            "phase": "implementation",
            "description": "Read before create",
            "type": "workflow_order",
            "condition": {
                "metric": "tool_call.file_read",
                "operator": "precedes_per_path",
                "target": "tool_call.file_create",
            },
        }
        result = _evaluate_precedes_per_path(check, trace, self._ctx())
        assert result.passed is True

    def test_unmappable_metric_returns_skipped(self):
        trace = _simple_trace(_user_prompt("task"))
        check = {
            "id": "test_ppp",
            "phase": "implementation",
            "description": "Bad metric",
            "type": "workflow_order",
            "condition": {
                "metric": "unknown.metric",
                "operator": "precedes_per_path",
                "target": "tool_call.file_create",
            },
        }
        result = _evaluate_precedes_per_path(check, trace, self._ctx())
        assert result.passed is None
        assert "cannot map" in result.skip_reason



# ===========================================================================
# Constants
# ===========================================================================

class TestConstants:
    def test_tool_name_map_covers_all_tool_call_metrics(self):
        expected_keys = {
            "tool_call.file_read",
            "tool_call.file_write",
            "tool_call.file_edit",
            "tool_call.file_create",
            "tool_call.execute_command",
            "tool_call.search",
            "tool_call.glob",
            "tool_call.ask_user",
            "tool_call.skill",
            "tool_call.subagent_dispatch",
        }
        assert set(TOOL_NAME_MAP.keys()) == expected_keys

    def test_content_operators_are_frozen(self):
        assert isinstance(_CONTENT_OPERATORS, frozenset)
        assert "regex_not_match" in _CONTENT_OPERATORS
        assert "contains" in _CONTENT_OPERATORS
        assert "not_contains" in _CONTENT_OPERATORS

    def test_all_metric_names_includes_tool_call_metrics(self):
        for key in TOOL_NAME_MAP:
            assert key in _ALL_METRIC_NAMES

    def test_all_metric_names_includes_special_metrics(self):
        for name in [
            "tool_call.search.ordered_calls",
            "tool_call.search.args",
            "execution.parallel_batch",
            "trace.text_response",
            "trace.tool_call_batches",
            "tool_call.result",
            "tool_call.next",
            "task_completed",
        ]:
            assert name in _ALL_METRIC_NAMES

    def test_task_completed_sentinel_is_large(self):
        assert _TASK_COMPLETED > 1_000_000


# ===========================================================================
# metric_args support
# ===========================================================================

class TestMetricArgs:
    """Tests for the metric_args condition field."""

    def test_metric_args_filters_metric_side(self):
        """metric_args filters the metric (LHS) by primary arg substring."""
        jsonl = "\n".join([
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "npm test"}, "t1"),
            _tool_result("t1", "ok"),
            _assistant_tool_use("Bash", {"command": "git status"}, "t2"),
            _tool_result("t2", "ok"),
        ])
        trace = load_trace_from_string(jsonl)
        # With metric_args filtering to "npm test", only 1 matching Bash call
        cond = {
            "metric": "tool_call.execute_command",
            "operator": "exists_before",
            "target": "task_completed",
            "metric_args": "npm test",
        }
        passed, _ = evaluate_condition(cond, trace, {"variables": {}})
        assert passed is True

    def test_metric_args_with_no_match_fails(self):
        """metric_args that matches nothing should yield empty indices → fail."""
        jsonl = "\n".join([
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "git status"}, "t1"),
            _tool_result("t1", "ok"),
        ])
        trace = load_trace_from_string(jsonl)
        cond = {
            "metric": "tool_call.execute_command",
            "operator": "exists_before",
            "target": "task_completed",
            "metric_args": "npm test",
        }
        passed, _ = evaluate_condition(cond, trace, {"variables": {}})
        assert passed is False

    def test_metric_args_with_variable_interpolation(self):
        """metric_args supports ${variable} interpolation."""
        jsonl = "\n".join([
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "pytest tests/"}, "t1"),
            _tool_result("t1", "ok"),
        ])
        trace = load_trace_from_string(jsonl)
        cond = {
            "metric": "tool_call.execute_command",
            "operator": "exists_before",
            "target": "task_completed",
            "metric_args": "${test_command}",
        }
        passed, _ = evaluate_condition(cond, trace, {"variables": {"test_command": "pytest"}})
        assert passed is True


# ===========================================================================
# Glob in search metrics
# ===========================================================================

class TestGlobInSearchMetrics:
    """Tests for Glob inclusion in tool_call.search.* metrics."""

    def test_search_args_includes_glob_path(self):
        """tool_call.search.args should include Glob's 'path' key."""
        jsonl = "\n".join([
            _user_prompt("task"),
            _assistant_tool_use("Glob", {"pattern": "*.py", "path": "src/"}, "t1"),
            _tool_result("t1", "src/main.py"),
        ])
        trace = load_trace_from_string(jsonl)
        args = resolve_metric("tool_call.search.args", trace, {"variables": {}})
        assert "path" in args
        assert "pattern" in args

    def test_search_args_merges_grep_and_glob(self):
        """tool_call.search.args merges keys from both Grep and Glob."""
        jsonl = "\n".join([
            _user_prompt("task"),
            _assistant_tool_use("Grep", {"pattern": "def main", "type": "py"}, "t1"),
            _tool_result("t1", "match"),
            _assistant_tool_use("Glob", {"pattern": "*.py", "path": "lib/"}, "t2"),
            _tool_result("t2", "lib/a.py"),
        ])
        trace = load_trace_from_string(jsonl)
        args = resolve_metric("tool_call.search.args", trace, {"variables": {}})
        assert "type" in args  # from Grep
        assert "path" in args  # from Glob

    def test_search_ordered_calls_includes_glob(self):
        """tool_call.search.ordered_calls includes Glob calls in trace order."""
        jsonl = "\n".join([
            _user_prompt("task"),
            _assistant_tool_use("Glob", {"pattern": "*.py"}, "t1"),
            _tool_result("t1", "main.py"),
            _assistant_tool_use("Grep", {"pattern": "class Foo"}, "t2"),
            _tool_result("t2", "match"),
        ])
        trace = load_trace_from_string(jsonl)
        calls = resolve_metric("tool_call.search.ordered_calls", trace, {"variables": {}})
        assert len(calls) == 2
        assert calls[0]["pattern"] == "*.py"
        assert calls[1]["pattern"] == "class Foo"

    def test_contains_count_gte_counts_glob_as_search(self):
        """contains_count_gte for tool_call.search counts both Grep and Glob."""
        jsonl = "\n".join([
            _user_prompt("task"),
            _assistant_parallel([
                ("Grep", {"pattern": "def main"}, "t1"),
                ("Glob", {"pattern": "*.py"}, "t2"),
            ]),
            _tool_result("t1", "match"),
        ])
        trace = load_trace_from_string(jsonl)
        batches = resolve_metric("execution.parallel_batch", trace, {"variables": {}})
        target = {"metric": "tool_call.search", "count": 2}
        passed = _apply_operator("contains_count_gte", batches, target, 10, {})
        assert passed is True

    def test_contains_count_gte_grep_only_batch(self):
        """A batch with 2 Grep calls also passes search count >= 2."""
        jsonl = "\n".join([
            _user_prompt("task"),
            _assistant_parallel([
                ("Grep", {"pattern": "foo"}, "t1"),
                ("Grep", {"pattern": "bar"}, "t2"),
            ]),
            _tool_result("t1", "match"),
        ])
        trace = load_trace_from_string(jsonl)
        batches = resolve_metric("execution.parallel_batch", trace, {"variables": {}})
        target = {"metric": "tool_call.search", "count": 2}
        passed = _apply_operator("contains_count_gte", batches, target, 10, {})
        assert passed is True


# ===========================================================================
# only_via integration via evaluate_check
# ===========================================================================

class TestOnlyViaEvaluateCheck:
    """Integration tests for only_via through the evaluate_check path."""

    def _ctx(self):
        return {"conditions": {}, "variables": {}}

    def test_only_via_no_writes_passes(self):
        """No writes at all → vacuously true (all done by subagents)."""
        trace = _simple_trace(
            _user_prompt("task"),
            _assistant_tool_use("Agent", {"prompt": "write tests"}, "t1"),
            _tool_result("t1", "done"),
        )
        check = {
            "id": "test_only_via",
            "phase": "delegation",
            "description": "writes only via dispatch",
            "type": "constraint",
            "condition": {
                "metric": "tool_call.file_write",
                "operator": "only_via",
                "target": "tool_call.subagent_dispatch",
            },
        }
        result = evaluate_check(check, trace, self._ctx())
        assert result.passed is True

    def test_only_via_writes_with_dispatch_passes(self):
        """Writes preceded by dispatch → passes."""
        trace = _simple_trace(
            _user_prompt("task"),
            _assistant_tool_use("Agent", {"prompt": "write tests"}, "t1"),
            _tool_result("t1", "done"),
            _write("test_foo.py", "t2"),
            _tool_result("t2", "ok"),
        )
        check = {
            "id": "test_only_via",
            "phase": "delegation",
            "description": "writes only via dispatch",
            "type": "constraint",
            "condition": {
                "metric": "tool_call.file_write",
                "operator": "only_via",
                "target": "tool_call.subagent_dispatch",
            },
        }
        result = evaluate_check(check, trace, self._ctx())
        assert result.passed is True

    def test_only_via_writes_without_dispatch_fails(self):
        """Writes without any dispatch → fails."""
        trace = _simple_trace(
            _user_prompt("task"),
            _write("test_foo.py", "t1"),
            _tool_result("t1", "ok"),
        )
        check = {
            "id": "test_only_via",
            "phase": "delegation",
            "description": "writes only via dispatch",
            "type": "constraint",
            "condition": {
                "metric": "tool_call.file_write",
                "operator": "only_via",
                "target": "tool_call.subagent_dispatch",
            },
        }
        result = evaluate_check(check, trace, self._ctx())
        assert result.passed is False


# ===========================================================================
# Graceful handling of unknown operators/transforms in evaluate_check
# ===========================================================================

class TestEvaluateCheckGracefulSkip:
    """Unknown operators or transforms skip the check rather than crash."""

    def _ctx(self):
        return {"conditions": {}, "variables": {}}

    def test_unknown_operator_skips(self):
        """An unknown operator should skip (passed=None), not crash."""
        trace = _simple_trace(
            _user_prompt("task"),
            _write("foo.py", "t1"),
            _tool_result("t1"),
        )
        check = {
            "id": "test_skip",
            "phase": "test",
            "description": "unknown op",
            "type": "constraint",
            "condition": {
                "metric": "tool_call.file_write",
                "operator": "nonexistent_future_op",
                "target": 0,
            },
        }
        result = evaluate_check(check, trace, self._ctx())
        assert result.passed is None
        assert "Unknown operator" in result.skip_reason

    def test_unknown_transform_skips(self):
        """An unknown transform should skip (passed=None), not crash."""
        trace = _simple_trace(
            _user_prompt("task"),
            _write("foo.py", "t1"),
            _tool_result("t1"),
        )
        check = {
            "id": "test_skip",
            "phase": "test",
            "description": "unknown transform",
            "type": "constraint",
            "condition": {
                "metric": "tool_call.file_write",
                "transform": "nonexistent_transform",
                "operator": "eq",
                "target": 0,
            },
        }
        result = evaluate_check(check, trace, self._ctx())
        assert result.passed is None
        assert "Unknown transform" in result.skip_reason


# ===========================================================================
# Audit item 5: commit checks fail (not skip) when no commits in trace
# ===========================================================================

class TestCommitChecksFailWithoutCommits:
    """Commit-related checks must FAIL when there are no git commits in the
    trace, not SKIP.  Skipping excludes them from the denominator and inflates
    the pass rate (audit item 5).
    """

    def _ctx(self):
        return {"conditions": {}, "variables": {}}

    def _trace_without_commits(self):
        """A trace with no git commit commands — only non-commit work."""
        return _simple_trace(
            _user_prompt("fix the bug"),
            _bash("cargo build", "t1"),
            _tool_result("t1"),
            _bash("cargo test", "t2"),
            _tool_result("t2"),
        )

    def test_commit_msg_imperative_mood_fails_not_skips(self):
        trace = self._trace_without_commits()
        check = {
            "id": "commit_msg_imperative_mood",
            "phase": "commit",
            "description": "test",
            "type": "constraint",
            "condition": {
                "metric": "git.commit_message.subject",
                "operator": "imperative_mood",
                "target": True,
            },
        }
        result = evaluate_check(check, trace, self._ctx())
        assert result.passed is False, f"Expected FAIL, got passed={result.passed}, skip={result.skip_reason}"
        assert result.skip_reason is None
        assert "No git commit" in result.detail

    def test_commit_msg_subject_length_fails_not_skips(self):
        trace = self._trace_without_commits()
        check = {
            "id": "commit_msg_subject_length",
            "phase": "commit",
            "description": "test",
            "type": "constraint",
            "condition": {
                "metric": "git.commit_message.subject_length",
                "operator": "lte",
                "target": 50,
            },
        }
        result = evaluate_check(check, trace, self._ctx())
        assert result.passed is False, f"Expected FAIL, got passed={result.passed}, skip={result.skip_reason}"
        assert result.skip_reason is None

    def test_commit_msg_capitalize_no_period_fails_not_skips(self):
        trace = self._trace_without_commits()
        check = {
            "id": "commit_msg_capitalize_no_period",
            "phase": "commit",
            "description": "test",
            "type": "constraint",
            "condition": {
                "metric": "git.commit_message.subject",
                "operator": "regex_match",
                "target": "^[A-Z].*[^.]$",
            },
        }
        result = evaluate_check(check, trace, self._ctx())
        assert result.passed is False, f"Expected FAIL, got passed={result.passed}, skip={result.skip_reason}"
        assert result.skip_reason is None

    def test_commit_msg_no_type_prefix_fails_not_skips(self):
        trace = self._trace_without_commits()
        check = {
            "id": "commit_msg_no_type_prefix",
            "phase": "commit",
            "description": "test",
            "type": "constraint",
            "condition": {
                "metric": "git.commit_message.subject",
                "operator": "regex_not_match",
                "target": "^(feat|fix|chore|refactor|docs|style|test|ci|perf|build)(\\(.+\\))?:",
            },
        }
        result = evaluate_check(check, trace, self._ctx())
        assert result.passed is False, f"Expected FAIL, got passed={result.passed}, skip={result.skip_reason}"
        assert result.skip_reason is None

    def test_commit_msg_body_format_fails_not_skips(self):
        trace = self._trace_without_commits()
        check = {
            "id": "commit_msg_body_format",
            "phase": "commit",
            "description": "test",
            "type": "constraint",
            "condition": {
                "metric": "git.commit_message.body_format",
                "operator": "valid_format",
                "target": True,
            },
        }
        result = evaluate_check(check, trace, self._ctx())
        assert result.passed is False, f"Expected FAIL, got passed={result.passed}, skip={result.skip_reason}"
        assert result.skip_reason is None

    def test_coverage_threshold_fails_not_skips(self):
        trace = self._trace_without_commits()
        check = {
            "id": "coverage_threshold",
            "phase": "testing",
            "description": "test",
            "type": "constraint",
            "condition": {
                "metric": "coverage.percentage",
                "operator": "gte",
                "target": 90,
            },
        }
        result = evaluate_check(check, trace, self._ctx())
        assert result.passed is False, f"Expected FAIL, got passed={result.passed}, skip={result.skip_reason}"
        assert result.skip_reason is None


# ===========================================================================
# Audit item 5 — defense in depth: metric resolver guards + structural tests
# ===========================================================================

class TestCommitMetricResolversRaiseOnEmpty:
    """The git commit message metric resolvers MUST raise MetricNotResolvable
    when there are no commits in the trace.

    This is load-bearing: three of the five commit-check operators
    (regex_match, regex_not_match, valid_format) return vacuous True on empty
    lists, so if these guards were ever removed the checks would silently
    pass instead of fail.  These tests exist to catch that.
    """

    def _ctx(self):
        return {"conditions": {}, "variables": {}}

    def _trace_without_commits(self):
        return _simple_trace(
            _user_prompt("fix the bug"),
            _bash("cargo build", "t1"),
            _tool_result("t1"),
        )

    def test_subject_raises_metric_not_resolvable(self):
        trace = self._trace_without_commits()
        with pytest.raises(MetricNotResolvable, match="No git commit messages"):
            resolve_metric("git.commit_message.subject", trace, self._ctx())

    def test_subject_length_raises_metric_not_resolvable(self):
        trace = self._trace_without_commits()
        with pytest.raises(MetricNotResolvable, match="No git commit messages"):
            resolve_metric("git.commit_message.subject_length", trace, self._ctx())

    def test_body_format_raises_metric_not_resolvable(self):
        trace = self._trace_without_commits()
        with pytest.raises(MetricNotResolvable, match="No git commit messages"):
            resolve_metric("git.commit_message.body_format", trace, self._ctx())


class TestMetricNotResolvableProducesFailure:
    """MetricNotResolvable must be caught by evaluate_check and converted to
    passed=False (not passed=None/skip).

    This test is structural: it verifies the contract between the metric
    resolvers and evaluate_check.  If MetricNotResolvable is ever renamed,
    removed, or its catch block changed to produce a skip, this test fails.
    """

    def test_exception_class_exists_and_is_importable(self):
        """Guard against the class being renamed or removed."""
        assert issubclass(MetricNotResolvable, Exception)

    def test_evaluate_check_converts_to_fail_not_skip(self):
        """The catch block in evaluate_check must produce passed=False."""
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo build", "t1"),
            _tool_result("t1"),
        )
        # Use a metric known to raise MetricNotResolvable on this trace
        check = {
            "id": "structural_test",
            "phase": "commit",
            "description": "structural",
            "type": "constraint",
            "condition": {
                "metric": "git.commit_message.subject",
                "operator": "imperative_mood",
                "target": True,
            },
        }
        result = evaluate_check(check, trace, {"conditions": {}, "variables": {}})
        # MUST be a fail, not a skip
        assert result.passed is False, (
            f"MetricNotResolvable must produce passed=False, got passed={result.passed}"
        )
        assert result.skip_reason is None, (
            f"MetricNotResolvable must produce skip_reason=None, got {result.skip_reason!r}"
        )
        assert result.detail is not None, (
            "MetricNotResolvable must propagate the exception message as detail"
        )


class TestOperatorsVacuousOnEmptyInputs:
    """Document and enforce that certain operators return WRONG results on
    empty lists.

    These tests prove WHY the MetricNotResolvable guards in the metric
    resolvers are load-bearing.  If you change an operator to correctly
    reject empty lists, update these tests accordingly — but you must also
    verify that the non-commit uses (e.g. regex_not_match on
    tool_call.execute_command) still work correctly with empty inputs.
    """

    def test_regex_match_vacuously_passes_empty_list(self):
        """regex_match([]) returns True — wrong for commit checks."""
        from operators import op_regex_match
        # all() on empty iterable is True in Python
        assert op_regex_match([], "^[A-Z].*[^.]$") is True

    def test_regex_not_match_vacuously_passes_empty_list(self):
        """regex_not_match([]) returns True — wrong for commit checks,
        but correct for 'no forbidden commands found' checks."""
        from operators import op_regex_not_match
        # not any() on empty iterable is True in Python
        assert op_regex_not_match([], r"^(feat|fix):") is True

    def test_valid_format_vacuously_passes_empty_list(self):
        """valid_format([], True) returns True — wrong for commit checks."""
        from operators import op_valid_format
        assert op_valid_format([], True) is True

    def test_imperative_mood_correctly_rejects_empty_list(self):
        """imperative_mood handles empty correctly — returns not target."""
        from operators import op_imperative_mood
        # This one already gets it right: empty + target=True → False
        assert op_imperative_mood([], True) is False


# ===========================================================================
# Bug 5: followed_by operator integration
# ===========================================================================

class TestFollowedByIntegration:
    """Test followed_by operator through evaluate_condition."""

    def test_followed_by_pass(self):
        """Edit followed by execute_command should pass."""
        trace = _simple_trace(
            _user_prompt("fix it"),
            _edit("src/lib.rs", "t1"),
            _tool_result("t1"),
            _bash("cargo test", "t2"),
            _tool_result("t2"),
        )
        condition = {
            "metric": "tool_call.file_edit",
            "operator": "followed_by",
            "target": "tool_call.execute_command",
        }
        passed, detail = evaluate_condition(condition, trace, {})
        assert passed is True

    def test_followed_by_fail(self):
        """execute_command with no subsequent edit should fail."""
        trace = _simple_trace(
            _user_prompt("fix it"),
            _bash("cargo test", "t1"),
            _tool_result("t1"),
        )
        condition = {
            "metric": "tool_call.execute_command",
            "operator": "followed_by",
            "target": "tool_call.file_edit",
        }
        passed, detail = evaluate_condition(condition, trace, {})
        assert passed is False

    def test_followed_by_no_metric_events(self):
        """No metric events → false."""
        trace = _simple_trace(
            _user_prompt("fix it"),
            _bash("cargo test", "t1"),
            _tool_result("t1"),
        )
        condition = {
            "metric": "tool_call.file_edit",
            "operator": "followed_by",
            "target": "tool_call.execute_command",
        }
        passed, detail = evaluate_condition(condition, trace, {})
        assert passed is False


# ===========================================================================
# Bug 2: tool_call.file_modify combined metric
# ===========================================================================

class TestFileModifyMetric:
    """Test that tool_call.file_modify resolves to both Write and Edit indices."""

    def test_file_modify_includes_write_and_edit(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _write("tests/test_foo.rs", "t1"),
            _tool_result("t1"),
            _edit("src/lib.rs", "t2"),
            _tool_result("t2"),
        )
        indices = resolve_metric("tool_call.file_modify", trace, {})
        assert len(indices) == 2

    def test_file_modify_in_all_metric_names(self):
        assert "tool_call.file_modify" in _ALL_METRIC_NAMES

    def test_file_modify_with_args_filter(self):
        """file_modify with metric_args should filter by substring in path."""
        trace = _simple_trace(
            _user_prompt("task"),
            _write("tests/test_foo.rs", "t1"),
            _tool_result("t1"),
            _edit("src/lib.rs", "t2"),
            _tool_result("t2"),
            _write("src/config.rs", "t3"),
            _tool_result("t3"),
        )
        condition = {
            "metric": "tool_call.file_modify",
            "metric_args": "src/",
            "transform": "count",
            "operator": "eq",
            "target": 2,
        }
        passed, detail = evaluate_condition(condition, trace, {})
        assert passed is True


# ===========================================================================
# Bug 3: content-based test matching (_matches_signal)
# ===========================================================================

class TestMatchesSignal:
    """Test _matches_signal with content_match for Rust inline tests."""

    def test_path_match(self):
        tc = ToolCall(tool_use_id="t1", name="Write", input={"file_path": "tests/test_foo.rs"}, event_index=0)
        assert _matches_signal(tc, "test") is True

    def test_content_match_rust_test_attr(self):
        tc = ToolCall(
            tool_use_id="t1", name="Write",
            input={"file_path": "src/schema.rs", "content": 'fn foo() {}\n#[test]\nfn test_foo() {}'},
            event_index=0,
        )
        assert _matches_signal(tc, "test", r"#\[test\]|#\[cfg\(test\)\]|mod tests") is True

    def test_content_match_cfg_test(self):
        tc = ToolCall(
            tool_use_id="t1", name="Write",
            input={"file_path": "src/schema.rs", "content": '#[cfg(test)]\nmod tests { }'},
            event_index=0,
        )
        assert _matches_signal(tc, "test", r"#\[test\]|#\[cfg\(test\)\]|mod tests") is True

    def test_content_match_edit_new_string(self):
        tc = ToolCall(
            tool_use_id="t1", name="Edit",
            input={"file_path": "src/schema.rs", "new_string": '#[test]\nfn it_works() {}'},
            event_index=0,
        )
        assert _matches_signal(tc, "test", r"#\[test\]|#\[cfg\(test\)\]|mod tests") is True

    def test_no_match_no_content_match(self):
        tc = ToolCall(
            tool_use_id="t1", name="Write",
            input={"file_path": "src/schema.rs", "content": 'fn foo() {}'},
            event_index=0,
        )
        # Path doesn't contain "test", content doesn't have test markers, no content_match set
        assert _matches_signal(tc, "test") is False

    def test_no_match_content_without_markers(self):
        tc = ToolCall(
            tool_use_id="t1", name="Write",
            input={"file_path": "src/schema.rs", "content": 'fn foo() {}'},
            event_index=0,
        )
        assert _matches_signal(tc, "test", r"#\[test\]|#\[cfg\(test\)\]|mod tests") is False


# ===========================================================================
# not_match: distinguish step commands from gate commands (bivvy.ape)
# ===========================================================================

GATE_EXCLUSION = r"\|\s*(awk|grep|wc|cut|head|tail|sort|uniq|sed)\b"


class TestNotMatchExcludesGateCommands:
    """Bivvy's APE workflow runs the same `cargo` binaries from steps and from
    gate evaluation. Step commands run the binary directly; gate commands wrap
    them in a parsing pipeline (e.g. ``cargo test ... 2>&1 | awk ...``).

    `not_match` excludes the gate-pipelined invocations so they don't get
    misclassified as the work phase. Without it, every gate that runs
    ``cargo test`` would register as a "testing" phase event regardless of
    which step it ran from.
    """

    def test_step_command_matches_without_pipe(self):
        tc = ToolCall(
            tool_use_id="t1", name="Bash",
            input={"command": "cargo test --all-features 2>&1"},
            event_index=0,
        )
        assert _matches_signal(tc, "cargo test", not_match=GATE_EXCLUSION) is True

    def test_gate_command_excluded_when_piped_to_awk(self):
        tc = ToolCall(
            tool_use_id="t1", name="Bash",
            input={"command": "cargo test --all-features 2>&1 | awk '/test result:/ { for(i=1;i<=NF;i++) if($i==\"failed;\") sum+=$(i-1) } END { print sum+0 }'"},
            event_index=0,
        )
        assert _matches_signal(tc, "cargo test", not_match=GATE_EXCLUSION) is False

    def test_gate_command_excluded_when_piped_to_grep(self):
        tc = ToolCall(
            tool_use_id="t1", name="Bash",
            input={"command": "cargo build 2>&1 | grep \"^error\" | grep -Evc \"(aborting|could not compile)\""},
            event_index=0,
        )
        assert _matches_signal(tc, "cargo build", not_match=GATE_EXCLUSION) is False

    def test_gate_command_excluded_when_piped_to_wc(self):
        tc = ToolCall(
            tool_use_id="t1", name="Bash",
            input={"command": "git diff --cached --name-only | wc -l"},
            event_index=0,
        )
        assert _matches_signal(tc, "git diff", not_match=GATE_EXCLUSION) is False

    def test_no_not_match_keeps_old_behavior(self):
        tc = ToolCall(
            tool_use_id="t1", name="Bash",
            input={"command": "cargo test --all-features 2>&1 | awk '...'"},
            event_index=0,
        )
        # Without not_match, the command still matches "cargo test"
        assert _matches_signal(tc, "cargo test") is True

    def test_2to1_redirect_alone_does_not_trigger_exclusion(self):
        # `2>&1` is a stderr-to-stdout redirect, not a pipe. The exclusion
        # regex must only fire on the `|` pipe character, not on `>`.
        tc = ToolCall(
            tool_use_id="t1", name="Bash",
            input={"command": "cargo doc --no-deps --all-features 2>&1"},
            event_index=0,
        )
        assert _matches_signal(tc, "cargo doc", not_match=GATE_EXCLUSION) is True


class TestPhaseDetectionIgnoresGateCommands:
    """End-to-end: gate-style cargo invocations run from non-testing steps must
    not be classified as `testing` phase events. This is the bug that caused
    `phase_ordering` to be disabled."""

    def _make_bivvy_config(self):
        return {
            "specification": {
                "signals": ["tool_call.file_write", "tool_call.file_edit"],
                "position": "before_implementation",
                "match": "test",
                "content_match": r"#\[test\]|#\[cfg\(test\)\]|mod tests",
            },
            "implementation": {
                "signals": ["tool_call.file_write", "tool_call.file_edit", "tool_call.file_create"],
                "position": "any",
            },
            "linting": {
                "signals": ["tool_call.execute_command"],
                "position": "after_implementation",
                "match": "cargo fmt",
                "not_match": GATE_EXCLUSION,
            },
            "testing": {
                "signals": ["tool_call.execute_command"],
                "position": "after_implementation",
                "match": "cargo test",
                "not_match": GATE_EXCLUSION,
            },
            "build": {
                "signals": ["tool_call.execute_command"],
                "position": "after_implementation",
                "match": "cargo build",
                "not_match": GATE_EXCLUSION,
            },
        }

    def _make_classification(self):
        return {
            "ordered": ["specification", "implementation", "linting", "testing", "build"],
            "floating": [],
        }

    def test_specification_gate_cargo_test_does_not_count_as_testing(self):
        """Step 1 (specification) gate runs ``cargo test ... | awk ...`` to
        count failures. That gate run must not be detected as a testing phase
        event - the actual testing step hasn't run yet."""
        trace = _simple_trace(
            _user_prompt("task"),
            _write("tests/foo_test.rs", "t1"),
            _tool_result("t1"),
            # Specification gate: cargo test piped through awk
            _bash("cargo test --all-features 2>&1 | awk '/test result:/ { print 0 }'", "t2"),
            _tool_result("t2"),
            _edit("src/lib.rs", "t3"),
            _tool_result("t3"),
        )
        order, events = _detect_phases(trace, self._make_bivvy_config(), self._make_classification())
        # No testing phase: the only cargo test was a gate command
        assert "testing" not in events

    def test_real_testing_step_still_detected(self):
        """When the agent actually runs ``cargo test`` as a step command, it
        should still be detected as the testing phase."""
        trace = _simple_trace(
            _user_prompt("task"),
            _write("tests/foo_test.rs", "t1"),
            _tool_result("t1"),
            _edit("src/lib.rs", "t2"),
            _tool_result("t2"),
            # Real testing step
            _bash("cargo test --all-features 2>&1", "t3"),
            _tool_result("t3"),
        )
        order, events = _detect_phases(trace, self._make_bivvy_config(), self._make_classification())
        assert "testing" in events

    def test_post_commit_gate_cargo_build_excluded(self):
        """Post-commit gate runs ``cargo build 2>&1 | grep ...`` to count
        errors. That must not register as another build phase event."""
        trace = _simple_trace(
            _user_prompt("task"),
            _edit("src/lib.rs", "t1"),
            _tool_result("t1"),
            # Real build step command
            _bash("cargo build --all-targets --all-features 2>&1", "t2"),
            _tool_result("t2"),
            # Post-commit gate command - must be excluded
            _bash("cargo build 2>&1 | grep \"^error\" | grep -Evc \"(aborting|could not compile)\"", "t3"),
            _tool_result("t3"),
        )
        order, events = _detect_phases(trace, self._make_bivvy_config(), self._make_classification())
        # build phase should only have the step-command event, not the gate one
        build_events = events.get("build", [])
        assert len(build_events) == 1, f"build should have exactly 1 event, got {build_events}"

    def test_full_bivvy_sequence_classifies_correctly(self):
        """Realistic bivvy.ape trace with mixed step + gate commands. The
        execution order must reflect the step sequence, not the gate runs."""
        trace = _simple_trace(
            _user_prompt("task"),
            # 1. specification (write tests)
            _write("tests/foo_test.rs", "t1"),
            _tool_result("t1"),
            # specification gate
            _bash("cargo test --all-features 2>&1 | awk '{print 1}'", "t2"),
            _tool_result("t2"),
            # 2. implementation
            _edit("src/lib.rs", "t3"),
            _tool_result("t3"),
            # implementation gate
            _bash("cargo test --all-features 2>&1 | awk '{print 0}'", "t4"),
            _tool_result("t4"),
            # 3. linting (step)
            _bash("cargo fmt -- --check 2>&1", "t5"),
            _tool_result("t5"),
            # linting gate
            _bash("cargo fmt -- --check 2>&1 | grep -c \"^Diff in\"", "t6"),
            _tool_result("t6"),
            # 4. testing (step)
            _bash("cargo test --all-features 2>&1", "t7"),
            _tool_result("t7"),
            # testing gate (cov-pct + test-failure-count)
            _bash("cargo llvm-cov --all-features 2>&1 | awk '{print 95}'", "t8"),
            _tool_result("t8"),
            # 5. build (step)
            _bash("cargo build --all-targets --all-features 2>&1", "t9"),
            _tool_result("t9"),
        )
        order, events = _detect_phases(trace, self._make_bivvy_config(), self._make_classification())
        # All 5 phases should be detected, in the declared order
        assert order == ["specification", "implementation", "linting", "testing", "build"], \
            f"Unexpected phase order: {order}"


# ===========================================================================
# Bug 4: after_tdd_specify position in _detect_phases
# ===========================================================================

class TestAfterTddSpecifyPosition:
    """Test that after_tdd_specify deferred position works in _detect_phases."""

    def _make_phase_config(self):
        return {
            "tdd_specify": {
                "signals": ["tool_call.file_write"],
                "position": "before_implementation",
                "match": "test",
            },
            "tdd_prove_fail": {
                "signals": ["tool_call.execute_command"],
                "position": "after_tdd_specify",
                "match": "cargo test",
            },
            "implementation": {
                # In Bivvy config, implementation also includes file_create
                # but for testing, use only file_edit to separate from test writes
                "signals": ["tool_call.file_edit"],
                "position": "any",
            },
        }

    def _make_class_config(self):
        return {"ordered": ["tdd_specify", "tdd_prove_fail", "implementation"], "floating": []}

    def test_tdd_prove_fail_after_tdd_specify(self):
        """cargo test after test write should be in tdd_prove_fail phase."""
        trace = _simple_trace(
            _user_prompt("task"),
            _write("tests/test_foo.rs", "t1"),
            _tool_result("t1"),
            _bash("cargo test", "t2"),
            _tool_result("t2"),
            _edit("src/lib.rs", "t3"),
            _tool_result("t3"),
        )
        order, events = _detect_phases(trace, self._make_phase_config(), self._make_class_config())
        assert "tdd_specify" in events
        assert "tdd_prove_fail" in events
        # tdd_prove_fail events should be after tdd_specify events
        assert min(events["tdd_prove_fail"]) > min(events["tdd_specify"])

    def test_no_tdd_specify_means_no_tdd_prove_fail(self):
        """If no tdd_specify detected, after_tdd_specify should find nothing."""
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo test", "t1"),
            _tool_result("t1"),
            _edit("src/lib.rs", "t2"),
            _tool_result("t2"),
        )
        order, events = _detect_phases(trace, self._make_phase_config(), self._make_class_config())
        assert "tdd_prove_fail" not in events


# ===========================================================================
# Bug 6: target_position / metric_position filtering
# ===========================================================================

class TestPositionFiltering:
    """Test that target_position filters indices to post-implementation events."""

    def _impl_context(self):
        return {
            "phase_tool_mapping": {
                "implementation": {
                    "signals": ["tool_call.file_write", "tool_call.file_edit"],
                    "position": "any",
                },
            },
            "phase_classification": {"ordered": ["implementation"], "floating": []},
        }

    def test_target_position_after_implementation(self):
        """With target_position, only post-impl test runs should be considered."""
        trace = _simple_trace(
            _user_prompt("task"),
            # TDD test run (before implementation)
            _bash("cargo test", "t1"),
            _tool_result("t1"),
            # Linting
            _bash("cargo fmt", "t2"),
            _tool_result("t2"),
            # Implementation
            _write("src/lib.rs", "t3"),
            _tool_result("t3"),
            # Post-impl test run
            _bash("cargo test", "t4"),
            _tool_result("t4"),
        )
        condition = {
            "metric": "tool_call.execute_command",
            "metric_args": "cargo fmt",
            "operator": "strictly_precedes",
            "target": "tool_call.execute_command",
            "target_args": "cargo test",
            "target_position": "after_implementation",
        }
        passed, detail = evaluate_condition(condition, trace, self._impl_context())
        # cargo fmt (idx 3) should strictly precede only post-impl cargo test (idx 7)
        assert passed is True

    def test_without_target_position_fails(self):
        """Without target_position, early TDD test runs cause failure."""
        trace = _simple_trace(
            _user_prompt("task"),
            # TDD test run (before implementation)
            _bash("cargo test", "t1"),
            _tool_result("t1"),
            # Linting
            _bash("cargo fmt", "t2"),
            _tool_result("t2"),
            # Implementation
            _write("src/lib.rs", "t3"),
            _tool_result("t3"),
            # Post-impl test run
            _bash("cargo test", "t4"),
            _tool_result("t4"),
        )
        condition = {
            "metric": "tool_call.execute_command",
            "metric_args": "cargo fmt",
            "operator": "strictly_precedes",
            "target": "tool_call.execute_command",
            "target_args": "cargo test",
        }
        passed, detail = evaluate_condition(condition, trace, {})
        # Without filtering, min(cargo test)=1, max(cargo fmt)=3, 3 > 1 → fails
        assert passed is False

    def test_after_implementation_uses_last_impl_event(self):
        """after_implementation boundary should use LAST impl event, not first.

        Tests during implementation (TDD cycles) should not be counted as
        post-implementation verification.
        """
        trace = _simple_trace(
            _user_prompt("task"),
            # Implementation phase: multiple writes
            _write("src/lib.rs", "t1"),
            _tool_result("t1"),
            # Test run DURING implementation (TDD cycle)
            _bash("cargo test", "t2"),
            _tool_result("t2"),
            # More implementation
            _edit("src/lib.rs", "t3"),
            _tool_result("t3"),
            # Linting (after implementation ends)
            _bash("cargo fmt", "t4"),
            _tool_result("t4"),
            # Final test run (after linting)
            _bash("cargo test", "t5"),
            _tool_result("t5"),
        )
        condition = {
            "metric": "tool_call.execute_command",
            "metric_args": "cargo fmt",
            "operator": "strictly_precedes",
            "target": "tool_call.execute_command",
            "target_args": "cargo test",
            "target_position": "after_implementation",
        }
        passed, detail = evaluate_condition(condition, trace, self._impl_context())
        # The boundary is the LAST impl event (edit at idx ~5), so only the
        # final cargo test (idx ~9) is considered. cargo fmt precedes it → pass
        assert passed is True


# ===========================================================================
# _summarize_value: no truncation
# ===========================================================================

class TestSummarizeValue:
    """Ensure _summarize_value shows full values without truncation."""

    def test_long_path_not_truncated(self):
        path = "/private/var/folders/_q/qzh8_t6s15388qqf3p6n3d200000gp/T/bench-xyz/src/config/loader.rs"
        result = _summarize_value(path)
        assert path in result
        assert "..." not in result

    def test_list_of_long_paths_not_truncated(self):
        paths = [
            "/private/var/folders/_q/qzh8_t6s15388qqf3p6n3d200000gp/T/bench-xyz/src/config/loader.rs",
            "/private/var/folders/_q/qzh8_t6s15388qqf3p6n3d200000gp/T/bench-xyz/src/config/schema.rs",
        ]
        result = _summarize_value(paths)
        # Both full paths must appear in the output
        assert "loader.rs" in result
        assert "schema.rs" in result
        assert result.count("...") == 0

    def test_empty_list(self):
        assert _summarize_value([]) == "[] (empty)"

    def test_none(self):
        assert _summarize_value(None) == "None"

    def test_int(self):
        assert _summarize_value(42) == "42"

    def test_bool(self):
        assert _summarize_value(True) == "True"

    def test_explicit_max_len_truncates(self):
        result = _summarize_value("a very long string indeed", max_len=10)
        assert "..." in result


# ===========================================================================
# Path normalization for scope_to_request
# ===========================================================================

class TestPathNormalization:
    """Test that diff.scope.permitted_paths normalizes absolute trace paths to relative."""

    def test_scope_permitted_normalizes_absolute_to_relative(self):
        """When workspace_path is set, trace paths should be normalized to relative."""
        workspace = "/private/var/folders/_q/qzh8_t6s15388qqf3p6n3d200000gp/T/bench-xyz"
        trace = _simple_trace(
            _user_prompt("task"),
            _read(f"{workspace}/src/config/loader.rs", "t1"),
            _tool_result("t1"),
            _read(f"{workspace}/src/config/schema.rs", "t2"),
            _tool_result("t2"),
        )
        ctx = {"conditions": {}, "variables": {}, "workspace_path": workspace}
        result = _resolve_diff_scope_permitted(trace, ctx)
        result_set = set(result)
        # Paths should be relative, not absolute
        assert "src/config/loader.rs" in result_set
        assert "src/config/schema.rs" in result_set
        assert not any(p.startswith("/private") for p in result_set)

    def test_scope_permitted_uses_trace_workspace_path(self):
        """When context has no workspace_path, trace.workspace_path is used."""
        workspace = "/tmp/bench-test"
        # Build a trace with a system init event that has cwd
        init_line = json.dumps({
            "type": "system", "subtype": "init",
            "cwd": workspace, "session_id": "test",
        })
        prompt = _user_prompt("task")
        read_call = _read(f"{workspace}/src/foo.rs", "t1")
        result_ev = _tool_result("t1")
        trace = load_trace_from_string("\n".join([init_line, prompt, read_call, result_ev]))

        ctx = {"conditions": {}, "variables": {}}  # No workspace_path in context
        result = _resolve_diff_scope_permitted(trace, ctx)
        # Should use trace.workspace_path to normalize
        assert "src/foo.rs" in result

    def test_subset_of_passes_with_normalized_paths(self):
        """The scope_to_request check should pass when files_changed and permitted are both relative."""
        workspace = "/tmp/bench-test"
        init_line = json.dumps({
            "type": "system", "subtype": "init",
            "cwd": workspace, "session_id": "test",
        })
        prompt = _user_prompt("task")
        read1 = _read(f"{workspace}/src/config/loader.rs", "t1")
        res1 = _tool_result("t1")
        read2 = _read(f"{workspace}/src/config/schema.rs", "t2")
        res2 = _tool_result("t2")
        trace = load_trace_from_string("\n".join([init_line, prompt, read1, res1, read2, res2]))

        ctx = {
            "conditions": {"explicit_edit_requested": True},
            "variables": {},
            "workspace_state": {
                "modified_files": ["src/config/loader.rs"],
            },
        }
        # diff.files_changed = ["src/config/loader.rs"] (relative)
        # diff.scope.permitted_paths = ["src/config/loader.rs", "src/config/schema.rs"] (normalized from trace)
        check = {
            "id": "scope_to_request",
            "phase": "implementation",
            "description": "test",
            "condition": {
                "metric": "diff.files_changed",
                "operator": "subset_of",
                "target": "diff.scope.permitted_paths",
            },
        }
        result = evaluate_check(check, trace, ctx)
        assert result.passed is True


# ===========================================================================
# _resolve_position_boundary: after_implementation uses LAST impl event
# ===========================================================================

class TestResolvePositionBoundary:
    def _ctx(self):
        return {
            "phase_tool_mapping": {
                "implementation": {
                    "signals": ["tool_call.file_write", "tool_call.file_edit"],
                    "position": "any",
                },
            },
            "phase_classification": {"ordered": ["implementation"], "floating": []},
        }

    def test_after_implementation_returns_last_impl_event(self):
        """Boundary should be the LAST implementation event, not the first."""
        trace = _simple_trace(
            _user_prompt("task"),
            _write("src/a.rs", "t1"),
            _tool_result("t1"),
            _bash("cargo test", "t2"),
            _tool_result("t2"),
            _edit("src/b.rs", "t3"),
            _tool_result("t3"),
        )
        boundary = _resolve_position_boundary("after_implementation", trace, self._ctx())
        # The last impl call is the Edit (call_index 2 after the Write at 0 and Bash at 1)
        write_idx = None
        edit_idx = None
        for tc in trace.all_tool_calls("Write"):
            write_idx = tc.call_index
        for tc in trace.all_tool_calls("Edit"):
            edit_idx = tc.call_index
        # Boundary should be the edit (later), not the write (earlier)
        assert boundary == edit_idx
        assert boundary > write_idx

    def test_after_implementation_none_when_no_impl(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo test", "t1"),
            _tool_result("t1"),
        )
        boundary = _resolve_position_boundary("after_implementation", trace, self._ctx())
        assert boundary is None


# ===========================================================================
# Phase ordering scoring spec - obligations from SCORING_SPEC section 6
# ===========================================================================

from evaluator import (  # noqa: E402
    _proximity_factor,
    _cross_phase_factor,
    _position_factor,
    _UnifiedEvent,
    _RequiredCommand,
    _PhaseSpec,
    _candidates_for,
    _unified_events,
    _score_phase,
    _migrate_legacy_phase,
    _parse_phase_spec,
)


def _ue(unified_pos: int, kind: str = "tool_call", call_index=None, event_index=None) -> _UnifiedEvent:
    """Build a synthetic _UnifiedEvent for factor-level tests."""
    if call_index is None and kind == "tool_call":
        call_index = unified_pos
    if event_index is None:
        event_index = unified_pos
    u = _UnifiedEvent(
        kind=kind,
        call_index=call_index,
        event_index=event_index,
        tc=None,
        text="",
    )
    u.unified_pos = unified_pos
    return u


class TestProximityFactor:
    """Obligation: proximity returns 1.0 at distance 0, 0.6 at distance 6,
    0.0 at distance >= window, and uses NEAREST other for 3+ commands."""

    def test_distance_zero_returns_one(self):
        assert _proximity_factor(_ue(10), [_ue(10)], window=15) == 1.0

    def test_distance_three_returns_eight_tenths(self):
        assert _proximity_factor(_ue(10), [_ue(13)], window=15) == pytest.approx(0.8)

    def test_distance_six_returns_six_tenths(self):
        assert _proximity_factor(_ue(10), [_ue(16)], window=15) == pytest.approx(0.6)

    def test_distance_at_or_above_window_returns_zero(self):
        assert _proximity_factor(_ue(10), [_ue(25)], window=15) == 0.0
        assert _proximity_factor(_ue(10), [_ue(30)], window=15) == 0.0

    def test_uses_nearest_with_three_commands(self):
        # Distances 14 and 1 → nearest is 1 → factor = 1 - 1/15.
        result = _proximity_factor(_ue(10), [_ue(24), _ue(11)], window=15)
        assert result == pytest.approx(1.0 - 1.0 / 15.0)

    def test_no_others_returns_one(self):
        # R == 1 case.
        assert _proximity_factor(_ue(10), [], window=15) == 1.0


class TestCrossPhaseFactor:
    """Obligation: cross_phase factor counts illegal candidates inside cluster
    intervals; honors legal_redirect_targets; never goes below zero; doesn't
    double-count an event in overlapping intervals."""

    def test_no_illegal_candidates_returns_one(self):
        u_i = _ue(10)
        u_j = _ue(15)
        # Only "self" candidates, none other.
        result = _cross_phase_factor(u_i, [u_j], {}, "self", set())
        assert result == 1.0

    def test_one_illegal_returns_zero_point_eight(self):
        u_i = _ue(10)
        u_j = _ue(20)
        other = {"build": [_ue(15)]}
        result = _cross_phase_factor(u_i, [u_j], other, "self", set())
        assert result == pytest.approx(0.8)

    def test_three_illegals_returns_zero_point_four(self):
        u_i = _ue(10)
        u_j = _ue(20)
        other = {"build": [_ue(12), _ue(14)], "lint": [_ue(16)]}
        result = _cross_phase_factor(u_i, [u_j], other, "self", set())
        assert result == pytest.approx(0.4)

    def test_never_below_zero(self):
        u_i = _ue(10)
        u_j = _ue(20)
        # Six illegal candidates → 1 - 0.2*6 = -0.2, clamped to 0.
        other = {"build": [_ue(p) for p in (11, 12, 13, 14, 15, 16)]}
        result = _cross_phase_factor(u_i, [u_j], other, "self", set())
        assert result == 0.0

    def test_legal_redirect_targets_not_counted(self):
        u_i = _ue(10)
        u_j = _ue(20)
        other = {"implementation": [_ue(15)]}
        result = _cross_phase_factor(u_i, [u_j], other, "self", legal_redirect_targets={"implementation"})
        assert result == 1.0

    def test_no_double_count_overlapping_intervals(self):
        # Three required commands at 10, 15, 20 → pair intervals (10,15),
        # (15,20), (10,20) merge to (10,20). One illegal at 12 should be
        # counted exactly once, not three times.
        u_i = _ue(10)
        others = [_ue(15), _ue(20)]
        other = {"build": [_ue(12)]}
        result = _cross_phase_factor(u_i, others, other, "self", set())
        assert result == pytest.approx(0.8)

    def test_no_others_returns_one(self):
        # R == 1 case.
        assert _cross_phase_factor(_ue(10), [], {"build": [_ue(15)]}, "self", set()) == 1.0


class TestPositionFactor:
    """Obligation: position factor returns 0.5 (soft) for out-of-window
    after_implementation, 0.0 (hard) for after_specification beyond impl
    and before_implementation past the impl boundary, and 1.0 with
    vacuous boundaries.

    Cascade-suppression cure: when the predecessor phase of an ``after_*``
    position did NOT fire (boundary is None), the constraint is vacuous
    and the factor returns 1.0. The previous 0.5 default capped per-required
    contributions at (1/R)*0.5, which made R=2 phases mathematically unable
    to reach the 0.8 threshold and silently cascaded skipped-phase failures
    into all downstream ``after_*`` phases. Score the cluster strength
    on its own merits when the predecessor never happened.
    """

    def test_any_returns_one(self):
        assert _position_factor(_ue(0), "any", {}) == 1.0

    def test_after_implementation_in_window(self):
        assert _position_factor(_ue(15), "after_implementation", {"implementation": 5}) == 1.0

    def test_after_implementation_out_of_window_soft(self):
        assert _position_factor(_ue(2), "after_implementation", {"implementation": 5}) == 0.5

    def test_after_implementation_no_boundary_vacuous_returns_one(self):
        # Predecessor phase did not fire; the "after" constraint is vacuous
        # so the factor is 1.0 (cascade-suppression cure).
        assert _position_factor(_ue(2), "after_implementation", {"implementation": None}) == 1.0

    def test_after_linting_no_boundary_vacuous_returns_one(self):
        assert _position_factor(_ue(2), "after_linting", {"linting": None}) == 1.0

    def test_after_testing_no_boundary_vacuous_returns_one(self):
        assert _position_factor(_ue(2), "after_testing", {"testing": None}) == 1.0

    def test_after_verification_no_boundary_vacuous_returns_one(self):
        assert _position_factor(_ue(2), "after_verification", {"testing": None}) == 1.0

    def test_before_implementation_vacuous_returns_one(self):
        assert _position_factor(_ue(2), "before_implementation", {"implementation": None}) == 1.0

    def test_before_implementation_in_window(self):
        """Event preceding the implementation boundary scores full credit."""
        assert _position_factor(_ue(2), "before_implementation", {"implementation": 5}) == 1.0

    def test_before_implementation_out_of_window_hard_zero(self):
        """Event after the implementation boundary contributes 0.0.

        `before_implementation` is HARD: mid-impl edits that look like
        spec/investigation must not earn partial credit, otherwise they
        cluster with mid-impl `cargo test` and incorrectly fire spec
        after implementation has started. The other soft positions
        (after_implementation, after_linting, etc.) remain at 0.5.
        """
        assert _position_factor(_ue(7), "before_implementation", {"implementation": 5}) == 0.0

    def test_after_specification_above_impl_hard_zero(self):
        assert _position_factor(
            _ue(20),
            "after_specification",
            {"specification_last": 5, "implementation": 10},
        ) == 0.0

    def test_after_specification_below_spec_soft(self):
        assert _position_factor(
            _ue(2),
            "after_specification",
            {"specification_last": 5, "implementation": 10},
        ) == 0.5

    def test_after_specification_in_window(self):
        assert _position_factor(
            _ue(7),
            "after_specification",
            {"specification_last": 5, "implementation": 10},
        ) == 1.0

    def test_last_in_window(self):
        assert _position_factor(_ue(15), "last", {"last_lower_bound": 10}) == 1.0

    def test_last_out_of_window_hard_zero(self):
        assert _position_factor(_ue(5), "last", {"last_lower_bound": 10}) == 0.0

    def test_last_no_lower_bound_returns_one(self):
        assert _position_factor(_ue(5), "last", {"last_lower_bound": None}) == 1.0

    def test_post_hoc_returns_zero(self):
        assert _position_factor(_ue(5), "post_hoc", {}) == 0.0


# ---------------------------------------------------------------------------
# Helpers for end-to-end phase scoring tests.
# ---------------------------------------------------------------------------

def _testing_phase_config():
    return {
        "implementation": {
            "position": "any",
            "legal_redirect_targets": ["linting", "testing"],
            "required_commands": [
                {"id": "src_edit", "signal": "tool_call.file_edit", "match": ".*"},
                {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
            ],
        },
        "linting": {
            "position": "after_implementation",
            "legal_redirect_targets": ["implementation", "testing"],
            "required_commands": [
                {"id": "cargo_fmt", "signal": "tool_call.execute_command", "match": "cargo fmt"},
                {"id": "cargo_clippy", "signal": "tool_call.execute_command", "match": "cargo clippy"},
            ],
        },
        "testing": {
            "position": "after_linting",
            "legal_redirect_targets": ["implementation", "linting"],
            "required_commands": [
                {"id": "cargo_llvm_cov", "signal": "tool_call.execute_command", "match": "cargo llvm-cov"},
                {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
            ],
        },
    }


def _testing_classification():
    return {"ordered": ["implementation", "linting", "testing"], "floating": []}


class TestRequiredCommandsScoring:
    """Obligations: R=1 fires when in-window candidate exists; R=2 fires at
    distance 0-3 and not at distance 6+ on a clean trace."""

    def test_r1_fires_when_candidate_in_window(self):
        config = {
            "linting": {
                "position": "any",
                "required_commands": [
                    {"id": "cargo_fmt", "signal": "tool_call.execute_command", "match": "cargo fmt"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo fmt", "t1"),
            _tool_result("t1"),
        )
        order, events = _detect_phases(trace, config, {"ordered": ["linting"], "floating": []})
        assert "linting" in events

    def test_r2_fires_at_distance_zero(self):
        config = {
            "linting": {
                "position": "any",
                "required_commands": [
                    {"id": "cargo_fmt", "signal": "tool_call.execute_command", "match": "cargo fmt"},
                    {"id": "cargo_clippy", "signal": "tool_call.execute_command", "match": "cargo clippy"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _assistant_parallel([
                ("Bash", {"command": "cargo fmt"}, "t1"),
                ("Bash", {"command": "cargo clippy"}, "t2"),
            ]),
            _tool_result("t1"),
            _tool_result("t2"),
        )
        order, events = _detect_phases(trace, config, {"ordered": ["linting"], "floating": []})
        assert "linting" in events

    def test_r2_fires_at_distance_three(self):
        config = {
            "linting": {
                "position": "any",
                "required_commands": [
                    {"id": "cargo_fmt", "signal": "tool_call.execute_command", "match": "cargo fmt"},
                    {"id": "cargo_clippy", "signal": "tool_call.execute_command", "match": "cargo clippy"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo fmt", "t1"),
            _tool_result("t1"),
            _bash("noop1", "t2"),
            _tool_result("t2"),
            _bash("noop2", "t3"),
            _tool_result("t3"),
            _bash("cargo clippy", "t4"),
            _tool_result("t4"),
        )
        order, events = _detect_phases(trace, config, {"ordered": ["linting"], "floating": []})
        assert "linting" in events

    def test_r2_does_not_fire_at_distance_six(self):
        config = {
            "linting": {
                "position": "any",
                "required_commands": [
                    {"id": "cargo_fmt", "signal": "tool_call.execute_command", "match": "cargo fmt"},
                    {"id": "cargo_clippy", "signal": "tool_call.execute_command", "match": "cargo clippy"},
                ],
            },
        }
        # Six unrelated commands between fmt and clippy.
        events_lst = [_user_prompt("task"), _bash("cargo fmt", "t1"), _tool_result("t1")]
        for i in range(2, 8):
            events_lst.append(_bash(f"noop{i}", f"tn{i}"))
            events_lst.append(_tool_result(f"tn{i}"))
        events_lst.append(_bash("cargo clippy", "tc"))
        events_lst.append(_tool_result("tc"))
        trace = _simple_trace(*events_lst)
        order, events = _detect_phases(trace, config, {"ordered": ["linting"], "floating": []})
        assert "linting" not in events


class TestHardPrerequisites:
    """Obligations: requires AND semantics; absent prereq = phase doesn't fire."""

    def test_commit_does_not_fire_without_git_add(self):
        config = {
            "commit": {
                "position": "any",
                "required_commands": [
                    {"id": "git_add", "signal": "tool_call.execute_command", "match": "git add"},
                    {"id": "git_commit", "signal": "tool_call.execute_command", "match": "git commit", "requires": ["git_add"]},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("git commit -m 'x'", "t1"),
            _tool_result("t1"),
        )
        order, events = _detect_phases(trace, config, {"ordered": ["commit"], "floating": []})
        assert "commit" not in events

    def test_commit_fires_when_git_add_precedes(self):
        config = {
            "commit": {
                "position": "any",
                "required_commands": [
                    {"id": "git_add", "signal": "tool_call.execute_command", "match": "git add"},
                    {"id": "git_commit", "signal": "tool_call.execute_command", "match": "git commit", "requires": ["git_add"]},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("git add -p", "t1"),
            _tool_result("t1"),
            _bash("git commit -m 'x'", "t2"),
            _tool_result("t2"),
        )
        order, events = _detect_phases(trace, config, {"ordered": ["commit"], "floating": []})
        assert "commit" in events

    def test_requires_and_semantics_two_prereqs(self):
        config = {
            "phase_x": {
                "position": "any",
                "required_commands": [
                    {"id": "a", "signal": "tool_call.execute_command", "match": "alpha"},
                    {"id": "b", "signal": "tool_call.execute_command", "match": "beta"},
                    {"id": "c", "signal": "tool_call.execute_command", "match": "gamma", "requires": ["a", "b"]},
                ],
            },
        }
        # alpha precedes gamma but beta does NOT precede gamma → phase should
        # not fire (no feasible assignment with beta before gamma).
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("alpha", "t1"),
            _tool_result("t1"),
            _bash("gamma", "t2"),
            _tool_result("t2"),
            _bash("beta", "t3"),
            _tool_result("t3"),
        )
        order, events = _detect_phases(trace, config, {"ordered": ["phase_x"], "floating": []})
        # No assignment of (a, b, c) has BOTH a and b strictly before c, so c
        # contributes 0 and the phase score = (1/3)+(1/3)+0 = 0.667 < 0.8.
        assert "phase_x" not in events


class TestOptionalCommands:
    """Obligations: optional present and clean adds up to 1/R; absent = no
    effect; isolated optional contributes 0; rescues borderline cluster."""

    def test_optional_present_and_clean_does_not_break_phase(self):
        config = {
            "testing": {
                "position": "any",
                "required_commands": [
                    {"id": "cargo_llvm_cov", "signal": "tool_call.execute_command", "match": "cargo llvm-cov"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                    {"id": "working_on", "signal": "assistant_text", "match": "Working on: testing", "optional": True},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _assistant_text("Working on: testing"),
            _bash("cargo llvm-cov", "t1"),
            _tool_result("t1"),
            _bash("cargo test", "t2"),
            _tool_result("t2"),
        )
        order, events = _detect_phases(trace, config, {"ordered": ["testing"], "floating": []})
        assert "testing" in events

    def test_optional_absent_no_penalty(self):
        config = {
            "testing": {
                "position": "any",
                "required_commands": [
                    {"id": "cargo_llvm_cov", "signal": "tool_call.execute_command", "match": "cargo llvm-cov"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                    {"id": "working_on", "signal": "assistant_text", "match": "Working on: testing", "optional": True},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo llvm-cov", "t1"),
            _tool_result("t1"),
            _bash("cargo test", "t2"),
            _tool_result("t2"),
        )
        order, events = _detect_phases(trace, config, {"ordered": ["testing"], "floating": []})
        assert "testing" in events

    def test_optional_rescues_borderline_cluster(self):
        # Two required at distance 9 → required contribution 0.4 (below 0.8).
        # Optional clean at the cluster contributes 0.5 → total 0.9 → fires.
        config = {
            "testing": {
                "position": "any",
                "required_commands": [
                    {"id": "cargo_llvm_cov", "signal": "tool_call.execute_command", "match": "cargo llvm-cov"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                    {"id": "working_on", "signal": "assistant_text", "match": "Working on: testing", "optional": True},
                ],
            },
        }
        events_lst = [
            _user_prompt("task"),
            _assistant_text("Working on: testing"),
            _bash("cargo llvm-cov", "t1"),
            _tool_result("t1"),
        ]
        for i in range(2, 11):
            events_lst.append(_bash(f"noop{i}", f"tn{i}"))
            events_lst.append(_tool_result(f"tn{i}"))
        events_lst.append(_bash("cargo test", "tc"))
        events_lst.append(_tool_result("tc"))
        trace = _simple_trace(*events_lst)
        order, events = _detect_phases(trace, config, {"ordered": ["testing"], "floating": []})
        assert "testing" in events


class TestAssistantTextSignal:
    """Obligations: assistant_text matches Working on: lines; indexed by
    TraceEvent.index, ordered before tool calls in the same event."""

    def test_assistant_text_matches_working_on(self):
        config = {
            "testing": {
                "position": "any",
                "required_commands": [
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                    {"id": "working_on", "signal": "assistant_text", "match": "Working on: testing"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _assistant_text("Working on: testing"),
            _bash("cargo test", "t1"),
            _tool_result("t1"),
        )
        order, events = _detect_phases(trace, config, {"ordered": ["testing"], "floating": []})
        assert "testing" in events

    def test_text_ordered_before_tool_calls_same_event(self):
        # Build a unified stream from a trace with text then a tool call.
        trace = _simple_trace(
            _user_prompt("task"),
            _assistant_text("Working on: testing"),
            _bash("cargo test", "t1"),
            _tool_result("t1"),
        )
        unified = _unified_events(trace)
        text_events = [u for u in unified if u.kind == "text"]
        bash_events = [u for u in unified if u.kind == "tool_call"]
        assert text_events
        assert bash_events
        # Text from event N should come before bash from event > N.
        assert text_events[0].unified_pos < bash_events[0].unified_pos


class TestMultiOccurrence:
    """Obligations: with multiple cargo test occurrences, scorer picks the one
    that pairs best; dedup prevents earlier-phase events being re-claimed."""

    def test_testing_picks_cargo_test_paired_with_llvm_cov(self):
        config = {
            "implementation": {
                "position": "any",
                "required_commands": [
                    {"id": "src_edit", "signal": "tool_call.file_edit", "match": ".*"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
            "testing": {
                "position": "after_implementation",
                "required_commands": [
                    {"id": "cargo_llvm_cov", "signal": "tool_call.execute_command", "match": "cargo llvm-cov"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _edit("src/lib.rs", "t1"),
            _tool_result("t1"),
            _bash("cargo test", "t2"),  # impl-gate test
            _tool_result("t2"),
            _bash("cargo llvm-cov", "t3"),
            _tool_result("t3"),
            _bash("cargo test", "t4"),  # testing-step test
            _tool_result("t4"),
        )
        order, events = _detect_phases(
            trace, config,
            {"ordered": ["implementation", "testing"], "floating": []},
        )
        assert "testing" in events
        # The testing phase should have indices that include the llvm-cov call
        # index and the second cargo test (paired with llvm-cov).
        # call_indices: Edit=0, cargo test#1=1, llvm-cov=2, cargo test#2=3.
        assert 2 in events["testing"]
        assert 3 in events["testing"]

    def test_dedup_prevents_post_commit_stealing_testing_indices(self):
        # Use position=any everywhere and legal_redirect_targets to allow each
        # phase's required commands to coexist without polluting each other.
        # This isolates the dedup behavior from cross-phase pollution math.
        config = {
            "testing": {
                "position": "any",
                "legal_redirect_targets": ["post-commit"],
                "required_commands": [
                    {"id": "cargo_llvm_cov", "signal": "tool_call.execute_command", "match": "cargo llvm-cov"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
            "post-commit": {
                "position": "any",
                "legal_redirect_targets": ["testing"],
                "required_commands": [
                    {"id": "git_log", "signal": "tool_call.execute_command", "match": "git log -1 --stat"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo llvm-cov", "t1"),  # call_index 0
            _tool_result("t1"),
            _bash("cargo test", "t2"),       # call_index 1 - testing-step
            _tool_result("t2"),
            _bash("git log -1 --stat", "t3"),  # call_index 2
            _tool_result("t3"),
            _bash("cargo test", "t4"),       # call_index 3 - post-commit
            _tool_result("t4"),
        )
        order, events = _detect_phases(
            trace, config,
            {"ordered": ["testing", "post-commit"], "floating": []},
        )
        assert "testing" in events
        assert "post-commit" in events
        # No index appears in both testing and post-commit lists (dedup).
        overlap = set(events["testing"]) & set(events["post-commit"])
        assert overlap == set()


class TestFirstOccurrence:
    """Obligation: streaming first-occurrence returns the first k where the
    partial score crosses; if pollution drops the final score below threshold,
    the phase does not fire."""

    def test_first_occurrence_streams_to_threshold(self):
        config = {
            "linting": {
                "position": "any",
                "required_commands": [
                    {"id": "cargo_fmt", "signal": "tool_call.execute_command", "match": "cargo fmt"},
                    {"id": "cargo_clippy", "signal": "tool_call.execute_command", "match": "cargo clippy"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo fmt", "t1"),
            _tool_result("t1"),
            _bash("cargo clippy", "t2"),
            _tool_result("t2"),
        )
        order, events = _detect_phases(trace, config, {"ordered": ["linting"], "floating": []})
        # The phase fires; its first-occurrence index reflects the second
        # contributor (where the cluster is complete).
        assert "linting" in events
        assert min(events["linting"]) == 0  # cargo fmt at call_index 0


class TestBoundaryOrdering:
    """Obligations: linting (boundary phase) is scored before testing (which
    depends on it). Implementation ignores its own position constraint."""

    def test_linting_scored_before_testing(self):
        # If testing's position requires after_linting and linting fires,
        # testing should benefit from the boundary value. Cluster impl edits
        # and a test run tightly so impl fires; legal_redirect_targets keep
        # cross-phase pollution from breaking the math.
        config = _testing_phase_config()
        trace = _simple_trace(
            _user_prompt("task"),
            _edit("src/lib.rs", "t1"),
            _tool_result("t1"),
            _bash("cargo test", "t2"),
            _tool_result("t2"),
            _bash("cargo fmt", "t3"),
            _tool_result("t3"),
            _bash("cargo clippy", "t4"),
            _tool_result("t4"),
            _bash("cargo llvm-cov", "t5"),
            _tool_result("t5"),
            _bash("cargo test", "t6"),
            _tool_result("t6"),
        )
        order, events = _detect_phases(trace, config, _testing_classification())
        assert "linting" in events
        assert "testing" in events
        # Linting should appear before testing in execution order.
        assert order.index("linting") < order.index("testing")

    def test_implementation_ignores_self_position(self):
        # If implementation declares some non-any position, it is forced to
        # `any` to break self-reference. Use a config where implementation has
        # `position: after_implementation` (nonsensical but valid syntactically).
        config = {
            "implementation": {
                "position": "after_implementation",
                "required_commands": [
                    {"id": "src_edit", "signal": "tool_call.file_edit", "match": ".*"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _edit("src/lib.rs", "t1"),
            _tool_result("t1"),
            _bash("cargo test", "t2"),
            _tool_result("t2"),
        )
        order, events = _detect_phases(trace, config, {"ordered": ["implementation"], "floating": []})
        assert "implementation" in events

    def _bivvy_like_spec_impl_config(self):
        # Mirrors the bivvy spec/impl mapping (path match, content_match,
        # not_match) so we can exercise the impl_first_idx_static fix.
        return {
            "specification": {
                "position": "before_implementation",
                "legal_redirect_targets": ["implementation"],
                "required_commands": [
                    {
                        "id": "test_edit",
                        "signal": ["tool_call.file_write", "tool_call.file_edit"],
                        "match": "test",
                        "content_match": "#\\[test\\]|#\\[cfg\\(test\\)\\]|mod tests",
                    },
                    {
                        "id": "cargo_test",
                        "signal": "tool_call.execute_command",
                        "match": "cargo test",
                    },
                ],
            },
            "implementation": {
                "position": "any",
                "repeatable": True,
                "legal_redirect_targets": [],
                "required_commands": [
                    {
                        "id": "src_edit",
                        "signal": ["tool_call.file_edit", "tool_call.file_write", "tool_call.file_create"],
                        "match": "src/",
                        "not_match": "(?:^|/)tests?(?:/|\\b)",
                    },
                    {
                        "id": "cargo_test",
                        "signal": "tool_call.execute_command",
                        "match": "cargo test",
                    },
                ],
            },
        }

    def test_specification_does_not_fire_on_mid_impl_inline_test_edit(self):
        # Reproduces the bug where specification was firing on an inline
        # `#[cfg(test)]` block added to a src/ file partway through the
        # implementation phase. With the impl_first_idx_static fix the
        # spec candidates that fall AT or AFTER the first non-test src
        # edit must score 0.0 on `before_implementation`, so spec cannot
        # fire and the timeline starts with implementation.
        config = self._bivvy_like_spec_impl_config()
        trace = _simple_trace(
            _user_prompt("task"),
            # 1. Non-test src edit (impl boundary).
            _assistant_tool_use(
                "Write",
                {"file_path": "src/foo.rs", "content": "pub fn foo() -> i32 { 42 }"},
                "t1",
            ),
            _tool_result("t1"),
            # 2. cargo test.
            _bash("cargo test", "t2"),
            _tool_result("t2"),
            # 3. Add an inline `#[cfg(test)]` block to the same src file.
            _assistant_tool_use(
                "Edit",
                {
                    "file_path": "src/foo.rs",
                    "new_string": "#[cfg(test)]\nmod tests {\n    #[test]\n    fn t() {}\n}",
                },
                "t3",
            ),
            _tool_result("t3"),
            # 4. cargo test again.
            _bash("cargo test", "t4"),
            _tool_result("t4"),
        )
        order, events = _detect_phases(
            trace,
            config,
            {"ordered": ["specification", "implementation"], "floating": []},
        )
        assert "specification" not in events, (
            f"specification should not fire on a mid-impl inline test edit, got events={events}"
        )
        assert "implementation" in events
        assert order and order[0] == "implementation"

    def test_specification_fires_on_genuine_tdd_first(self):
        # The mirror case: when the test edit and `cargo test` come BEFORE
        # any non-test src edit, specification must still fire and execution
        # order must be [specification, implementation].
        config = self._bivvy_like_spec_impl_config()
        trace = _simple_trace(
            _user_prompt("task"),
            # 1. Test edit first (tests/ path so impl src_edit's not_match
            # excludes it; content also signals a test).
            _assistant_tool_use(
                "Write",
                {
                    "file_path": "tests/test_foo.rs",
                    "content": "#[test]\nfn t() { assert!(false); }",
                },
                "t1",
            ),
            _tool_result("t1"),
            # 2. cargo test (the failing run).
            _bash("cargo test", "t2"),
            _tool_result("t2"),
            # 3. Non-test src edit (impl boundary).
            _assistant_tool_use(
                "Write",
                {"file_path": "src/foo.rs", "content": "pub fn foo() -> i32 { 42 }"},
                "t3",
            ),
            _tool_result("t3"),
            # 4. cargo test (the passing run).
            _bash("cargo test", "t4"),
            _tool_result("t4"),
        )
        order, events = _detect_phases(
            trace,
            config,
            {"ordered": ["specification", "implementation"], "floating": []},
        )
        assert "specification" in events
        assert "implementation" in events
        assert order[:2] == ["specification", "implementation"], (
            f"genuine TDD should produce [specification, implementation, ...], got order={order}"
        )


class TestDedupAndFloating:
    """Obligations: dedup removes claimed indices from later phases; floating
    phases appear in phase_events but not execution_order."""

    def test_dedup_removes_specification_index_from_implementation(self):
        # Default bivvy-style mapping where impl matches edits only on src.
        config = {
            "specification": {
                "position": "before_implementation",
                "required_commands": [
                    {"id": "test_edit", "signal": "tool_call.file_edit", "match": "test"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
            "implementation": {
                "position": "any",
                "required_commands": [
                    {"id": "src_edit", "signal": "tool_call.file_edit", "match": ".*"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _edit("tests/foo.rs", "t1"),  # spec-claimable
            _tool_result("t1"),
            _bash("cargo test", "t2"),
            _tool_result("t2"),
            _edit("src/lib.rs", "t3"),
            _tool_result("t3"),
            _bash("cargo test", "t4"),
            _tool_result("t4"),
        )
        order, events = _detect_phases(
            trace, config,
            {"ordered": ["specification", "implementation"], "floating": []},
        )
        assert "specification" in events
        assert "implementation" in events
        spec_idxs = set(events["specification"])
        impl_idxs = set(events["implementation"])
        # No overlap: dedup strips spec-claimed indices from impl's list.
        assert spec_idxs & impl_idxs == set()

    def test_floating_phase_in_events_not_in_order(self):
        config = {
            "implementation": {
                "position": "any",
                "required_commands": [
                    {"id": "src_edit", "signal": "tool_call.file_edit", "match": ".*"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
            "failure_recovery": {
                "position": "any",
                "required_commands": [
                    {"id": "edit", "signal": "tool_call.file_edit", "match": ".*"},
                    {"id": "command", "signal": "tool_call.execute_command", "match": ".*"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _edit("src/lib.rs", "t1"),
            _tool_result("t1"),
            _bash("cargo test", "t2"),
            _tool_result("t2"),
        )
        order, events = _detect_phases(
            trace, config,
            {"ordered": ["implementation"], "floating": ["failure_recovery"]},
        )
        assert "failure_recovery" in events
        assert "failure_recovery" not in order


class TestLegacyMigration:
    """Obligation: legacy schema (signals + match) auto-migrates and produces
    the same firing behavior as it did before for that phase."""

    def test_legacy_single_signal_phase_fires(self):
        legacy_config = {
            "linting": {
                "signals": ["tool_call.execute_command"],
                "position": "any",
                "match": "cargo fmt",
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo fmt", "t1"),
            _tool_result("t1"),
        )
        order, events = _detect_phases(trace, legacy_config, {"ordered": ["linting"], "floating": []})
        assert "linting" in events

    def test_migrate_legacy_phase_returns_required_commands(self):
        migrated = _migrate_legacy_phase("linting", {
            "signals": ["tool_call.execute_command"],
            "position": "after_implementation",
            "match": "cargo fmt",
        })
        assert "required_commands" in migrated
        assert len(migrated["required_commands"]) == 1
        cmd = migrated["required_commands"][0]
        assert cmd["signal"] == "tool_call.execute_command"
        assert cmd["match"] == "cargo fmt"
        assert cmd["optional"] is False


class TestMatchOperator:
    """Obligations: re.search semantics; \\bcargo test\\b excludes
    cargo testbed."""

    def test_re_search_matches_substring(self):
        config = {
            "testing": {
                "position": "any",
                "required_commands": [
                    {"id": "ct", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo test --release", "t1"),
            _tool_result("t1"),
        )
        order, events = _detect_phases(trace, config, {"ordered": ["testing"], "floating": []})
        assert "testing" in events

    def test_word_boundary_excludes_testbed(self):
        config = {
            "testing": {
                "position": "any",
                "required_commands": [
                    {"id": "ct", "signal": "tool_call.execute_command", "match": "\\bcargo test\\b"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo testbed", "t1"),
            _tool_result("t1"),
        )
        order, events = _detect_phases(trace, config, {"ordered": ["testing"], "floating": []})
        assert "testing" not in events


class TestEmptyAndRealisticTraces:
    """Obligations: empty trace returns ([], {}); realistic full-sequence
    trace fires all 8 phases in declared order."""

    def test_empty_trace(self):
        # An empty trace (no events) should return empty results regardless
        # of mapping.
        trace = _simple_trace(_user_prompt("task"))
        config = {
            "linting": {
                "position": "any",
                "required_commands": [
                    {"id": "cargo_fmt", "signal": "tool_call.execute_command", "match": "cargo fmt"},
                ],
            },
        }
        order, events = _detect_phases(trace, config, {"ordered": ["linting"], "floating": []})
        assert order == []
        assert events == {}

    def test_full_sequence_all_phases_in_order(self):
        # Compliant ape trace: spec edits, impl edits, doc, lint, test+cov,
        # build, commit, post-commit. Use a simplified config but still 8
        # ordered phases.
        config = {
            "specification": {
                "position": "before_implementation",
                "required_commands": [
                    {"id": "test_edit", "signal": "tool_call.file_edit", "match": "test"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
            "implementation": {
                "position": "any",
                "required_commands": [
                    {"id": "src_edit", "signal": "tool_call.file_edit", "match": "src/"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
            "documentation": {
                "position": "after_implementation",
                "required_commands": [
                    {"id": "cargo_doc_a", "signal": "tool_call.execute_command", "match": "cargo doc"},
                    {"id": "cargo_doc_b", "signal": "tool_call.execute_command", "match": "cargo doc"},
                ],
            },
            "linting": {
                "position": "after_implementation",
                "legal_redirect_targets": ["implementation"],
                "required_commands": [
                    {"id": "cargo_fmt", "signal": "tool_call.execute_command", "match": "cargo fmt"},
                    {"id": "cargo_clippy", "signal": "tool_call.execute_command", "match": "cargo clippy"},
                ],
            },
            "testing": {
                "position": "after_linting",
                "legal_redirect_targets": ["implementation", "build"],
                "required_commands": [
                    {"id": "cargo_llvm_cov", "signal": "tool_call.execute_command", "match": "cargo llvm-cov"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
            "build": {
                "position": "after_testing",
                "legal_redirect_targets": ["linting", "commit"],
                "required_commands": [
                    {"id": "build_dev", "signal": "tool_call.execute_command", "match": "cargo build --all-targets"},
                    {"id": "build_release", "signal": "tool_call.execute_command", "match": "cargo build --release"},
                ],
            },
            "commit": {
                "position": "after_verification",
                "required_commands": [
                    {"id": "git_add", "signal": "tool_call.execute_command", "match": "git add"},
                    {"id": "git_commit", "signal": "tool_call.execute_command", "match": "git commit", "requires": ["git_add"]},
                ],
            },
            "post-commit": {
                "position": "last",
                "legal_redirect_targets": ["implementation"],
                "required_commands": [
                    {"id": "git_log", "signal": "tool_call.execute_command", "match": "git log -1 --stat"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                    {"id": "cargo_build", "signal": "tool_call.execute_command", "match": "cargo build"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            # specification
            _edit("tests/foo.rs", "t1"),
            _tool_result("t1"),
            _bash("cargo test", "t2"),
            _tool_result("t2"),
            # implementation
            _edit("src/lib.rs", "t3"),
            _tool_result("t3"),
            _bash("cargo test", "t4"),
            _tool_result("t4"),
            # documentation
            _bash("cargo doc", "t5"),
            _tool_result("t5"),
            _bash("cargo doc --no-deps", "t6"),
            _tool_result("t6"),
            # linting
            _bash("cargo fmt", "t7"),
            _tool_result("t7"),
            _bash("cargo clippy", "t8"),
            _tool_result("t8"),
            # testing
            _bash("cargo llvm-cov", "t9"),
            _tool_result("t9"),
            _bash("cargo test", "ta"),
            _tool_result("ta"),
            # build
            _bash("cargo build --all-targets", "tb"),
            _tool_result("tb"),
            _bash("cargo build --release", "tc"),
            _tool_result("tc"),
            # commit
            _bash("git add -p", "td"),
            _tool_result("td"),
            _bash("git commit -m 'x'", "te"),
            _tool_result("te"),
            # post-commit
            _bash("git log -1 --stat", "tf"),
            _tool_result("tf"),
            _bash("cargo test", "tg"),
            _tool_result("tg"),
            _bash("cargo build", "th"),
            _tool_result("th"),
        )
        classification = {
            "ordered": ["specification", "implementation", "documentation", "linting", "testing", "build", "commit", "post-commit"],
            "floating": [],
        }
        order, events = _detect_phases(trace, config, classification)
        # All 8 phases should fire and be in declared order.
        assert order == classification["ordered"], f"got {order}"

    def test_out_of_order_trace_reorders_execution_order(self):
        # Trace runs linting BEFORE the formal testing cluster; impl is split
        # across the trace. Execution order should reflect actual first
        # occurrences regardless of declared sequence.
        config = _testing_phase_config()
        trace = _simple_trace(
            _user_prompt("task"),
            _edit("src/lib.rs", "t1"),
            _tool_result("t1"),
            _bash("cargo test", "t2"),
            _tool_result("t2"),
            # Linting cluster
            _bash("cargo fmt", "t3"),
            _tool_result("t3"),
            _bash("cargo clippy", "t4"),
            _tool_result("t4"),
            # Testing cluster
            _bash("cargo llvm-cov", "t5"),
            _tool_result("t5"),
            _bash("cargo test", "t6"),
            _tool_result("t6"),
        )
        order, events = _detect_phases(trace, config, _testing_classification())
        assert "implementation" in order
        assert "linting" in order
        assert "testing" in order
        # All three phases should be in declared order on this compliant trace.
        assert order.index("implementation") < order.index("linting") < order.index("testing")
        assert "testing" in order

    def test_build_fires_when_testing_did_not_fire(self):
        # Cascade-suppression cure: testing's R=2 cluster (cargo llvm-cov +
        # cargo test) is incomplete because the agent skipped `cargo llvm-cov`,
        # so testing does not fire. Build's `after_testing` position now sees
        # a vacuous boundary (None) and contributes 1.0, letting build's own
        # cluster (cargo build --all-targets + cargo build --release) decide
        # whether it fires. The cluster is near-adjacent so build MUST fire
        # and appear in the execution order.
        config = {
            "implementation": {
                "position": "any",
                "required_commands": [
                    {"id": "src_edit", "signal": "tool_call.file_edit", "match": "src/"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
            "testing": {
                "position": "after_implementation",
                "required_commands": [
                    {"id": "cargo_llvm_cov", "signal": "tool_call.execute_command", "match": "cargo llvm-cov"},
                    {"id": "cargo_test_cov", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
            "build": {
                "position": "after_testing",
                "required_commands": [
                    {"id": "build_dev", "signal": "tool_call.execute_command", "match": "cargo build --all-targets"},
                    {"id": "build_release", "signal": "tool_call.execute_command", "match": "cargo build --release"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            # implementation cluster.
            _edit("src/lib.rs", "t1"),
            _tool_result("t1"),
            _bash("cargo test", "t2"),
            _tool_result("t2"),
            # No cargo llvm-cov => testing should NOT fire.
            # build cluster, near-adjacent.
            _bash("cargo build --all-targets", "t3"),
            _tool_result("t3"),
            _bash("cargo build --release", "t4"),
            _tool_result("t4"),
        )
        classification = {
            "ordered": ["implementation", "testing", "build"],
            "floating": [],
        }
        order, events = _detect_phases(trace, config, classification)
        assert "testing" not in events
        assert "build" in events
        assert "build" in order


class TestSchemaValidation:
    """Sanity: schema validation rejects broken phase entries."""

    def test_duplicate_id_raises(self):
        bad = {
            "x": {
                "position": "any",
                "required_commands": [
                    {"id": "a", "signal": "tool_call.execute_command", "match": "alpha"},
                    {"id": "a", "signal": "tool_call.execute_command", "match": "beta"},
                ],
            },
        }
        with pytest.raises(ValueError, match="duplicate"):
            _detect_phases(_simple_trace(_user_prompt("task")), bad, {"ordered": ["x"], "floating": []})

    def test_unknown_requires_id_raises(self):
        bad = {
            "x": {
                "position": "any",
                "required_commands": [
                    {"id": "a", "signal": "tool_call.execute_command", "match": "alpha"},
                    {"id": "b", "signal": "tool_call.execute_command", "match": "beta", "requires": ["nope"]},
                ],
            },
        }
        with pytest.raises(ValueError, match="unknown id"):
            _detect_phases(_simple_trace(_user_prompt("task")), bad, {"ordered": ["x"], "floating": []})

    def test_threshold_out_of_range_raises(self):
        bad = {
            "x": {
                "threshold": 1.5,
                "position": "any",
                "required_commands": [
                    {"id": "a", "signal": "tool_call.execute_command", "match": "alpha"},
                ],
            },
        }
        with pytest.raises(ValueError, match="threshold"):
            _detect_phases(_simple_trace(_user_prompt("task")), bad, {"ordered": ["x"], "floating": []})

    def test_proximity_window_below_one_raises(self):
        bad = {
            "x": {
                "proximity_window": 0,
                "position": "any",
                "required_commands": [
                    {"id": "a", "signal": "tool_call.execute_command", "match": "alpha"},
                ],
            },
        }
        with pytest.raises(ValueError, match="proximity_window"):
            _detect_phases(_simple_trace(_user_prompt("task")), bad, {"ordered": ["x"], "floating": []})


class TestEvalTracePayload:
    """Obligation: eval_trace payload includes per-phase scoring detail."""

    def test_eval_trace_logs_per_phase_score(self):
        from eval_trace import EvalTrace
        config = {
            "linting": {
                "position": "any",
                "required_commands": [
                    {"id": "cargo_fmt", "signal": "tool_call.execute_command", "match": "cargo fmt"},
                    {"id": "cargo_clippy", "signal": "tool_call.execute_command", "match": "cargo clippy"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo fmt", "t1"),
            _tool_result("t1"),
            _bash("cargo clippy", "t2"),
            _tool_result("t2"),
        )
        et = EvalTrace()
        order, events = _detect_phases(trace, config, {"ordered": ["linting"], "floating": []}, eval_trace=et)
        log = et.to_list()
        phase_log = [e for e in log if e["action"] == "phase_detection"]
        assert phase_log
        per_phase = phase_log[0]["per_phase"]
        assert "linting" in per_phase
        # The payload must record the score and required-command details.
        assert "score" in per_phase["linting"]
        assert "required" in per_phase["linting"]
        assert any(d["id"] == "cargo_fmt" for d in per_phase["linting"]["required"])
        assert any(d["id"] == "cargo_clippy" for d in per_phase["linting"]["required"])


class TestSameEventGuard:
    """A single UnifiedEvent must not satisfy two required-command slots in
    the same phase. Spec section 3 assumes distinct cluster events, and
    chained Bash invocations (e.g. `cargo fmt -- --check && cargo clippy`)
    can match multiple regexes simultaneously."""

    def test_chained_fmt_clippy_does_not_fire_linting(self):
        config = {
            "linting": {
                "position": "any",
                "required_commands": [
                    {"id": "cargo_fmt", "signal": "tool_call.execute_command", "match": "cargo fmt"},
                    {"id": "cargo_clippy", "signal": "tool_call.execute_command", "match": "cargo clippy"},
                ],
            },
        }
        # Single chained Bash call matching both regexes; no other cargo
        # invocations in the trace.
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo fmt -- --check && cargo clippy --all-targets", "t1"),
            _tool_result("t1"),
        )
        order, events = _detect_phases(trace, config, {"ordered": ["linting"], "floating": []})
        assert "linting" not in events

    def test_distinct_events_still_fire_when_both_present(self):
        config = {
            "linting": {
                "position": "any",
                "required_commands": [
                    {"id": "cargo_fmt", "signal": "tool_call.execute_command", "match": "cargo fmt"},
                    {"id": "cargo_clippy", "signal": "tool_call.execute_command", "match": "cargo clippy"},
                ],
            },
        }
        # One chained call matches both, but a second clippy call is
        # available; the assignment search must pair fmt with the chained
        # call (or use the chained call once) without claiming it twice.
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo fmt && cargo clippy", "t1"),
            _tool_result("t1"),
            _bash("cargo clippy", "t2"),
            _tool_result("t2"),
        )
        order, events = _detect_phases(trace, config, {"ordered": ["linting"], "floating": []})
        assert "linting" in events
        # Two distinct unified positions claimed; not the same event twice.
        assert len(set(events["linting"])) == len(events["linting"])
        assert len(events["linting"]) >= 2


class TestImplVerifyCycleCounter:
    """Cycle counter must observe interleaved impl/test events for repeatable
    phases. With clustering the chosen-cluster lists are too thin to count
    cycles, so repeatable phases expand to their full candidate sets after
    scoring."""

    def _config(self):
        return {
            "implementation": {
                "position": "any",
                "repeatable": True,
                "required_commands": [
                    {
                        "id": "src_edit",
                        "signal": ["tool_call.file_edit", "tool_call.file_write"],
                        "match": "src/",
                        "not_match": "(?:^|/)tests?(?:/|\\b)",
                    },
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
            "testing": {
                "position": "after_implementation",
                "repeatable": True,
                "legal_redirect_targets": ["implementation"],
                "required_commands": [
                    {"id": "cargo_llvm_cov", "signal": "tool_call.execute_command", "match": "cargo llvm-cov"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
        }

    def _classification(self):
        return {"ordered": ["implementation", "testing"], "floating": []}

    def test_three_cycles_observed_when_interleaved(self):
        # impl-test-impl-test-impl-test cycle: the trace-aware cycle counter
        # sees src edits as impl activity and cargo test runs as verify
        # activity, observing three impl->verify transitions.
        trace = _simple_trace(
            _user_prompt("task"),
            _edit("src/lib.rs", "i1"),
            _tool_result("i1"),
            _bash("cargo test", "v1"),
            _tool_result("v1"),
            _edit("src/foo.rs", "i2"),
            _tool_result("i2"),
            _bash("cargo test", "v2"),
            _tool_result("v2"),
            _edit("src/bar.rs", "i3"),
            _tool_result("i3"),
            _bash("cargo test", "v3"),
            _tool_result("v3"),
            _bash("cargo llvm-cov", "v4"),
            _tool_result("v4"),
            _bash("cargo test", "v5"),
            _tool_result("v5"),
        )
        config = self._config()
        _, events = _detect_phases(trace, config, self._classification())
        cycles = _count_impl_verify_cycles(events, trace=trace, phase_tool_mapping=config)
        assert cycles >= 3

    def test_single_impl_single_test_one_cycle(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _edit("src/lib.rs", "i1"),
            _tool_result("i1"),
            _bash("cargo test", "v1"),
            _tool_result("v1"),
            _bash("cargo llvm-cov", "v2"),
            _tool_result("v2"),
            _bash("cargo test", "v3"),
            _tool_result("v3"),
        )
        config = self._config()
        _, events = _detect_phases(trace, config, self._classification())
        assert _count_impl_verify_cycles(
            events, trace=trace, phase_tool_mapping=config,
        ) == 1

    def test_no_test_zero_cycles(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _edit("src/lib.rs", "i1"),
            _tool_result("i1"),
            _edit("src/foo.rs", "i2"),
            _tool_result("i2"),
        )
        config = self._config()
        _, events = _detect_phases(trace, config, self._classification())
        assert _count_impl_verify_cycles(
            events, trace=trace, phase_tool_mapping=config,
        ) == 0


class TestRepeatableExpansionPreservesOrdering:
    """Expanding repeatable phases' event lists must not shift the canonical
    cluster-anchor first-occurrence used for execution_order ordering."""

    def test_execution_order_uses_cluster_anchor_not_min(self):
        # Implementation has an early src edit that becomes part of the
        # expanded set after scoring. Testing's cluster fires later.
        # Without a canonical first-occurrence, expanding implementation's
        # event list could put it before testing in execution_order; with
        # the canonical anchor, the order matches the true cluster anchor.
        config = {
            "implementation": {
                "position": "any",
                "repeatable": True,
                "required_commands": [
                    {
                        "id": "src_edit",
                        "signal": ["tool_call.file_edit", "tool_call.file_write"],
                        "match": "src/",
                    },
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
            "testing": {
                "position": "any",
                "repeatable": True,
                "legal_redirect_targets": ["implementation"],
                "required_commands": [
                    {"id": "cargo_llvm_cov", "signal": "tool_call.execute_command", "match": "cargo llvm-cov"},
                    {"id": "cargo_test", "signal": "tool_call.execute_command", "match": "cargo test"},
                ],
            },
        }
        trace = _simple_trace(
            _user_prompt("task"),
            _edit("src/lib.rs", "e1"),
            _tool_result("e1"),
            _bash("cargo test", "t1"),
            _tool_result("t1"),
            _bash("cargo llvm-cov", "t2"),
            _tool_result("t2"),
            _bash("cargo test", "t3"),
            _tool_result("t3"),
        )
        order, events = _detect_phases(
            trace, config,
            {"ordered": ["implementation", "testing"], "floating": []},
        )
        assert order == ["implementation", "testing"]
        # Implementation's expanded event list contains the early edit.
        assert min(events["implementation"]) == 0


class TestRepeatableExpansionRespectsEarlierPhaseTerritory:
    """Pass E2 must not pull events into a repeatable phase if those events
    sit at unified_pos values inside an earlier-ordered phase's candidate
    union, even when that earlier phase only retained a small chosen cluster.

    Regression: an inline `#[test]` write under src/ matches both
    `specification`'s test-content edit (content_match) and
    `implementation`'s src_edit. If impl absorbs it during expansion the
    activation timeline can open with `implementation` before
    `specification`, producing an illegal `implementation -> specification`
    transition."""

    def test_repeatable_expansion_respects_earlier_phase_candidate_territory(self):
        config = {
            "specification": {
                "position": "before_implementation",
                "legal_redirect_targets": ["implementation"],
                "required_commands": [
                    {
                        "id": "test_edit",
                        "signal": ["tool_call.file_write", "tool_call.file_edit"],
                        "match": "test",
                        "content_match": "#\\[test\\]|#\\[cfg\\(test\\)\\]|mod tests",
                    },
                    {
                        "id": "cargo_test",
                        "signal": "tool_call.execute_command",
                        "match": "cargo test",
                    },
                ],
            },
            "implementation": {
                "position": "any",
                "repeatable": True,
                "legal_redirect_targets": [],
                "required_commands": [
                    {
                        "id": "src_edit",
                        "signal": ["tool_call.file_edit", "tool_call.file_write", "tool_call.file_create"],
                        "match": "src/",
                        "not_match": "(?:^|/)tests?(?:/|\\b)",
                    },
                    {
                        "id": "cargo_test",
                        "signal": "tool_call.execute_command",
                        "match": "cargo test",
                    },
                ],
            },
        }
        # Trace layout (unified_pos in comments):
        #   user prompt (pos 0)
        #   1. Test edit at tests/ path -> spec test_edit cand (pos 2 after result)
        #   2. cargo test -> spec + impl cand (pos 4)
        #   3. Non-test src edit src/foo.rs -> impl src_edit cand (pos 6)
        #   4. cargo test (pos 8)
        #   5. Inline #[test] edit under src/foo.rs -> matches BOTH spec
        #      (content_match catches #[test]) AND impl src_edit (pos 10)
        #   6. cargo test (pos 12)
        # Expected: spec fires on (test edit, cargo test) cluster at the
        # front; impl's expansion does NOT include event 5 because that
        # unified_pos sits in specification's candidate union.
        trace = _simple_trace(
            _user_prompt("task"),
            _assistant_tool_use(
                "Write",
                {
                    "file_path": "tests/test_foo.rs",
                    "content": "#[test]\nfn t() { assert!(false); }",
                },
                "t1",
            ),
            _tool_result("t1"),
            _bash("cargo test", "t2"),
            _tool_result("t2"),
            _assistant_tool_use(
                "Write",
                {"file_path": "src/foo.rs", "content": "pub fn foo() -> i32 { 42 }"},
                "t3",
            ),
            _tool_result("t3"),
            _bash("cargo test", "t4"),
            _tool_result("t4"),
            _assistant_tool_use(
                "Edit",
                {
                    "file_path": "src/foo.rs",
                    "new_string": "#[cfg(test)]\nmod tests {\n    #[test]\n    fn t() {}\n}",
                },
                "t5",
            ),
            _tool_result("t5"),
            _bash("cargo test", "t6"),
            _tool_result("t6"),
        )
        classification = {"ordered": ["specification", "implementation"], "floating": []}
        order, events = _detect_phases(trace, config, classification)

        # Trace tool_call indices (0-based) for the 6 assistant tool uses:
        # 0 -> Write tests/test_foo.rs (t1)
        # 1 -> Bash cargo test (t2)
        # 2 -> Write src/foo.rs non-test (t3)
        # 3 -> Bash cargo test (t4)
        # 4 -> Edit src/foo.rs with #[test] (t5)
        # 5 -> Bash cargo test (t6)
        assert "specification" in events
        assert "implementation" in events
        # specification chose the two TDD anchors at the front.
        assert set(events["specification"]) == {0, 1}, (
            f"specification anchors expected to be the test edit + cargo test, "
            f"got events={events['specification']}"
        )
        # implementation MUST NOT include the inline #[test] write (index 4),
        # because its unified_pos sits in specification's candidate union.
        assert 4 not in events["implementation"], (
            f"implementation must not absorb the inline #[test] write at "
            f"call_index 4; got events={events['implementation']}"
        )

        timeline = _build_activation_timeline(events, config, classification)
        assert timeline[:2] == ["specification", "implementation"], (
            f"timeline must open with [specification, implementation, ...], "
            f"got {timeline}"
        )


class TestDocumentationR2:
    """The documentation phase requires BOTH a doc-file edit AND a `cargo
    doc` run; a lone `cargo doc` invocation must not satisfy R=2."""

    def _config(self):
        return {
            "documentation": {
                "position": "any",
                "required_commands": [
                    {
                        "id": "doc_edit",
                        "signal": ["tool_call.file_write", "tool_call.file_edit"],
                        "match": "README\\.md|/docs/|^docs/",
                        "content_match": "///|//!",
                    },
                    {"id": "cargo_doc", "signal": "tool_call.execute_command", "match": "cargo doc"},
                ],
            },
        }

    def test_cargo_doc_alone_does_not_fire(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo doc --no-deps", "t1"),
            _tool_result("t1"),
        )
        order, events = _detect_phases(
            trace, self._config(),
            {"ordered": ["documentation"], "floating": []},
        )
        assert "documentation" not in events

    def test_readme_edit_plus_cargo_doc_fires(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _edit("README.md", "t1"),
            _tool_result("t1"),
            _bash("cargo doc --no-deps", "t2"),
            _tool_result("t2"),
        )
        order, events = _detect_phases(
            trace, self._config(),
            {"ordered": ["documentation"], "floating": []},
        )
        assert "documentation" in events

    def test_rustdoc_content_edit_plus_cargo_doc_fires(self):
        # Content_match catches /// rustdoc comments even when the path is
        # not under docs/.
        trace = _simple_trace(
            _user_prompt("task"),
            _assistant_tool_use(
                "Edit",
                {"file_path": "src/lib.rs", "new_string": "/// docstring\npub fn foo() {}"},
                "t1",
            ),
            _tool_result("t1"),
            _bash("cargo doc", "t2"),
            _tool_result("t2"),
        )
        order, events = _detect_phases(
            trace, self._config(),
            {"ordered": ["documentation"], "floating": []},
        )
        assert "documentation" in events

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
    _TASK_COMPLETED,
    _path_index_map,
    _tool_indices,
    _tool_indices_matching,
    _primary_arg,
    _tool_content,
    _apply_operator,
    _evaluate_precedes_per_path,
    _CONTENT_OPERATORS,
    _ALL_METRIC_NAMES,
    _detect_phases,
    _count_impl_verify_cycles,
    _resolve_diff_files_changed,
    _resolve_diff_scope_permitted,
    _resolve_workspace_untracked,
    _resolve_git_committed_files,
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

    def test_unresolvable_metric_skips(self):
        """Phase metric without phase_tool_mapping in context is skipped."""
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
        assert result.passed is None
        assert "requires phase_tool_mapping" in result.skip_reason

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
                "condition": {
                    "metric": "phase.execution_order",
                    "operator": "strictly_ordered_subset",
                    "target": [],
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

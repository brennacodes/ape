"""Tests for eval_trace.py and eval tracing integration."""

import json
import sys
import os

import pytest

# Wire up imports
_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.join(_HERE, "..", "runner")
if _RUNNER not in sys.path:
    sys.path.insert(0, _RUNNER)

from eval_trace import EvalTrace
from evaluator import (
    CheckResult,
    evaluate_check,
    evaluate,
    _tool_indices_matching,
    _tool_indices_multi_matching,
    _detect_phases,
)
from trace import load_trace_from_string


# ---------------------------------------------------------------------------
# JSONL helpers (same pattern as test_evaluator.py)
# ---------------------------------------------------------------------------

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


def _tool_result(tool_id: str = "t1", content: str = "ok") -> str:
    return _ev("user", "user", [
        {"type": "tool_result", "tool_use_id": tool_id, "content": content}
    ])


def _assistant_text(text: str) -> str:
    return _ev("assistant", "assistant", [{"type": "text", "text": text}])


def _to_jsonl(*lines: str) -> str:
    return "\n".join(lines)


def _simple_trace(*events: str):
    return load_trace_from_string(_to_jsonl(*events))


def _bash(cmd: str, tid: str = "t1"):
    return _assistant_tool_use("Bash", {"command": cmd}, tid)


def _read(path: str, tid: str = "t1"):
    return _assistant_tool_use("Read", {"file_path": path}, tid)


def _write(path: str, tid: str = "t1"):
    return _assistant_tool_use("Write", {"file_path": path}, tid)


def _edit(path: str, tid: str = "t1"):
    return _assistant_tool_use("Edit", {"file_path": path}, tid)


def _grep(pattern: str, tid: str = "t1"):
    return _assistant_tool_use("Grep", {"pattern": pattern}, tid)


# ===========================================================================
# EvalTrace unit tests
# ===========================================================================

class TestEvalTrace:
    def test_empty_trace(self):
        et = EvalTrace()
        assert len(et) == 0
        assert et.to_list() == []
        # Always truthy even when empty
        assert bool(et) is True

    def test_log_entry(self):
        et = EvalTrace()
        et.log("my_func", "did_thing", value=42)
        entries = et.to_list()
        assert len(entries) == 1
        assert entries[0]["function"] == "my_func"
        assert entries[0]["action"] == "did_thing"
        assert entries[0]["value"] == 42
        assert "timestamp" in entries[0]

    def test_multiple_entries(self):
        et = EvalTrace()
        et.log("f1", "a1")
        et.log("f2", "a2", data="x")
        et.log("f3", "a3")
        assert len(et) == 3
        entries = et.to_list()
        assert entries[0]["function"] == "f1"
        assert entries[1]["function"] == "f2"
        assert entries[1]["data"] == "x"
        assert entries[2]["function"] == "f3"

    def test_to_list_returns_copy(self):
        et = EvalTrace()
        et.log("f", "a")
        list1 = et.to_list()
        list2 = et.to_list()
        assert list1 == list2
        assert list1 is not list2

    def test_arbitrary_data(self):
        et = EvalTrace()
        et.log("f", "a",
               raw_event={"type": "assistant", "message": {"content": []}},
               indices=[1, 2, 3],
               nested={"a": {"b": "c"}})
        entry = et.to_list()[0]
        assert entry["raw_event"]["type"] == "assistant"
        assert entry["indices"] == [1, 2, 3]
        assert entry["nested"]["a"]["b"] == "c"

    def test_timestamps_are_monotonic(self):
        et = EvalTrace()
        et.log("f", "a1")
        et.log("f", "a2")
        entries = et.to_list()
        assert entries[1]["timestamp"] >= entries[0]["timestamp"]


# ===========================================================================
# Eval tracing integration: evaluate_check produces eval_trace
# ===========================================================================

class TestEvalTraceIntegration:
    def test_evaluate_check_returns_eval_trace(self):
        """evaluate_check should attach eval_trace to the result."""
        trace = _simple_trace(
            _user_prompt("fix the bug"),
            _bash("cargo test", "t1"),
            _tool_result("t1", "test output"),
        )
        check = {
            "id": "test_check",
            "phase": "verification",
            "description": "Agent ran tests",
            "condition": {
                "metric": "tool_call.execute_command",
                "operator": "gte",
                "target": 1,
                "transform": "count",
            },
        }
        result = evaluate_check(check, trace, {})
        assert result.eval_trace is not None
        assert isinstance(result.eval_trace, list)
        assert len(result.eval_trace) > 0
        # Should have check_started and check_completed entries
        actions = [e["action"] for e in result.eval_trace]
        assert "check_started" in actions
        assert "check_completed" in actions

    def test_eval_trace_has_check_config(self):
        """check_started entry should contain the check config."""
        trace = _simple_trace(
            _user_prompt("task"),
            _read("/a.py", "t1"),
            _tool_result("t1"),
        )
        check = {
            "id": "reads_file",
            "phase": "investigation",
            "description": "Agent reads a file",
            "condition": {
                "metric": "tool_call.file_read",
                "operator": "gte",
                "target": 1,
                "transform": "count",
            },
        }
        result = evaluate_check(check, trace, {})
        started = [e for e in result.eval_trace if e["action"] == "check_started"]
        assert len(started) == 1
        assert started[0]["check_id"] == "reads_file"

    def test_eval_trace_contains_operator_entry(self):
        """Should log the operator application with metric/target values."""
        trace = _simple_trace(
            _user_prompt("fix it"),
            _bash("cargo test", "t1"),
            _tool_result("t1"),
            _bash("cargo build", "t2"),
            _tool_result("t2"),
        )
        check = {
            "id": "ran_enough_commands",
            "phase": "verification",
            "description": "Ran at least 2 bash commands",
            "condition": {
                "metric": "tool_call.execute_command",
                "operator": "gte",
                "target": 2,
                "transform": "count",
            },
        }
        result = evaluate_check(check, trace, {})
        op_entries = [e for e in result.eval_trace
                      if e["action"] == "applied_operator"]
        assert len(op_entries) == 1
        assert op_entries[0]["operator"] == "gte"
        assert op_entries[0]["passed"] is True

    def test_eval_trace_skipped_check(self):
        """Skipped checks should still get eval_trace with check_started and prompt_condition_skipped."""
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("echo hello", "t1"),
            _tool_result("t1"),
        )
        check = {
            "id": "conditional_check",
            "phase": "verification",
            "description": "Only when condition met",
            "prompt_condition": "nonexistent_condition",
            "condition": {
                "metric": "tool_call.execute_command",
                "operator": "gte",
                "target": 1,
                "transform": "count",
            },
        }
        result = evaluate_check(check, trace, {"conditions": {}})
        assert result.passed is None  # skipped
        assert result.eval_trace is not None
        actions = [e["action"] for e in result.eval_trace]
        assert "check_started" in actions
        assert "prompt_condition_skipped" in actions

    def test_evaluate_batch_has_traces(self):
        """evaluate() should produce eval_trace on each CheckResult."""
        trace = _simple_trace(
            _user_prompt("do stuff"),
            _bash("ls", "t1"),
            _tool_result("t1"),
        )
        checks = [
            {
                "id": "check_1",
                "phase": "investigation",
                "description": "Ran bash",
                "condition": {
                    "metric": "tool_call.execute_command",
                    "operator": "gte",
                    "target": 1,
                    "transform": "count",
                },
            },
            {
                "id": "check_2",
                "phase": "investigation",
                "description": "Ran bash again",
                "condition": {
                    "metric": "tool_call.execute_command",
                    "operator": "gte",
                    "target": 5,
                    "transform": "count",
                },
            },
        ]
        results = evaluate(trace, checks, {})
        for r in results:
            assert r.eval_trace is not None
            assert any(e["action"] == "check_started" for e in r.eval_trace)


# ===========================================================================
# Tracing through filtering functions
# ===========================================================================

class TestFilteringTracing:
    def test_tool_indices_matching_logs(self):
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("cargo test", "t1"),
            _tool_result("t1"),
            _bash("cargo build", "t2"),
            _tool_result("t2"),
            _bash("echo hello", "t3"),
            _tool_result("t3"),
        )
        et = EvalTrace()
        result = _tool_indices_matching(trace, "Bash", "cargo", eval_trace=et)
        entries = et.to_list()
        filter_entries = [e for e in entries if e["action"] == "filtered_tool_calls"]
        assert len(filter_entries) == 1
        entry = filter_entries[0]
        assert entry["tool_name"] == "Bash"
        assert entry["substring"] == "cargo"
        assert len(entry["matched"]) == 2
        assert len(entry["rejected"]) == 1

    def test_tool_indices_matching_none_trace(self):
        """Passing eval_trace=None should not raise."""
        trace = _simple_trace(
            _user_prompt("task"),
            _bash("ls", "t1"),
            _tool_result("t1"),
        )
        result = _tool_indices_matching(trace, "Bash", "ls", eval_trace=None)
        assert len(result) == 1

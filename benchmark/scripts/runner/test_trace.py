"""
Unit tests for trace.py.

Every function in trace.py is covered. Tests use inline JSONL fixtures —
no dependency on real session files.
"""

import json
import pytest
from pathlib import Path

from trace import (
    Trace,
    TraceEvent,
    ToolCall,
    ToolResult,
    TextBlock,
    load_trace,
    load_trace_from_string,
    parse_trace_jsonl,
    _parse_event,
    _merge_consecutive_events,
)


# ---------------------------------------------------------------------------
# JSONL fixture helpers
# ---------------------------------------------------------------------------

def _user_prompt(text: str, session_id: str = "test-session") -> dict:
    return {
        "type": "user",
        "sessionId": session_id,
        "parentUuid": None,
        "message": {"role": "user", "content": text},
    }


def _assistant_text(text: str, session_id: str = "test-session") -> dict:
    return {
        "type": "assistant",
        "sessionId": session_id,
        "parentUuid": "abc",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }


def _assistant_tool_use(name: str, tool_input: dict, tool_id: str = "toolu_001",
                         session_id: str = "test-session") -> dict:
    return {
        "type": "assistant",
        "sessionId": session_id,
        "parentUuid": "abc",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}],
        },
    }


def _assistant_parallel_tool_use(calls: list[tuple[str, dict]],
                                  session_id: str = "test-session") -> dict:
    """Multiple tool_use blocks in one message (parallel batch)."""
    content = [
        {"type": "tool_use", "id": f"toolu_{i:03d}", "name": name, "input": inp}
        for i, (name, inp) in enumerate(calls)
    ]
    return {
        "type": "assistant",
        "sessionId": session_id,
        "parentUuid": "abc",
        "message": {"role": "assistant", "content": content},
    }


def _tool_result(tool_use_id: str, content: str,
                 session_id: str = "test-session") -> dict:
    return {
        "type": "user",
        "sessionId": session_id,
        "parentUuid": "abc",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}],
        },
    }


def _queue_op(session_id: str = "test-session") -> dict:
    return {"type": "queue-operation", "operation": "dequeue", "sessionId": session_id}


def _to_jsonl(*objs) -> str:
    return "\n".join(json.dumps(o) for o in objs)


# ---------------------------------------------------------------------------
# _parse_event
# ---------------------------------------------------------------------------

class TestParseEvent:
    def test_parses_user_prompt_string(self):
        obj = _user_prompt("Fix the bug")
        event = _parse_event(obj, index=0)
        assert event.type == "user"
        assert event.index == 0
        assert len(event.text_blocks) == 1
        assert event.text_blocks[0].text == "Fix the bug"
        assert event.tool_calls == []
        assert event.tool_results == []

    def test_parses_assistant_text(self):
        obj = _assistant_text("I'll fix that now.")
        event = _parse_event(obj, index=1)
        assert event.type == "assistant"
        assert len(event.text_blocks) == 1
        assert event.text_blocks[0].text == "I'll fix that now."
        assert event.tool_calls == []

    def test_parses_assistant_tool_use(self):
        obj = _assistant_tool_use("Bash", {"command": "ls -la"}, tool_id="toolu_abc")
        event = _parse_event(obj, index=2)
        assert len(event.tool_calls) == 1
        tc = event.tool_calls[0]
        assert tc.name == "Bash"
        assert tc.input == {"command": "ls -la"}
        assert tc.tool_use_id == "toolu_abc"
        assert tc.event_index == 2

    def test_parses_tool_result(self):
        obj = _tool_result("toolu_abc", "file1.txt\nfile2.txt")
        event = _parse_event(obj, index=3)
        assert len(event.tool_results) == 1
        tr = event.tool_results[0]
        assert tr.tool_use_id == "toolu_abc"
        assert tr.content == "file1.txt\nfile2.txt"
        assert tr.event_index == 3

    def test_parses_parallel_tool_use(self):
        obj = _assistant_parallel_tool_use([
            ("Read", {"file_path": "/src/a.py"}),
            ("Read", {"file_path": "/src/b.py"}),
        ])
        event = _parse_event(obj, index=0)
        assert len(event.tool_calls) == 2
        assert event.tool_calls[0].name == "Read"
        assert event.tool_calls[1].name == "Read"

    def test_tool_result_with_list_content(self):
        """tool_result content can be a list of text blocks — should be joined."""
        obj = {
            "type": "user",
            "sessionId": "s",
            "parentUuid": "p",
            "message": {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu_x",
                    "content": [{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}],
                }],
            },
        }
        event = _parse_event(obj, index=0)
        assert "line1" in event.tool_results[0].content
        assert "line2" in event.tool_results[0].content

    def test_preserves_raw_object(self):
        obj = _user_prompt("hello")
        event = _parse_event(obj, index=0)
        assert event.raw is obj


# ---------------------------------------------------------------------------
# TraceEvent properties
# ---------------------------------------------------------------------------

class TestTraceEventProperties:
    def test_is_parallel_batch_false_for_single_call(self):
        obj = _assistant_tool_use("Bash", {"command": "echo hi"})
        event = _parse_event(obj, index=0)
        assert event.is_parallel_batch is False

    def test_is_parallel_batch_true_for_multiple_calls(self):
        obj = _assistant_parallel_tool_use([
            ("Read", {"file_path": "/a.py"}),
            ("Read", {"file_path": "/b.py"}),
        ])
        event = _parse_event(obj, index=0)
        assert event.is_parallel_batch is True

    def test_is_tool_use(self):
        obj = _assistant_tool_use("Bash", {"command": "ls"})
        event = _parse_event(obj, index=0)
        assert event.is_tool_use is True
        assert event.is_tool_result is False
        assert event.is_text is False

    def test_is_tool_result(self):
        obj = _tool_result("toolu_x", "output")
        event = _parse_event(obj, index=0)
        assert event.is_tool_result is True
        assert event.is_tool_use is False

    def test_is_text(self):
        obj = _assistant_text("hello")
        event = _parse_event(obj, index=0)
        assert event.is_text is True
        assert event.is_tool_use is False


# ---------------------------------------------------------------------------
# load_trace_from_string
# ---------------------------------------------------------------------------

class TestLoadTraceFromString:
    def test_loads_session_id(self):
        jsonl = _to_jsonl(_user_prompt("hi", session_id="sess-123"))
        trace = load_trace_from_string(jsonl)
        assert trace.session_id == "sess-123"

    def test_skips_queue_operations(self):
        jsonl = _to_jsonl(
            _queue_op(),
            _user_prompt("do something"),
        )
        trace = load_trace_from_string(jsonl)
        assert len(trace.events) == 1
        assert trace.events[0].type == "user"

    def test_skips_blank_lines(self):
        jsonl = json.dumps(_user_prompt("hello")) + "\n\n" + json.dumps(_assistant_text("hi"))
        trace = load_trace_from_string(jsonl)
        assert len(trace.events) == 2

    def test_events_have_sequential_indices(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "ls"}),
            _tool_result("toolu_001", "file.txt"),
        )
        trace = load_trace_from_string(jsonl)
        assert [ev.index for ev in trace.events] == [0, 1, 2]

    def test_raises_on_empty_jsonl(self):
        with pytest.raises(ValueError, match="No message events"):
            load_trace_from_string(_to_jsonl(_queue_op()))

    def test_raises_on_blank_string(self):
        with pytest.raises(ValueError):
            load_trace_from_string("")


# ---------------------------------------------------------------------------
# load_trace (file-based)
# ---------------------------------------------------------------------------

class TestLoadTrace:
    def test_loads_from_file(self, tmp_path):
        p = tmp_path / "session.jsonl"
        p.write_text(_to_jsonl(_user_prompt("hi", session_id="file-sess")))
        trace = load_trace(p)
        assert trace.session_id == "file-sess"
        assert len(trace.events) == 1


# ---------------------------------------------------------------------------
# Trace.all_tool_calls
# ---------------------------------------------------------------------------

class TestAllToolCalls:
    def _make_trace(self) -> Trace:
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Read",  {"file_path": "/src/a.py"}, tool_id="t1"),
            _tool_result("t1", "content a"),
            _assistant_tool_use("Bash",  {"command": "ls"},           tool_id="t2"),
            _tool_result("t2", "a.py"),
            _assistant_tool_use("Write", {"file_path": "/src/b.py", "content": "x"}, tool_id="t3"),
            _tool_result("t3", ""),
        )
        return load_trace_from_string(jsonl)

    def test_returns_all_calls_in_order(self):
        trace = self._make_trace()
        calls = trace.all_tool_calls()
        assert [tc.name for tc in calls] == ["Read", "Bash", "Write"]

    def test_filters_by_name(self):
        trace = self._make_trace()
        reads = trace.all_tool_calls("Read")
        assert len(reads) == 1
        assert reads[0].name == "Read"

    def test_returns_empty_when_tool_not_used(self):
        trace = self._make_trace()
        assert trace.all_tool_calls("Edit") == []


# ---------------------------------------------------------------------------
# Trace.parallel_batches
# ---------------------------------------------------------------------------

class TestParallelBatches:
    def test_returns_only_multi_call_messages(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "ls"}),          # single — excluded
            _tool_result("toolu_000", "out"),
            _assistant_parallel_tool_use([
                ("Read", {"file_path": "/a.py"}),
                ("Read", {"file_path": "/b.py"}),
            ]),
        )
        trace = load_trace_from_string(jsonl)
        batches = trace.parallel_batches()
        assert len(batches) == 1
        assert len(batches[0]) == 2

    def test_returns_empty_when_no_parallel_calls(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "ls"}),
        )
        trace = load_trace_from_string(jsonl)
        assert trace.parallel_batches() == []


# ---------------------------------------------------------------------------
# Trace.bash_commands / bash_commands_matching / any_bash_command_matches
# ---------------------------------------------------------------------------

class TestBashCommands:
    def _make_trace(self) -> Trace:
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "git status"},         tool_id="t1"),
            _tool_result("t1", "clean"),
            _assistant_tool_use("Bash", {"command": "git add src/main.py"}, tool_id="t2"),
            _tool_result("t2", ""),
            _assistant_tool_use("Bash", {"command": "git commit -m 'fix'"}, tool_id="t3"),
            _tool_result("t3", ""),
        )
        return load_trace_from_string(jsonl)

    def test_bash_commands_returns_all_in_order(self):
        trace = self._make_trace()
        assert trace.bash_commands() == [
            "git status",
            "git add src/main.py",
            "git commit -m 'fix'",
        ]

    def test_bash_commands_matching_filters_by_substring(self):
        trace = self._make_trace()
        assert trace.bash_commands_matching("git add") == ["git add src/main.py"]

    def test_any_bash_command_matches_true(self):
        trace = self._make_trace()
        assert trace.any_bash_command_matches("git add .") is False
        assert trace.any_bash_command_matches("git commit") is True

    def test_any_bash_command_matches_false_when_no_match(self):
        trace = self._make_trace()
        assert trace.any_bash_command_matches("npm test") is False

    def test_bash_commands_empty_when_no_bash_calls(self):
        jsonl = _to_jsonl(_user_prompt("task"), _assistant_text("done"))
        trace = load_trace_from_string(jsonl)
        assert trace.bash_commands() == []


# ---------------------------------------------------------------------------
# Trace file path queries
# ---------------------------------------------------------------------------

class TestFilePathQueries:
    def _make_trace(self) -> Trace:
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Read",  {"file_path": "/src/a.py"}, tool_id="t1"),
            _tool_result("t1", "content"),
            _assistant_tool_use("Write", {"file_path": "/src/b.py", "content": "x"}, tool_id="t2"),
            _tool_result("t2", ""),
            _assistant_tool_use("Edit",  {"file_path": "/src/a.py", "old_string": "x", "new_string": "y"}, tool_id="t3"),
            _tool_result("t3", ""),
        )
        return load_trace_from_string(jsonl)

    def test_file_paths_read(self):
        trace = self._make_trace()
        assert trace.file_paths_read() == ["/src/a.py"]

    def test_file_paths_written(self):
        trace = self._make_trace()
        assert trace.file_paths_written() == ["/src/b.py"]

    def test_file_paths_edited(self):
        trace = self._make_trace()
        assert trace.file_paths_edited() == ["/src/a.py"]

    def test_all_file_paths_modified_combines_write_and_edit(self):
        trace = self._make_trace()
        assert trace.all_file_paths_modified() == ["/src/b.py", "/src/a.py"]


# ---------------------------------------------------------------------------
# Trace ordering queries
# ---------------------------------------------------------------------------

class TestOrderingQueries:
    def test_first_event_index_for_tool_returns_correct_index(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Read",  {"file_path": "/a.py"}, tool_id="t1"),
            _tool_result("t1", ""),
            _assistant_tool_use("Write", {"file_path": "/a.py", "content": "x"}, tool_id="t2"),
            _tool_result("t2", ""),
        )
        trace = load_trace_from_string(jsonl)
        assert trace.first_event_index_for_tool("Read") == 1
        assert trace.first_event_index_for_tool("Write") == 3

    def test_first_event_index_for_tool_returns_none_when_not_called(self):
        jsonl = _to_jsonl(_user_prompt("task"), _assistant_text("done"))
        trace = load_trace_from_string(jsonl)
        assert trace.first_event_index_for_tool("Bash") is None

    def test_tool_called_before_true(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Read",  {"file_path": "/a.py"}, tool_id="t1"),
            _tool_result("t1", ""),
            _assistant_tool_use("Write", {"file_path": "/a.py", "content": "x"}, tool_id="t2"),
            _tool_result("t2", ""),
        )
        trace = load_trace_from_string(jsonl)
        assert trace.tool_called_before("Read", "Write") is True

    def test_tool_called_before_false_when_order_reversed(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Write", {"file_path": "/a.py", "content": "x"}, tool_id="t1"),
            _tool_result("t1", ""),
            _assistant_tool_use("Read",  {"file_path": "/a.py"}, tool_id="t2"),
            _tool_result("t2", ""),
        )
        trace = load_trace_from_string(jsonl)
        assert trace.tool_called_before("Read", "Write") is False

    def test_tool_called_before_false_when_tool_absent(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Read", {"file_path": "/a.py"}, tool_id="t1"),
        )
        trace = load_trace_from_string(jsonl)
        assert trace.tool_called_before("Read", "Bash") is False

    def test_read_before_write_per_path_true(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Read",  {"file_path": "/a.py"}, tool_id="t1"),
            _tool_result("t1", ""),
            _assistant_tool_use("Write", {"file_path": "/a.py", "content": "x"}, tool_id="t2"),
            _tool_result("t2", ""),
        )
        trace = load_trace_from_string(jsonl)
        assert trace.read_before_write_per_path() is True

    def test_read_before_write_per_path_false_when_no_prior_read(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Write", {"file_path": "/a.py", "content": "x"}, tool_id="t1"),
            _tool_result("t1", ""),
        )
        trace = load_trace_from_string(jsonl)
        assert trace.read_before_write_per_path() is False

    def test_read_before_write_per_path_true_when_nothing_written(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Read", {"file_path": "/a.py"}, tool_id="t1"),
            _tool_result("t1", "content"),
        )
        trace = load_trace_from_string(jsonl)
        assert trace.read_before_write_per_path() is True


# ---------------------------------------------------------------------------
# Trace inter-batch and batch count queries
# ---------------------------------------------------------------------------

class TestInterBatchQueries:
    def test_has_text_between_tool_batches_true(self):
        # Text must be in a separate turn (not adjacent to another assistant event)
        # to remain distinct after merge. A user event between the text and
        # the next tool call keeps them in separate turns.
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Read", {"file_path": "/a.py"}, tool_id="t1"),
            _tool_result("t1", "content"),
            _assistant_text("I see the file."),
            _user_prompt("OK, please fix it"),
            _assistant_tool_use("Write", {"file_path": "/a.py", "content": "x"}, tool_id="t2"),
            _tool_result("t2", ""),
        )
        trace = load_trace_from_string(jsonl)
        assert trace.has_text_between_tool_batches() is True

    def test_has_text_between_tool_batches_false_when_no_interstitial_text(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Read",  {"file_path": "/a.py"}, tool_id="t1"),
            _tool_result("t1", "content"),
            _assistant_tool_use("Write", {"file_path": "/a.py", "content": "x"}, tool_id="t2"),
            _tool_result("t2", ""),
        )
        trace = load_trace_from_string(jsonl)
        assert trace.has_text_between_tool_batches() is False

    def test_has_text_between_tool_batches_false_with_only_text(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_text("Just text, no tools."),
        )
        trace = load_trace_from_string(jsonl)
        assert trace.has_text_between_tool_batches() is False

    def test_tool_call_batch_count(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Read",  {"file_path": "/a.py"}, tool_id="t1"),
            _tool_result("t1", ""),
            _assistant_text("thinking"),
            _assistant_tool_use("Write", {"file_path": "/a.py", "content": "x"}, tool_id="t2"),
            _tool_result("t2", ""),
        )
        trace = load_trace_from_string(jsonl)
        assert trace.tool_call_batch_count() == 2

    def test_tool_call_batch_count_zero_when_no_tools(self):
        jsonl = _to_jsonl(_user_prompt("task"), _assistant_text("done"))
        trace = load_trace_from_string(jsonl)
        assert trace.tool_call_batch_count() == 0


# ---------------------------------------------------------------------------
# Thinking block parsing
# ---------------------------------------------------------------------------

class TestThinkingBlocks:
    def test_thinking_block_becomes_text_block(self):
        obj = {
            "type": "assistant",
            "sessionId": "test-session",
            "parentUuid": "abc",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "Let me reason about this..."}],
            },
        }
        event = _parse_event(obj, index=0)
        assert len(event.text_blocks) == 1
        assert event.text_blocks[0].text == "Let me reason about this..."

    def test_thinking_and_text_combined(self):
        obj = {
            "type": "assistant",
            "sessionId": "test-session",
            "parentUuid": "abc",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "thinking..."},
                    {"type": "text", "text": "Here's my answer."},
                ],
            },
        }
        event = _parse_event(obj, index=0)
        assert len(event.text_blocks) == 2
        assert event.text_blocks[0].text == "thinking..."
        assert event.text_blocks[1].text == "Here's my answer."


# ---------------------------------------------------------------------------
# _merge_consecutive_events
# ---------------------------------------------------------------------------

class TestMergeConsecutiveEvents:
    def test_merges_consecutive_assistant_events(self):
        """Three consecutive assistant lines → one merged event."""
        events = [
            _parse_event(_assistant_text("thinking..."), index=0),
            _parse_event(_assistant_tool_use("Glob", {"pattern": "*.py"}, tool_id="t1"), index=1),
            _parse_event(_assistant_tool_use("Glob", {"pattern": "*.js"}, tool_id="t2"), index=2),
        ]
        merged = _merge_consecutive_events(events)
        assert len(merged) == 1
        assert merged[0].type == "assistant"
        assert len(merged[0].text_blocks) == 1
        assert len(merged[0].tool_calls) == 2

    def test_parallel_batch_detected_after_merge(self):
        """Stream-json splits should become a parallel batch after merging."""
        events = [
            _parse_event(_user_prompt("task"), index=0),
            _parse_event(_assistant_text("Let me search."), index=1),
            _parse_event(_assistant_tool_use("Glob", {"pattern": "*.py"}, tool_id="t1"), index=2),
            _parse_event(_assistant_tool_use("Glob", {"pattern": "*.js"}, tool_id="t2"), index=3),
            _parse_event(_assistant_tool_use("Grep", {"pattern": "def main"}, tool_id="t3"), index=4),
        ]
        merged = _merge_consecutive_events(events)
        # user(0), then assistant(1-4) merged into one
        assert len(merged) == 2
        assistant_ev = merged[1]
        assert assistant_ev.is_parallel_batch is True
        assert len(assistant_ev.tool_calls) == 3

    def test_non_message_type_breaks_merge(self):
        """A non-mergeable event type breaks the consecutive sequence."""
        events = [
            _parse_event(_assistant_text("before"), index=0),
            TraceEvent(index=1, type="system", tool_calls=[], tool_results=[], text_blocks=[], raw={}),
            _parse_event(_assistant_text("after"), index=2),
        ]
        merged = _merge_consecutive_events(events)
        assert len(merged) == 3
        assert merged[0].type == "assistant"
        assert merged[1].type == "system"
        assert merged[2].type == "assistant"

    def test_user_events_merge_separately(self):
        """Consecutive user events merge together but not with assistant events."""
        events = [
            _parse_event(_assistant_text("response"), index=0),
            _parse_event(_tool_result("t1", "result1"), index=1),
            _parse_event(_tool_result("t2", "result2"), index=2),
        ]
        merged = _merge_consecutive_events(events)
        assert len(merged) == 2
        assert merged[0].type == "assistant"
        assert merged[1].type == "user"
        assert len(merged[1].tool_results) == 2

    def test_idempotent_on_well_formed_traces(self):
        """Merge is a no-op when no consecutive same-type events exist."""
        events = [
            _parse_event(_user_prompt("task"), index=0),
            _parse_event(_assistant_tool_use("Bash", {"command": "ls"}), index=1),
            _parse_event(_tool_result("toolu_001", "output"), index=2),
            _parse_event(_assistant_text("done"), index=3),
        ]
        merged = _merge_consecutive_events(events)
        assert len(merged) == 4
        assert [ev.index for ev in merged] == [0, 1, 2, 3]

    def test_reindexes_correctly(self):
        """After merge, events are sequentially indexed starting at 0."""
        events = [
            _parse_event(_user_prompt("task"), index=0),
            _parse_event(_assistant_text("thinking"), index=1),
            _parse_event(_assistant_tool_use("Glob", {"pattern": "*.py"}, tool_id="t1"), index=2),
            _parse_event(_assistant_tool_use("Glob", {"pattern": "*.js"}, tool_id="t2"), index=3),
            _parse_event(_tool_result("t1", "a.py"), index=4),
        ]
        merged = _merge_consecutive_events(events)
        assert [ev.index for ev in merged] == [0, 1, 2]
        # Check child objects have correct event_index
        assert all(tc.event_index == 1 for tc in merged[1].tool_calls)
        assert all(tb.event_index == 1 for tb in merged[1].text_blocks)

    def test_empty_list(self):
        assert _merge_consecutive_events([]) == []

    def test_stream_json_via_parse_trace_jsonl(self):
        """parse_trace_jsonl applies merge, so split lines yield parallel batch."""
        text = "\n".join([
            json.dumps(_user_prompt("task")),
            json.dumps(_assistant_text("Looking...")),
            json.dumps(_assistant_tool_use("Glob", {"pattern": "*.py"}, tool_id="t1")),
            json.dumps(_assistant_tool_use("Glob", {"pattern": "*.js"}, tool_id="t2")),
        ])
        trace = parse_trace_jsonl(text)
        assert any(ev.is_parallel_batch for ev in trace.events)

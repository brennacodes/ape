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
    _build_raw_event_maps,
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


# ---------------------------------------------------------------------------
# Trace.result_for_tool_call
# ---------------------------------------------------------------------------

class TestResultForToolCall:
    def test_finds_matching_result(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "ls"}, tool_id="t123"),
            _tool_result("t123", "file1.txt\nfile2.txt"),
        )
        trace = load_trace_from_string(jsonl)
        tc = trace.all_tool_calls("Bash")[0]
        result = trace.result_for_tool_call(tc)
        assert result is not None
        assert result.tool_use_id == "t123"
        assert "file1.txt" in result.content

    def test_returns_none_when_no_match(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "ls"}, tool_id="t123"),
        )
        trace = load_trace_from_string(jsonl)
        tc = trace.all_tool_calls("Bash")[0]
        result = trace.result_for_tool_call(tc)
        assert result is None

    def test_returns_correct_result_among_many(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "ls"}, tool_id="t1"),
            _assistant_tool_use("Bash", {"command": "pwd"}, tool_id="t2"),
            _tool_result("t1", "output1"),
            _tool_result("t2", "output2"),
        )
        trace = load_trace_from_string(jsonl)
        tc2 = [tc for tc in trace.all_tool_calls("Bash") if tc.tool_use_id == "t2"][0]
        result = trace.result_for_tool_call(tc2)
        assert result is not None
        assert result.content == "output2"


# ---------------------------------------------------------------------------
# Trace.all_tool_results
# ---------------------------------------------------------------------------

class TestAllToolResults:
    def test_returns_all_results_in_order(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "ls"}, tool_id="t1"),
            _tool_result("t1", "output1"),
            _assistant_tool_use("Bash", {"command": "pwd"}, tool_id="t2"),
            _tool_result("t2", "output2"),
        )
        trace = load_trace_from_string(jsonl)
        results = trace.all_tool_results()
        assert len(results) == 2
        assert results[0].content == "output1"
        assert results[1].content == "output2"

    def test_returns_empty_when_no_results(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_text("done"),
        )
        trace = load_trace_from_string(jsonl)
        assert trace.all_tool_results() == []


# ---------------------------------------------------------------------------
# Trace.bash_exit_code
# ---------------------------------------------------------------------------

class TestBashExitCode:
    def test_extracts_explicit_exit_code_from_content(self):
        """Extract exit code from result content containing 'Exit code: N'."""
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "ls /nonexistent"}, tool_id="t1"),
            _tool_result("t1", "ls: cannot access '/nonexistent': No such file or directory\nExit code: 2"),
        )
        trace = load_trace_from_string(jsonl)
        tc = trace.all_tool_calls("Bash")[0]
        exit_code = trace.bash_exit_code(tc)
        assert exit_code == 2

    def test_extracts_exit_code_lowercase(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "false"}, tool_id="t1"),
            _tool_result("t1", "output\nexit code 1"),
        )
        trace = load_trace_from_string(jsonl)
        tc = trace.all_tool_calls("Bash")[0]
        exit_code = trace.bash_exit_code(tc)
        assert exit_code == 1

    def test_assumes_zero_when_content_present_no_error(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "echo hello"}, tool_id="t1"),
            _tool_result("t1", "hello"),
        )
        trace = load_trace_from_string(jsonl)
        tc = trace.all_tool_calls("Bash")[0]
        exit_code = trace.bash_exit_code(tc)
        assert exit_code == 0

    def test_returns_none_when_no_result(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "ls"}, tool_id="t1"),
        )
        trace = load_trace_from_string(jsonl)
        tc = trace.all_tool_calls("Bash")[0]
        exit_code = trace.bash_exit_code(tc)
        assert exit_code is None


# ---------------------------------------------------------------------------
# Trace.bash_commands_with_results
# ---------------------------------------------------------------------------

class TestBashCommandsWithResults:
    def test_returns_commands_with_output_and_exit_codes(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "echo test"}, tool_id="t1"),
            _tool_result("t1", "test"),
            _assistant_tool_use("Bash", {"command": "false"}, tool_id="t2"),
            _tool_result("t2", "Exit code: 1"),
        )
        trace = load_trace_from_string(jsonl)
        results = trace.bash_commands_with_results()
        assert len(results) == 2
        assert results[0]['command'] == "echo test"
        assert results[0]['output'] == "test"
        assert results[0]['exit_code'] == 0
        assert results[0]['succeeded'] is True
        assert results[1]['command'] == "false"
        assert results[1]['exit_code'] == 1
        assert results[1]['succeeded'] is False

    def test_includes_event_index(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "ls"}, tool_id="t1"),
            _tool_result("t1", "files"),
        )
        trace = load_trace_from_string(jsonl)
        results = trace.bash_commands_with_results()
        assert results[0]['event_index'] == 1  # after user prompt


# ---------------------------------------------------------------------------
# Trace.cargo_test_results
# ---------------------------------------------------------------------------

class TestCargoTestResults:
    def test_parses_passing_tests(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "cargo test"}, tool_id="t1"),
            _tool_result("t1", "test result: ok. 5 passed; 0 failed; 0 ignored"),
        )
        trace = load_trace_from_string(jsonl)
        results = trace.cargo_test_results()
        assert len(results) == 1
        assert results[0]['passed'] is True
        assert results[0]['test_count'] == 5
        assert results[0]['failed_count'] == 0

    def test_parses_failing_tests(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "cargo test"}, tool_id="t1"),
            _tool_result("t1", "test result: FAILED. 3 passed; 2 failed; 0 ignored"),
        )
        trace = load_trace_from_string(jsonl)
        results = trace.cargo_test_results()
        assert len(results) == 1
        assert results[0]['passed'] is False
        assert results[0]['test_count'] == 3
        assert results[0]['failed_count'] == 2

    def test_ignores_non_cargo_test_commands(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "echo test"}, tool_id="t1"),
            _tool_result("t1", "test"),
        )
        trace = load_trace_from_string(jsonl)
        results = trace.cargo_test_results()
        assert len(results) == 0

    def test_returns_empty_when_no_cargo_test(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "cargo build"}, tool_id="t1"),
            _tool_result("t1", "Finished"),
        )
        trace = load_trace_from_string(jsonl)
        results = trace.cargo_test_results()
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Trace.cargo_clippy_results
# ---------------------------------------------------------------------------

class TestCargoClippyResults:
    def test_detects_warnings(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "cargo clippy"}, tool_id="t1"),
            _tool_result("t1", "warning: unused variable\nwarning: dead code"),
        )
        trace = load_trace_from_string(jsonl)
        results = trace.cargo_clippy_results()
        assert len(results) == 1
        assert results[0]['has_warnings'] is True
        assert results[0]['warning_count'] == 2

    def test_no_warnings(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "cargo clippy"}, tool_id="t1"),
            _tool_result("t1", "Finished"),
        )
        trace = load_trace_from_string(jsonl)
        results = trace.cargo_clippy_results()
        assert len(results) == 1
        assert results[0]['has_warnings'] is False

    def test_ignores_non_clippy_commands(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "cargo build"}, tool_id="t1"),
            _tool_result("t1", "Finished"),
        )
        trace = load_trace_from_string(jsonl)
        results = trace.cargo_clippy_results()
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Trace.cargo_build_results
# ---------------------------------------------------------------------------

class TestCargoBuildResults:
    def test_includes_build_commands_with_success(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "cargo build"}, tool_id="t1"),
            _tool_result("t1", "Finished debug"),
            _assistant_tool_use("Bash", {"command": "cargo build --release"}, tool_id="t2"),
            _tool_result("t2", "Exit code: 1"),
        )
        trace = load_trace_from_string(jsonl)
        results = trace.cargo_build_results()
        assert len(results) == 2
        assert results[0]['command'] == "cargo build"
        assert results[0]['succeeded'] is True
        assert results[1]['command'] == "cargo build --release"
        assert results[1]['succeeded'] is False

    def test_ignores_non_build_commands(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "ls"}, tool_id="t1"),
            _tool_result("t1", "files"),
        )
        trace = load_trace_from_string(jsonl)
        results = trace.cargo_build_results()
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Trace.cargo_llvm_cov_results
# ---------------------------------------------------------------------------

class TestCargoLlvmCovResults:
    def test_extracts_coverage_percentage(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "cargo llvm-cov"}, tool_id="t1"),
            _tool_result("t1", "Total coverage: 85.50%"),
        )
        trace = load_trace_from_string(jsonl)
        results = trace.cargo_llvm_cov_results()
        assert len(results) == 1
        assert results[0]['coverage_percentage'] == 85.50

    def test_handles_llvm_cov_variations(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "llvm-cov report"}, tool_id="t1"),
            _tool_result("t1", "95.0%"),
        )
        trace = load_trace_from_string(jsonl)
        results = trace.cargo_llvm_cov_results()
        assert len(results) == 1
        assert results[0]['coverage_percentage'] == 95.0

    def test_returns_none_when_no_percentage_found(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "cargo llvm-cov"}, tool_id="t1"),
            _tool_result("t1", "generating coverage..."),
        )
        trace = load_trace_from_string(jsonl)
        results = trace.cargo_llvm_cov_results()
        assert len(results) == 1
        assert results[0]['coverage_percentage'] is None


# ---------------------------------------------------------------------------
# Trace.git_commit_messages
# ---------------------------------------------------------------------------

class TestGitCommitMessages:
    def test_extracts_simple_commit_message(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": 'git commit -m "Fix bug"'}, tool_id="t1"),
            _tool_result("t1", "1 file changed"),
        )
        trace = load_trace_from_string(jsonl)
        commits = trace.git_commit_messages()
        assert len(commits) == 1
        assert commits[0]['subject'] == "Fix bug"
        assert commits[0]['body'] is None

    def test_extracts_multiline_commit_message(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash",
                {"command": 'git commit -m "Fix bug\n\nThis fixes issue #123"'},
                tool_id="t1"),
            _tool_result("t1", "1 file changed"),
        )
        trace = load_trace_from_string(jsonl)
        commits = trace.git_commit_messages()
        assert len(commits) == 1
        assert commits[0]['subject'] == "Fix bug"
        assert "issue #123" in commits[0]['body']

    def test_ignores_non_commit_commands(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "git status"}, tool_id="t1"),
            _tool_result("t1", "clean"),
        )
        trace = load_trace_from_string(jsonl)
        commits = trace.git_commit_messages()
        assert len(commits) == 0

    def test_returns_empty_when_no_commit_messages(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "git log"}, tool_id="t1"),
            _tool_result("t1", "commits"),
        )
        trace = load_trace_from_string(jsonl)
        commits = trace.git_commit_messages()
        assert len(commits) == 0

    def test_extracts_heredoc_commit_message(self):
        """Heredoc pattern must be checked before simple-quoted pattern.

        Without this, the regex for simple quotes matches the opening
        ``"$(cat <<'EOF'`` as a quoted string and captures ``$(cat <<``
        as the commit message subject (bug 5b in the audit).
        """
        heredoc_cmd = (
            "git commit -m \"$(cat <<'EOF'\n"
            "Reject unknown fields in config instead of silently defaulting\n"
            "\n"
            "The previous implementation silently fell back to defaults when\n"
            "unrecognized fields appeared in config files.\n"
            "EOF\n"
            ")\""
        )
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": heredoc_cmd}, tool_id="t1"),
            _tool_result("t1", "1 file changed"),
        )
        trace = load_trace_from_string(jsonl)
        commits = trace.git_commit_messages()
        assert len(commits) == 1
        assert commits[0]['subject'] == "Reject unknown fields in config instead of silently defaulting"
        assert "silently fell back" in commits[0]['body']


# ---------------------------------------------------------------------------
# Trace.command_failed_at
# ---------------------------------------------------------------------------

class TestCommandFailedAt:
    def test_finds_failed_commands_matching_pattern(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "cargo test"}, tool_id="t1"),
            _tool_result("t1", "Exit code: 0"),
            _assistant_tool_use("Bash", {"command": "cargo build"}, tool_id="t2"),
            _tool_result("t2", "Exit code: 1"),
            _assistant_tool_use("Bash", {"command": "cargo test again"}, tool_id="t3"),
            _tool_result("t3", "Exit code: 1"),
        )
        trace = load_trace_from_string(jsonl)
        failed_indices = trace.command_failed_at("cargo test")
        assert len(failed_indices) == 1
        # The second "cargo test again" command is at event index 5
        assert failed_indices[0] == 5

    def test_returns_empty_when_no_failures(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "cargo test"}, tool_id="t1"),
            _tool_result("t1", "passed"),
        )
        trace = load_trace_from_string(jsonl)
        failed_indices = trace.command_failed_at("cargo test")
        assert failed_indices == []

    def test_returns_empty_when_pattern_not_found(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "npm test"}, tool_id="t1"),
            _tool_result("t1", "Exit code: 1"),
        )
        trace = load_trace_from_string(jsonl)
        failed_indices = trace.command_failed_at("cargo test")
        assert failed_indices == []


# ---------------------------------------------------------------------------
# Trace.file_modifications_after_event
# ---------------------------------------------------------------------------

class TestFileModificationsAfterEvent:
    def test_returns_write_and_edit_after_index(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Read", {"file_path": "/a.py"}, tool_id="t1"),
            _tool_result("t1", "content"),
            _assistant_tool_use("Write", {"file_path": "/b.py", "content": "x"}, tool_id="t2"),
            _tool_result("t2", ""),
            _assistant_tool_use("Edit", {"file_path": "/a.py", "old_string": "x", "new_string": "y"}, tool_id="t3"),
            _tool_result("t3", ""),
        )
        trace = load_trace_from_string(jsonl)
        # After event index 1 (Read)
        mods = trace.file_modifications_after_event(1)
        assert len(mods) == 2
        assert mods[0]['path'] == "/b.py"
        assert mods[0]['tool'] == "Write"
        assert mods[1]['path'] == "/a.py"
        assert mods[1]['tool'] == "Edit"

    def test_returns_empty_when_nothing_after_index(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Write", {"file_path": "/a.py", "content": "x"}, tool_id="t1"),
            _tool_result("t1", ""),
        )
        trace = load_trace_from_string(jsonl)
        mods = trace.file_modifications_after_event(1)
        assert mods == []

    def test_excludes_modifications_at_or_before_index(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Write", {"file_path": "/a.py", "content": "x"}, tool_id="t1"),
            _tool_result("t1", ""),
            _assistant_tool_use("Edit", {"file_path": "/a.py", "old_string": "x", "new_string": "y"}, tool_id="t2"),
            _tool_result("t2", ""),
        )
        trace = load_trace_from_string(jsonl)
        # After event 1 should only include event 2's modifications
        mods = trace.file_modifications_after_event(1)
        assert len(mods) == 1
        assert mods[0]['tool'] == "Edit"


# ===========================================================================
# Raw event maps (pre-merge preservation for eval tracing)
# ===========================================================================

class TestBuildRawEventMaps:
    """Tests for _build_raw_event_maps and Trace.raw_event_pair."""

    def test_single_tool_use_and_result(self):
        jsonl = _to_jsonl(
            _user_prompt("do something"),
            _assistant_tool_use("Bash", {"command": "ls"}, tool_id="t1"),
            _tool_result("t1", "file.txt"),
        )
        trace = load_trace_from_string(jsonl)
        assert "t1" in trace.raw_tool_use_events
        assert "t1" in trace.raw_tool_result_events
        # The raw event should be the full dict from stream.json
        raw_use = trace.raw_tool_use_events["t1"]
        assert raw_use["type"] == "assistant"
        assert any(
            b.get("id") == "t1" and b.get("name") == "Bash"
            for b in raw_use["message"]["content"]
            if isinstance(b, dict)
        )
        raw_result = trace.raw_tool_result_events["t1"]
        assert raw_result["type"] == "user"

    def test_multiple_tool_calls(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Read", {"file_path": "/a.py"}, tool_id="t1"),
            _tool_result("t1", "code"),
            _assistant_tool_use("Write", {"file_path": "/b.py"}, tool_id="t2"),
            _tool_result("t2", "ok"),
        )
        trace = load_trace_from_string(jsonl)
        assert "t1" in trace.raw_tool_use_events
        assert "t2" in trace.raw_tool_use_events
        assert "t1" in trace.raw_tool_result_events
        assert "t2" in trace.raw_tool_result_events

    def test_parallel_tool_use(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_parallel_tool_use([
                ("Grep", {"pattern": "foo"}),
                ("Glob", {"pattern": "*.py"}),
            ]),
            _tool_result("toolu_000", "match"),
            _tool_result("toolu_001", "file.py"),
        )
        trace = load_trace_from_string(jsonl)
        assert "toolu_000" in trace.raw_tool_use_events
        assert "toolu_001" in trace.raw_tool_use_events
        # Both tool_use IDs should point to the same raw assistant event
        assert trace.raw_tool_use_events["toolu_000"] is trace.raw_tool_use_events["toolu_001"]

    def test_raw_event_pair(self):
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "echo hi"}, tool_id="t1"),
            _tool_result("t1", "hi"),
        )
        trace = load_trace_from_string(jsonl)
        tc = trace.all_tool_calls("Bash")[0]
        pair = trace.raw_event_pair(tc)
        assert "tool_use_event" in pair
        assert "tool_result_event" in pair
        assert pair["tool_use_event"]["type"] == "assistant"
        assert pair["tool_result_event"]["type"] == "user"

    def test_raw_event_pair_missing_result(self):
        """Tool use without a result should return None for tool_result_event."""
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "echo"}, tool_id="t1"),
        )
        trace = load_trace_from_string(jsonl)
        tc = trace.all_tool_calls("Bash")[0]
        pair = trace.raw_event_pair(tc)
        assert pair["tool_use_event"] is not None
        assert pair["tool_result_event"] is None

    def test_maps_survive_merge(self):
        """Raw event maps should be built BEFORE merge, so merged events still resolve."""
        # Two consecutive assistant events with tool calls get merged
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Read", {"file_path": "/a"}, tool_id="t1"),
            _assistant_tool_use("Read", {"file_path": "/b"}, tool_id="t2"),
            _tool_result("t1", "a"),
            _tool_result("t2", "b"),
        )
        trace = load_trace_from_string(jsonl)
        # Both tool IDs should be in the raw maps even if events were merged
        assert "t1" in trace.raw_tool_use_events
        assert "t2" in trace.raw_tool_use_events

    def test_parse_trace_jsonl_builds_maps(self):
        """parse_trace_jsonl should also build raw event maps."""
        jsonl = _to_jsonl(
            _user_prompt("task"),
            _assistant_tool_use("Bash", {"command": "ls"}, tool_id="t1"),
            _tool_result("t1", "ok"),
        )
        trace = parse_trace_jsonl(jsonl)
        assert "t1" in trace.raw_tool_use_events
        assert "t1" in trace.raw_tool_result_events

    def test_build_raw_event_maps_empty(self):
        """Empty event list should produce empty maps."""
        use_map, result_map = _build_raw_event_maps([])
        assert use_map == {}
        assert result_map == {}

    def test_build_raw_event_maps_no_tools(self):
        """Events with no tool_use/tool_result content produce empty maps."""
        jsonl = _to_jsonl(
            _user_prompt("hello"),
            _assistant_text("hi there"),
        )
        trace = load_trace_from_string(jsonl)
        assert trace.raw_tool_use_events == {}
        assert trace.raw_tool_result_events == {}

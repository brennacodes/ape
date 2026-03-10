"""
Parse Claude Code session JSONL logs into structured trace objects.

A session log is a JSONL file where each line is a message event. This module
loads those events into typed dataclasses and exposes query methods the
evaluator uses to check behavioral assertions.

Session log structure (per line):
  {
    "type": "user" | "assistant" | "queue-operation",
    "parentUuid": "<uuid> | null",
    "sessionId": "<uuid>",
    "message": {
      "role": "user" | "assistant",
      "content": <string> | [content_block, ...]
    },
    ...
  }

Content block types:
  {"type": "text",       "text": "..."}
  {"type": "tool_use",   "id": "...", "name": "Bash", "input": {...}}
  {"type": "tool_result","tool_use_id": "...", "content": "..."}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation by the assistant."""

    tool_use_id: str
    name: str           # e.g. "Bash", "Read", "Write", "Edit", "Grep", "Glob"
    input: dict
    event_index: int    # index of the parent TraceEvent in the trace


@dataclass(frozen=True)
class ToolResult:
    """The output returned for a tool call."""

    tool_use_id: str
    content: str
    event_index: int    # index of the parent TraceEvent in the trace


@dataclass(frozen=True)
class TextBlock:
    """A text response from the assistant."""

    text: str
    event_index: int    # index of the parent TraceEvent in the trace


@dataclass
class TraceEvent:
    """One message in the session, parsed from a single JSONL line."""

    index: int
    type: str                            # "user" | "assistant" | "queue-operation"
    tool_calls: list[ToolCall]           # non-empty for assistant tool-use messages
    tool_results: list[ToolResult]       # non-empty for user tool-result messages
    text_blocks: list[TextBlock]         # non-empty for messages containing text
    raw: dict                            # the original parsed JSON object

    @property
    def is_parallel_batch(self) -> bool:
        """True when the assistant fired more than one tool call in this message."""
        return len(self.tool_calls) > 1

    @property
    def is_tool_use(self) -> bool:
        return bool(self.tool_calls)

    @property
    def is_tool_result(self) -> bool:
        return bool(self.tool_results)

    @property
    def is_text(self) -> bool:
        return bool(self.text_blocks)


@dataclass
class Trace:
    """A fully parsed Claude Code session."""

    session_id: str
    events: list[TraceEvent]

    # ------------------------------------------------------------------
    # Tool call queries
    # ------------------------------------------------------------------

    def all_tool_calls(self, name: Optional[str] = None) -> list[ToolCall]:
        """All tool calls in trace order, optionally filtered by tool name."""
        calls = [tc for ev in self.events for tc in ev.tool_calls]
        if name is not None:
            calls = [tc for tc in calls if tc.name == name]
        return calls

    def parallel_batches(self) -> list[list[ToolCall]]:
        """
        Groups of tool calls that fired together in the same assistant message.
        Only returns groups with more than one call (genuine parallel batches).
        """
        return [ev.tool_calls for ev in self.events if ev.is_parallel_batch]

    # ------------------------------------------------------------------
    # Bash command queries
    # ------------------------------------------------------------------

    def bash_commands(self) -> list[str]:
        """All command strings passed to the Bash tool, in trace order."""
        return [tc.input.get("command", "") for tc in self.all_tool_calls("Bash")]

    def bash_commands_matching(self, pattern: str) -> list[str]:
        """Bash commands whose command string contains the given substring."""
        return [cmd for cmd in self.bash_commands() if pattern in cmd]

    def any_bash_command_matches(self, pattern: str) -> bool:
        """True if any Bash command contains the given substring."""
        return any(pattern in cmd for cmd in self.bash_commands())

    # ------------------------------------------------------------------
    # File path queries
    # ------------------------------------------------------------------

    def file_paths_read(self) -> list[str]:
        """Paths passed to the Read tool, in trace order."""
        return [tc.input.get("file_path", "") for tc in self.all_tool_calls("Read")]

    def file_paths_written(self) -> list[str]:
        """Paths passed to the Write tool, in trace order."""
        return [tc.input.get("file_path", "") for tc in self.all_tool_calls("Write")]

    def file_paths_edited(self) -> list[str]:
        """Paths passed to the Edit tool, in trace order."""
        return [tc.input.get("file_path", "") for tc in self.all_tool_calls("Edit")]

    def all_file_paths_modified(self) -> list[str]:
        """Paths from Write and Edit calls combined, in trace order."""
        modified = []
        for ev in self.events:
            for tc in ev.tool_calls:
                if tc.name in ("Write", "Edit"):
                    path = tc.input.get("file_path", "")
                    if path:
                        modified.append(path)
        return modified

    # ------------------------------------------------------------------
    # Ordering queries
    # ------------------------------------------------------------------

    def first_event_index_for_tool(self, name: str) -> Optional[int]:
        """
        Event index of the first call to the named tool.
        Returns None if the tool was never called.
        """
        for ev in self.events:
            for tc in ev.tool_calls:
                if tc.name == name:
                    return ev.index
        return None

    def tool_called_before(self, first: str, second: str) -> bool:
        """
        True if the first call to `first` appears before the first call to `second`.
        False if either tool was never called.
        """
        a = self.first_event_index_for_tool(first)
        b = self.first_event_index_for_tool(second)
        if a is None or b is None:
            return False
        return a < b

    def read_before_write_per_path(self) -> bool:
        """
        True if, for every path that was written, a Read to that same path
        appeared earlier in the trace.
        """
        read_paths = set(self.file_paths_read())
        for path in self.all_file_paths_modified():
            if path not in read_paths:
                return False
        return True

    # ------------------------------------------------------------------
    # Text / inter-batch queries
    # ------------------------------------------------------------------

    def has_text_between_tool_batches(self) -> bool:
        """
        True if at least one assistant text block appears between two separate
        tool-call events (indicates the agent paused to reflect between batches).
        """
        saw_tool_batch = False
        for ev in self.events:
            if ev.is_tool_use:
                saw_tool_batch = True
            elif ev.is_text and saw_tool_batch:
                return True
        return False

    def tool_call_batch_count(self) -> int:
        """Number of distinct assistant messages that contained tool calls."""
        return sum(1 for ev in self.events if ev.is_tool_use)


# ---------------------------------------------------------------------------
# Event merging (stream-json produces one line per content block)
# ---------------------------------------------------------------------------

# Event types that carry behavioral content and should be merged when
# consecutive.  Non-message types (system, rate_limit_event, result, etc.)
# act as merge boundaries.
_MERGEABLE_TYPES = frozenset({"assistant", "user"})


def _merge_consecutive_events(events: list[TraceEvent]) -> list[TraceEvent]:
    """
    Merge consecutive events with the same type into single TraceEvents.

    Stream-json output splits a single assistant turn (e.g. thinking + text +
    3 Glob calls) into separate JSONL lines.  Merging restores the logical
    turn boundaries so that ``is_parallel_batch`` and inter-batch analysis
    work correctly.

    Non-mergeable event types (anything not in _MERGEABLE_TYPES) break the
    merge sequence and are passed through unchanged.

    After merging the events are re-indexed sequentially and all frozen child
    objects (ToolCall, ToolResult, TextBlock) are rebuilt with corrected
    ``event_index`` values.
    """
    if not events:
        return events

    merged: list[TraceEvent] = []
    current: TraceEvent | None = None

    for ev in events:
        if ev.type not in _MERGEABLE_TYPES:
            # Flush current accumulator, emit non-mergeable as-is
            if current is not None:
                merged.append(current)
                current = None
            merged.append(ev)
            continue

        if current is not None and ev.type == current.type:
            # Same type — merge into current
            current.tool_calls.extend(ev.tool_calls)
            current.tool_results.extend(ev.tool_results)
            current.text_blocks.extend(ev.text_blocks)
        else:
            # Different type or first event — flush & start new
            if current is not None:
                merged.append(current)
            current = TraceEvent(
                index=ev.index,
                type=ev.type,
                tool_calls=list(ev.tool_calls),
                tool_results=list(ev.tool_results),
                text_blocks=list(ev.text_blocks),
                raw=ev.raw,
            )

    if current is not None:
        merged.append(current)

    # Re-index and rebuild frozen children with correct event_index
    for new_idx, ev in enumerate(merged):
        ev.index = new_idx
        ev.tool_calls = [
            ToolCall(
                tool_use_id=tc.tool_use_id,
                name=tc.name,
                input=tc.input,
                event_index=new_idx,
            )
            for tc in ev.tool_calls
        ]
        ev.tool_results = [
            ToolResult(
                tool_use_id=tr.tool_use_id,
                content=tr.content,
                event_index=new_idx,
            )
            for tr in ev.tool_results
        ]
        ev.text_blocks = [
            TextBlock(text=tb.text, event_index=new_idx)
            for tb in ev.text_blocks
        ]

    return merged


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def load_trace(path: Path) -> Trace:
    """
    Parse a Claude Code session JSONL file into a Trace.

    Skips queue-operation lines, which carry no behavioral data.
    Raises ValueError if the file contains no valid message events.
    """
    raw_lines = path.read_text(encoding="utf-8").strip().splitlines()
    events = []
    session_id = ""

    for line in raw_lines:
        if not line.strip():
            continue
        obj = json.loads(line)

        if obj.get("type") == "queue-operation":
            continue

        if not session_id:
            session_id = obj.get("sessionId", "")

        event = _parse_event(obj, index=len(events))
        events.append(event)

    if not events:
        raise ValueError(f"No message events found in {path}")

    events = _merge_consecutive_events(events)
    return Trace(session_id=session_id, events=events)


def parse_trace_jsonl(text: str) -> Trace:
    """
    Parse JSONL text directly into a Trace.

    Useful for parsing CLI stdout from ``claude -p --output-format stream-json``.
    Lenient: skips lines that aren't valid JSON, aren't dicts, or produce empty
    events (no tool calls, tool results, or text blocks).

    Raises ValueError if no content-bearing events are found.
    """
    events: list[TraceEvent] = []
    session_id = ""

    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "queue-operation":
            continue

        if not session_id:
            session_id = obj.get("sessionId", "") or obj.get("session_id", "")

        event = _parse_event(obj, index=len(events))
        # Only keep events with actual content (skip system/init/result metadata)
        if event.tool_calls or event.tool_results or event.text_blocks:
            events.append(event)

    if not events:
        raise ValueError("No message events found in provided text")

    events = _merge_consecutive_events(events)
    return Trace(session_id=session_id, events=events)


def load_trace_from_string(jsonl: str) -> Trace:
    """Parse a JSONL string directly. Useful for tests."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(jsonl)
        tmp_path = Path(f.name)
    try:
        return load_trace(tmp_path)
    finally:
        tmp_path.unlink()


def _parse_event(obj: dict, index: int) -> TraceEvent:
    """Convert one raw JSON object into a TraceEvent."""
    event_type = obj.get("type", "unknown")
    message = obj.get("message", {})
    content = message.get("content", [])

    tool_calls: list[ToolCall] = []
    tool_results: list[ToolResult] = []
    text_blocks: list[TextBlock] = []

    if isinstance(content, str):
        # Initial user prompt arrives as a plain string
        text_blocks.append(TextBlock(text=content, event_index=index))
    elif isinstance(content, list):
        for block in content:
            btype = block.get("type")
            if btype == "tool_use":
                tool_calls.append(ToolCall(
                    tool_use_id=block.get("id", ""),
                    name=block.get("name", ""),
                    input=block.get("input", {}),
                    event_index=index,
                ))
            elif btype == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, list):
                    # Some tool results wrap content in a list of blocks
                    result_content = " ".join(
                        b.get("text", "") for b in result_content
                        if isinstance(b, dict)
                    )
                tool_results.append(ToolResult(
                    tool_use_id=block.get("tool_use_id", ""),
                    content=str(result_content),
                    event_index=index,
                ))
            elif btype == "text":
                text_blocks.append(TextBlock(
                    text=block.get("text", ""),
                    event_index=index,
                ))
            elif btype == "thinking":
                text_blocks.append(TextBlock(
                    text=block.get("thinking", ""),
                    event_index=index,
                ))

    return TraceEvent(
        index=index,
        type=event_type,
        tool_calls=tool_calls,
        tool_results=tool_results,
        text_blocks=text_blocks,
        raw=obj,
    )

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
    call_index: int = 0  # unique sequential index across all tool calls in the trace


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
    workspace_path: Optional[str] = None  # cwd from the init event
    # Pre-merge raw event lookup by tool_use_id
    raw_tool_use_events: dict[str, dict] = field(default_factory=dict)
    raw_tool_result_events: dict[str, dict] = field(default_factory=dict)

    def raw_event_pair(self, tc: ToolCall) -> dict:
        """Get the raw stream.json objects for a tool call and its result."""
        return {
            "tool_use_event": self.raw_tool_use_events.get(tc.tool_use_id),
            "tool_result_event": self.raw_tool_result_events.get(tc.tool_use_id),
        }

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

    # ------------------------------------------------------------------
    # Tool result access
    # ------------------------------------------------------------------

    def result_for_tool_call(self, tc: ToolCall) -> Optional[ToolResult]:
        """Find the ToolResult corresponding to a ToolCall by matching tool_use_id."""
        for ev in self.events:
            for tr in ev.tool_results:
                if tr.tool_use_id == tc.tool_use_id:
                    return tr
        return None

    def all_tool_results(self) -> list[ToolResult]:
        """All tool results in trace order."""
        return [tr for ev in self.events for tr in ev.tool_results]

    # ------------------------------------------------------------------
    # Bash outcome analysis
    # ------------------------------------------------------------------

    def bash_exit_code(self, tc: ToolCall) -> Optional[int]:
        """
        Extract exit code from a Bash tool call's result.

        Looks for patterns in the result content that indicate exit codes.
        Common patterns in Claude Code output:
        - Result content contains stdout/stderr; non-zero exit indicated by error markers
        - The tool_result block may have is_error flag
        """
        result = self.result_for_tool_call(tc)
        if result is None:
            return None
        content = result.content
        # Look for explicit exit code patterns
        import re
        # Pattern: "Exit code: N" or "exit code N" at end of output
        m = re.search(r'[Ee]xit\s+code[:\s]+(\d+)', content)
        if m:
            return int(m.group(1))
        # Pattern: tool result indicates error (common in Claude Code)
        # Check the raw event data for is_error flag
        for ev in self.events:
            for tr_raw in ev.raw.get('message', {}).get('content', []):
                if isinstance(tr_raw, dict) and tr_raw.get('type') == 'tool_result':
                    if tr_raw.get('tool_use_id') == tc.tool_use_id:
                        if tr_raw.get('is_error'):
                            return 1  # Non-zero exit
        # If no error indicators found, assume success
        if content.strip():
            return 0
        return None

    def bash_commands_with_results(self) -> list[dict]:
        """All Bash commands with their results, exit codes, and success status."""
        results = []
        for tc in self.all_tool_calls("Bash"):
            cmd = tc.input.get("command", "")
            tr = self.result_for_tool_call(tc)
            output = tr.content if tr else ""
            exit_code = self.bash_exit_code(tc)
            results.append({
                'command': cmd,
                'output': output,
                'exit_code': exit_code,
                'event_index': tc.event_index,
                'tool_call': tc,
                'succeeded': exit_code == 0 if exit_code is not None else None,
            })
        return results

    # ------------------------------------------------------------------
    # Command output parsing
    # ------------------------------------------------------------------

    def cargo_test_results(self) -> list[dict]:
        """Parse all cargo test invocations and outcomes."""
        import re
        results = []
        for info in self.bash_commands_with_results():
            if 'cargo test' not in info['command']:
                continue
            output = info['output']
            passed = None
            test_count = None
            failed_count = None
            # Parse: "test result: ok. N passed; M failed; K ignored"
            m = re.search(r'test result:\s*(ok|FAILED)\.\s*(\d+)\s*passed;\s*(\d+)\s*failed', output)
            if m:
                passed = m.group(1) == 'ok'
                test_count = int(m.group(2))
                failed_count = int(m.group(3))
            else:
                # Fallback: check exit code
                if info['exit_code'] is not None:
                    passed = info['exit_code'] == 0
            results.append({
                'command': info['command'],
                'event_index': info['event_index'],
                'passed': passed,
                'test_count': test_count,
                'failed_count': failed_count,
                'output': output,
            })
        return results

    def cargo_clippy_results(self) -> list[dict]:
        """Parse all cargo clippy invocations and outcomes."""
        import re
        results = []
        for info in self.bash_commands_with_results():
            if 'cargo clippy' not in info['command']:
                continue
            output = info['output']
            has_warnings = None
            warning_count = None
            # Look for "warning:" lines
            warnings = re.findall(r'^warning:', output, re.MULTILINE)
            if warnings:
                has_warnings = True
                warning_count = len(warnings)
            elif info['exit_code'] is not None:
                has_warnings = info['exit_code'] != 0
                if not has_warnings:
                    warning_count = 0
            results.append({
                'command': info['command'],
                'event_index': info['event_index'],
                'has_warnings': has_warnings,
                'warning_count': warning_count,
                'output': output,
            })
        return results

    def cargo_build_results(self) -> list[dict]:
        """Parse all cargo build invocations and outcomes."""
        results = []
        for info in self.bash_commands_with_results():
            if 'cargo build' not in info['command']:
                continue
            results.append({
                'command': info['command'],
                'event_index': info['event_index'],
                'succeeded': info['succeeded'],
                'output': info['output'],
            })
        return results

    def cargo_llvm_cov_results(self) -> list[dict]:
        """Parse all cargo llvm-cov invocations and outcomes.

        Coverage output from ``cargo llvm-cov`` can be very large (thousands of
        test lines) and is often truncated in the Bash tool result.  When that
        happens, the model typically runs a follow-up command (grep, cat, or
        tail) on the saved tool-results file to extract the TOTAL line.  We
        look for the percentage in the original command output first, then scan
        subsequent Bash commands for a TOTAL summary line if the percentage
        wasn't found.
        """
        import re
        results = []
        all_commands = list(self.bash_commands_with_results())
        for i, info in enumerate(all_commands):
            if 'cargo llvm-cov' not in info['command'] and 'llvm-cov' not in info['command']:
                continue
            output = info['output']
            coverage_pct = None

            # Try 1: Look for the TOTAL summary line directly (most reliable).
            # Format: "TOTAL  <regions> <missed> <pct>%  <functions> <missed> <pct>%  <lines> <missed> <pct>%"
            m = re.search(r'TOTAL\s+.*?(\d+\.?\d*)\s*%\s+.*?(\d+\.?\d*)\s*%\s+.*?(\d+\.?\d*)\s*%', output)
            if m:
                # Last percentage is line coverage
                coverage_pct = float(m.group(3))
            else:
                # Try 2: Any percentage pattern in the output
                m = re.search(r'(\d+\.?\d*)\s*%', output)
                if m:
                    coverage_pct = float(m.group(1))

            # Try 3: Output was truncated — check subsequent commands for the
            # TOTAL line (model often greps/cats the tool-results file).
            if coverage_pct is None:
                for subsequent in all_commands[i+1:]:
                    sub_out = subsequent['output']
                    # Look for TOTAL line in the output of follow-up commands
                    m = re.search(
                        r'TOTAL\s+.*?(\d+\.?\d*)\s*%\s+.*?(\d+\.?\d*)\s*%\s+.*?(\d+\.?\d*)\s*%',
                        sub_out,
                    )
                    if m:
                        coverage_pct = float(m.group(3))
                        break
                    # Also try a simpler percentage if the grep is just the
                    # summary portion (e.g. "92.84%")
                    if 'TOTAL' in sub_out:
                        m = re.search(r'(\d+\.?\d*)\s*%', sub_out)
                        if m:
                            coverage_pct = float(m.group(1))
                            break

            results.append({
                'command': info['command'],
                'event_index': info['event_index'],
                'coverage_percentage': coverage_pct,
                'output': output,
            })
        return results

    def git_commit_messages(self) -> list[dict]:
        """Extract git commit messages from the trace."""
        import re
        import shlex
        results = []
        for tc in self.all_tool_calls("Bash"):
            cmd = tc.input.get("command", "")
            if 'git commit' not in cmd:
                continue
            subject = None
            body = None
            full_message = None
            # Check heredoc patterns FIRST — the simple quoted regex would
            # incorrectly match the opening `"$(cat <<'EOF'` as a quoted
            # string, capturing `$(cat <<` as the commit message.
            m = re.search(r"git\s+commit\s+.*-m\s+\"\$\(cat\s+<<'?EOF'?\n(.+?)\nEOF", cmd, re.DOTALL)
            if m:
                full_message = m.group(1).strip()
            # Extract from -m flag (simple quoted message)
            if full_message is None:
                m = re.search(r'git\s+commit\s+.*-m\s+["\'](.+?)["\']', cmd, re.DOTALL)
                if m:
                    full_message = m.group(1)
                elif '-m' in cmd:
                    # Try shell parsing
                    try:
                        parts = shlex.split(cmd)
                        for i, p in enumerate(parts):
                            if p == '-m' and i + 1 < len(parts):
                                full_message = parts[i + 1]
                                break
                    except ValueError:
                        pass
            if full_message:
                lines = full_message.split('\n')
                subject = lines[0].strip()
                if len(lines) > 2 and lines[1].strip() == '':
                    body = '\n'.join(lines[2:])
                elif len(lines) > 1:
                    body = '\n'.join(lines[1:])
                results.append({
                    'subject': subject,
                    'body': body,
                    'full_message': full_message,
                    'event_index': tc.event_index,
                })
        return results

    def command_failed_at(self, pattern: str) -> list[int]:
        """Event indices where a command matching pattern had non-zero exit."""
        failed = []
        for info in self.bash_commands_with_results():
            if pattern in info['command'] and info['exit_code'] and info['exit_code'] != 0:
                failed.append(info['event_index'])
        return failed

    def file_modifications_after_event(self, after_index: int) -> list[dict]:
        """All Write/Edit calls after a given event index."""
        mods = []
        for ev in self.events:
            if ev.index <= after_index:
                continue
            for tc in ev.tool_calls:
                if tc.name in ("Write", "Edit"):
                    path = tc.input.get("file_path", "")
                    if path:
                        mods.append({
                            'path': path,
                            'tool': tc.name,
                            'event_index': ev.index,
                        })
        return mods


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

    # Re-index and rebuild frozen children with correct event_index and call_index
    call_counter = 0
    for new_idx, ev in enumerate(merged):
        ev.index = new_idx
        new_calls = []
        for tc in ev.tool_calls:
            new_calls.append(ToolCall(
                tool_use_id=tc.tool_use_id,
                name=tc.name,
                input=tc.input,
                event_index=new_idx,
                call_index=call_counter,
            ))
            call_counter += 1
        ev.tool_calls = new_calls
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
    workspace_path = None

    for line in raw_lines:
        if not line.strip():
            continue
        obj = json.loads(line)

        if obj.get("type") == "queue-operation":
            continue

        # Extract workspace path from the init event
        if obj.get("type") == "system" and obj.get("subtype") == "init":
            workspace_path = obj.get("cwd")

        if not session_id:
            session_id = obj.get("sessionId", "")

        event = _parse_event(obj, index=len(events))
        events.append(event)

    if not events:
        raise ValueError(f"No message events found in {path}")

    raw_tool_use_events, raw_tool_result_events = _build_raw_event_maps(events)
    events = _merge_consecutive_events(events)
    return Trace(
        session_id=session_id, events=events, workspace_path=workspace_path,
        raw_tool_use_events=raw_tool_use_events,
        raw_tool_result_events=raw_tool_result_events,
    )


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
    workspace_path = None

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

        # Extract workspace path from the init event
        if obj.get("type") == "system" and obj.get("subtype") == "init":
            workspace_path = obj.get("cwd")

        if not session_id:
            session_id = obj.get("sessionId", "") or obj.get("session_id", "")

        event = _parse_event(obj, index=len(events))
        # Only keep events with actual content (skip system/init/result metadata)
        if event.tool_calls or event.tool_results or event.text_blocks:
            events.append(event)

    if not events:
        raise ValueError("No message events found in provided text")

    raw_tool_use_events, raw_tool_result_events = _build_raw_event_maps(events)
    events = _merge_consecutive_events(events)
    return Trace(
        session_id=session_id, events=events, workspace_path=workspace_path,
        raw_tool_use_events=raw_tool_use_events,
        raw_tool_result_events=raw_tool_result_events,
    )


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


def _build_raw_event_maps(
    events: list[TraceEvent],
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Build tool_use_id → raw event dicts from pre-merge events.

    Must be called *before* ``_merge_consecutive_events`` so that every raw
    event is still associated with its original TraceEvent.
    """
    raw_tool_use_events: dict[str, dict] = {}
    raw_tool_result_events: dict[str, dict] = {}
    for ev in events:
        for block in ev.raw.get("message", {}).get("content", []):
            if isinstance(block, dict):
                if block.get("type") == "tool_use":
                    tid = block.get("id", "")
                    if tid:
                        raw_tool_use_events[tid] = ev.raw
                elif block.get("type") == "tool_result":
                    tid = block.get("tool_use_id", "")
                    if tid:
                        raw_tool_result_events[tid] = ev.raw
    return raw_tool_use_events, raw_tool_result_events


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

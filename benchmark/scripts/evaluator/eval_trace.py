"""
Structured evaluation tracing — audit trail for benchmark evaluator decisions.

Every function that reads, filters, or compares data records the actual JSON
objects from stream.json it examined and the conclusions it drew.  The output
is an ``eval_trace`` field on each per-check result — a chronological list of
log entries forming a complete audit trail.

Core principle: log the actual data, not extractions of it.  When the
evaluator examines a Bash tool call, the evidence is the full raw JSON event
object from stream.json.
"""

from __future__ import annotations

import time
from typing import Any


class EvalTrace:
    """Mutable collector of structured trace entries for one check evaluation."""

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []

    def log(self, function: str, action: str, **data: Any) -> None:
        """Append a trace entry.

        Parameters
        ----------
        function : str
            Name of the evaluator function emitting the entry.
        action : str
            What the function did at this point (e.g. ``resolved_tool_call_metric``).
        **data
            Arbitrary payload — raw event objects, indices, filter results, etc.
            No sanitisation or truncation is applied.
        """
        self._entries.append({
            "function": function,
            "action": action,
            "timestamp": time.monotonic(),
            **data,
        })

    def to_list(self) -> list[dict[str, Any]]:
        """Serialize to a plain list of dicts suitable for JSON output."""
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return True  # an EvalTrace is always truthy (even when empty)

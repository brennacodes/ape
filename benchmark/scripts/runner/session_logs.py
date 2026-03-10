"""
Parse Claude Code session logs for token usage and cost data.

Claude Code writes JSONL session logs under ~/.claude/projects/. Each line
is a JSON object representing a message exchange. Token usage data is found
in various schema locations depending on the message type.

Public API
----------
SessionLogParser  — finds and parses session logs for token summaries.
MessageTokens     — token counts for a single message exchange.
SessionTokenSummary — aggregated token data for a session.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class MessageTokens:
    """Token counts for a single message exchange."""
    role: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    timestamp: str = ""


@dataclass
class SessionTokenSummary:
    """Aggregated token data for an entire session."""
    session_id: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    num_turns: int = 0
    messages: list[MessageTokens] = field(default_factory=list)


def _extract_message_tokens(entry: dict) -> Optional[MessageTokens]:
    """
    Extract token data from a session log entry.

    Checks multiple schema locations:
    - entry.usage
    - entry.message.usage
    - entry.result.usage

    Cost is extracted from:
    - entry.costUSD
    - entry.result.costUSD
    """
    usage = None

    # Try different locations for usage data
    if "usage" in entry and isinstance(entry["usage"], dict):
        usage = entry["usage"]
    elif "message" in entry and isinstance(entry.get("message"), dict):
        msg = entry["message"]
        if "usage" in msg and isinstance(msg["usage"], dict):
            usage = msg["usage"]
    elif "result" in entry and isinstance(entry.get("result"), dict):
        result = entry["result"]
        if "usage" in result and isinstance(result["usage"], dict):
            usage = result["usage"]

    if usage is None:
        return None

    # Extract cost
    cost = 0.0
    if "costUSD" in entry:
        try:
            cost = float(entry["costUSD"])
        except (TypeError, ValueError):
            pass
    elif "result" in entry and isinstance(entry.get("result"), dict):
        if "costUSD" in entry["result"]:
            try:
                cost = float(entry["result"]["costUSD"])
            except (TypeError, ValueError):
                pass

    # Determine role
    role = entry.get("role", "")
    if not role and "message" in entry and isinstance(entry.get("message"), dict):
        role = entry["message"].get("role", "")
    if not role:
        role = entry.get("type", "unknown")

    # Determine model
    model = entry.get("model", "")
    if not model and "message" in entry and isinstance(entry.get("message"), dict):
        model = entry["message"].get("model", "")

    return MessageTokens(
        role=role,
        model=model,
        input_tokens=usage.get("input_tokens", 0) or 0,
        output_tokens=usage.get("output_tokens", 0) or 0,
        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
        cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
        cost_usd=cost,
        timestamp=entry.get("timestamp", ""),
    )


class SessionLogParser:
    """Find and parse Claude Code session logs for token data."""

    def __init__(self, claude_dir: Optional[Path] = None):
        self.claude_dir = claude_dir or (Path.home() / ".claude")

    def find_session_log(self, session_id: str) -> Optional[Path]:
        """
        Find the session log file for a given session ID.

        Searches ~/.claude/projects/ via rglob for {session_id}.jsonl.
        """
        projects_dir = self.claude_dir / "projects"
        if not projects_dir.is_dir():
            return None

        pattern = f"{session_id}.jsonl"
        for path in projects_dir.rglob(pattern):
            return path
        return None

    def parse_session(self, session_id: str) -> Optional[SessionTokenSummary]:
        """
        Parse a session log and return aggregated token data.

        Returns None if the session log cannot be found or parsed.
        """
        log_path = self.find_session_log(session_id)
        if log_path is None:
            return None

        return self._parse_log_file(log_path, session_id)

    def _parse_log_file(self, path: Path, session_id: str) -> Optional[SessionTokenSummary]:
        """Parse a JSONL session log file."""
        summary = SessionTokenSummary(session_id=session_id)
        num_turns = 0

        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if not isinstance(entry, dict):
                        continue

                    tokens = _extract_message_tokens(entry)
                    if tokens is None:
                        continue

                    summary.messages.append(tokens)
                    summary.input_tokens += tokens.input_tokens
                    summary.output_tokens += tokens.output_tokens
                    summary.cache_creation_tokens += tokens.cache_creation_tokens
                    summary.cache_read_tokens += tokens.cache_read_tokens
                    summary.cost_usd += tokens.cost_usd

                    if tokens.model and not summary.model:
                        summary.model = tokens.model

                    # Count assistant turns
                    if tokens.role in ("assistant", "response"):
                        num_turns += 1

        except OSError:
            return None

        summary.num_turns = num_turns
        return summary

    def get_ccusage_summary(self, since: str = "", until: str = "") -> dict:
        """
        Get a token usage summary via the ccusage CLI tool (optional cross-check).

        Returns an empty dict if ccusage is not available.
        """
        cmd = ["ccusage"]
        if since:
            cmd.extend(["--since", since])
        if until:
            cmd.extend(["--until", until])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return {"raw_output": result.stdout}
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return {}

"""
Structured result storage with directory hierarchy and resumption support.

Layout:
    results_dir/raw/{fixture_id}/{format}/{prompt_id}/{run_id:03d}.json
    results_dir/logs/{fixture_id}/{format}/{prompt_id}/{run_id:03d}.trace.json

Public API
----------
RunRecord     — extended RunSummary with CLI output, tokens, workspace state.
Recorder      — manages structured storage on disk.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional


@dataclass
class RunRecord:
    """Extended run result with CLI output, tokens, workspace state."""

    # Identity
    fixture_id: str = ""
    format: str = ""
    prompt_id: str = ""
    run_id: int = 0

    # Evaluation
    outcomes: list[dict] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    pass_rate: float = 0.0

    # CLI output
    raw_output: str = ""
    json_metadata: dict = field(default_factory=dict)
    wall_clock_ms: float = 0.0
    exit_code: int = 0
    stderr: str = ""

    # Context
    model: str = ""
    session_id: str = ""
    prompt_text: str = ""

    # Workspace
    workspace_state: dict = field(default_factory=dict)

    # Tokens
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    num_turns: int = 0

    # Timing
    duration_ms: float = 0.0
    duration_api_ms: float = 0.0

    # Turn limit
    max_turns_configured: int = 0
    hit_turn_limit: bool = False

    # Meta
    timestamp: str = ""
    consistency_scores: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> RunRecord:
        """Reconstruct from a dict (loaded from JSON)."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    @classmethod
    def from_run_summary(cls, summary: Any, run_id: int, **extra: Any) -> RunRecord:
        """
        Build a RunRecord from an existing RunSummary.

        Parameters
        ----------
        summary : RunSummary
            The summary object from results.py.
        run_id : int
            Assigned run ID.
        **extra
            Additional fields to set (e.g. raw_output, wall_clock_ms).
        """
        outcomes = []
        for o in summary.outcomes:
            if hasattr(o, "__dataclass_fields__"):
                outcomes.append(asdict(o))
            elif isinstance(o, dict):
                outcomes.append(o)
            else:
                outcomes.append(vars(o))

        record = cls(
            fixture_id=summary.metadata.fixture_id,
            format=summary.metadata.format,
            prompt_id=summary.metadata.prompt_id,
            run_id=run_id,
            outcomes=outcomes,
            total=summary.total,
            passed=summary.passed,
            failed=summary.failed,
            skipped=summary.skipped,
            pass_rate=summary.pass_rate,
            model=summary.metadata.model,
            session_id=summary.metadata.session_id,
            timestamp=summary.metadata.timestamp or datetime.now().isoformat(),
        )

        for k, v in extra.items():
            if hasattr(record, k):
                setattr(record, k, v)

        return record


class Recorder:
    """Structured storage for benchmark run records."""

    def __init__(self, results_dir: Path | str):
        self.results_dir = Path(results_dir)

    def _raw_dir(self, fixture_id: str, fmt: str, prompt_id: str) -> Path:
        return self.results_dir / "raw" / fixture_id / fmt / prompt_id

    def _log_dir(self, fixture_id: str, fmt: str, prompt_id: str) -> Path:
        return self.results_dir / "logs" / fixture_id / fmt / prompt_id

    def _run_path(self, fixture_id: str, fmt: str, prompt_id: str, run_id: int) -> Path:
        return self._raw_dir(fixture_id, fmt, prompt_id) / f"{run_id:03d}.json"

    def _trace_path(self, fixture_id: str, fmt: str, prompt_id: str, run_id: int) -> Path:
        return self._log_dir(fixture_id, fmt, prompt_id) / f"{run_id:03d}.trace.json"

    def save_run(self, record: RunRecord) -> Path:
        """Write a RunRecord to structured storage. Returns the written path."""
        path = self._run_path(record.fixture_id, record.format, record.prompt_id, record.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, indent=2)
        return path

    def load_run(self, fixture_id: str, fmt: str, prompt_id: str, run_id: int) -> RunRecord:
        """Load a single RunRecord from disk."""
        path = self._run_path(fixture_id, fmt, prompt_id, run_id)
        with open(path, "r", encoding="utf-8") as f:
            return RunRecord.from_dict(json.load(f))

    def update_run(self, record: RunRecord) -> None:
        """Overwrite an existing RunRecord on disk."""
        self.save_run(record)

    def all_runs(self) -> Iterator[RunRecord]:
        """Iterate over every stored RunRecord."""
        raw_dir = self.results_dir / "raw"
        if not raw_dir.is_dir():
            return
        for json_path in sorted(raw_dir.rglob("*.json")):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    yield RunRecord.from_dict(json.load(f))
            except (json.JSONDecodeError, KeyError):
                continue

    def runs_for_fixture(
        self,
        fixture_id: str,
        fmt: Optional[str] = None,
        prompt_id: Optional[str] = None,
    ) -> Iterator[RunRecord]:
        """Iterate over runs matching the given filters."""
        base = self.results_dir / "raw" / fixture_id
        if fmt:
            base = base / fmt
        if fmt and prompt_id:
            base = base / prompt_id

        if not base.is_dir():
            return
        for json_path in sorted(base.rglob("*.json")):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    yield RunRecord.from_dict(json.load(f))
            except (json.JSONDecodeError, KeyError):
                continue

    def next_run_id(self, fixture_id: str, fmt: str, prompt_id: str) -> int:
        """Return the next available run ID for a fixture/format/prompt triple."""
        d = self._raw_dir(fixture_id, fmt, prompt_id)
        if not d.is_dir():
            return 0
        existing = [
            int(p.stem) for p in d.glob("*.json")
            if p.stem.isdigit()
        ]
        return max(existing) + 1 if existing else 0

    def save_trace(self, record: RunRecord, trace_data: Any) -> Path:
        """Save trace data alongside a run record."""
        path = self._trace_path(record.fixture_id, record.format, record.prompt_id, record.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(trace_data, f, indent=2)
        return path

    def load_trace(self, fixture_id: str, fmt: str, prompt_id: str, run_id: int) -> Optional[dict]:
        """Load trace data for a run. Returns None if not found."""
        path = self._trace_path(fixture_id, fmt, prompt_id, run_id)
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

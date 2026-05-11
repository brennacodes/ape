"""
Structured result storage with per-run directory hierarchy.

Layout (new):
    results_dir/{fixture_id}/{format}/{prompt_id}/{run_id:03d}/
        stream.json              # Raw JSONL stream as valid JSON array
        state.json               # Workspace state (git log, diffs, files)
        {check_id}.json          # One file per check outcome
        summary.json             # Run metadata, grades, file references

Layout (legacy, read-only):
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


def _load_json_file(path: Path) -> list:
    """Load a JSON file, returning an empty list on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _extract_result_metadata(stream_events: list[dict]) -> dict:
    """Extract metadata from the result event in an already-parsed stream.

    Finds the last ``"type": "result"`` object and extracts session, cost,
    duration, token usage, and modelUsage data.

    Returns an empty dict if no result event is found.
    """
    result_event = None
    for obj in stream_events:
        if isinstance(obj, dict) and obj.get("type") == "result":
            result_event = obj

    if result_event is None:
        return {}

    meta: dict[str, Any] = {}
    for key in ("session_id", "sessionId"):
        if key in result_event:
            meta["session_id"] = result_event[key]
            break

    if "total_cost_usd" in result_event:
        meta["total_cost_usd"] = result_event["total_cost_usd"]
    elif "costUSD" in result_event:
        meta["total_cost_usd"] = result_event["costUSD"]

    for key in ("duration_ms", "durationMs"):
        if key in result_event:
            meta["duration_ms"] = result_event[key]
            break

    for key in ("duration_api_ms", "durationApiMs"):
        if key in result_event:
            meta["duration_api_ms"] = result_event[key]
            break

    for key in ("num_turns", "numTurns"):
        if key in result_event:
            meta["num_turns"] = result_event[key]
            break

    usage = result_event.get("usage", {})
    if isinstance(usage, dict):
        meta["input_tokens"] = usage.get("input_tokens", 0)
        meta["output_tokens"] = usage.get("output_tokens", 0)
        meta["cache_creation_input_tokens"] = usage.get("cache_creation_input_tokens", 0)
        meta["cache_read_input_tokens"] = usage.get("cache_read_input_tokens", 0)

    model_usage = result_event.get("modelUsage")
    if model_usage is not None:
        meta["model_usage"] = model_usage

    return meta


def extract_stream_metadata(raw_output: str) -> dict:
    """Parse JSONL string and extract metadata from the result event.

    Convenience wrapper for callers that have a raw JSONL string rather
    than an already-parsed list.
    """
    if not raw_output or not raw_output.strip():
        return {}
    events = []
    for line in raw_output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return _extract_result_metadata(events)


def format_duration_hms(ms: float) -> str:
    """Format milliseconds as a human-readable duration string.

    Examples: ``"1h 02m 03s"``, ``"05m 30s"``, ``"12s"``.
    """
    if ms <= 0:
        return "0s"
    total_seconds = int(ms / 1000)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    if hours > 0:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    elif minutes > 0:
        return f"{minutes:02d}m {seconds:02d}s"
    else:
        return f"{seconds}s"


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
    disabled: int = 0
    not_applicable: int = 0
    pass_rate: float = 0.0

    # CLI output
    error: str = ""
    json_metadata: dict = field(default_factory=dict)
    wall_clock_ms: float = 0.0
    exit_code: int = 0
    stderr: str = ""

    # Context
    model: str = ""
    session_id: str = ""
    prompt_text: str = ""
    eval_conditions: dict = field(default_factory=dict)
    eval_variables: dict = field(default_factory=dict)

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
    started_at: str = ""
    completed_at: str = ""
    duration_ms: float = 0.0
    duration_api_ms: float = 0.0

    # Turn limit
    max_turns_configured: int = 0
    hit_turn_limit: bool = False

    # Model usage
    model_usage: dict = field(default_factory=dict)

    # Version tracking
    ape_version: str = ""
    workflow_hash: str = ""
    git_sha: str = ""

    # Meta
    timestamp: str = ""
    consistency_scores: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> RunRecord:
        """Reconstruct from a dict (loaded from JSON).

        Ignores unknown fields so old on-disk files with ``raw_output``
        still load without error.
        """
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
            Additional fields to set (e.g. wall_clock_ms).
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
            disabled=summary.disabled,
            not_applicable=summary.not_applicable,
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
    """Structured storage for benchmark run records.

    New format: per-run directories with separate files for stream,
    state, checks, and summary.
    Legacy format: flat JSON files under ``raw/`` (read-only fallback).
    """

    def __init__(self, results_dir: Path | str):
        self.results_dir = Path(results_dir)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _run_dir(self, fixture_id: str, fmt: str, prompt_id: str, run_id: int) -> Path:
        """Return the per-run directory path (new format)."""
        return self.results_dir / fixture_id / fmt / prompt_id / f"{run_id:03d}"

    def _run_path_legacy(self, fixture_id: str, fmt: str, prompt_id: str, run_id: int) -> Path:
        """Return the legacy flat-file path (read-only)."""
        return self.results_dir / "raw" / fixture_id / fmt / prompt_id / f"{run_id:03d}.json"

    def _log_dir(self, fixture_id: str, fmt: str, prompt_id: str) -> Path:
        return self.results_dir / "logs" / fixture_id / fmt / prompt_id

    def _trace_path(self, fixture_id: str, fmt: str, prompt_id: str, run_id: int) -> Path:
        return self._log_dir(fixture_id, fmt, prompt_id) / f"{run_id:03d}.trace.json"

    # ------------------------------------------------------------------
    # Incremental writes
    # ------------------------------------------------------------------

    def init_run_dir(self, fixture_id: str, fmt: str, prompt_id: str, run_id: int) -> Path:
        """Create the run directory early so incremental writes have a destination.

        Returns the run directory Path.  Safe to call multiple times.
        """
        run_dir = self._run_dir(fixture_id, fmt, prompt_id, run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def write_state(self, fixture_id: str, fmt: str, prompt_id: str, run_id: int, state: dict) -> None:
        """Write (or overwrite) state.json in an already-created run directory.

        Called incrementally as phases complete — first with setup state,
        then again with the full post-run workspace state.
        """
        run_dir = self._run_dir(fixture_id, fmt, prompt_id, run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        with open(run_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save_run(
        self,
        record: RunRecord,
        raw_output: str = "",
        stream_path: Path | None = None,
    ) -> Path:
        """Write a RunRecord to structured per-run directory storage.

        The stream can be provided in two ways (checked in order):
        1. *stream_path* — an already-formatted JSON array file (produced by
           the runner via ``jq -s '.'``).  Moved into the run directory.
        2. *raw_output* — a JSONL string, parsed into a JSON array and written
           to ``stream.json``.  Kept for tests and backward compatibility.

        Also creates:
            state.json      — workspace state dict
            {check_id}.json — one file per check outcome
            summary.json    — run metadata, grades, file references

        Returns the run directory Path.
        """
        run_dir = self._run_dir(record.fixture_id, record.format, record.prompt_id, record.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        dest_stream = run_dir / "stream.json"

        # -- stream.json --
        if stream_path is not None and stream_path.exists():
            import shutil
            shutil.move(str(stream_path), str(dest_stream))
        elif raw_output and raw_output.strip():
            stream_data: list[dict] = []
            for line in raw_output.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    stream_data.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            with open(dest_stream, "w", encoding="utf-8") as f:
                json.dump(stream_data, f, indent=2)
        else:
            with open(dest_stream, "w", encoding="utf-8") as f:
                json.dump([], f)

        # -- Load stream for metadata extraction --
        stream_events = _load_json_file(dest_stream)

        # -- state.json --
        with open(run_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump(record.workspace_state or {}, f, indent=2)

        # -- per-check files --
        check_files = []
        for outcome in record.outcomes:
            check_id = outcome.get("check_id", "unknown")
            # Sanitize check_id for filesystem
            safe_id = check_id.replace("/", "_").replace("\\", "_")
            check_path = run_dir / f"{safe_id}.json"
            with open(check_path, "w", encoding="utf-8") as f:
                json.dump(outcome, f, indent=2)
            check_files.append({
                "check_id": check_id,
                "passed": outcome.get("passed", False),
                "phase": outcome.get("phase", ""),
                "file": f"{safe_id}.json",
            })

        # -- Extract metadata from the result event in the stream --
        stream_meta = _extract_result_metadata(stream_events)

        # Merge stream metadata into record fields where record has defaults
        session_id = stream_meta.get("session_id", "") or record.session_id
        cost_usd = stream_meta.get("total_cost_usd", 0.0) or record.cost_usd
        duration_api_ms = stream_meta.get("duration_api_ms", 0.0) or record.duration_api_ms
        num_turns = stream_meta.get("num_turns", 0) or record.num_turns
        input_tokens = stream_meta.get("input_tokens", 0) or record.input_tokens
        output_tokens = stream_meta.get("output_tokens", 0) or record.output_tokens
        cache_creation_tokens = stream_meta.get("cache_creation_input_tokens", 0) or record.cache_creation_tokens
        cache_read_tokens = stream_meta.get("cache_read_input_tokens", 0) or record.cache_read_tokens
        model_usage = stream_meta.get("model_usage", {}) or record.model_usage

        # Compute derived fields
        succeeded = record.error in ("", None) and record.exit_code == 0
        pass_rate_pct = record.pass_rate * 100 if record.pass_rate else 0.0
        grade = f"{pass_rate_pct:.0f}%"

        # -- summary.json --
        summary = {
            "fixture_id": record.fixture_id,
            "format": record.format,
            "prompt_id": record.prompt_id,
            "run_id": record.run_id,
            "ape_version": record.ape_version,
            "workflow_hash": record.workflow_hash,
            "git_sha": record.git_sha,
            "model": record.model,
            "session_id": session_id,
            "started_at": record.started_at,
            "completed_at": record.completed_at,
            "wall_clock_ms": record.wall_clock_ms,
            "wall_clock_formatted": format_duration_hms(record.wall_clock_ms),
            "duration_api_ms": duration_api_ms,
            "api_time_formatted": format_duration_hms(duration_api_ms),
            "prompt_text": record.prompt_text,
            "exit_code": record.exit_code,
            "error": record.error,
            "succeeded": succeeded,
            "max_turns_configured": record.max_turns_configured,
            "hit_turn_limit": record.hit_turn_limit,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cost_usd": cost_usd,
            "num_turns": num_turns,
            "model_usage": model_usage,
            "total": record.total,
            "passed": record.passed,
            "failed": record.failed,
            "skipped": record.skipped,
            "disabled": record.disabled,
            "not_applicable": record.not_applicable,
            "pass_rate": record.pass_rate,
            "grade": grade,
            "checks": check_files,
            "eval_conditions": record.eval_conditions,
            "eval_variables": record.eval_variables,
            "timestamp": record.timestamp,
        }
        with open(run_dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        return run_dir

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load_run(self, fixture_id: str, fmt: str, prompt_id: str, run_id: int) -> RunRecord:
        """Load a single RunRecord from disk.

        Tries the new per-run directory format first, then falls back
        to the legacy flat file under ``raw/``.
        """
        run_dir = self._run_dir(fixture_id, fmt, prompt_id, run_id)
        summary_path = run_dir / "summary.json"

        if summary_path.exists():
            return self._load_from_directory(run_dir)

        # Legacy fallback
        legacy_path = self._run_path_legacy(fixture_id, fmt, prompt_id, run_id)
        with open(legacy_path, "r", encoding="utf-8") as f:
            return RunRecord.from_dict(json.load(f))

    def _load_from_directory(self, run_dir: Path) -> RunRecord:
        """Reconstruct a RunRecord from a per-run directory."""
        with open(run_dir / "summary.json", "r", encoding="utf-8") as f:
            summary = json.load(f)

        # Load individual check outcomes
        outcomes = []
        for check_ref in summary.get("checks", []):
            check_file = run_dir / check_ref.get("file", "")
            if check_file.exists():
                with open(check_file, "r", encoding="utf-8") as f:
                    outcomes.append(json.load(f))
            else:
                outcomes.append(check_ref)

        return RunRecord(
            fixture_id=summary.get("fixture_id", ""),
            format=summary.get("format", ""),
            prompt_id=summary.get("prompt_id", ""),
            run_id=summary.get("run_id", 0),
            outcomes=outcomes,
            total=summary.get("total", 0),
            passed=summary.get("passed", 0),
            failed=summary.get("failed", 0),
            skipped=summary.get("skipped", 0),
            disabled=summary.get("disabled", 0),
            not_applicable=summary.get("not_applicable", 0),
            pass_rate=summary.get("pass_rate", 0.0),
            error=summary.get("error", "") or "",
            wall_clock_ms=summary.get("wall_clock_ms", 0.0),
            exit_code=summary.get("exit_code", 0),
            model=summary.get("model", ""),
            session_id=summary.get("session_id", ""),
            prompt_text=summary.get("prompt_text", ""),
            eval_conditions=summary.get("eval_conditions", {}),
            eval_variables=summary.get("eval_variables", {}),
            input_tokens=summary.get("input_tokens", 0),
            output_tokens=summary.get("output_tokens", 0),
            cache_creation_tokens=summary.get("cache_creation_tokens", 0),
            cache_read_tokens=summary.get("cache_read_tokens", 0),
            cost_usd=summary.get("cost_usd", 0.0),
            num_turns=summary.get("num_turns", 0),
            started_at=summary.get("started_at", ""),
            completed_at=summary.get("completed_at", ""),
            duration_api_ms=summary.get("duration_api_ms", 0.0),
            max_turns_configured=summary.get("max_turns_configured", 0),
            hit_turn_limit=summary.get("hit_turn_limit", False),
            model_usage=summary.get("model_usage", {}),
            ape_version=summary.get("ape_version", ""),
            workflow_hash=summary.get("workflow_hash", ""),
            git_sha=summary.get("git_sha", ""),
            timestamp=summary.get("timestamp", ""),
        )

    def update_run(self, record: RunRecord) -> None:
        """Overwrite an existing RunRecord on disk.

        Loads stream.json from the existing run directory (if any) and
        re-saves so that the stream data is preserved while the summary
        and check files reflect the updated record.
        """
        run_dir = self._run_dir(record.fixture_id, record.format, record.prompt_id, record.run_id)
        raw_output = ""
        stream_path = run_dir / "stream.json"
        if stream_path.exists():
            raw_output = self.load_raw_output(
                record.fixture_id, record.format, record.prompt_id, record.run_id,
            )
        self.save_run(record, raw_output=raw_output)

    # ------------------------------------------------------------------
    # Iterators
    # ------------------------------------------------------------------

    def all_runs(self) -> Iterator[RunRecord]:
        """Iterate over every stored RunRecord (new format then legacy)."""
        seen: set[tuple[str, str, str, int]] = set()

        # New format: scan for summary.json files
        if self.results_dir.is_dir():
            for summary_path in sorted(self.results_dir.rglob("summary.json")):
                # Exclude paths under raw/ or logs/ (legacy dirs)
                rel = summary_path.relative_to(self.results_dir)
                parts = rel.parts
                if parts and parts[0] in ("raw", "logs", "reports"):
                    continue
                try:
                    record = self._load_from_directory(summary_path.parent)
                    key = (record.fixture_id, record.format, record.prompt_id, record.run_id)
                    if key not in seen:
                        seen.add(key)
                        yield record
                except (json.JSONDecodeError, KeyError, OSError):
                    continue

        # Legacy format: scan raw/ for flat JSON files
        raw_dir = self.results_dir / "raw"
        if raw_dir.is_dir():
            for json_path in sorted(raw_dir.rglob("*.json")):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        record = RunRecord.from_dict(json.load(f))
                    key = (record.fixture_id, record.format, record.prompt_id, record.run_id)
                    if key not in seen:
                        seen.add(key)
                        yield record
                except (json.JSONDecodeError, KeyError):
                    continue

    def runs_for_fixture(
        self,
        fixture_id: str,
        fmt: Optional[str] = None,
        prompt_id: Optional[str] = None,
    ) -> Iterator[RunRecord]:
        """Iterate over runs matching the given filters."""
        # New format
        base = self.results_dir / fixture_id
        if fmt:
            base = base / fmt
        if fmt and prompt_id:
            base = base / prompt_id

        seen: set[tuple[str, str, str, int]] = set()

        if base.is_dir():
            for summary_path in sorted(base.rglob("summary.json")):
                try:
                    record = self._load_from_directory(summary_path.parent)
                    key = (record.fixture_id, record.format, record.prompt_id, record.run_id)
                    if key not in seen:
                        seen.add(key)
                        yield record
                except (json.JSONDecodeError, KeyError, OSError):
                    continue

        # Legacy fallback
        legacy_base = self.results_dir / "raw" / fixture_id
        if fmt:
            legacy_base = legacy_base / fmt
        if fmt and prompt_id:
            legacy_base = legacy_base / prompt_id

        if legacy_base.is_dir():
            for json_path in sorted(legacy_base.rglob("*.json")):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        record = RunRecord.from_dict(json.load(f))
                    key = (record.fixture_id, record.format, record.prompt_id, record.run_id)
                    if key not in seen:
                        seen.add(key)
                        yield record
                except (json.JSONDecodeError, KeyError):
                    continue

    # ------------------------------------------------------------------
    # Run ID management
    # ------------------------------------------------------------------

    def next_run_id(self, fixture_id: str, fmt: str, prompt_id: str) -> int:
        """Return the next available run ID for a fixture/format/prompt triple.

        Detects both new-format directories and legacy flat files.
        """
        existing: list[int] = []

        # New format: directories with numeric names
        new_dir = self.results_dir / fixture_id / fmt / prompt_id
        if new_dir.is_dir():
            for p in new_dir.iterdir():
                if p.is_dir() and p.name.isdigit():
                    existing.append(int(p.name))

        # Legacy format
        legacy_dir = self.results_dir / "raw" / fixture_id / fmt / prompt_id
        if legacy_dir.is_dir():
            for p in legacy_dir.glob("*.json"):
                if p.stem.isdigit():
                    existing.append(int(p.stem))

        return max(existing) + 1 if existing else 0

    # ------------------------------------------------------------------
    # Stream / state / raw_output loaders
    # ------------------------------------------------------------------

    def load_stream(self, fixture_id: str, fmt: str, prompt_id: str, run_id: int) -> list[dict]:
        """Load stream.json as a list of dicts."""
        stream_path = self._run_dir(fixture_id, fmt, prompt_id, run_id) / "stream.json"
        with open(stream_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_state(self, fixture_id: str, fmt: str, prompt_id: str, run_id: int) -> dict:
        """Load state.json."""
        state_path = self._run_dir(fixture_id, fmt, prompt_id, run_id) / "state.json"
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_raw_output(self, fixture_id: str, fmt: str, prompt_id: str, run_id: int) -> str:
        """Load stream.json and convert back to JSONL string.

        For consumers that need the original JSONL format.
        """
        stream_data = self.load_stream(fixture_id, fmt, prompt_id, run_id)
        return "\n".join(json.dumps(obj) for obj in stream_data)

    # ------------------------------------------------------------------
    # Trace compat (deprecated stubs)
    # ------------------------------------------------------------------

    def save_trace(self, record: RunRecord, trace_data: Any) -> Path:
        """Deprecated: stream.json replaces trace files.

        Kept for backward compatibility — writes to the legacy log path.
        """
        path = self._trace_path(record.fixture_id, record.format, record.prompt_id, record.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(trace_data, f, indent=2)
        return path

    def load_trace(self, fixture_id: str, fmt: str, prompt_id: str, run_id: int) -> Optional[dict]:
        """Deprecated: use load_stream() instead.

        Falls back to legacy trace file if stream.json doesn't exist.
        """
        # Try stream.json first
        stream_path = self._run_dir(fixture_id, fmt, prompt_id, run_id) / "stream.json"
        if stream_path.exists():
            with open(stream_path, "r", encoding="utf-8") as f:
                return json.load(f)

        # Legacy fallback
        path = self._trace_path(fixture_id, fmt, prompt_id, run_id)
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

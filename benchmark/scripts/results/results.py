"""
Aggregate and summarize benchmark evaluation results.

Takes a list of CheckResults (from the evaluator) and produces structured
summaries for human review and cross-format comparison.

Public API
----------
summarize_run(results, metadata) -> RunSummary
    Summarize a single benchmark run (one fixture × one format × one prompt).

summarize_comparison(summaries) -> ComparisonSummary
    Compare RunSummaries across formats for the same fixture + prompt.

format_run_summary(summary) -> str
    Human-readable text for one run.

format_comparison(comparison) -> str
    Human-readable comparison table across formats.

write_json(summary_or_comparison, path)
    Serialize to JSON for machine consumption.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Data types (mirrors evaluator.CheckResult but decoupled)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CheckOutcome:
    """Flattened outcome for one check in a run."""

    check_id: str
    phase: str
    passed: Optional[bool]   # None = skipped
    skip_reason: Optional[str]
    detail: Optional[str] = None  # explanation of why the check failed


@dataclass
class RunMetadata:
    """Context about the benchmark run that produced these results."""

    fixture_id: str
    format: str              # "plain-text" | "adhoc-xml" | "ape"
    prompt_id: str
    model: str = ""
    session_id: str = ""
    timestamp: str = ""


@dataclass
class RunSummary:
    """Aggregated results for a single benchmark run."""

    metadata: RunMetadata
    total: int
    passed: int
    failed: int
    skipped: int
    pass_rate: float         # passed / (total - skipped), 0.0 if all skipped
    outcomes: list[CheckOutcome]


@dataclass
class FormatScore:
    """One format's scores within a comparison."""

    format: str
    total: int
    passed: int
    failed: int
    skipped: int
    pass_rate: float


@dataclass
class ComparisonSummary:
    """Cross-format comparison for the same fixture + prompt."""

    fixture_id: str
    prompt_id: str
    formats: list[FormatScore]
    per_check: dict[str, dict[str, Optional[bool]]]  # {check_id: {format: passed}}


# ---------------------------------------------------------------------------
# Summarization
# ---------------------------------------------------------------------------

def make_outcome(check_id: str, phase: str, passed: Optional[bool],
                 skip_reason: Optional[str],
                 detail: Optional[str] = None) -> CheckOutcome:
    """Create a CheckOutcome from individual fields."""
    return CheckOutcome(
        check_id=check_id,
        phase=phase,
        passed=passed,
        skip_reason=skip_reason,
        detail=detail,
    )


def summarize_run(outcomes: list[CheckOutcome], metadata: RunMetadata) -> RunSummary:
    """
    Aggregate a list of CheckOutcomes into a RunSummary.

    Pass rate is computed over evaluated (non-skipped) checks only.
    """
    total = len(outcomes)
    passed = sum(1 for o in outcomes if o.passed is True)
    skipped = sum(1 for o in outcomes if o.passed is None)
    failed = total - passed - skipped

    evaluated = total - skipped
    pass_rate = (passed / evaluated) if evaluated > 0 else 0.0

    return RunSummary(
        metadata=metadata,
        total=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        pass_rate=round(pass_rate, 4),
        outcomes=outcomes,
    )


def summarize_comparison(summaries: list[RunSummary]) -> ComparisonSummary:
    """
    Compare multiple RunSummaries that share the same fixture + prompt but
    differ by format.

    Raises ValueError if summaries have mismatched fixture_id or prompt_id.
    """
    if not summaries:
        raise ValueError("Cannot compare empty list of summaries")

    fixture_ids = {s.metadata.fixture_id for s in summaries}
    prompt_ids = {s.metadata.prompt_id for s in summaries}

    if len(fixture_ids) > 1:
        raise ValueError(f"Mismatched fixture_ids: {fixture_ids}")
    if len(prompt_ids) > 1:
        raise ValueError(f"Mismatched prompt_ids: {prompt_ids}")

    fixture_id = fixture_ids.pop()
    prompt_id = prompt_ids.pop()

    formats = []
    for s in summaries:
        formats.append(FormatScore(
            format=s.metadata.format,
            total=s.total,
            passed=s.passed,
            failed=s.failed,
            skipped=s.skipped,
            pass_rate=s.pass_rate,
        ))

    # Build per-check cross-format view
    all_check_ids: list[str] = []
    seen: set[str] = set()
    for s in summaries:
        for o in s.outcomes:
            if o.check_id not in seen:
                all_check_ids.append(o.check_id)
                seen.add(o.check_id)

    per_check: dict[str, dict[str, Optional[bool]]] = {}
    for check_id in all_check_ids:
        per_check[check_id] = {}
        for s in summaries:
            outcome = next(
                (o for o in s.outcomes if o.check_id == check_id), None
            )
            per_check[check_id][s.metadata.format] = (
                outcome.passed if outcome else None
            )

    return ComparisonSummary(
        fixture_id=fixture_id,
        prompt_id=prompt_id,
        formats=formats,
        per_check=per_check,
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_run_summary(summary: RunSummary, record: Any = None) -> str:
    """
    Human-readable text summary of one benchmark run with Rich markup.

    Parameters
    ----------
    summary : RunSummary
        The evaluation results.
    record : RunRecord, optional
        If provided, includes timing, token, and cost details.
    """
    # Pass rate color coding
    rate = summary.pass_rate
    if rate >= 0.9:
        rate_color = "green"
    elif rate >= 0.5:
        rate_color = "yellow"
    else:
        rate_color = "red"

    lines = [
        f"[bold cyan]{summary.metadata.fixture_id}[/bold cyan] / "
        f"[cyan]{summary.metadata.format}[/cyan] / "
        f"[cyan]{summary.metadata.prompt_id}[/cyan]",
    ]
    if summary.metadata.model:
        lines.append(f"  Model:   [dim]{summary.metadata.model}[/dim]")
    if summary.metadata.session_id:
        lines.append(f"  Session: [dim]{summary.metadata.session_id}[/dim]")

    lines.append(
        f"  Checks:  [green]{summary.passed} passed[/green]  "
        f"[red]{summary.failed} failed[/red]  "
        f"[dim]{summary.skipped} skipped[/dim]  "
        f"(of {summary.total})"
    )
    lines.append(f"  Rate:    [{rate_color}]{summary.pass_rate:.1%}[/{rate_color}]")

    # Timing / tokens / cost from RunRecord
    if record is not None:
        detail_parts = []
        wall = getattr(record, "wall_clock_ms", 0)
        if wall:
            secs = wall / 1000
            if secs >= 60:
                detail_parts.append(f"{secs / 60:.1f}m")
            else:
                detail_parts.append(f"{secs:.1f}s")
        inp = getattr(record, "input_tokens", 0)
        out = getattr(record, "output_tokens", 0)
        if inp or out:
            detail_parts.append(f"{inp + out:,} tokens ({inp:,} in / {out:,} out)")
        cost = getattr(record, "cost_usd", 0)
        if cost:
            detail_parts.append(f"${cost:.4f}")
        turns = getattr(record, "num_turns", 0)
        if turns:
            detail_parts.append(f"{turns} turns")
        if detail_parts:
            lines.append(f"  [dim]{' | '.join(detail_parts)}[/dim]")

    failed = [o for o in summary.outcomes if o.passed is False]
    if failed:
        lines.append("  [red]Failed checks:[/red]")
        for o in failed:
            line = f"    [red]{o.check_id}[/red] ({o.phase})"
            if o.detail:
                line += f"\n      [dim]{o.detail}[/dim]"
            lines.append(line)

    skipped = [o for o in summary.outcomes if o.passed is None]
    if skipped:
        lines.append("  [yellow]Skipped checks:[/yellow]")
        for o in skipped:
            reason = o.skip_reason or "unknown"
            lines.append(f"    [dim]{o.check_id}: {reason}[/dim]")

    return "\n".join(lines)


def format_comparison(comparison: ComparisonSummary) -> str:
    """Human-readable comparison table across formats."""
    lines = [
        f"Comparison: {comparison.fixture_id} / {comparison.prompt_id}",
        "",
    ]

    # Header
    fmt_names = [f.format for f in comparison.formats]
    header = f"  {'Check':<40s}" + "".join(f"  {f:<14s}" for f in fmt_names)
    lines.append(header)
    lines.append("  " + "-" * (40 + 16 * len(fmt_names)))

    # Per-check rows
    for check_id, results in comparison.per_check.items():
        row = f"  {check_id:<40s}"
        for fmt in fmt_names:
            val = results.get(fmt)
            if val is True:
                cell = "PASS"
            elif val is False:
                cell = "FAIL"
            else:
                cell = "SKIP"
            row += f"  {cell:<14s}"
        lines.append(row)

    # Summary row
    lines.append("  " + "-" * (40 + 16 * len(fmt_names)))
    summary_row = f"  {'Pass rate':<40s}"
    for f in comparison.formats:
        summary_row += f"  {f.pass_rate:.1%}{'':<10s}"
    lines.append(summary_row)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def to_dict(obj: Any) -> Any:
    """Recursively convert dataclasses to dicts for JSON serialization."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


def write_json(data: Any, path: Path) -> None:
    """Serialize a dataclass (RunSummary or ComparisonSummary) to JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_dict(data), f, indent=2)


def load_run_summary_json(path: Path) -> dict:
    """Load a previously written RunSummary JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_json(path: Path) -> dict:
    """Load any JSON file as a dict."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

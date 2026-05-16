"""
Aggregate and summarize benchmark evaluation results.

Takes a list of CheckResults (from the evaluator) and produces structured
summaries for human review and cross-format comparison.

Integrates statistical analysis (Section 6.2.5 of APE Benchmark Audit):
- Pairwise format comparisons with bootstrap CIs
- Holm-Bonferroni multiple comparison correction
- Cohen's d effect sizes

Public API
----------
summarize_run(results, metadata) -> RunSummary
    Summarize a single benchmark run (one fixture × one format × one prompt).

summarize_comparison(summaries) -> ComparisonSummary
    Compare RunSummaries across formats for the same fixture + prompt.

compute_statistical_report(comparison) -> StatisticalReport
    Analyze format differences with statistical tests and corrections.

format_run_summary(summary) -> str
    Human-readable text for one run.

format_comparison(comparison) -> str
    Human-readable comparison table across formats.

format_statistical_report(report) -> str
    Human-readable statistical analysis with Rich markup.

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
    category: Optional[str] = None  # "adherence" | "tool_usage" | None for unknown
    metric_value: Any = None  # resolved metric data used for determination
    target_value: Any = None  # resolved target data compared against
    operator: Optional[str] = None  # operator applied to metric/target
    eval_trace: Optional[list[dict]] = None  # structured audit trail


@dataclass
class RunMetadata:
    """Context about the benchmark run that produced these results."""

    fixture_id: str
    format: str              # "plain-text" | "adhoc-xml" | "ape" | ...
    prompt_id: str
    source: str = ""         # "claude-md" | "prompt" | "" (no-workflow only)
    model: str = ""
    session_id: str = ""
    timestamp: str = ""


@dataclass
class CategoryScore:
    """Scores for a single category of checks within a run."""

    category: str
    total: int
    passed: int
    failed: int
    skipped: int             # disabled + not_applicable
    disabled: int            # checks turned off via `enabled: false`
    not_applicable: int      # everything else that produced passed=None
    pass_rate: float         # passed / (total - skipped), 0.0 if all skipped


@dataclass
class RunSummary:
    """Aggregated results for a single benchmark run."""

    metadata: RunMetadata
    total: int
    passed: int
    failed: int
    skipped: int             # disabled + not_applicable
    disabled: int
    not_applicable: int
    pass_rate: float         # passed / (total - skipped), 0.0 if all skipped
    outcomes: list[CheckOutcome]
    category_scores: dict[str, CategoryScore] = field(default_factory=dict)


@dataclass
class FormatScore:
    """One (format, source) cell's scores within a comparison."""

    format: str
    total: int
    passed: int
    failed: int
    skipped: int             # disabled + not_applicable
    disabled: int
    not_applicable: int
    pass_rate: float
    source: str = ""         # "claude-md" | "prompt" | "" (no-workflow only)


@dataclass
class ComparisonSummary:
    """Cross-format comparison for the same fixture + prompt."""

    fixture_id: str
    prompt_id: str
    formats: list[FormatScore]
    per_check: dict[str, dict[str, Optional[bool]]]  # {check_id: {format: passed}}
    per_category: dict[str, dict[str, CategoryScore]] = field(default_factory=dict)  # {category: {format: CategoryScore}}


# ---------------------------------------------------------------------------
# Summarization
# ---------------------------------------------------------------------------

def make_outcome(check_id: str, phase: str, passed: Optional[bool],
                 skip_reason: Optional[str],
                 detail: Optional[str] = None,
                 category: Optional[str] = None,
                 metric_value: Any = None,
                 target_value: Any = None,
                 operator: Optional[str] = None,
                 eval_trace: Optional[list[dict]] = None) -> CheckOutcome:
    """Create a CheckOutcome from individual fields."""
    return CheckOutcome(
        check_id=check_id,
        phase=phase,
        passed=passed,
        skip_reason=skip_reason,
        detail=detail,
        category=category,
        metric_value=metric_value,
        target_value=target_value,
        operator=operator,
        eval_trace=eval_trace,
    )


_DISABLED_SKIP_REASON = "check disabled"


def _format_key(meta: "RunMetadata") -> str:
    """Composite ``(format, source)`` key used in cross-format comparison maps.

    Source-bound rows surface as ``"<format>:<source>"`` so that, e.g.,
    ``markdown:claude-md`` and ``markdown:prompt`` aren't silently pooled
    together. No-workflow keeps its bare format name because the source
    layer doesn't apply.
    """
    if meta.source:
        return f"{meta.format}:{meta.source}"
    return meta.format


def _classify_skip(skip_reason: Optional[str]) -> str:
    """Bucket a skip into 'disabled' (config kill switch) or 'not_applicable'."""
    if skip_reason == _DISABLED_SKIP_REASON:
        return "disabled"
    return "not_applicable"


def summarize_run(outcomes: list[CheckOutcome], metadata: RunMetadata) -> RunSummary:
    """
    Aggregate a list of CheckOutcomes into a RunSummary.

    Pass rate is computed over evaluated (non-skipped) checks only.
    Computes per-category scores if category information is available.
    """
    total = len(outcomes)
    passed = sum(1 for o in outcomes if o.passed is True)
    skipped_outcomes = [o for o in outcomes if o.passed is None]
    skipped = len(skipped_outcomes)
    disabled = sum(1 for o in skipped_outcomes if _classify_skip(o.skip_reason) == "disabled")
    not_applicable = skipped - disabled
    failed = total - passed - skipped

    evaluated = total - skipped
    pass_rate = (passed / evaluated) if evaluated > 0 else 0.0

    # Compute per-category scores
    category_scores = _summarize_run_by_category(outcomes)

    return RunSummary(
        metadata=metadata,
        total=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        disabled=disabled,
        not_applicable=not_applicable,
        pass_rate=round(pass_rate, 4),
        outcomes=outcomes,
        category_scores=category_scores,
    )


def _summarize_run_by_category(outcomes: list[CheckOutcome]) -> dict[str, CategoryScore]:
    """
    Compute category-level scores from a list of CheckOutcomes.

    Returns a dict mapping category name to CategoryScore. Only includes
    outcomes with a non-None category.
    """
    by_category: dict[str, list[CheckOutcome]] = {}
    for outcome in outcomes:
        cat = outcome.category or "uncategorized"
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(outcome)

    result = {}
    for cat, cat_outcomes in by_category.items():
        total = len(cat_outcomes)
        passed = sum(1 for o in cat_outcomes if o.passed is True)
        skipped_outcomes = [o for o in cat_outcomes if o.passed is None]
        skipped = len(skipped_outcomes)
        disabled = sum(1 for o in skipped_outcomes if _classify_skip(o.skip_reason) == "disabled")
        not_applicable = skipped - disabled
        failed = total - passed - skipped

        evaluated = total - skipped
        cat_pass_rate = (passed / evaluated) if evaluated > 0 else 0.0

        result[cat] = CategoryScore(
            category=cat,
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            disabled=disabled,
            not_applicable=not_applicable,
            pass_rate=round(cat_pass_rate, 4),
        )

    return result


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
            source=s.metadata.source,
            total=s.total,
            passed=s.passed,
            failed=s.failed,
            skipped=s.skipped,
            disabled=s.disabled,
            not_applicable=s.not_applicable,
            pass_rate=s.pass_rate,
        ))

    # Build per-check cross-(format, source) view. Same-format runs from
    # different sources stay distinct via the composite key.
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
            per_check[check_id][_format_key(s.metadata)] = (
                outcome.passed if outcome else None
            )

    # Build per-category cross-(format, source) view
    per_category: dict[str, dict[str, CategoryScore]] = {}
    all_categories: set[str] = set()
    for s in summaries:
        all_categories.update(s.category_scores.keys())

    for cat in all_categories:
        per_category[cat] = {}
        for s in summaries:
            if cat in s.category_scores:
                per_category[cat][_format_key(s.metadata)] = s.category_scores[cat]

    return ComparisonSummary(
        fixture_id=fixture_id,
        prompt_id=prompt_id,
        formats=formats,
        per_check=per_check,
        per_category=per_category,
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

    header = (
        f"[bold cyan]{summary.metadata.fixture_id}[/bold cyan] / "
        f"[cyan]{summary.metadata.format}[/cyan]"
    )
    if summary.metadata.source:
        header += f" / [cyan]{summary.metadata.source}[/cyan]"
    header += f" / [cyan]{summary.metadata.prompt_id}[/cyan]"
    lines = [header]
    if summary.metadata.model:
        lines.append(f"  Model:   [dim]{summary.metadata.model}[/dim]")
    if summary.metadata.session_id:
        lines.append(f"  Session: [dim]{summary.metadata.session_id}[/dim]")

    skip_parts = []
    if summary.disabled:
        skip_parts.append(f"{summary.disabled} disabled")
    if summary.not_applicable:
        skip_parts.append(f"{summary.not_applicable} not_applicable")
    skip_text = "  ".join(skip_parts) if skip_parts else "0 skipped"
    lines.append(
        f"  Checks:  [green]{summary.passed} passed[/green]  "
        f"[red]{summary.failed} failed[/red]  "
        f"[dim]{skip_text}[/dim]  "
        f"(of {summary.total})"
    )
    lines.append(f"  Rate:    [{rate_color}]{summary.pass_rate:.1%}[/{rate_color}]")

    # Category breakdown
    if summary.category_scores:
        lines.append("  [bold]By Category:[/bold]")
        for cat_name in sorted(summary.category_scores.keys()):
            cat_score = summary.category_scores[cat_name]
            cat_color = "green" if cat_score.pass_rate >= 0.9 else "yellow" if cat_score.pass_rate >= 0.5 else "red"
            lines.append(
                f"    [cyan]{cat_name}[/cyan]: [{cat_color}]{cat_score.pass_rate:.1%}[/{cat_color}]  "
                f"({cat_score.passed}/{cat_score.total - cat_score.skipped})"
            )

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
        disabled_checks = [o for o in skipped if _classify_skip(o.skip_reason) == "disabled"]
        not_applicable_checks = [o for o in skipped if _classify_skip(o.skip_reason) != "disabled"]
        if disabled_checks:
            lines.append("  [yellow]Disabled checks:[/yellow]")
            for o in disabled_checks:
                reason = o.skip_reason or "unknown"
                lines.append(f"    [dim]{o.check_id}: {reason}[/dim]")
        if not_applicable_checks:
            lines.append("  [yellow]Not-applicable checks:[/yellow]")
            for o in not_applicable_checks:
                reason = o.skip_reason or "unknown"
                lines.append(f"    [dim]{o.check_id}: {reason}[/dim]")

    return "\n".join(lines)


def _format_score_key(score: FormatScore) -> str:
    if score.source:
        return f"{score.format}:{score.source}"
    return score.format


def format_comparison(comparison: ComparisonSummary) -> str:
    """Human-readable comparison table across (format, source) cells."""
    lines = [
        f"Comparison: {comparison.fixture_id} / {comparison.prompt_id}",
        "",
    ]

    # Column keys carry both format and source where applicable.
    fmt_keys = [_format_score_key(f) for f in comparison.formats]
    header = f"  {'Check':<40s}" + "".join(f"  {f:<18s}" for f in fmt_keys)
    lines.append(header)
    lines.append("  " + "-" * (40 + 20 * len(fmt_keys)))

    # Per-check rows
    for check_id, results in comparison.per_check.items():
        row = f"  {check_id:<40s}"
        for fmt in fmt_keys:
            val = results.get(fmt)
            if val is True:
                cell = "PASS"
            elif val is False:
                cell = "FAIL"
            else:
                cell = "SKIP"
            row += f"  {cell:<18s}"
        lines.append(row)

    # Summary row
    lines.append("  " + "-" * (40 + 20 * len(fmt_keys)))
    summary_row = f"  {'Pass rate':<40s}"
    for f in comparison.formats:
        summary_row += f"  {f.pass_rate:.1%}{'':<14s}"
    lines.append(summary_row)

    # Category comparison section
    if comparison.per_category:
        lines.append("")
        lines.append("Category Breakdown:")
        lines.append("")
        for cat_name in sorted(comparison.per_category.keys()):
            cat_results = comparison.per_category[cat_name]
            lines.append(f"  {cat_name}:")
            for fmt in fmt_keys:
                if fmt in cat_results:
                    score = cat_results[fmt]
                    lines.append(
                        f"    {fmt}: {score.pass_rate:.1%} ({score.passed}/{score.total - score.skipped})"
                    )

    return "\n".join(lines)


def compute_statistical_report(
    comparison: ComparisonSummary,
    alpha: float = 0.05,
    n_bootstrap: int = 10000,
) -> Any:
    """
    Compute statistical analysis (Section 6.2.5) for a format comparison.

    Performs pairwise comparisons of formats using bootstrap CIs, permutation tests,
    and Holm-Bonferroni correction. This requires the statistical_report module.

    Parameters
    ----------
    comparison : ComparisonSummary
        The comparison to analyze.
    alpha : float
        Significance level.
    n_bootstrap : int
        Number of bootstrap resamples.

    Returns
    -------
    StatisticalReport
        Complete statistical analysis with corrections. Returns None if statistical
        analysis is not available (optional dependency).
    """
    try:
        from statistical_report import analyze_format_effects
    except ImportError:
        return None

    # Build format_scores dict: {format_name: [pass_rate per run]}
    # For now, we use a single pass_rate per format from the comparison.
    # This is a placeholder; in a full pipeline, you'd pass per-run data.
    format_scores = {
        f.format: [f.pass_rate]
        for f in comparison.formats
    }

    try:
        return analyze_format_effects(format_scores, alpha=alpha, n_bootstrap=n_bootstrap)
    except (ValueError, RuntimeError):
        # If statistical analysis fails (e.g., insufficient samples), return None
        return None


def format_statistical_report(report: Any) -> str:
    """
    Format a statistical report for human display.

    Parameters
    ----------
    report : StatisticalReport
        The report to format.

    Returns
    -------
    str
        Rich-formatted text.
    """
    if report is None:
        return "[yellow]Statistical analysis not available.[/yellow]"

    try:
        from statistical_report import format_statistical_report as format_report
        return format_report(report)
    except ImportError:
        return "[yellow]Statistical analysis module not available.[/yellow]"


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

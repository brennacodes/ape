"""
Statistical analysis and reporting for cross-format benchmark comparisons.

This module integrates the statistical infrastructure (bootstrap CIs, permutation tests,
effect sizes, multiple comparison corrections) into the results pipeline for Section 6.2.5
of the APE Benchmark Audit.

Public API
----------
PairwiseFormatComparison   — Result of comparing two formats.
StatisticalReport          — Aggregated statistical analysis across all format pairs.
analyze_format_effects()   — Perform all pairwise comparisons with correction.
format_statistical_report() — Produce human-readable report with Rich markup.
"""

from __future__ import annotations

import itertools
import numpy as np
from dataclasses import dataclass
from typing import Optional

# Import from the existing stats modules
import sys
from pathlib import Path

# Add scripts to path for importing stats modules
_scripts_dir = Path(__file__).parent.parent
sys.path.insert(0, str(_scripts_dir))

from stats.bootstrap import paired_analysis, PairedResult
from stats.corrections import holm_bonferroni


@dataclass(frozen=True)
class PairwiseFormatComparison:
    """Statistical comparison between two formats (pairwise)."""

    format_a: str
    format_b: str
    n_runs: int
    mean_a: float
    mean_b: float
    mean_delta: float
    ci_lower: float
    ci_upper: float
    p_value: float
    p_value_corrected: float
    effect_size: float  # Cohen's d
    significant: bool  # after correction


@dataclass(frozen=True)
class StatisticalReport:
    """Full statistical analysis across all format pairs."""

    comparisons: list[PairwiseFormatComparison]
    correction_method: str  # "holm_bonferroni"
    alpha: float
    n_comparisons: int
    any_significant: bool


def analyze_format_effects(
    format_scores: dict[str, list[float]],
    alpha: float = 0.05,
    n_bootstrap: int = 10000,
    rng: Optional[np.random.Generator] = None,
) -> StatisticalReport:
    """
    Perform all pairwise format comparisons with multiple comparison correction.

    This function:
    1. Generates all pairwise combinations of formats
    2. For each pair, runs paired_analysis() to get raw CIs and p-values
    3. Collects all raw p-values and applies Holm-Bonferroni correction
    4. Reports Cohen's d effect sizes
    5. Returns a structured StatisticalReport

    Parameters
    ----------
    format_scores : dict[str, list[float]]
        Mapping from format name to list of per-run pass rates.
        Example: {"plain-text": [0.85, 0.90, 0.88], "ape": [0.92, 0.95, 0.93]}
    alpha : float
        Significance level for confidence intervals and hypothesis tests.
    n_bootstrap : int
        Number of bootstrap resamples for BCa confidence intervals.
    rng : np.random.Generator, optional
        Random number generator for reproducibility.

    Returns
    -------
    StatisticalReport
        Complete statistical analysis with corrections applied.

    Raises
    ------
    ValueError
        If fewer than 2 formats are provided.
    """
    if len(format_scores) < 2:
        raise ValueError("Need at least 2 formats to perform statistical comparison")

    # Validate that all format lists are non-empty
    for fmt, scores in format_scores.items():
        if not scores:
            raise ValueError(f"Format '{fmt}' has no scores")

    # Generate all pairwise combinations
    format_names = sorted(format_scores.keys())
    pairs = list(itertools.combinations(format_names, 2))

    # Perform paired analysis for each pair
    raw_comparisons: list[tuple[PairwiseFormatComparison, float]] = []
    raw_p_values: list[float] = []

    for fmt_a, fmt_b in pairs:
        scores_a = format_scores[fmt_a]
        scores_b = format_scores[fmt_b]

        # Handle case where formats have different numbers of runs
        # Use min(n) paired samples
        n_samples = min(len(scores_a), len(scores_b))

        if n_samples < 2:
            # Skip pairs where we don't have enough paired samples
            # (can't compute paired statistics with < 2 samples)
            continue

        scores_a = scores_a[:n_samples]
        scores_b = scores_b[:n_samples]

        # Compute paired analysis (ape_scores -> fmt_a, md_scores -> fmt_b)
        result: PairedResult = paired_analysis(
            ape_scores=scores_a,
            md_scores=scores_b,
            n_bootstrap=n_bootstrap,
            alpha=alpha,
            rng=rng,
        )

        # Create comparison object (without correction yet)
        comparison = PairwiseFormatComparison(
            format_a=fmt_a,
            format_b=fmt_b,
            n_runs=n_samples,
            mean_a=round(float(np.mean(scores_a)), 4),
            mean_b=round(float(np.mean(scores_b)), 4),
            mean_delta=result.mean_delta,
            ci_lower=result.ci_lower,
            ci_upper=result.ci_upper,
            p_value=result.p_value,
            p_value_corrected=result.p_value,  # Will be corrected below
            effect_size=result.effect_size,
            significant=False,  # Will be updated below
        )

        raw_comparisons.append((comparison, result.p_value))
        raw_p_values.append(result.p_value)

    # If no valid pairs remain, return empty report
    if not raw_comparisons:
        return StatisticalReport(
            comparisons=[],
            correction_method="holm_bonferroni",
            alpha=alpha,
            n_comparisons=0,
            any_significant=False,
        )

    # Apply Holm-Bonferroni correction to all p-values
    corrected_results = holm_bonferroni(raw_p_values, alpha=alpha)

    # Reconstruct comparisons with corrected p-values and significance
    final_comparisons: list[PairwiseFormatComparison] = []
    any_sig = False

    for (comparison, _), (corrected_p, is_sig) in zip(raw_comparisons, corrected_results):
        corrected_comparison = PairwiseFormatComparison(
            format_a=comparison.format_a,
            format_b=comparison.format_b,
            n_runs=comparison.n_runs,
            mean_a=comparison.mean_a,
            mean_b=comparison.mean_b,
            mean_delta=comparison.mean_delta,
            ci_lower=comparison.ci_lower,
            ci_upper=comparison.ci_upper,
            p_value=comparison.p_value,
            p_value_corrected=round(corrected_p, 4),
            effect_size=comparison.effect_size,
            significant=is_sig,
        )
        final_comparisons.append(corrected_comparison)
        if is_sig:
            any_sig = True

    return StatisticalReport(
        comparisons=final_comparisons,
        correction_method="holm_bonferroni",
        alpha=alpha,
        n_comparisons=len(raw_p_values),
        any_significant=any_sig,
    )


def format_statistical_report(report: StatisticalReport) -> str:
    """
    Produce a human-readable statistical report with Rich markup.

    Parameters
    ----------
    report : StatisticalReport
        The statistical analysis to format.

    Returns
    -------
    str
        Rich-formatted human-readable report.
    """
    lines = [
        "[bold cyan]Statistical Report: Format Comparisons[/bold cyan]",
        "",
        f"Correction method: [cyan]{report.correction_method}[/cyan]",
        f"Significance level (α): [cyan]{report.alpha}[/cyan]",
        f"Number of comparisons: [cyan]{report.n_comparisons}[/cyan]",
        "",
    ]

    if not report.comparisons:
        lines.append("[yellow]No valid format pairs for comparison (need ≥2 samples per pair).[/yellow]")
        return "\n".join(lines)

    # Summary of significant findings
    n_sig = sum(1 for c in report.comparisons if c.significant)
    if report.any_significant:
        lines.append(
            f"[green bold]Significant findings: {n_sig} of {len(report.comparisons)} comparisons[/green bold]"
        )
    else:
        lines.append(f"[yellow]No significant differences found after {report.correction_method} correction.[/yellow]")

    lines.append("")
    lines.append("[bold]Pairwise Comparisons[/bold]")
    lines.append("")

    # Format each comparison
    for i, comp in enumerate(report.comparisons, 1):
        # Header
        sig_marker = "[green bold]***[/green bold]" if comp.significant else ""
        lines.append(
            f"{i}. [bold]{comp.format_a}[/bold] vs [bold]{comp.format_b}[/bold] {sig_marker}"
        )

        # Means
        lines.append(
            f"   Mean (A): {comp.mean_a:.4f}  |  Mean (B): {comp.mean_b:.4f}  "
            f"|  Δ: {comp.mean_delta:+.4f}"
        )

        # Confidence interval
        ci_color = "green" if comp.ci_lower > 0 or comp.ci_upper < 0 else "yellow"
        lines.append(
            f"   95% CI on Δ: [{ci_color}][{comp.ci_lower:.4f}, {comp.ci_upper:.4f}][/{ci_color}]"
        )

        # Effect size interpretation
        d = abs(comp.effect_size)
        if d < 0.2:
            d_interp = "negligible"
        elif d < 0.5:
            d_interp = "small"
        elif d < 0.8:
            d_interp = "medium"
        else:
            d_interp = "large"

        lines.append(
            f"   Effect size (Cohen's d): {comp.effect_size:+.4f} ({d_interp})"
        )

        # P-values
        p_str = f"{comp.p_value:.4f}"
        p_corr_str = f"{comp.p_value_corrected:.4f}"
        sig_str = "[green]significant[/green]" if comp.significant else "[dim]not significant[/dim]"
        lines.append(
            f"   Raw p-value: [cyan]{p_str}[/cyan]  |  "
            f"Corrected p-value: [cyan]{p_corr_str}[/cyan]  ({sig_str})"
        )

        # Sample size
        lines.append(f"   Paired samples (n): {comp.n_runs}")
        lines.append("")

    # Footer with interpretation
    lines.append("[dim]Note: Confidence intervals that do not include 0 suggest significant differences.[/dim]")
    lines.append(
        "[dim]Effect sizes: |d| < 0.2 (negligible), 0.2-0.5 (small), 0.5-0.8 (medium), ≥0.8 (large)[/dim]"
    )

    return "\n".join(lines)

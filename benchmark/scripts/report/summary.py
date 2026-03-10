"""
Narrative summary generation for benchmark reports.

Public API
----------
generate_claim(metric_name, ape_val, md_val, ci, p_val, effect_size) -> str
generate_summary(all_results) -> str
"""

from __future__ import annotations

from typing import Any, Optional


def generate_claim(
    metric_name: str,
    ape_val: float,
    md_val: float,
    ci: tuple[float, float],
    p_val: float,
    effect_size: float,
) -> str:
    """
    Generate a calibrated claim based on statistical evidence.

    Uses effect size thresholds:
    - |d| >= 0.8: "substantially"
    - |d| >= 0.5: "meaningfully"
    - |d| >= 0.2: "modestly"
    - else: "no significant difference"

    Parameters
    ----------
    metric_name : str
        Human-readable metric name.
    ape_val, md_val : float
        Mean values for each condition.
    ci : tuple[float, float]
        95% confidence interval for the difference.
    p_val : float
        P-value from the statistical test.
    effect_size : float
        Cohen's d or similar effect size measure.
    """
    delta = ape_val - md_val
    direction = "higher" if delta > 0 else "lower"
    abs_d = abs(effect_size)

    if p_val > 0.05:
        return (
            f"{metric_name}: No significant difference between conditions "
            f"(APE={ape_val:.3f}, MD={md_val:.3f}, p={p_val:.3f}, d={effect_size:.2f})."
        )

    if abs_d >= 0.8:
        magnitude = "substantially"
    elif abs_d >= 0.5:
        magnitude = "meaningfully"
    elif abs_d >= 0.2:
        magnitude = "modestly"
    else:
        magnitude = "marginally"

    return (
        f"{metric_name}: APE is {magnitude} {direction} than MD "
        f"(APE={ape_val:.3f}, MD={md_val:.3f}, delta={delta:+.3f}, "
        f"95% CI [{ci[0]:.3f}, {ci[1]:.3f}], p={p_val:.3f}, d={effect_size:.2f})."
    )


def generate_summary(all_results: dict[str, Any]) -> str:
    """
    Generate a multi-paragraph narrative report from analysis results.

    Parameters
    ----------
    all_results : dict
        Mapping from metric name to result dict with keys:
        - ape_mean, md_mean: condition means
        - ci: (lower, upper) tuple
        - p_value: float
        - effect_size: float
        - significant: bool

    Returns
    -------
    str
        Formatted narrative report.
    """
    lines = []
    lines.append("=" * 60)
    lines.append("BENCHMARK ANALYSIS REPORT")
    lines.append("=" * 60)
    lines.append("")

    if not all_results:
        lines.append("No results to analyze.")
        return "\n".join(lines)

    # Separate significant and non-significant findings
    significant = {}
    non_significant = {}
    for name, r in all_results.items():
        if r.get("significant", False):
            significant[name] = r
        else:
            non_significant[name] = r

    # Significant findings
    if significant:
        lines.append("SIGNIFICANT FINDINGS")
        lines.append("-" * 40)
        for name, r in significant.items():
            claim = generate_claim(
                name,
                r.get("ape_mean", 0),
                r.get("md_mean", 0),
                r.get("ci", (0, 0)),
                r.get("p_value", 1),
                r.get("effect_size", 0),
            )
            lines.append(f"  {claim}")
        lines.append("")

    # Non-significant findings
    if non_significant:
        lines.append("NON-SIGNIFICANT FINDINGS")
        lines.append("-" * 40)
        for name, r in non_significant.items():
            claim = generate_claim(
                name,
                r.get("ape_mean", 0),
                r.get("md_mean", 0),
                r.get("ci", (0, 0)),
                r.get("p_value", 1),
                r.get("effect_size", 0),
            )
            lines.append(f"  {claim}")
        lines.append("")

    # Overall verdict
    lines.append("OVERALL VERDICT")
    lines.append("-" * 40)
    n_sig = len(significant)
    n_total = len(all_results)
    if n_sig == 0:
        lines.append("  No statistically significant differences found between conditions.")
    elif n_sig == n_total:
        lines.append("  All metrics show statistically significant differences.")
    else:
        lines.append(
            f"  {n_sig} of {n_total} metrics show statistically significant differences."
        )
    lines.append("")

    return "\n".join(lines)

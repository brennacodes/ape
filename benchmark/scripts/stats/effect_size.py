"""
Effect size computation for paired comparisons.

Public API
----------
cohens_d(group1, group2) -> float
odds_ratio(ape_success, ape_total, md_success, md_total) -> float
odds_ratio_ci(ape_success, ape_total, md_success, md_total, alpha) -> tuple
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats as scipy_stats


def cohens_d(
    group1: list[float] | np.ndarray,
    group2: list[float] | np.ndarray,
) -> float:
    """
    Compute paired Cohen's d_z.

    This is the standardized mean difference of the paired differences,
    appropriate for within-subjects designs.

    Parameters
    ----------
    group1, group2 : array-like
        Paired observations.

    Returns
    -------
    float
        Cohen's d_z. Positive means group1 > group2.
    """
    g1 = np.asarray(group1, dtype=float)
    g2 = np.asarray(group2, dtype=float)

    n = min(len(g1), len(g2))
    if n < 2:
        return 0.0

    deltas = g1[:n] - g2[:n]
    mean_d = float(np.mean(deltas))
    std_d = float(np.std(deltas, ddof=1))

    if std_d == 0:
        return 0.0

    return mean_d / std_d


def odds_ratio(
    ape_success: int,
    ape_total: int,
    md_success: int,
    md_total: int,
) -> float:
    """
    Compute odds ratio with Haldane-Anscombe correction.

    The correction adds 0.5 to each cell to handle zero counts.

    Parameters
    ----------
    ape_success, ape_total : int
        Successes and total for APE condition.
    md_success, md_total : int
        Successes and total for MD condition.

    Returns
    -------
    float
        Odds ratio (APE vs MD). Values > 1 favor APE.
    """
    # Haldane-Anscombe correction
    a = ape_success + 0.5
    b = (ape_total - ape_success) + 0.5
    c = md_success + 0.5
    d = (md_total - md_success) + 0.5

    return (a * d) / (b * c)


def odds_ratio_ci(
    ape_success: int,
    ape_total: int,
    md_success: int,
    md_total: int,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """
    Compute odds ratio with Woolf log-OR confidence interval.

    Parameters
    ----------
    ape_success, ape_total, md_success, md_total : int
        Cell counts.
    alpha : float
        Significance level.

    Returns
    -------
    (or_value, ci_lower, ci_upper) : tuple[float, float, float]
    """
    # Haldane-Anscombe correction
    a = ape_success + 0.5
    b = (ape_total - ape_success) + 0.5
    c = md_success + 0.5
    d = (md_total - md_success) + 0.5

    or_val = (a * d) / (b * c)
    log_or = math.log(or_val)

    # Woolf standard error of log(OR)
    se = math.sqrt(1/a + 1/b + 1/c + 1/d)

    z = scipy_stats.norm.ppf(1 - alpha / 2)
    ci_lower = math.exp(log_or - z * se)
    ci_upper = math.exp(log_or + z * se)

    return (round(or_val, 4), round(ci_lower, 4), round(ci_upper, 4))

"""
Bootstrap confidence intervals and permutation tests for paired analysis.

Public API
----------
PairedResult                    — results of a paired statistical comparison.
bca_bootstrap_ci(data, ...)     — BCa bootstrap confidence intervals.
sign_flip_permutation_test(deltas, ...) — two-sided permutation p-value.
paired_analysis(ape_scores, md_scores, ...) -> PairedResult
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats as scipy_stats


@dataclass(frozen=True)
class PairedResult:
    """Result of a paired statistical comparison."""
    mean_delta: float = 0.0
    ci_lower: float = 0.0
    ci_upper: float = 0.0
    p_value: float = 1.0
    effect_size: float = 0.0
    significant: bool = False
    n_samples: int = 0


def bca_bootstrap_ci(
    data: np.ndarray,
    n_bootstrap: int = 10000,
    alpha: float = 0.05,
    rng: np.random.Generator | None = None,
) -> tuple[float, float]:
    """
    Compute BCa (bias-corrected and accelerated) bootstrap confidence intervals.

    Parameters
    ----------
    data : np.ndarray
        1-D array of observed values.
    n_bootstrap : int
        Number of bootstrap resamples.
    alpha : float
        Significance level (e.g. 0.05 for 95% CI).
    rng : np.random.Generator, optional
        Random number generator for reproducibility.

    Returns
    -------
    (ci_lower, ci_upper) : tuple[float, float]
    """
    if rng is None:
        rng = np.random.default_rng()

    n = len(data)
    if n < 2:
        m = float(np.mean(data)) if n == 1 else 0.0
        return (m, m)

    observed_mean = float(np.mean(data))

    # Bootstrap resamples
    boot_means = np.array([
        float(np.mean(rng.choice(data, size=n, replace=True)))
        for _ in range(n_bootstrap)
    ])

    # Bias correction: z0
    prop_below = np.mean(boot_means < observed_mean)
    prop_below = np.clip(prop_below, 1e-10, 1 - 1e-10)
    z0 = scipy_stats.norm.ppf(prop_below)

    # Acceleration: jackknife
    jackknife_means = np.array([
        float(np.mean(np.delete(data, i)))
        for i in range(n)
    ])
    jack_mean = np.mean(jackknife_means)
    diff = jack_mean - jackknife_means
    numerator = np.sum(diff ** 3)
    denominator = 6.0 * (np.sum(diff ** 2)) ** 1.5
    a_hat = numerator / denominator if denominator != 0 else 0.0

    # Adjusted percentiles
    z_alpha_lower = scipy_stats.norm.ppf(alpha / 2)
    z_alpha_upper = scipy_stats.norm.ppf(1 - alpha / 2)

    def _adjusted_percentile(z_alpha: float) -> float:
        num = z0 + z_alpha
        denom = 1 - a_hat * num
        if denom == 0:
            return 0.5
        adjusted_z = z0 + num / denom
        return float(scipy_stats.norm.cdf(adjusted_z))

    pct_lower = _adjusted_percentile(z_alpha_lower) * 100
    pct_upper = _adjusted_percentile(z_alpha_upper) * 100

    pct_lower = np.clip(pct_lower, 0, 100)
    pct_upper = np.clip(pct_upper, 0, 100)

    ci_lower = float(np.percentile(boot_means, pct_lower))
    ci_upper = float(np.percentile(boot_means, pct_upper))

    return (ci_lower, ci_upper)


def sign_flip_permutation_test(
    deltas: np.ndarray,
    n_permutations: int = 10000,
    rng: np.random.Generator | None = None,
) -> float:
    """
    Two-sided sign-flip permutation test.

    Under H0, each delta is equally likely to be positive or negative.

    Parameters
    ----------
    deltas : np.ndarray
        Paired differences (ape_i - md_i).
    n_permutations : int
        Number of permutations.
    rng : np.random.Generator, optional
        Random number generator.

    Returns
    -------
    float
        Two-sided p-value.
    """
    if rng is None:
        rng = np.random.default_rng()

    n = len(deltas)
    if n == 0:
        return 1.0

    observed_stat = abs(float(np.mean(deltas)))
    count_extreme = 0

    for _ in range(n_permutations):
        signs = rng.choice([-1, 1], size=n)
        perm_stat = abs(float(np.mean(deltas * signs)))
        if perm_stat >= observed_stat:
            count_extreme += 1

    return (count_extreme + 1) / (n_permutations + 1)


def paired_analysis(
    ape_scores: list[float] | np.ndarray,
    md_scores: list[float] | np.ndarray,
    n_bootstrap: int = 10000,
    alpha: float = 0.05,
    rng: np.random.Generator | None = None,
) -> PairedResult:
    """
    Perform paired analysis comparing APE vs MD scores.

    Computes:
    - Mean delta (APE - MD)
    - BCa bootstrap CI on the deltas
    - Sign-flip permutation test p-value
    - Cohen's d effect size

    Parameters
    ----------
    ape_scores, md_scores : array-like
        Paired scores for each condition.
    n_bootstrap : int
        Bootstrap resamples.
    alpha : float
        Significance level.
    rng : np.random.Generator, optional
        Random number generator.

    Returns
    -------
    PairedResult
    """
    ape = np.asarray(ape_scores, dtype=float)
    md = np.asarray(md_scores, dtype=float)

    n = min(len(ape), len(md))
    if n == 0:
        return PairedResult()

    ape = ape[:n]
    md = md[:n]
    deltas = ape - md

    mean_delta = float(np.mean(deltas))
    ci_lower, ci_upper = bca_bootstrap_ci(deltas, n_bootstrap, alpha, rng)
    p_value = sign_flip_permutation_test(deltas, n_bootstrap, rng)

    # Cohen's d (paired)
    std_delta = float(np.std(deltas, ddof=1)) if n > 1 else 1.0
    effect_size = mean_delta / std_delta if std_delta > 0 else 0.0

    return PairedResult(
        mean_delta=round(mean_delta, 4),
        ci_lower=round(ci_lower, 4),
        ci_upper=round(ci_upper, 4),
        p_value=round(p_value, 4),
        effect_size=round(effect_size, 4),
        significant=p_value < alpha,
        n_samples=n,
    )

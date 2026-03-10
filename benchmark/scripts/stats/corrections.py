"""
Multiple comparison corrections for p-values.

Pure Python implementation (no external dependencies).

Public API
----------
bonferroni(p_values, alpha) -> list[tuple[float, bool]]
holm_bonferroni(p_values, alpha) -> list[tuple[float, bool]]
apply_corrections(results_dict, method, alpha) -> dict
"""

from __future__ import annotations


def bonferroni(
    p_values: list[float],
    alpha: float = 0.05,
) -> list[tuple[float, bool]]:
    """
    Apply Bonferroni correction to a list of p-values.

    Parameters
    ----------
    p_values : list[float]
        Raw p-values.
    alpha : float
        Family-wise error rate.

    Returns
    -------
    list[tuple[float, bool]]
        List of (corrected_p, significant) for each input p-value.
    """
    m = len(p_values)
    if m == 0:
        return []

    return [
        (min(p * m, 1.0), p * m <= alpha)
        for p in p_values
    ]


def holm_bonferroni(
    p_values: list[float],
    alpha: float = 0.05,
) -> list[tuple[float, bool]]:
    """
    Apply Holm-Bonferroni step-down correction.

    Parameters
    ----------
    p_values : list[float]
        Raw p-values.
    alpha : float
        Family-wise error rate.

    Returns
    -------
    list[tuple[float, bool]]
        List of (corrected_p, significant) in the original order.
    """
    m = len(p_values)
    if m == 0:
        return []

    # Sort by p-value, keeping original indices
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])

    # Step-down correction
    corrected = [0.0] * m
    sig = [False] * m

    cumulative_max = 0.0
    for rank, (orig_idx, p) in enumerate(indexed):
        adjusted = p * (m - rank)
        # Enforce monotonicity
        cumulative_max = max(cumulative_max, adjusted)
        corrected_p = min(cumulative_max, 1.0)
        corrected[orig_idx] = corrected_p
        sig[orig_idx] = corrected_p <= alpha

    return list(zip(corrected, sig))


def apply_corrections(
    results_dict: dict[str, float],
    method: str = "holm",
    alpha: float = 0.05,
) -> dict[str, tuple[float, bool]]:
    """
    Apply multiple comparison correction to a dict of {name: p_value}.

    Parameters
    ----------
    results_dict : dict[str, float]
        Mapping from metric name to raw p-value.
    method : str
        "bonferroni" or "holm" (Holm-Bonferroni).
    alpha : float
        Family-wise error rate.

    Returns
    -------
    dict[str, tuple[float, bool]]
        Mapping from metric name to (corrected_p, significant).
    """
    if not results_dict:
        return {}

    names = list(results_dict.keys())
    p_values = [results_dict[n] for n in names]

    if method == "bonferroni":
        corrected = bonferroni(p_values, alpha)
    else:
        corrected = holm_bonferroni(p_values, alpha)

    return dict(zip(names, corrected))

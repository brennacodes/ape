"""
Token usage metrics computation.

Public API
----------
TokenCounts      — raw token counts for a run.
TokenMetrics     — derived metrics comparing two conditions.
compute_token_metrics(ape_counts, md_counts, ape_quality, md_quality) -> TokenMetrics
summarize_token_data(runs) -> dict
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class TokenCounts:
    """Raw token counts for a benchmark run."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass(frozen=True)
class TokenMetrics:
    """Derived token metrics comparing two conditions."""
    token_overhead_pct: float = 0.0
    output_delta_pct: float = 0.0
    net_token_delta_pct: float = 0.0
    token_cost: float = 0.0


def compute_token_metrics(
    ape_counts: TokenCounts,
    md_counts: TokenCounts,
    ape_quality: float = 0.0,
    md_quality: float = 0.0,
) -> TokenMetrics:
    """
    Compute derived token metrics comparing APE vs MD conditions.

    Parameters
    ----------
    ape_counts : TokenCounts
        Token counts for the APE condition.
    md_counts : TokenCounts
        Token counts for the MD (markdown) condition.
    ape_quality : float
        Quality score (e.g. pass rate) for APE.
    md_quality : float
        Quality score for MD.
    """
    md_total = md_counts.total_tokens or 1  # avoid div-by-zero

    token_overhead_pct = ((ape_counts.total_tokens - md_counts.total_tokens) / md_total) * 100
    output_delta_pct = (
        ((ape_counts.output_tokens - md_counts.output_tokens) / (md_counts.output_tokens or 1)) * 100
    )

    quality_delta = ape_quality - md_quality
    net_token_delta_pct = token_overhead_pct - (quality_delta * 100) if quality_delta else token_overhead_pct

    return TokenMetrics(
        token_overhead_pct=round(token_overhead_pct, 2),
        output_delta_pct=round(output_delta_pct, 2),
        net_token_delta_pct=round(net_token_delta_pct, 2),
        token_cost=0.0,
    )


def summarize_token_data(runs: list[Any]) -> dict:
    """
    Summarize token data across multiple runs.

    Parameters
    ----------
    runs : list
        RunRecord objects (or dicts) with token fields.

    Returns
    -------
    dict
        Statistics (mean, median, std, min, max) per token field.
    """
    fields = [
        "input_tokens", "output_tokens", "cache_creation_tokens",
        "cache_read_tokens", "cost_usd",
    ]
    result = {}

    for fname in fields:
        values = []
        for r in runs:
            v = getattr(r, fname, None) if hasattr(r, fname) else r.get(fname)
            if v is not None:
                values.append(float(v))

        if not values:
            result[fname] = {"mean": 0, "median": 0, "std": 0, "min": 0, "max": 0}
            continue

        arr = np.array(values)
        result[fname] = {
            "mean": round(float(np.mean(arr)), 2),
            "median": round(float(np.median(arr)), 2),
            "std": round(float(np.std(arr)), 2),
            "min": round(float(np.min(arr)), 2),
            "max": round(float(np.max(arr)), 2),
        }

    return result

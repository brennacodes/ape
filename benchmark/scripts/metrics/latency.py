"""
Latency metrics computation.

Public API
----------
LatencyMetrics           — percentile-based latency summary.
compute_latency_metrics(wall_clock_times) -> LatencyMetrics
compare_latency(ape_times, md_times, ...) -> dict
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class LatencyMetrics:
    """Percentile-based latency summary (all values in milliseconds)."""
    p50_ms: float = 0.0
    p75_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    mean_ms: float = 0.0
    std_ms: float = 0.0


def _filter_outliers(values: list[float], iqr_factor: float = 1.5) -> list[float]:
    """Remove outliers using the IQR method."""
    if len(values) < 4:
        return values
    arr = np.array(values)
    q1, q3 = np.percentile(arr, [25, 75])
    iqr = q3 - q1
    lower = q1 - iqr_factor * iqr
    upper = q3 + iqr_factor * iqr
    return [v for v in values if lower <= v <= upper]


def compute_latency_metrics(wall_clock_times: list[float]) -> LatencyMetrics:
    """
    Compute latency percentiles from a list of wall-clock times (in ms).

    Returns a LatencyMetrics with all zeros if the input is empty.
    """
    if not wall_clock_times:
        return LatencyMetrics()

    arr = np.array(wall_clock_times)
    return LatencyMetrics(
        p50_ms=round(float(np.percentile(arr, 50)), 2),
        p75_ms=round(float(np.percentile(arr, 75)), 2),
        p95_ms=round(float(np.percentile(arr, 95)), 2),
        p99_ms=round(float(np.percentile(arr, 99)), 2),
        mean_ms=round(float(np.mean(arr)), 2),
        std_ms=round(float(np.std(arr)), 2),
    )


def compare_latency(
    ape_times: list[float],
    md_times: list[float],
    filter_outliers: bool = True,
    iqr_factor: float = 1.5,
) -> dict:
    """
    Compare latency between APE and MD conditions.

    Parameters
    ----------
    ape_times : list[float]
        Wall-clock times in ms for APE runs.
    md_times : list[float]
        Wall-clock times in ms for MD runs.
    filter_outliers : bool
        Whether to filter outliers using IQR method.
    iqr_factor : float
        IQR multiplier for outlier detection.

    Returns
    -------
    dict
        Comparison with metrics for each condition and deltas.
    """
    if filter_outliers:
        ape_times = _filter_outliers(ape_times, iqr_factor)
        md_times = _filter_outliers(md_times, iqr_factor)

    ape_metrics = compute_latency_metrics(ape_times)
    md_metrics = compute_latency_metrics(md_times)

    delta_mean = ape_metrics.mean_ms - md_metrics.mean_ms
    md_mean = md_metrics.mean_ms or 1.0
    delta_pct = (delta_mean / md_mean) * 100

    return {
        "ape": ape_metrics,
        "md": md_metrics,
        "delta_mean_ms": round(delta_mean, 2),
        "delta_pct": round(delta_pct, 2),
    }

"""
Reliability metrics for benchmark runs.

Measures completion rates and per-criteria pass rates.

Public API
----------
ReliabilityMetrics                  — aggregated reliability scores.
compute_reliability(records) -> ReliabilityMetrics
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ReliabilityMetrics:
    """Aggregated reliability scores across multiple runs."""
    completion_rate: float = 0.0
    criteria_pass_rates: dict = field(default_factory=dict)


def compute_reliability(records: list[Any]) -> ReliabilityMetrics:
    """
    Compute reliability metrics from a list of RunRecords.

    Parameters
    ----------
    records : list
        RunRecord objects with `outcomes`, `total`, `pass_rate` fields.
        Each outcome has `check_id`, `passed`.

    Returns
    -------
    ReliabilityMetrics
        completion_rate: fraction of runs that completed (non-zero total).
        criteria_pass_rates: per check_id pass rate across runs.
    """
    if not records:
        return ReliabilityMetrics()

    # Completion rate: runs with at least one evaluated check
    completed = sum(1 for r in records if _get_total(r) > 0)
    completion_rate = completed / len(records)

    # Per-criteria pass rates
    criteria_results: dict[str, list[bool]] = {}

    for r in records:
        outcomes = _get_outcomes(r)
        for o in outcomes:
            check_id = _outcome_field(o, "check_id")
            passed = _outcome_field(o, "passed")

            if passed is None:
                continue  # skipped

            if check_id not in criteria_results:
                criteria_results[check_id] = []
            criteria_results[check_id].append(bool(passed))

    criteria_pass_rates = {}
    for check_id, results in criteria_results.items():
        if results:
            criteria_pass_rates[check_id] = round(float(np.mean(results)), 4)

    return ReliabilityMetrics(
        completion_rate=round(completion_rate, 4),
        criteria_pass_rates=criteria_pass_rates,
    )


def _get_total(record: Any) -> int:
    """Get total checks from a record (RunRecord or dict)."""
    if hasattr(record, "total"):
        return record.total
    if isinstance(record, dict):
        return record.get("total", 0)
    return 0


def _get_outcomes(record: Any) -> list:
    """Get outcomes from a record (RunRecord or dict)."""
    if hasattr(record, "outcomes"):
        return record.outcomes
    if isinstance(record, dict):
        return record.get("outcomes", [])
    return []


def _outcome_field(outcome: Any, field: str) -> Any:
    """Get a field from an outcome (dataclass, dict, or object)."""
    if isinstance(outcome, dict):
        return outcome.get(field)
    return getattr(outcome, field, None)

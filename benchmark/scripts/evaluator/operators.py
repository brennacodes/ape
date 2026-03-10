"""
Operator and transform implementations for the benchmark evaluator.

All functions are pure — they operate on already-resolved values with no
dependencies on trace.py or any external state. This makes them cheap to
unit-test independently of the trace parsing logic.

Evaluation order for any condition:
  1. resolve_metric(name, trace, context) → raw value
  2. apply_transform(name, value) → scalar  [optional]
  3. apply_operator(name, a, b, **kwargs) → bool
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------

def transform_count(value: Any) -> int:
    """Number of items in a collection. Returns 0 for None."""
    if value is None:
        return 0
    return len(value)


def transform_length(value: Any) -> int:
    """Character length of a string, or item count of a list. Returns 0 for None."""
    if value is None:
        return 0
    return len(value)


def transform_first(value: list) -> Any:
    """First element of a list. Returns None for empty."""
    if not value:
        return None
    return value[0]


def transform_last(value: list) -> Any:
    """Last element of a list. Returns None for empty."""
    if not value:
        return None
    return value[-1]


def transform_min(value: list) -> Any:
    """Minimum value in a numeric list. Returns None for empty."""
    if not value:
        return None
    return min(value)


def transform_max(value: list) -> Any:
    """Maximum value in a numeric list. Returns None for empty."""
    if not value:
        return None
    return max(value)


TRANSFORMS: dict[str, Any] = {
    "count": transform_count,
    "length": transform_length,
    "first": transform_first,
    "last": transform_last,
    "min": transform_min,
    "max": transform_max,
}


def apply_transform(name: str, value: Any) -> Any:
    """Apply a named transform to a value. Raises ValueError for unknown names."""
    if name not in TRANSFORMS:
        raise ValueError(f"Unknown transform: {name!r}. Valid: {sorted(TRANSFORMS)}")
    return TRANSFORMS[name](value)


# ---------------------------------------------------------------------------
# Scalar operators
# ---------------------------------------------------------------------------

def op_eq(a: Any, b: Any) -> bool:
    return a == b


def op_neq(a: Any, b: Any) -> bool:
    return a != b


def op_gt(a: Any, b: Any) -> bool:
    return a > b


def op_gte(a: Any, b: Any) -> bool:
    return a >= b


def op_lt(a: Any, b: Any) -> bool:
    return a < b


def op_lte(a: Any, b: Any) -> bool:
    return a <= b


# ---------------------------------------------------------------------------
# Collection operators
# ---------------------------------------------------------------------------

def op_exists_before(a_indices: list[int], b_indices: list[int]) -> bool:
    """
    True if at least one A appears before the first B.

    Vacuously true when B is empty (no B to precede → no constraint to violate).
    False when A is empty and B is non-empty (can't precede without A).
    """
    if not b_indices:
        return True
    if not a_indices:
        return False
    return min(a_indices) < min(b_indices)


def op_exists_after(a_indices: list[int], b_indices: list[int]) -> bool:
    """
    True if at least one A appears after the last B.

    Vacuously true when B is empty.
    False when A is empty and B is non-empty.
    """
    if not b_indices:
        return True
    if not a_indices:
        return False
    return max(a_indices) > max(b_indices)


def op_exists_between(
    a_indices: list[int],
    start_indices: list[int],
    end_indices: list[int],
) -> bool:
    """
    True if at least one A appears strictly between some start and some end event.

    Uses the widest possible window: min(start_indices) as the lower bound and
    max(end_indices) as the upper bound. At least one A must fall inside.

    False when either start or end is empty (no window to check).
    """
    if not start_indices or not end_indices:
        return False
    lo = min(start_indices)
    hi = max(end_indices)
    if lo >= hi:
        return False
    return any(lo < i < hi for i in a_indices)


def op_strictly_precedes(a_indices: list[int], b_indices: list[int]) -> bool:
    """
    True if all occurrences of B appear after all occurrences of A.
    Equivalently: max(A) < min(B).

    Vacuously true when either list is empty.
    """
    if not a_indices or not b_indices:
        return True
    return max(a_indices) < min(b_indices)


def op_strictly_ordered_subset(observed: list[str], defined: list[str]) -> bool:
    """
    Filter `observed` to only phases also present in `defined`. The resulting
    subsequence must respect the relative order defined in `defined`.

    Phases in `observed` that are not in `defined` are ignored.
    An empty filtered sequence trivially passes.
    """
    defined_set = set(defined)
    filtered = [p for p in observed if p in defined_set]
    defined_order = {p: i for i, p in enumerate(defined)}
    indices = [defined_order[p] for p in filtered]
    return indices == sorted(indices)


def op_subset_of(a: Any, b: Any) -> bool:
    """True if A ⊆ B (both coerced to sets)."""
    return set(a) <= set(b)


def op_each_preceded_by_within_N_steps(
    a_indices: list[int],
    b_indices: list[int],
    window: int = 10,
) -> bool:
    """
    For every event in A at index i, there must exist an event of type B at
    index j where j < i and (i - j) <= window.

    Vacuously true if A is empty.
    """
    if not a_indices:
        return True
    b_set = set(b_indices)
    for i in a_indices:
        in_window = any(j in b_set for j in range(max(0, i - window), i))
        if not in_window:
            return False
    return True


def op_precedes_per_path(
    a_path_map: dict[str, list[int]],
    b_path_map: dict[str, list[int]],
) -> bool:
    """
    For each file path P where B(P) occurs, A(P) must also occur at a strictly
    earlier trace index.

    a_path_map: {path: [event_indices where this tool was called with path]}
    b_path_map: {path: [event_indices where this tool was called with path]}

    Vacuously true if b_path_map is empty.
    False if A has no calls for a path that B does have.
    False if the earliest A for a path is not earlier than the earliest B.
    """
    for path, b_indices in b_path_map.items():
        if not b_indices:
            continue
        a_indices = a_path_map.get(path, [])
        if not a_indices:
            return False
        if min(a_indices) >= min(b_indices):
            return False
    return True


def op_not_contains(collection: Any, value: Any) -> bool:
    """True if value is not in collection."""
    return value not in collection


def op_regex_not_match(string_list: list[str], pattern: str) -> bool:
    """True if no element of string_list matches the regex pattern."""
    compiled = re.compile(pattern)
    return not any(compiled.search(s) for s in string_list)


def op_has_key(d: Any, key: str) -> bool:
    """True if key is a key in dict d. False for non-dict inputs."""
    if not isinstance(d, dict):
        return False
    return key in d


def op_has_key_any(d: Any, keys: list[str]) -> bool:
    """True if any of the keys is present in dict d. False for non-dict inputs."""
    if not isinstance(d, dict):
        return False
    return any(k in d for k in keys)


def op_contains(collection: Any, value: Any) -> bool:
    """True if value is in collection."""
    return value in collection


def op_contains_count_gte(
    parallel_batches: list[list[str]],
    metric_type: str,
    count: int,
) -> bool:
    """
    True if at least one parallel batch contains >= `count` calls matching
    `metric_type` (compared by tool name string).

    parallel_batches: list of batches; each batch is a list of tool name strings.
    metric_type: tool name to count within each batch (e.g. "Grep").
    count: minimum required occurrences in at least one batch.
    """
    for batch in parallel_batches:
        if batch.count(metric_type) >= count:
            return True
    return False


def op_first_search_broader_than_final(search_calls: list[dict]) -> bool:
    """
    True if the first search pattern is no longer than the last (i.e., the
    first search is at least as broad as the final one).

    A shorter pattern string = broader search.
    Vacuously true when fewer than 2 calls exist.

    search_calls: ordered list of dicts, each with a 'pattern' key.
    """
    if len(search_calls) < 2:
        return True
    first_len = len(search_calls[0].get("pattern", ""))
    last_len = len(search_calls[-1].get("pattern", ""))
    return first_len <= last_len


# ---------------------------------------------------------------------------
# Dispatch tables
# ---------------------------------------------------------------------------

SCALAR_OPERATORS: dict[str, Any] = {
    "eq": op_eq,
    "neq": op_neq,
    "gt": op_gt,
    "gte": op_gte,
    "lt": op_lt,
    "lte": op_lte,
}

COLLECTION_OPERATORS: dict[str, Any] = {
    "exists_before": op_exists_before,
    "exists_after": op_exists_after,
    "exists_between": op_exists_between,
    "strictly_precedes": op_strictly_precedes,
    "strictly_ordered_subset": op_strictly_ordered_subset,
    "subset_of": op_subset_of,
    "each_preceded_by_within_N_steps": op_each_preceded_by_within_N_steps,
    "precedes_per_path": op_precedes_per_path,
    "not_contains": op_not_contains,
    "regex_not_match": op_regex_not_match,
    "has_key": op_has_key,
    "has_key_any": op_has_key_any,
    "contains": op_contains,
    "contains_count_gte": op_contains_count_gte,
    "first_search_broader_than_final": op_first_search_broader_than_final,
}

ALL_OPERATORS: dict[str, Any] = {**SCALAR_OPERATORS, **COLLECTION_OPERATORS}

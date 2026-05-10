"""Date-window filtering for benchmark summaries and re-evaluation runs.

Provides:
- parse_since(value): argparse type for --since CLI flags. Accepts
  relative durations (Nd, Nh) and ISO 8601 dates / datetimes.
- filter_by_since(items, since, ...): drops items whose started_at is
  missing, unparseable, or earlier than the cutoff.

Both summary.py and re_evaluate.py import from here so the flag behaves
identically across the two CLIs.
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timedelta
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")

_RELATIVE_RE = re.compile(r"^(\d+)([dh])$")


def parse_since(
    value: str,
    *,
    now: datetime | None = None,
) -> datetime:
    """Parse a --since CLI value into an absolute datetime cutoff.

    Accepts:
      - "Nd" / "Nh" where N is a positive integer (e.g. "15d", "36h").
      - ISO 8601 date "YYYY-MM-DD" (interpreted as local-naive midnight).
      - ISO 8601 datetime "YYYY-MM-DDTHH:MM:SS" (and other forms accepted
        by datetime.fromisoformat).

    Raises argparse.ArgumentTypeError on any other input so argparse
    surfaces a clean CLI error.

    The `now` argument is for tests; production callers omit it.
    """
    if not isinstance(value, str) or not value.strip():
        raise argparse.ArgumentTypeError(
            "--since requires a value like '15d', '36h', or '2026-04-24'"
        )

    text = value.strip()
    rel = _RELATIVE_RE.match(text)
    if rel:
        amount = int(rel.group(1))
        unit = rel.group(2)
        if amount <= 0:
            raise argparse.ArgumentTypeError(
                f"--since duration must be positive, got '{value}'"
            )
        delta = (
            timedelta(days=amount) if unit == "d" else timedelta(hours=amount)
        )
        base = now if now is not None else datetime.now()
        return base - delta

    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--since value '{value}' is not a recognised duration "
            "(Nd, Nh) or ISO date/datetime"
        ) from exc


def filter_by_since(
    items: Iterable[T],
    since: datetime | None,
    *,
    get_started_at: Callable[[T], str | None],
    label_for: Callable[[T], str],
    warn: Callable[[str], None],
) -> list[T]:
    """Keep items whose started_at is at or after `since`.

    Items with missing or unparseable started_at are dropped. When any
    are dropped for that reason, `warn` is called once with a single
    aggregated message listing labels.

    Returns the input unchanged (as a list) when `since` is None.
    """
    items_list = list(items)
    if since is None:
        return items_list

    kept: list[T] = []
    skipped_labels: list[str] = []

    for item in items_list:
        raw = get_started_at(item)
        if not raw:
            skipped_labels.append(label_for(item))
            continue
        try:
            started = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            skipped_labels.append(label_for(item))
            continue
        if started >= since:
            kept.append(item)

    if skipped_labels:
        warn(
            f"skipped {len(skipped_labels)} run(s) with missing or "
            f"unparseable started_at: {', '.join(skipped_labels)}"
        )

    return kept

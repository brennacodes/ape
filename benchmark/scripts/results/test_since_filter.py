"""Tests for since_filter.parse_since and since_filter.filter_by_since."""

import argparse
from datetime import datetime

import pytest

from since_filter import filter_by_since, parse_since


FIXED_NOW = datetime(2026, 5, 9, 12, 0, 0)


def _label(item: dict) -> str:
    return item["id"]


def _started_at(item: dict):
    return item.get("started_at")


# ---------------------------------------------------------------------------
# parse_since
# ---------------------------------------------------------------------------


def test_parse_since_relative_days():
    assert parse_since("15d", now=FIXED_NOW) == datetime(2026, 4, 24, 12, 0, 0)


def test_parse_since_relative_hours():
    assert parse_since("36h", now=FIXED_NOW) == datetime(2026, 5, 8, 0, 0, 0)


def test_parse_since_iso_date():
    assert parse_since("2026-04-24") == datetime(2026, 4, 24, 0, 0, 0)


def test_parse_since_iso_datetime():
    assert parse_since("2026-04-24T12:00:00") == datetime(2026, 4, 24, 12, 0, 0)


@pytest.mark.parametrize(
    "bad",
    [
        "", "   ", "0", "0d", "15", "15w", "-1d", "15dd",
        "d", "h", "+1d", "15 d", "not-a-date", "2026-13-40",
    ],
)
def test_parse_since_rejects_bad_input(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_since(bad, now=FIXED_NOW)


# ---------------------------------------------------------------------------
# filter_by_since
# ---------------------------------------------------------------------------


def test_filter_by_since_returns_input_when_since_is_none():
    items = [{"id": "a", "started_at": "2026-01-01T00:00:00"}]
    warnings: list[str] = []
    result = filter_by_since(
        items,
        None,
        get_started_at=_started_at,
        label_for=_label,
        warn=warnings.append,
    )
    assert result == items
    assert warnings == []


def test_filter_by_since_keeps_items_at_or_after_cutoff():
    cutoff = datetime(2026, 4, 24, 0, 0, 0)
    items = [
        {"id": "old", "started_at": "2026-04-23T23:59:59"},
        {"id": "boundary", "started_at": "2026-04-24T00:00:00"},
        {"id": "new", "started_at": "2026-05-01T10:00:00"},
    ]
    warnings: list[str] = []
    result = filter_by_since(
        items,
        cutoff,
        get_started_at=_started_at,
        label_for=_label,
        warn=warnings.append,
    )
    assert [r["id"] for r in result] == ["boundary", "new"]
    assert warnings == []


def test_filter_by_since_drops_and_warns_for_missing_or_bad_timestamps():
    cutoff = datetime(2026, 4, 24, 0, 0, 0)
    items = [
        {"id": "missing-key"},
        {"id": "empty", "started_at": ""},
        {"id": "garbage", "started_at": "yesterday"},
        {"id": "good", "started_at": "2026-05-01T10:00:00"},
    ]
    warnings: list[str] = []
    result = filter_by_since(
        items,
        cutoff,
        get_started_at=_started_at,
        label_for=_label,
        warn=warnings.append,
    )
    assert [r["id"] for r in result] == ["good"]
    assert len(warnings) == 1
    msg = warnings[0]
    assert "skipped 3" in msg
    assert "missing-key" in msg
    assert "empty" in msg
    assert "garbage" in msg


def test_filter_by_since_no_warning_when_nothing_dropped_for_bad_timestamps():
    cutoff = datetime(2026, 4, 24, 0, 0, 0)
    items = [
        {"id": "old", "started_at": "2026-04-01T00:00:00"},
        {"id": "new", "started_at": "2026-05-01T00:00:00"},
    ]
    warnings: list[str] = []
    filter_by_since(
        items,
        cutoff,
        get_started_at=_started_at,
        label_for=_label,
        warn=warnings.append,
    )
    assert warnings == []


def test_filter_by_since_does_not_mutate_input():
    items = [{"id": "a", "started_at": "2026-04-23T00:00:00"}]
    snapshot = [dict(it) for it in items]
    filter_by_since(
        items,
        datetime(2026, 4, 24, 0, 0, 0),
        get_started_at=_started_at,
        label_for=_label,
        warn=lambda _msg: None,
    )
    assert items == snapshot


def test_filter_by_since_accepts_generator_input():
    def gen():
        yield {"id": "old", "started_at": "2026-04-23T00:00:00"}
        yield {"id": "new", "started_at": "2026-05-01T00:00:00"}

    result = filter_by_since(
        gen(),
        datetime(2026, 4, 24, 0, 0, 0),
        get_started_at=_started_at,
        label_for=_label,
        warn=lambda _msg: None,
    )
    assert [r["id"] for r in result] == ["new"]

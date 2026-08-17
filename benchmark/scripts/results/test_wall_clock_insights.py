"""Tests for bin/wall-clock-insights.

The script lives in `bin/` without a `.py` extension, so we load it via
importlib to exercise its pure aggregation functions directly.
"""

import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "bin" / "wall-clock-insights"


def _load_module():
    loader = SourceFileLoader("wall_clock_insights", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loader.exec_module(module)
    return module


wci = _load_module()


# ---------------------------------------------------------------------------
# Synthetic session helpers
# ---------------------------------------------------------------------------


def _assistant(model: str, **usage) -> dict:
    return {
        "type": "assistant",
        "message": {
            "model": model,
            "usage": {
                "input_tokens": usage.get("input", 0),
                "output_tokens": usage.get("output", 0),
                "cache_creation_input_tokens": usage.get("cache_create", 0),
                "cache_read_input_tokens": usage.get("cache_read", 0),
            },
        },
    }


def _write_session(
    base: Path,
    name: str,
    *,
    wall_clock_ms: float,
    succeeded: bool,
    stream: list[dict],
) -> Path:
    session_dir = base / name
    session_dir.mkdir(parents=True)
    summary = {
        "wall_clock_ms": wall_clock_ms,
        "succeeded": succeeded,
        "started_at": "2026-05-17T00:00:00",
        "completed_at": "2026-05-17T00:01:00",
    }
    (session_dir / "summary.json").write_text(json.dumps(summary))
    (session_dir / "stream.json").write_text(json.dumps(stream))
    return session_dir


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_id,expected",
    [
        ("claude-haiku-4-5-20251001", "Haiku 4.5"),
        ("claude-opus-4-6", "Opus 4.6"),
        ("claude-sonnet-4-6", "Sonnet 4.6"),
        ("claude-opus-4-7", "Opus 4.7"),
        ("custom-model-id", "custom-model-id"),
        ("", "unknown"),
    ],
)
def test_normalize_model(model_id, expected):
    assert wci.normalize_model(model_id) == expected


def test_format_duration():
    assert wci.format_duration(0) == "0s"
    assert wci.format_duration(45) == "45s"
    assert wci.format_duration(125) == "2m 05s"
    assert wci.format_duration(3725) == "1h 02m 05s"


def test_discover_sessions_finds_nested_dirs_and_skips_incomplete(tmp_path):
    good = _write_session(
        tmp_path,
        "nested/group/sess-a",
        wall_clock_ms=60_000,
        succeeded=True,
        stream=[_assistant("claude-opus-4-6")],
    )
    bad = tmp_path / "nested/group/sess-b"
    bad.mkdir(parents=True)
    (bad / "summary.json").write_text("{}")  # missing stream.json

    found = sorted(wci.discover_sessions(tmp_path))
    assert found == [good]


def test_load_session_extracts_turns_and_wall_clock(tmp_path):
    session_dir = _write_session(
        tmp_path,
        "s1",
        wall_clock_ms=120_000,
        succeeded=True,
        stream=[
            {"type": "system", "subtype": "init"},
            _assistant("claude-opus-4-6", input=10, output=20, cache_read=100),
            _assistant("claude-haiku-4-5-20251001", input=5, output=8),
            {"type": "user"},
            {"type": "result"},
        ],
    )
    session = wci.load_session(session_dir)
    assert session is not None
    assert session.wall_clock_seconds == 120.0
    assert session.succeeded is True
    assert [t.model for t in session.turns] == [
        "claude-opus-4-6",
        "claude-haiku-4-5-20251001",
    ]
    assert session.turns[0].cache_read_tokens == 100


def test_load_session_falls_back_to_iso_timestamps(tmp_path):
    session_dir = tmp_path / "s_iso"
    session_dir.mkdir()
    (session_dir / "summary.json").write_text(
        json.dumps(
            {
                "started_at": "2026-05-17T12:00:00",
                "completed_at": "2026-05-17T12:00:30",
                "succeeded": True,
            }
        )
    )
    (session_dir / "stream.json").write_text(json.dumps([_assistant("claude-opus-4-6")]))
    session = wci.load_session(session_dir)
    assert session is not None
    assert session.wall_clock_seconds == 30.0


def test_load_session_skips_malformed_json(tmp_path, capsys):
    session_dir = tmp_path / "bad"
    session_dir.mkdir()
    (session_dir / "summary.json").write_text("{not json")
    (session_dir / "stream.json").write_text("[]")
    assert wci.load_session(session_dir) is None
    err = capsys.readouterr().err
    assert "skipping" in err


def test_aggregate_computes_turns_per_minute_and_model_share(tmp_path):
    _write_session(
        tmp_path,
        "s1",
        wall_clock_ms=60_000,
        succeeded=True,
        stream=[
            _assistant("claude-opus-4-6", input=10, output=20, cache_read=100),
            _assistant("claude-opus-4-6", input=5, output=15, cache_create=50),
            _assistant("claude-haiku-4-5-20251001", input=2, output=4),
        ],
    )
    _write_session(
        tmp_path,
        "s2",
        wall_clock_ms=120_000,
        succeeded=False,
        stream=[
            _assistant("claude-haiku-4-5-20251001", input=1, output=2),
        ],
    )

    overall, failed = wci.run(tmp_path)

    assert overall.session_count == 2
    assert overall.total_turns == 4
    assert overall.wall_clock_seconds == 180.0
    # 4 turns in 3 minutes -> 1.333... turns/min
    assert overall.turns_per_minute == pytest.approx(4 / 3)
    assert dict(overall.turns_by_model) == {"Opus 4.6": 2, "Haiku 4.5": 2}

    opus = overall.tokens_by_model["Opus 4.6"]
    assert opus.input_tokens == 15
    assert opus.output_tokens == 35
    assert opus.cache_creation_tokens == 50
    assert opus.cache_read_tokens == 100
    assert opus.total == 200

    haiku = overall.tokens_by_model["Haiku 4.5"]
    assert haiku.input_tokens == 3
    assert haiku.output_tokens == 6
    assert haiku.total == 9

    assert failed.session_count == 1
    assert failed.total_turns == 1
    assert failed.wall_clock_seconds == 120.0
    assert dict(failed.turns_by_model) == {"Haiku 4.5": 1}


def test_load_session_handles_null_usage_fields(tmp_path):
    session_dir = tmp_path / "s_null"
    session_dir.mkdir()
    (session_dir / "summary.json").write_text(
        json.dumps({"wall_clock_ms": 1000, "succeeded": True})
    )
    (session_dir / "stream.json").write_text(
        json.dumps(
            [
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-opus-4-6",
                        "usage": {
                            "input_tokens": None,
                            "output_tokens": None,
                            "cache_creation_input_tokens": None,
                            "cache_read_input_tokens": None,
                        },
                    },
                }
            ]
        )
    )
    session = wci.load_session(session_dir)
    assert session is not None
    assert session.turns[0].input_tokens == 0
    assert session.turns[0].output_tokens == 0
    assert session.turns[0].cache_creation_tokens == 0
    assert session.turns[0].cache_read_tokens == 0


def test_synthetic_model_passes_through_normalization(tmp_path):
    _write_session(
        tmp_path,
        "s_synth",
        wall_clock_ms=60_000,
        succeeded=True,
        stream=[
            _assistant("<synthetic>", input=0, output=0),
            _assistant("claude-opus-4-6", input=10, output=20),
        ],
    )
    overall, _ = wci.run(tmp_path)
    assert overall.turns_by_model["<synthetic>"] == 1
    assert overall.turns_by_model["Opus 4.6"] == 1


def test_load_session_returns_zero_wall_clock_when_all_fields_missing(tmp_path):
    session_dir = tmp_path / "s_zero"
    session_dir.mkdir()
    (session_dir / "summary.json").write_text(
        json.dumps({"wall_clock_ms": 0, "succeeded": True})
    )
    (session_dir / "stream.json").write_text(json.dumps([_assistant("claude-opus-4-6")]))
    session = wci.load_session(session_dir)
    assert session is not None
    assert session.wall_clock_seconds == 0.0


def test_turns_per_minute_is_zero_when_wall_clock_zero():
    agg = wci.Aggregate(label="x")
    agg.total_turns = 5
    assert agg.turns_per_minute == 0.0


def test_tokens_per_minute_helper():
    assert wci.tokens_per_minute(0, 60.0) == 0.0
    assert wci.tokens_per_minute(100, 0) == 0.0
    assert wci.tokens_per_minute(100, 60.0) == pytest.approx(100.0)
    assert wci.tokens_per_minute(300, 120.0) == pytest.approx(150.0)


def test_token_rate_table_uses_aggregate_wall_clock(tmp_path):
    # 60s session + 60s session = 120s total. Opus gets 600 input + 1200 output
    # across both, so totals are 600/120s*60 = 300 input/min, 600 output/min.
    _write_session(
        tmp_path,
        "s1",
        wall_clock_ms=60_000,
        succeeded=True,
        stream=[_assistant("claude-opus-4-6", input=300, output=600)],
    )
    _write_session(
        tmp_path,
        "s2",
        wall_clock_ms=60_000,
        succeeded=True,
        stream=[_assistant("claude-opus-4-6", input=300, output=600)],
    )
    overall, _ = wci.run(tmp_path)
    table = wci.render_token_rate_table(overall)
    assert "Opus 4.6" in table
    assert "input/min" in table
    # 600 input over 120s -> 300/min; 1200 output over 120s -> 600/min
    assert "300" in table
    assert "600" in table


def test_aggregate_to_dict_includes_token_rates(tmp_path):
    _write_session(
        tmp_path,
        "s1",
        wall_clock_ms=60_000,
        succeeded=True,
        stream=[_assistant("claude-opus-4-6", input=100, output=200, cache_read=300)],
    )
    overall, _ = wci.run(tmp_path)
    payload = wci.aggregate_to_dict(overall)
    rates = payload["tokens_per_minute_by_model"]["Opus 4.6"]
    assert rates["input_per_min"] == pytest.approx(100.0)
    assert rates["output_per_min"] == pytest.approx(200.0)
    assert rates["cache_read_per_min"] == pytest.approx(300.0)
    assert rates["total_per_min"] == pytest.approx(600.0)


def test_main_prints_tables(tmp_path, capsys):
    _write_session(
        tmp_path,
        "s1",
        wall_clock_ms=60_000,
        succeeded=True,
        stream=[
            _assistant("claude-opus-4-6", input=10, output=20),
            _assistant("claude-haiku-4-5-20251001", input=1, output=2),
        ],
    )
    exit_code = wci.main(["--root", str(tmp_path)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Cadence" in out
    assert "all sessions" in out
    assert "failed sessions" in out
    assert "Opus 4.6" in out
    assert "Haiku 4.5" in out
    assert "turns/min" in out
    assert "Token throughput" in out
    assert "input/min" in out


def test_main_json_mode(tmp_path, capsys):
    _write_session(
        tmp_path,
        "s1",
        wall_clock_ms=60_000,
        succeeded=True,
        stream=[_assistant("claude-opus-4-6", input=10, output=20)],
    )
    exit_code = wci.main(["--root", str(tmp_path), "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["all_sessions"]["total_turns"] == 1
    assert payload["all_sessions"]["turns_by_model"] == {"Opus 4.6": 1}
    assert payload["failed_sessions"]["total_turns"] == 0


def test_main_errors_on_missing_root(tmp_path, capsys):
    missing = tmp_path / "nope"
    exit_code = wci.main(["--root", str(missing)])
    assert exit_code == 2
    assert "not a directory" in capsys.readouterr().err

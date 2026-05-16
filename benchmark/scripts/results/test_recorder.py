"""Tests for benchmark/scripts/results/recorder.py.

Focuses on source-aware path routing and RunRecord round-tripping.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from recorder import Recorder, RunRecord


def _make_record(
    *,
    fixture_id: str = "bivvy",
    fmt: str = "markdown",
    prompt_id: str = "bugs/silent_yaml_failure",
    run_id: int = 0,
    source: str = "claude-md",
    error: str = "",
) -> RunRecord:
    return RunRecord(
        fixture_id=fixture_id,
        format=fmt,
        prompt_id=prompt_id,
        run_id=run_id,
        source=source,
        outcomes=[],
        total=1,
        passed=1,
        failed=0,
        skipped=0,
        pass_rate=1.0,
        error=error,
    )


# ===========================================================================
# Path helpers
# ===========================================================================


class TestPathHelpers:
    def test_run_dir_with_claude_md_source(self, tmp_path):
        rec = Recorder(tmp_path)
        path = rec._run_dir("bivvy", "markdown", "bugs/x", 3, "claude-md")
        assert path == tmp_path / "claude-md" / "bivvy" / "markdown" / "bugs/x" / "003"

    def test_run_dir_with_prompt_source(self, tmp_path):
        rec = Recorder(tmp_path)
        path = rec._run_dir("bivvy", "markdown", "bugs/x", 3, "prompt")
        assert path == tmp_path / "prompt" / "bivvy" / "markdown" / "bugs/x" / "003"

    def test_run_dir_no_workflow_skips_source_layer(self, tmp_path):
        rec = Recorder(tmp_path)
        path = rec._run_dir("bivvy", "no-workflow", "bugs/x", 3, "")
        assert path == tmp_path / "bivvy" / "no-workflow" / "bugs/x" / "003"


# ===========================================================================
# Save / load round-trip
# ===========================================================================


class TestSaveLoadRoundtrip:
    def test_source_persisted_in_summary_json(self, tmp_path):
        rec = Recorder(tmp_path)
        record = _make_record(source="claude-md", run_id=0)
        run_dir = rec.save_run(record)
        assert run_dir == tmp_path / "claude-md" / "bivvy" / "markdown" / "bugs/silent_yaml_failure" / "000"
        with open(run_dir / "summary.json") as f:
            data = json.load(f)
        assert data["source"] == "claude-md"

    def test_load_run_roundtrips_source(self, tmp_path):
        rec = Recorder(tmp_path)
        record = _make_record(source="prompt", run_id=2)
        rec.save_run(record)
        loaded = rec.load_run("bivvy", "markdown", "bugs/silent_yaml_failure", 2, "prompt")
        assert loaded.source == "prompt"
        assert loaded.run_id == 2

    def test_no_workflow_lands_without_source_layer(self, tmp_path):
        rec = Recorder(tmp_path)
        record = _make_record(fmt="no-workflow", source="", run_id=5)
        run_dir = rec.save_run(record)
        assert run_dir == tmp_path / "bivvy" / "no-workflow" / "bugs/silent_yaml_failure" / "005"

    def test_to_dict_includes_source(self):
        record = _make_record(source="prompt")
        d = record.to_dict()
        assert d["source"] == "prompt"

    def test_from_dict_defaults_source_to_empty(self):
        # Records persisted before the source field existed must still load.
        record = RunRecord.from_dict({"fixture_id": "bivvy", "format": "markdown", "prompt_id": "p", "run_id": 0})
        assert record.source == ""

    def test_from_dict_roundtrips_source(self):
        record = RunRecord.from_dict({
            "fixture_id": "bivvy", "format": "markdown",
            "prompt_id": "p", "run_id": 0, "source": "claude-md",
        })
        assert record.source == "claude-md"


# ===========================================================================
# next_run_id
# ===========================================================================


class TestNextRunId:
    def test_separate_counters_per_source(self, tmp_path):
        rec = Recorder(tmp_path)
        rec.save_run(_make_record(source="claude-md", run_id=0))
        rec.save_run(_make_record(source="claude-md", run_id=1))
        rec.save_run(_make_record(source="prompt", run_id=0))
        # claude-md: next is 2; prompt: next is 1.
        assert rec.next_run_id("bivvy", "markdown", "bugs/silent_yaml_failure", "claude-md") == 2
        assert rec.next_run_id("bivvy", "markdown", "bugs/silent_yaml_failure", "prompt") == 1

    def test_no_workflow_uses_source_agnostic_path(self, tmp_path):
        rec = Recorder(tmp_path)
        rec.save_run(_make_record(fmt="no-workflow", source="", run_id=0))
        assert rec.next_run_id("bivvy", "no-workflow", "bugs/silent_yaml_failure", "") == 1


# ===========================================================================
# all_runs
# ===========================================================================


class TestAllRuns:
    def test_iterates_across_source_subtrees(self, tmp_path):
        rec = Recorder(tmp_path)
        rec.save_run(_make_record(source="claude-md", run_id=0))
        rec.save_run(_make_record(source="prompt", run_id=0))
        rec.save_run(_make_record(fmt="no-workflow", source="", run_id=0))
        runs = list(rec.all_runs())
        sources = sorted(r.source for r in runs)
        assert sources == ["", "claude-md", "prompt"]

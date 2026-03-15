"""Tests for benchmark/scripts/results/results.py.

Every public function is directly tested. Tests use synthetic CheckOutcomes
built via make_outcome — no dependency on the evaluator or trace modules.
"""

import json
import pytest
from pathlib import Path

from results import (
    CheckOutcome,
    RunMetadata,
    RunSummary,
    CategoryScore,
    FormatScore,
    ComparisonSummary,
    make_outcome,
    summarize_run,
    summarize_comparison,
    format_run_summary,
    format_comparison,
    to_dict,
    write_json,
    load_run_summary_json,
)


# ===========================================================================
# Helpers
# ===========================================================================

def _meta(fmt="plain-text", fixture="centminmod", prompt="centminmod-bug-fix"):
    return RunMetadata(
        fixture_id=fixture,
        format=fmt,
        prompt_id=prompt,
        model="claude-sonnet-4-20250514",
        session_id="sess-001",
        timestamp="2025-01-01T00:00:00Z",
    )


def _pass(check_id, phase="p"):
    return make_outcome(check_id, phase, True, None)


def _fail(check_id, phase="p"):
    return make_outcome(check_id, phase, False, None)


def _skip(check_id, phase="p", reason="not applicable", category=None):
    return make_outcome(check_id, phase, None, reason, category=category)


def _pass_adherence(check_id, phase="p"):
    return make_outcome(check_id, phase, True, None, category="adherence")


def _pass_tool_usage(check_id, phase="p"):
    return make_outcome(check_id, phase, True, None, category="tool_usage")


def _fail_adherence(check_id, phase="p"):
    return make_outcome(check_id, phase, False, None, category="adherence")


def _fail_tool_usage(check_id, phase="p"):
    return make_outcome(check_id, phase, False, None, category="tool_usage")


# ===========================================================================
# make_outcome
# ===========================================================================

class TestMakeOutcome:
    def test_creates_passing_outcome(self):
        o = make_outcome("c1", "intake", True, None)
        assert o.check_id == "c1"
        assert o.phase == "intake"
        assert o.passed is True
        assert o.skip_reason is None

    def test_creates_failing_outcome(self):
        o = make_outcome("c2", "implementation", False, None)
        assert o.passed is False

    def test_creates_skipped_outcome(self):
        o = make_outcome("c3", "workflow", None, "phase metrics not implemented")
        assert o.passed is None
        assert o.skip_reason == "phase metrics not implemented"

    def test_outcome_is_frozen(self):
        o = make_outcome("c1", "p", True, None)
        with pytest.raises(AttributeError):
            o.check_id = "changed"


# ===========================================================================
# summarize_run
# ===========================================================================

class TestSummarizeRun:
    def test_all_passing(self):
        outcomes = [_pass("c1"), _pass("c2"), _pass("c3")]
        s = summarize_run(outcomes, _meta())
        assert s.total == 3
        assert s.passed == 3
        assert s.failed == 0
        assert s.skipped == 0
        assert s.pass_rate == 1.0

    def test_all_failing(self):
        outcomes = [_fail("c1"), _fail("c2")]
        s = summarize_run(outcomes, _meta())
        assert s.total == 2
        assert s.passed == 0
        assert s.failed == 2
        assert s.pass_rate == 0.0

    def test_mixed_results(self):
        outcomes = [_pass("c1"), _fail("c2"), _skip("c3")]
        s = summarize_run(outcomes, _meta())
        assert s.total == 3
        assert s.passed == 1
        assert s.failed == 1
        assert s.skipped == 1
        # pass_rate = 1 / (3 - 1) = 0.5
        assert s.pass_rate == 0.5

    def test_all_skipped(self):
        outcomes = [_skip("c1"), _skip("c2")]
        s = summarize_run(outcomes, _meta())
        assert s.total == 2
        assert s.passed == 0
        assert s.skipped == 2
        assert s.pass_rate == 0.0

    def test_empty_outcomes(self):
        s = summarize_run([], _meta())
        assert s.total == 0
        assert s.pass_rate == 0.0

    def test_metadata_preserved(self):
        meta = _meta(fmt="ape", fixture="centminmod", prompt="bug-fix")
        s = summarize_run([_pass("c1")], meta)
        assert s.metadata.format == "ape"
        assert s.metadata.fixture_id == "centminmod"
        assert s.metadata.prompt_id == "bug-fix"

    def test_outcomes_list_preserved(self):
        outcomes = [_pass("c1"), _fail("c2")]
        s = summarize_run(outcomes, _meta())
        assert len(s.outcomes) == 2
        assert s.outcomes[0].check_id == "c1"
        assert s.outcomes[1].check_id == "c2"

    def test_pass_rate_rounding(self):
        # 1 pass out of 3 evaluated = 0.3333... → rounded to 0.3333
        outcomes = [_pass("c1"), _fail("c2"), _fail("c3")]
        s = summarize_run(outcomes, _meta())
        assert s.pass_rate == 0.3333


# ===========================================================================
# summarize_comparison
# ===========================================================================

class TestSummarizeComparison:
    def test_two_formats(self):
        s1 = summarize_run(
            [_pass("c1"), _fail("c2")],
            _meta(fmt="plain-text"),
        )
        s2 = summarize_run(
            [_pass("c1"), _pass("c2")],
            _meta(fmt="ape"),
        )
        comp = summarize_comparison([s1, s2])
        assert comp.fixture_id == "centminmod"
        assert comp.prompt_id == "centminmod-bug-fix"
        assert len(comp.formats) == 2

    def test_per_check_results(self):
        s1 = summarize_run(
            [_pass("c1"), _fail("c2")],
            _meta(fmt="plain-text"),
        )
        s2 = summarize_run(
            [_fail("c1"), _pass("c2")],
            _meta(fmt="ape"),
        )
        comp = summarize_comparison([s1, s2])
        assert comp.per_check["c1"]["plain-text"] is True
        assert comp.per_check["c1"]["ape"] is False
        assert comp.per_check["c2"]["plain-text"] is False
        assert comp.per_check["c2"]["ape"] is True

    def test_skipped_check_in_per_check(self):
        s1 = summarize_run(
            [_pass("c1"), _skip("c2")],
            _meta(fmt="plain-text"),
        )
        s2 = summarize_run(
            [_pass("c1"), _pass("c2")],
            _meta(fmt="ape"),
        )
        comp = summarize_comparison([s1, s2])
        assert comp.per_check["c2"]["plain-text"] is None
        assert comp.per_check["c2"]["ape"] is True

    def test_format_scores(self):
        s1 = summarize_run(
            [_pass("c1"), _fail("c2"), _skip("c3")],
            _meta(fmt="plain-text"),
        )
        comp = summarize_comparison([s1])
        assert len(comp.formats) == 1
        f = comp.formats[0]
        assert f.format == "plain-text"
        assert f.total == 3
        assert f.passed == 1
        assert f.failed == 1
        assert f.skipped == 1
        assert f.pass_rate == 0.5

    def test_mismatched_fixture_raises(self):
        s1 = summarize_run([], _meta(fixture="a"))
        s2 = summarize_run([], _meta(fixture="b"))
        with pytest.raises(ValueError, match="fixture_ids"):
            summarize_comparison([s1, s2])

    def test_mismatched_prompt_raises(self):
        s1 = summarize_run([], _meta(prompt="a"))
        s2 = summarize_run([], _meta(prompt="b"))
        with pytest.raises(ValueError, match="prompt_ids"):
            summarize_comparison([s1, s2])

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            summarize_comparison([])

    def test_check_order_preserved(self):
        s1 = summarize_run(
            [_pass("z_last"), _pass("a_first")],
            _meta(fmt="plain-text"),
        )
        comp = summarize_comparison([s1])
        check_ids = list(comp.per_check.keys())
        assert check_ids == ["z_last", "a_first"]

    def test_check_missing_in_one_format(self):
        s1 = summarize_run([_pass("c1")], _meta(fmt="plain-text"))
        s2 = summarize_run([_pass("c1"), _pass("c2")], _meta(fmt="ape"))
        comp = summarize_comparison([s1, s2])
        # c2 not in plain-text results
        assert comp.per_check["c2"].get("plain-text") is None
        assert comp.per_check["c2"]["ape"] is True


# ===========================================================================
# format_run_summary
# ===========================================================================

class TestFormatRunSummary:
    def test_contains_metadata(self):
        s = summarize_run([_pass("c1")], _meta(fmt="ape"))
        text = format_run_summary(s)
        assert "centminmod" in text
        assert "ape" in text

    def test_contains_counts(self):
        s = summarize_run([_pass("c1"), _fail("c2")], _meta())
        text = format_run_summary(s)
        assert "1 passed" in text
        assert "1 failed" in text

    def test_shows_failed_checks(self):
        s = summarize_run([_fail("bad_check", phase="intake")], _meta())
        text = format_run_summary(s)
        assert "bad_check" in text
        assert "intake" in text
        assert "Failed checks" in text

    def test_shows_skipped_checks(self):
        s = summarize_run([_skip("skipped_check", reason="phase not impl")], _meta())
        text = format_run_summary(s)
        assert "skipped_check" in text
        assert "phase not impl" in text
        assert "Skipped checks" in text

    def test_pass_rate_formatted_as_percent(self):
        s = summarize_run([_pass("c1"), _fail("c2")], _meta())
        text = format_run_summary(s)
        assert "50.0%" in text

    def test_all_passing_no_failed_section(self):
        s = summarize_run([_pass("c1"), _pass("c2")], _meta())
        text = format_run_summary(s)
        assert "Failed checks" not in text

    def test_no_skipped_section_when_none_skipped(self):
        s = summarize_run([_pass("c1")], _meta())
        text = format_run_summary(s)
        assert "Skipped checks" not in text

    def test_failed_check_shows_phase(self):
        s = summarize_run([_fail("c1", phase="intake")], _meta())
        text = format_run_summary(s)
        assert "c1" in text
        assert "intake" in text


# ===========================================================================
# format_comparison
# ===========================================================================

class TestFormatComparison:
    def test_contains_fixture_and_prompt(self):
        s1 = summarize_run([_pass("c1")], _meta(fmt="plain-text"))
        s2 = summarize_run([_pass("c1")], _meta(fmt="ape"))
        comp = summarize_comparison([s1, s2])
        text = format_comparison(comp)
        assert "centminmod" in text

    def test_contains_format_headers(self):
        s1 = summarize_run([_pass("c1")], _meta(fmt="plain-text"))
        s2 = summarize_run([_pass("c1")], _meta(fmt="ape"))
        comp = summarize_comparison([s1, s2])
        text = format_comparison(comp)
        assert "plain-text" in text
        assert "ape" in text

    def test_shows_pass_fail_skip(self):
        s1 = summarize_run(
            [_pass("c1"), _fail("c2"), _skip("c3")],
            _meta(fmt="plain-text"),
        )
        s2 = summarize_run(
            [_fail("c1"), _pass("c2"), _pass("c3")],
            _meta(fmt="ape"),
        )
        comp = summarize_comparison([s1, s2])
        text = format_comparison(comp)
        assert "PASS" in text
        assert "FAIL" in text
        assert "SKIP" in text

    def test_shows_pass_rate(self):
        s1 = summarize_run([_pass("c1"), _fail("c2")], _meta(fmt="plain-text"))
        comp = summarize_comparison([s1])
        text = format_comparison(comp)
        assert "50.0%" in text


# ===========================================================================
# to_dict
# ===========================================================================

class TestToDict:
    def test_outcome_to_dict(self):
        o = make_outcome("c1", "intake", True, None)
        d = to_dict(o)
        assert d["check_id"] == "c1"
        assert d["passed"] is True
        assert isinstance(d, dict)

    def test_metadata_to_dict(self):
        m = _meta()
        d = to_dict(m)
        assert d["fixture_id"] == "centminmod"
        assert d["format"] == "plain-text"

    def test_nested_dataclass(self):
        s = summarize_run([_pass("c1")], _meta())
        d = to_dict(s)
        assert isinstance(d["metadata"], dict)
        assert isinstance(d["outcomes"], list)
        assert isinstance(d["outcomes"][0], dict)

    def test_plain_values_passthrough(self):
        assert to_dict(42) == 42
        assert to_dict("hello") == "hello"
        assert to_dict(None) is None

    def test_list_of_dicts(self):
        result = to_dict([{"a": 1}, {"b": 2}])
        assert result == [{"a": 1}, {"b": 2}]


# ===========================================================================
# write_json / load_run_summary_json
# ===========================================================================

class TestJsonIO:
    def test_write_and_load_roundtrip(self, tmp_path):
        s = summarize_run(
            [_pass("c1"), _fail("c2"), _skip("c3", reason="deferred")],
            _meta(),
        )
        out = tmp_path / "result.json"
        write_json(s, out)

        loaded = load_run_summary_json(out)
        assert loaded["total"] == 3
        assert loaded["passed"] == 1
        assert loaded["failed"] == 1
        assert loaded["skipped"] == 1
        assert loaded["metadata"]["format"] == "plain-text"
        assert len(loaded["outcomes"]) == 3

    def test_write_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "a" / "b" / "result.json"
        s = summarize_run([], _meta())
        write_json(s, out)
        assert out.exists()

    def test_json_is_valid(self, tmp_path):
        s = summarize_run([_pass("c1")], _meta())
        out = tmp_path / "result.json"
        write_json(s, out)
        # Should be valid JSON
        with open(out) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_comparison_write(self, tmp_path):
        s1 = summarize_run([_pass("c1")], _meta(fmt="plain-text"))
        s2 = summarize_run([_fail("c1")], _meta(fmt="ape"))
        comp = summarize_comparison([s1, s2])
        out = tmp_path / "comparison.json"
        write_json(comp, out)
        loaded = load_run_summary_json(out)
        assert loaded["fixture_id"] == "centminmod"
        assert len(loaded["formats"]) == 2


# ===========================================================================
# RunMetadata
# ===========================================================================

class TestRunMetadata:
    def test_defaults(self):
        m = RunMetadata(fixture_id="f", format="plain-text", prompt_id="p")
        assert m.model == ""
        assert m.session_id == ""
        assert m.timestamp == ""

    def test_all_fields(self):
        m = _meta()
        assert m.fixture_id == "centminmod"
        assert m.format == "plain-text"
        assert m.prompt_id == "centminmod-bug-fix"
        assert m.model == "claude-sonnet-4-20250514"


# ===========================================================================
# Category scoring
# ===========================================================================

class TestCategoryScoring:
    def test_categorized_outcomes_produce_category_scores(self):
        outcomes = [
            _pass_adherence("c1"),
            _pass_adherence("c2"),
            _fail_adherence("c3"),
            _pass_tool_usage("c4"),
            _fail_tool_usage("c5"),
        ]
        s = summarize_run(outcomes, _meta())
        assert "adherence" in s.category_scores
        assert "tool_usage" in s.category_scores

    def test_adherence_category_score(self):
        outcomes = [
            _pass_adherence("c1"),
            _pass_adherence("c2"),
            _fail_adherence("c3"),
        ]
        s = summarize_run(outcomes, _meta())
        adh = s.category_scores["adherence"]
        assert adh.category == "adherence"
        assert adh.total == 3
        assert adh.passed == 2
        assert adh.failed == 1
        assert adh.skipped == 0
        assert adh.pass_rate == round(2 / 3, 4)

    def test_tool_usage_category_score(self):
        outcomes = [
            _pass_tool_usage("c1"),
            _fail_tool_usage("c2"),
            _fail_tool_usage("c3"),
        ]
        s = summarize_run(outcomes, _meta())
        tool = s.category_scores["tool_usage"]
        assert tool.total == 3
        assert tool.passed == 1
        assert tool.failed == 2
        assert tool.pass_rate == round(1 / 3, 4)

    def test_category_scores_with_skipped(self):
        outcomes = [
            _pass_adherence("c1"),
            _skip("c2", category="adherence"),
            _fail_adherence("c3"),
        ]
        s = summarize_run(outcomes, _meta())
        adh = s.category_scores["adherence"]
        # pass_rate = 1 / (3 - 1) = 0.5
        assert adh.skipped == 1
        assert adh.pass_rate == round(0.5, 4)

    def test_uncategorized_outcomes_grouped_together(self):
        outcomes = [
            make_outcome("c1", "p", True, None),  # no category
            make_outcome("c2", "p", False, None),  # no category
        ]
        s = summarize_run(outcomes, _meta())
        assert "uncategorized" in s.category_scores
        unc = s.category_scores["uncategorized"]
        assert unc.total == 2
        assert unc.passed == 1
        assert unc.failed == 1

    def test_mixed_categories_and_uncategorized(self):
        outcomes = [
            _pass_adherence("c1"),
            make_outcome("c2", "p", True, None),  # uncategorized
            _pass_tool_usage("c3"),
        ]
        s = summarize_run(outcomes, _meta())
        assert len(s.category_scores) == 3
        assert s.category_scores["adherence"].total == 1
        assert s.category_scores["tool_usage"].total == 1
        assert s.category_scores["uncategorized"].total == 1


# ===========================================================================
# Category comparison
# ===========================================================================

class TestCategoryComparison:
    def test_category_scores_in_comparison(self):
        s1 = summarize_run(
            [_pass_adherence("c1"), _fail_tool_usage("c2")],
            _meta(fmt="plain-text"),
        )
        s2 = summarize_run(
            [_pass_adherence("c1"), _pass_tool_usage("c2")],
            _meta(fmt="ape"),
        )
        comp = summarize_comparison([s1, s2])
        assert "adherence" in comp.per_category
        assert "tool_usage" in comp.per_category

    def test_per_category_cross_format(self):
        s1 = summarize_run(
            [_pass_adherence("c1"), _fail_tool_usage("c2")],
            _meta(fmt="plain-text"),
        )
        s2 = summarize_run(
            [_pass_adherence("c1"), _pass_tool_usage("c2")],
            _meta(fmt="ape"),
        )
        comp = summarize_comparison([s1, s2])
        # Check adherence category scores for both formats
        adh_scores = comp.per_category["adherence"]
        assert "plain-text" in adh_scores
        assert "ape" in adh_scores
        assert adh_scores["plain-text"].pass_rate == 1.0
        assert adh_scores["ape"].pass_rate == 1.0

    def test_per_category_mismatched_formats(self):
        # One format has a category that the other doesn't
        s1 = summarize_run(
            [_pass_adherence("c1"), _pass_tool_usage("c2")],
            _meta(fmt="plain-text"),
        )
        s2 = summarize_run(
            [_pass_adherence("c1")],  # no tool_usage check
            _meta(fmt="ape"),
        )
        comp = summarize_comparison([s1, s2])
        # Both categories should be in per_category
        assert "adherence" in comp.per_category
        assert "tool_usage" in comp.per_category
        # tool_usage should have plain-text but not ape
        tool_scores = comp.per_category["tool_usage"]
        assert "plain-text" in tool_scores
        assert "ape" not in tool_scores


# ===========================================================================
# Format with categories
# ===========================================================================

class TestFormatWithCategories:
    def test_run_summary_shows_category_breakdown(self):
        outcomes = [
            _pass_adherence("c1"),
            _fail_adherence("c2"),
            _pass_tool_usage("c3"),
        ]
        s = summarize_run(outcomes, _meta())
        text = format_run_summary(s)
        assert "By Category:" in text
        assert "adherence" in text
        assert "tool_usage" in text

    def test_comparison_shows_category_breakdown(self):
        s1 = summarize_run(
            [_pass_adherence("c1"), _fail_tool_usage("c2")],
            _meta(fmt="plain-text"),
        )
        s2 = summarize_run(
            [_pass_adherence("c1"), _pass_tool_usage("c2")],
            _meta(fmt="ape"),
        )
        comp = summarize_comparison([s1, s2])
        text = format_comparison(comp)
        assert "Category Breakdown:" in text
        assert "adherence" in text
        assert "tool_usage" in text

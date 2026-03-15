"""
Tests for benchmark/scripts/results/statistical_report.py.

Tests cover:
- analyze_format_effects() with various data scenarios
- Pairwise comparisons
- Multiple comparison correction (Holm-Bonferroni)
- Effect size reporting
- Human-readable formatting
- Edge cases (single runs, unequal sample sizes, no significant differences)
"""

from __future__ import annotations

import numpy as np
import pytest

from statistical_report import (
    PairwiseFormatComparison,
    StatisticalReport,
    analyze_format_effects,
    format_statistical_report,
)


# ===========================================================================
# Test data generators
# ===========================================================================


def _make_scores(n: int, mean: float = 0.85, std: float = 0.05) -> list[float]:
    """Generate synthetic pass rates with fixed seed for reproducibility."""
    rng = np.random.default_rng(42)
    scores = rng.normal(mean, std, n)
    # Clip to [0, 1] range
    return [float(np.clip(s, 0.0, 1.0)) for s in scores]


# ===========================================================================
# Basic functionality tests
# ===========================================================================


class TestAnalyzeFormatEffects:
    def test_two_formats_identical(self):
        """Two identical formats should show no significant difference."""
        scores = _make_scores(10, mean=0.85, std=0.02)
        format_scores = {
            "format_a": scores,
            "format_b": scores,
        }
        report = analyze_format_effects(format_scores, alpha=0.05, n_bootstrap=1000)

        assert len(report.comparisons) == 1
        assert report.comparisons[0].format_a == "format_a"
        assert report.comparisons[0].format_b == "format_b"
        # With identical scores, delta should be ~0
        assert abs(report.comparisons[0].mean_delta) < 0.01

    def test_two_formats_different(self):
        """Two formats with different means should produce comparison."""
        scores_a = _make_scores(10, mean=0.90, std=0.02)
        scores_b = _make_scores(10, mean=0.80, std=0.02)
        format_scores = {
            "format_a": scores_a,
            "format_b": scores_b,
        }
        report = analyze_format_effects(format_scores, alpha=0.05, n_bootstrap=1000)

        assert len(report.comparisons) == 1
        comp = report.comparisons[0]
        # format_a has higher mean
        assert comp.mean_a > comp.mean_b
        assert comp.mean_delta > 0

    def test_three_formats(self):
        """Three formats should produce 3 pairwise comparisons."""
        scores_a = _make_scores(10, mean=0.85)
        scores_b = _make_scores(10, mean=0.87)
        scores_c = _make_scores(10, mean=0.89)
        format_scores = {
            "a": scores_a,
            "b": scores_b,
            "c": scores_c,
        }
        report = analyze_format_effects(format_scores, alpha=0.05, n_bootstrap=500)

        # Should have 3 pairwise comparisons: (a,b), (a,c), (b,c)
        assert len(report.comparisons) == 3
        assert report.n_comparisons == 3

    def test_four_formats(self):
        """Four formats should produce 6 pairwise comparisons."""
        format_scores = {
            f"fmt{i}": _make_scores(8, mean=0.80 + i*0.02)
            for i in range(4)
        }
        report = analyze_format_effects(format_scores, alpha=0.05, n_bootstrap=500)

        # C(4,2) = 6 pairs
        assert len(report.comparisons) == 6
        assert report.n_comparisons == 6

    def test_fewer_than_two_formats_raises(self):
        """Needs at least 2 formats."""
        with pytest.raises(ValueError, match="at least 2 formats"):
            analyze_format_effects({"fmt1": [0.8, 0.9]}, alpha=0.05)

    def test_empty_format_raises(self):
        """Empty score list raises ValueError."""
        with pytest.raises(ValueError, match="no scores"):
            analyze_format_effects({"fmt1": [], "fmt2": [0.8]}, alpha=0.05)


# ===========================================================================
# Unequal sample sizes
# ===========================================================================


class TestUnequalSampleSizes:
    def test_different_number_of_runs(self):
        """Formats with different numbers of runs should use min(n)."""
        scores_a = _make_scores(10, mean=0.85)
        scores_b = _make_scores(5, mean=0.87)  # Fewer samples
        format_scores = {
            "a": scores_a,
            "b": scores_b,
        }
        report = analyze_format_effects(format_scores, alpha=0.05, n_bootstrap=500)

        assert len(report.comparisons) == 1
        # Should only use min(10, 5) = 5 paired samples
        assert report.comparisons[0].n_runs == 5

    def test_single_run_format_skipped(self):
        """Formats with only 1 run should be skipped (need >= 2 for paired test)."""
        scores_a = [0.85]  # Single run
        scores_b = _make_scores(5, mean=0.87)
        format_scores = {
            "a": scores_a,
            "b": scores_b,
        }
        report = analyze_format_effects(format_scores, alpha=0.05, n_bootstrap=500)

        # Should be skipped because paired test needs n >= 2
        assert len(report.comparisons) == 0


# ===========================================================================
# Multiple comparison correction
# ===========================================================================


class TestHolmBonferroniCorrection:
    def test_correction_applied(self):
        """Corrected p-values should be >= raw p-values."""
        scores_a = _make_scores(20, mean=0.85, std=0.01)
        scores_b = _make_scores(20, mean=0.90, std=0.01)  # Clearly different
        scores_c = _make_scores(20, mean=0.92, std=0.01)
        format_scores = {
            "a": scores_a,
            "b": scores_b,
            "c": scores_c,
        }
        report = analyze_format_effects(format_scores, alpha=0.05, n_bootstrap=2000)

        for comp in report.comparisons:
            # Corrected p >= raw p (always, by Holm procedure)
            assert comp.p_value_corrected >= comp.p_value

    def test_correction_method_recorded(self):
        """Report should record which correction method was used."""
        format_scores = {
            "a": _make_scores(10),
            "b": _make_scores(10),
        }
        report = analyze_format_effects(format_scores)
        assert report.correction_method == "holm_bonferroni"

    def test_alpha_preserved(self):
        """Report should preserve alpha."""
        alpha = 0.01
        format_scores = {
            "a": _make_scores(10),
            "b": _make_scores(10),
        }
        report = analyze_format_effects(format_scores, alpha=alpha)
        assert report.alpha == alpha


# ===========================================================================
# Effect sizes and statistical properties
# ===========================================================================


class TestEffectSizes:
    def test_cohens_d_computed(self):
        """Each comparison should report Cohen's d as a float."""
        scores_a = _make_scores(15, mean=0.85, std=0.03)
        scores_b = _make_scores(15, mean=0.90, std=0.03)
        format_scores = {
            "a": scores_a,
            "b": scores_b,
        }
        report = analyze_format_effects(format_scores, alpha=0.05, n_bootstrap=1000)

        assert len(report.comparisons) == 1
        d = report.comparisons[0].effect_size
        assert isinstance(d, float)
        # Effect size should be computed (not NaN or inf)
        assert not np.isnan(d) and not np.isinf(d)

    def test_confidence_intervals(self):
        """Each comparison should have a CI."""
        format_scores = {
            "a": _make_scores(12, mean=0.85, std=0.02),
            "b": _make_scores(12, mean=0.87, std=0.02),
        }
        report = analyze_format_effects(format_scores, alpha=0.05, n_bootstrap=1000)

        comp = report.comparisons[0]
        # CI should be properly ordered
        assert comp.ci_lower <= comp.ci_upper
        # CI should be centered roughly around mean_delta
        ci_center = (comp.ci_lower + comp.ci_upper) / 2
        assert abs(ci_center - comp.mean_delta) < 0.05


# ===========================================================================
# Significance and any_significant flag
# ===========================================================================


class TestSignificanceFlags:
    def test_any_significant_true_when_sig_found(self):
        """any_significant should be True if any comparison is significant."""
        # Create formats with large, consistent differences
        scores_a = [0.95] * 20
        scores_b = [0.70] * 20
        format_scores = {
            "a": scores_a,
            "b": scores_b,
        }
        report = analyze_format_effects(format_scores, alpha=0.05, n_bootstrap=2000)

        # With such large difference, should be significant
        assert any(c.significant for c in report.comparisons)
        assert report.any_significant is True

    def test_any_significant_false_when_none_sig(self):
        """any_significant should be False if no comparisons are significant."""
        # Identical or very similar formats
        scores = _make_scores(10, mean=0.85, std=0.001)
        format_scores = {
            "a": scores[:],
            "b": scores[:],
        }
        report = analyze_format_effects(format_scores, alpha=0.05, n_bootstrap=1000)

        assert report.any_significant is False


# ===========================================================================
# Empty and edge cases
# ===========================================================================


class TestEdgeCases:
    def test_all_pairs_skipped_empty_report(self):
        """If all pairs are skipped, report should be empty but valid."""
        format_scores = {
            "a": [0.8],  # Single run, will be skipped
            "b": [0.9],  # Single run, will be skipped
        }
        report = analyze_format_effects(format_scores)

        assert len(report.comparisons) == 0
        assert report.any_significant is False
        assert report.n_comparisons == 0

    def test_reproducibility_with_rng(self):
        """Results should be reproducible with seeded RNG."""
        format_scores = {
            "a": _make_scores(10, mean=0.85),
            "b": _make_scores(10, mean=0.87),
        }

        rng1 = np.random.default_rng(12345)
        report1 = analyze_format_effects(format_scores, n_bootstrap=500, rng=rng1)

        rng2 = np.random.default_rng(12345)
        report2 = analyze_format_effects(format_scores, n_bootstrap=500, rng=rng2)

        assert report1.comparisons[0].p_value == report2.comparisons[0].p_value
        assert report1.comparisons[0].ci_lower == report2.comparisons[0].ci_lower


# ===========================================================================
# PairwiseFormatComparison dataclass
# ===========================================================================


class TestPairwiseFormatComparison:
    def test_frozen_dataclass(self):
        """PairwiseFormatComparison should be immutable."""
        comp = PairwiseFormatComparison(
            format_a="a",
            format_b="b",
            n_runs=10,
            mean_a=0.85,
            mean_b=0.87,
            mean_delta=-0.02,
            ci_lower=-0.05,
            ci_upper=0.01,
            p_value=0.15,
            p_value_corrected=0.3,
            effect_size=-0.4,
            significant=False,
        )

        with pytest.raises((AttributeError, TypeError)):
            comp.significant = True

    def test_fields_preserved(self):
        """All fields should be correctly stored."""
        comp = PairwiseFormatComparison(
            format_a="plain-text",
            format_b="ape",
            n_runs=42,
            mean_a=0.8123,
            mean_b=0.9234,
            mean_delta=0.1111,
            ci_lower=0.0567,
            ci_upper=0.1655,
            p_value=0.0234,
            p_value_corrected=0.0468,
            effect_size=2.5,
            significant=True,
        )

        assert comp.format_a == "plain-text"
        assert comp.format_b == "ape"
        assert comp.n_runs == 42
        assert comp.mean_a == 0.8123
        assert comp.significant is True


# ===========================================================================
# StatisticalReport dataclass
# ===========================================================================


class TestStatisticalReport:
    def test_frozen_dataclass(self):
        """StatisticalReport should be immutable."""
        report = StatisticalReport(
            comparisons=[],
            correction_method="holm_bonferroni",
            alpha=0.05,
            n_comparisons=0,
            any_significant=False,
        )

        with pytest.raises((AttributeError, TypeError)):
            report.alpha = 0.01

    def test_fields_preserved(self):
        """All fields should be correctly stored."""
        comp = PairwiseFormatComparison(
            format_a="a", format_b="b", n_runs=5,
            mean_a=0.8, mean_b=0.85, mean_delta=0.05,
            ci_lower=0.01, ci_upper=0.09, p_value=0.05,
            p_value_corrected=0.1, effect_size=0.5, significant=False,
        )
        report = StatisticalReport(
            comparisons=[comp],
            correction_method="holm_bonferroni",
            alpha=0.05,
            n_comparisons=1,
            any_significant=False,
        )

        assert len(report.comparisons) == 1
        assert report.n_comparisons == 1
        assert report.correction_method == "holm_bonferroni"


# ===========================================================================
# Human-readable formatting
# ===========================================================================


class TestFormatStatisticalReport:
    def test_empty_report_formatting(self):
        """Empty report should format gracefully."""
        report = StatisticalReport(
            comparisons=[],
            correction_method="holm_bonferroni",
            alpha=0.05,
            n_comparisons=0,
            any_significant=False,
        )
        text = format_statistical_report(report)

        assert "Statistical Report" in text
        assert "No valid format pairs" in text

    def test_single_comparison_formatting(self):
        """Single comparison should format correctly."""
        comp = PairwiseFormatComparison(
            format_a="plain-text",
            format_b="ape",
            n_runs=10,
            mean_a=0.85,
            mean_b=0.90,
            mean_delta=0.05,
            ci_lower=0.01,
            ci_upper=0.09,
            p_value=0.02,
            p_value_corrected=0.02,
            effect_size=0.5,
            significant=True,
        )
        report = StatisticalReport(
            comparisons=[comp],
            correction_method="holm_bonferroni",
            alpha=0.05,
            n_comparisons=1,
            any_significant=True,
        )
        text = format_statistical_report(report)

        assert "plain-text" in text
        assert "ape" in text
        assert "0.85" in text
        assert "0.90" in text
        assert "Significant findings" in text

    def test_significant_comparison_marked(self):
        """Significant comparisons should be marked."""
        comp = PairwiseFormatComparison(
            format_a="a", format_b="b", n_runs=20,
            mean_a=0.95, mean_b=0.70, mean_delta=0.25,
            ci_lower=0.20, ci_upper=0.30, p_value=0.001,
            p_value_corrected=0.001, effect_size=2.5, significant=True,
        )
        report = StatisticalReport(
            comparisons=[comp],
            correction_method="holm_bonferroni",
            alpha=0.05,
            n_comparisons=1,
            any_significant=True,
        )
        text = format_statistical_report(report)

        # Should contain significance marker
        assert "***" in text or "significant" in text.lower()

    def test_effect_size_interpretation(self):
        """Report should interpret effect size magnitude."""
        comp = PairwiseFormatComparison(
            format_a="a", format_b="b", n_runs=10,
            mean_a=0.8, mean_b=0.82, mean_delta=0.02,
            ci_lower=-0.01, ci_upper=0.05, p_value=0.3,
            p_value_corrected=0.3, effect_size=0.15, significant=False,
        )
        report = StatisticalReport(
            comparisons=[comp],
            correction_method="holm_bonferroni",
            alpha=0.05,
            n_comparisons=1,
            any_significant=False,
        )
        text = format_statistical_report(report)

        # Should label the effect size
        assert "negligible" in text or "small" in text

    def test_multiple_comparisons_formatting(self):
        """Multiple comparisons should be clearly separated."""
        comps = [
            PairwiseFormatComparison(
                format_a="a", format_b="b", n_runs=10,
                mean_a=0.8+i*0.05, mean_b=0.85+i*0.05,
                mean_delta=0.05, ci_lower=0.01, ci_upper=0.09,
                p_value=0.05, p_value_corrected=0.1,
                effect_size=0.5, significant=False,
            )
            for i in range(2)
        ]
        report = StatisticalReport(
            comparisons=comps,
            correction_method="holm_bonferroni",
            alpha=0.05,
            n_comparisons=2,
            any_significant=False,
        )
        text = format_statistical_report(report)

        # Should list both comparisons
        assert text.count("vs") >= 2


# ===========================================================================
# Integration tests
# ===========================================================================


class TestIntegration:
    def test_end_to_end_analysis_and_formatting(self):
        """Full workflow: analyze then format."""
        format_scores = {
            "fmt_a": _make_scores(15, mean=0.80, std=0.02),
            "fmt_b": _make_scores(15, mean=0.85, std=0.02),
            "fmt_c": _make_scores(15, mean=0.88, std=0.02),
        }

        report = analyze_format_effects(format_scores, alpha=0.05, n_bootstrap=1000)
        text = format_statistical_report(report)

        # Should have successfully analyzed 3 formats
        assert len(report.comparisons) == 3
        # Should produce readable output
        assert len(text) > 100
        assert "Statistical Report" in text
        assert "fmt_a" in text or "format_a" in text or "Format" in text

    def test_realistic_benchmark_scenario(self):
        """Simulate realistic benchmark with multiple runs and formats."""
        # 5 runs of each format
        format_scores = {
            "plain-text": [0.75, 0.78, 0.80, 0.76, 0.79],
            "adhoc-xml": [0.82, 0.85, 0.83, 0.84, 0.86],
            "ape": [0.88, 0.90, 0.89, 0.91, 0.92],
        }

        report = analyze_format_effects(
            format_scores,
            alpha=0.05,
            n_bootstrap=2000,
        )

        # Should have all 3 pairwise comparisons
        assert len(report.comparisons) == 3
        # With such large differences, likely to find significance
        # (at least some comparisons)
        text = format_statistical_report(report)
        assert "Statistical Report" in text

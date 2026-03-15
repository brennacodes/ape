"""Tests for baseline metrics capture in environment.py."""

import os
import subprocess
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from environment import (
    BaselineMetrics,
    BenchmarkEnvironment,
    SetupSnapshot,
    _parse_test_count,
    _parse_coverage_pct,
    _truncate,
)


# ===========================================================================
# _truncate
# ===========================================================================

class TestTruncate:
    def test_short_string_unchanged(self):
        assert _truncate("hello", 100) == "hello"

    def test_exact_length_unchanged(self):
        assert _truncate("abcde", 5) == "abcde"

    def test_long_string_returned_in_full(self):
        result = _truncate("abcdefghij", 5)
        assert result == "abcdefghij"

    def test_empty_string(self):
        assert _truncate("", 100) == ""

    def test_no_max_len_returns_full(self):
        assert _truncate("hello world") == "hello world"


# ===========================================================================
# _parse_test_count
# ===========================================================================

class TestParseTestCount:
    def test_standard_output(self):
        output = "test result: ok. 42 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"
        assert _parse_test_count(output) == 42

    def test_with_failures(self):
        output = "test result: FAILED. 10 passed; 2 failed; 0 ignored"
        assert _parse_test_count(output) == 10

    def test_zero_passed(self):
        output = "test result: FAILED. 0 passed; 5 failed; 0 ignored"
        assert _parse_test_count(output) == 0

    def test_no_match(self):
        assert _parse_test_count("compiling something...") is None

    def test_empty_string(self):
        assert _parse_test_count("") is None


# ===========================================================================
# _parse_coverage_pct
# ===========================================================================

class TestParseCoveragePct:
    def test_total_line(self):
        output = "TOTAL   100   200   36.54%"
        assert _parse_coverage_pct(output) == 36.54

    def test_percentage_at_end_of_line(self):
        output = "some stats\ncoverage: 85.2%\nmore stuff"
        assert _parse_coverage_pct(output) == 85.2

    def test_hundred_percent(self):
        output = "TOTAL   50   50   100.00%"
        assert _parse_coverage_pct(output) == 100.0

    def test_zero_percent(self):
        output = "TOTAL   0   100   0.00%"
        assert _parse_coverage_pct(output) == 0.0

    def test_no_match(self):
        assert _parse_coverage_pct("no coverage data here") is None

    def test_empty_string(self):
        assert _parse_coverage_pct("") is None


# ===========================================================================
# _capture_baseline
# ===========================================================================

class TestCaptureBaseline:
    def test_returns_none_without_cargo_toml(self, tmp_path):
        env = BenchmarkEnvironment()
        result = env._capture_baseline(tmp_path)
        assert result is None

    def test_returns_metrics_with_cargo_toml(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'test'\n")
        env = BenchmarkEnvironment()

        fake_result = subprocess.CompletedProcess(
            args=["cargo"], returncode=0, stdout="test result: ok. 5 passed; 0 failed", stderr="",
        )

        with patch("environment._run_cargo_cmd", return_value=fake_result):
            result = env._capture_baseline(tmp_path)

        assert result is not None
        assert isinstance(result, BaselineMetrics)
        assert result.cargo_test_exit_code == 0
        assert result.test_count == 5

    def test_handles_cargo_failure(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'test'\n")
        env = BenchmarkEnvironment()

        with patch("environment._run_cargo_cmd", return_value=None):
            result = env._capture_baseline(tmp_path)

        assert result is not None
        assert result.cargo_test_exit_code is None
        assert result.test_count is None
        assert result.coverage_pct is None

    def test_captures_coverage(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'test'\n")
        env = BenchmarkEnvironment()

        def mock_cargo(workspace, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "llvm-cov":
                return subprocess.CompletedProcess(
                    args=["cargo"], returncode=0,
                    stdout="TOTAL   100   200   36.54%", stderr="",
                )
            return subprocess.CompletedProcess(
                args=["cargo"], returncode=0,
                stdout="test result: ok. 10 passed; 0 failed", stderr="",
            )

        with patch("environment._run_cargo_cmd", side_effect=mock_cargo):
            result = env._capture_baseline(tmp_path)

        assert result.coverage_pct == 36.54
        assert result.test_count == 10


# ===========================================================================
# SetupSnapshot.baseline field
# ===========================================================================

class TestSetupSnapshotBaseline:
    def test_default_none(self):
        snap = SetupSnapshot()
        assert snap.baseline is None

    def test_with_baseline(self):
        baseline = BaselineMetrics(cargo_test_exit_code=0, test_count=42)
        snap = SetupSnapshot(baseline=baseline)
        assert snap.baseline is not None
        assert snap.baseline.test_count == 42

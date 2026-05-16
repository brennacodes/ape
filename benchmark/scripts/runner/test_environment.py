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
    PromptInjection,
    SetupSnapshot,
    _parse_test_count,
    _parse_coverage_pct,
    _truncate,
    _ADHOC_XML_PROMPT_PREAMBLE,
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


# ===========================================================================
# (format, source) injection semantics
# ===========================================================================


def _write_text(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestInjectBenchmarkFiles:
    def test_plain_text_claude_md_writes_claude_md(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        workflow = tmp_path / "wf.txt"
        workflow.write_text("PLAIN WORKFLOW", encoding="utf-8")
        env = BenchmarkEnvironment()
        env._inject_benchmark_files(workspace, workflow, "plain-text", "claude-md")
        assert (workspace / "CLAUDE.md").read_text(encoding="utf-8") == "PLAIN WORKFLOW"

    def test_plain_text_prompt_does_not_write_claude_md(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        workflow = tmp_path / "wf.txt"
        workflow.write_text("PLAIN WORKFLOW", encoding="utf-8")
        env = BenchmarkEnvironment()
        env._inject_benchmark_files(workspace, workflow, "plain-text", "prompt")
        assert not (workspace / "CLAUDE.md").exists()

    def test_markdown_prompt_does_not_write_claude_md(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        workflow = tmp_path / "wf.md"
        workflow.write_text("# MD WORKFLOW", encoding="utf-8")
        env = BenchmarkEnvironment()
        env._inject_benchmark_files(workspace, workflow, "markdown", "prompt")
        assert not (workspace / "CLAUDE.md").exists()

    def test_adhoc_xml_claude_md_keeps_both_fixture_files(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        _write_text(workspace / "CLAUDE.md", "FIXTURE CLAUDE_MD")
        _write_text(workspace / ".claude" / "bivvy-dev-workflow.md", "WF")
        workflow = workspace / "CLAUDE.md"  # adhoc-xml uses fixture ref
        env = BenchmarkEnvironment()
        env._inject_benchmark_files(
            workspace, workflow, "adhoc-xml", "claude-md",
            fixture_workflow_files=["CLAUDE.md", ".claude/bivvy-dev-workflow.md"],
        )
        assert (workspace / "CLAUDE.md").read_text(encoding="utf-8") == "FIXTURE CLAUDE_MD"
        assert (workspace / ".claude" / "bivvy-dev-workflow.md").is_file()

    def test_adhoc_xml_prompt_strips_claude_md_keeps_workflow(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        _write_text(workspace / "CLAUDE.md", "FIXTURE CLAUDE_MD")
        _write_text(workspace / ".claude" / "bivvy-dev-workflow.md", "WF")
        workflow = workspace / "CLAUDE.md"  # adhoc-xml ref
        env = BenchmarkEnvironment()
        env._inject_benchmark_files(
            workspace, workflow, "adhoc-xml", "prompt",
            fixture_workflow_files=["CLAUDE.md", ".claude/bivvy-dev-workflow.md"],
        )
        assert not (workspace / "CLAUDE.md").exists()
        assert (workspace / ".claude" / "bivvy-dev-workflow.md").is_file()


class TestGetWorkflowContent:
    def test_plain_text_claude_md_returns_none(self, tmp_path):
        workflow = tmp_path / "wf.txt"
        workflow.write_text("PT", encoding="utf-8")
        env = BenchmarkEnvironment()
        assert env.get_workflow_content(workflow, "plain-text", "claude-md") is None

    def test_plain_text_prompt_returns_injection_with_divider(self, tmp_path):
        workflow = tmp_path / "wf.txt"
        workflow.write_text("PT BODY", encoding="utf-8")
        env = BenchmarkEnvironment()
        injection = env.get_workflow_content(workflow, "plain-text", "prompt")
        assert isinstance(injection, PromptInjection)
        assert injection.preamble == "PT BODY"
        assert injection.divider is True

    def test_markdown_prompt_returns_injection_with_divider(self, tmp_path):
        workflow = tmp_path / "wf.md"
        workflow.write_text("# MD", encoding="utf-8")
        env = BenchmarkEnvironment()
        injection = env.get_workflow_content(workflow, "markdown", "prompt")
        assert injection is not None
        assert injection.preamble == "# MD"
        assert injection.divider is True

    def test_ape_claude_md_returns_none(self, tmp_path):
        workflow = tmp_path / "wf.ape"
        workflow.write_text("<ape />", encoding="utf-8")
        env = BenchmarkEnvironment()
        assert env.get_workflow_content(workflow, "ape", "claude-md") is None

    def test_adhoc_xml_prompt_returns_hardcoded_preamble_no_divider(self, tmp_path):
        # adhoc-xml uses a ref path; the content of the file is NOT read.
        ref = tmp_path / "ignored.md"
        ref.write_text("SHOULD NOT BE READ", encoding="utf-8")
        env = BenchmarkEnvironment()
        injection = env.get_workflow_content(ref, "adhoc-xml", "prompt")
        assert injection is not None
        assert injection.preamble == _ADHOC_XML_PROMPT_PREAMBLE
        assert injection.divider is False

    def test_adhoc_xml_claude_md_returns_none(self, tmp_path):
        ref = tmp_path / "ignored.md"
        ref.write_text("X", encoding="utf-8")
        env = BenchmarkEnvironment()
        assert env.get_workflow_content(ref, "adhoc-xml", "claude-md") is None

    def test_no_workflow_returns_none(self, tmp_path):
        ref = tmp_path / "ignored"
        ref.write_text("X", encoding="utf-8")
        env = BenchmarkEnvironment()
        assert env.get_workflow_content(ref, "no-workflow", "") is None
        assert env.get_workflow_content(ref, "no-workflow", "prompt") is None
        assert env.get_workflow_content(ref, "no-workflow", "claude-md") is None

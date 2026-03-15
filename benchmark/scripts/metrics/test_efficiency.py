"""
Comprehensive tests for efficiency metrics module.
"""

import json
import tempfile
from pathlib import Path
import sys
import os

import pytest

# Wire up runner module so trace.py is importable
_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.join(_HERE, "..", "runner")
_METRICS = os.path.dirname(_HERE)  # parent directory (scripts/)
if _RUNNER not in sys.path:
    sys.path.insert(0, _RUNNER)
if _METRICS not in sys.path:
    sys.path.insert(0, _METRICS)

from trace import load_trace_from_string
from metrics.efficiency import (
    EfficiencyMetrics,
    EfficiencySummary,
    extract_efficiency,
    summarize_efficiency,
    format_efficiency_comparison,
    _count_impl_test_cycles,
    _count_fix_retry_cycles,
    _count_unique_files_touched,
)


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def sample_record():
    """Create a sample run record JSON."""
    return {
        "fixture_id": "bivvy",
        "format": "ape",
        "prompt_id": "test/example",
        "run_id": 0,
        "outcomes": [],
        "total": 10,
        "passed": 7,
        "failed": 2,
        "skipped": 1,
        "pass_rate": 0.7,
        "wall_clock_ms": 120000.0,  # 120 seconds
        "duration_ms": 100.0,
        "duration_api_ms": 50.0,
        "input_tokens": 5000,
        "output_tokens": 3000,
        "cache_creation_tokens": 1000,
        "cache_read_tokens": 500,
        "cost_usd": 0.25,
        "num_turns": 5,
        "max_turns_configured": 10,
        "hit_turn_limit": False,
        "error": None,
        "raw_output": "",
    }


@pytest.fixture
def sample_trace_jsonl():
    """Create a sample JSONL trace with tool calls."""
    lines = [
        json.dumps({
            "type": "system",
            "subtype": "init",
            "cwd": "/tmp/test",
            "session_id": "test-session",
        }),
        json.dumps({
            "type": "user",
            "sessionId": "test-session",
            "message": {
                "role": "user",
                "content": "Fix the bug",
            },
        }),
        json.dumps({
            "type": "assistant",
            "sessionId": "test-session",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "read1",
                        "name": "Read",
                        "input": {"file_path": "/tmp/test/src/main.rs"},
                    }
                ],
            },
        }),
        json.dumps({
            "type": "user",
            "sessionId": "test-session",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "read1",
                        "content": "fn main() { }",
                    }
                ],
            },
        }),
        json.dumps({
            "type": "assistant",
            "sessionId": "test-session",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "write1",
                        "name": "Write",
                        "input": {
                            "file_path": "/tmp/test/src/lib.rs",
                            "content": "// test",
                        },
                    }
                ],
            },
        }),
        json.dumps({
            "type": "user",
            "sessionId": "test-session",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "write1",
                        "content": "File written",
                    }
                ],
            },
        }),
        json.dumps({
            "type": "assistant",
            "sessionId": "test-session",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "bash1",
                        "name": "Bash",
                        "input": {"command": "cd /tmp/test && cargo test"},
                    }
                ],
            },
        }),
        json.dumps({
            "type": "user",
            "sessionId": "test-session",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "bash1",
                        "content": "test result: ok. 5 passed; 0 failed",
                    }
                ],
            },
        }),
    ]
    return "\n".join(lines)


@pytest.fixture
def trace_with_cycles(sample_trace_jsonl):
    """Create a trace with multiple implement→test and fix→retry cycles."""
    # Start with base trace
    lines = sample_trace_jsonl.split("\n")

    # Add another Write + cargo test (impl_test_cycle 2)
    lines.append(json.dumps({
        "type": "assistant",
        "sessionId": "test-session",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "write2",
                    "name": "Write",
                    "input": {
                        "file_path": "/tmp/test/src/main.rs",
                        "content": "// fixed",
                    },
                }
            ],
        },
    }))
    lines.append(json.dumps({
        "type": "user",
        "sessionId": "test-session",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "write2",
                    "content": "File written",
                }
            ],
        },
    }))
    lines.append(json.dumps({
        "type": "assistant",
        "sessionId": "test-session",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "bash2",
                    "name": "Bash",
                    "input": {"command": "cd /tmp/test && cargo test"},
                }
            ],
        },
    }))
    lines.append(json.dumps({
        "type": "user",
        "sessionId": "test-session",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "bash2",
                    "content": "test result: ok. 5 passed; 0 failed",
                }
            ],
        },
    }))

    return "\n".join(lines)


# ============================================================================
# Tests for Cycle Counting
# ============================================================================


class TestCycleCounting:
    """Tests for impl_test and fix_retry cycle counting."""

    def test_impl_test_cycles_basic(self, trace_with_cycles):
        """Test counting of implement→test cycles."""
        trace = load_trace_from_string(trace_with_cycles)
        cycles = _count_impl_test_cycles(trace)
        # We have 2 Write operations followed by cargo tests
        assert cycles >= 2, f"Expected at least 2 cycles, got {cycles}"

    def test_impl_test_cycles_no_tests(self, sample_trace_jsonl):
        """Test when there are no cargo tests."""
        # Modify trace to have only Write without cargo test
        lines = sample_trace_jsonl.split("\n")
        # Remove the cargo test calls
        filtered = [line for line in lines if "cargo test" not in line]
        trace = load_trace_from_string("\n".join(filtered))
        cycles = _count_impl_test_cycles(trace)
        # Should be 0 since no tests
        assert cycles == 0

    def test_impl_test_cycles_no_writes(self):
        """Test when there are no writes but tests exist."""
        jsonl = "\n".join([
            json.dumps({
                "type": "system",
                "subtype": "init",
                "cwd": "/tmp",
                "session_id": "test",
            }),
            json.dumps({
                "type": "assistant",
                "sessionId": "test",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "bash1",
                            "name": "Bash",
                            "input": {"command": "cargo test"},
                        }
                    ],
                },
            }),
            json.dumps({
                "type": "user",
                "sessionId": "test",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "bash1",
                            "content": "test result: ok. 0 passed; 0 failed",
                        }
                    ],
                },
            }),
        ])
        trace = load_trace_from_string(jsonl)
        cycles = _count_impl_test_cycles(trace)
        # Should be 0 since no writes before tests
        assert cycles == 0

    def test_fix_retry_cycles_basic(self):
        """Test counting of fix→retry cycles."""
        jsonl = "\n".join([
            json.dumps({
                "type": "system",
                "subtype": "init",
                "cwd": "/tmp",
                "session_id": "test",
            }),
            # First failing cargo test
            json.dumps({
                "type": "assistant",
                "sessionId": "test",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "bash1",
                            "name": "Bash",
                            "input": {"command": "cargo test"},
                        }
                    ],
                },
            }),
            json.dumps({
                "type": "user",
                "sessionId": "test",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "bash1",
                            "content": "test result: FAILED. 2 passed; 1 failed",
                            "is_error": True,
                        }
                    ],
                },
            }),
            # Edit to fix
            json.dumps({
                "type": "assistant",
                "sessionId": "test",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "edit1",
                            "name": "Edit",
                            "input": {
                                "file_path": "/tmp/src/main.rs",
                                "old_string": "broken",
                                "new_string": "fixed",
                            },
                        }
                    ],
                },
            }),
            json.dumps({
                "type": "user",
                "sessionId": "test",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "edit1",
                            "content": "Edited successfully",
                        }
                    ],
                },
            }),
            # Re-run the same test command
            json.dumps({
                "type": "assistant",
                "sessionId": "test",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "bash2",
                            "name": "Bash",
                            "input": {"command": "cargo test"},
                        }
                    ],
                },
            }),
            json.dumps({
                "type": "user",
                "sessionId": "test",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "bash2",
                            "content": "test result: ok. 3 passed; 0 failed",
                        }
                    ],
                },
            }),
        ])
        trace = load_trace_from_string(jsonl)
        cycles = _count_fix_retry_cycles(trace)
        assert cycles >= 1, f"Expected at least 1 fix-retry cycle, got {cycles}"


# ============================================================================
# Tests for File Touching
# ============================================================================


class TestUniqueFilesTouched:
    """Tests for counting unique files accessed."""

    def test_unique_files_basic(self, sample_trace_jsonl):
        """Test counting of unique files touched."""
        trace = load_trace_from_string(sample_trace_jsonl)
        count = _count_unique_files_touched(trace)
        # We have /tmp/test/src/main.rs (Read) and /tmp/test/src/lib.rs (Write)
        assert count == 2, f"Expected 2 files, got {count}"

    def test_unique_files_duplicates(self):
        """Test that duplicate accesses to same file count once."""
        jsonl = "\n".join([
            json.dumps({
                "type": "system",
                "subtype": "init",
                "cwd": "/tmp",
                "session_id": "test",
            }),
            json.dumps({
                "type": "assistant",
                "sessionId": "test",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "read1",
                            "name": "Read",
                            "input": {"file_path": "/tmp/test.rs"},
                        }
                    ],
                },
            }),
            json.dumps({
                "type": "user",
                "sessionId": "test",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "read1",
                            "content": "content",
                        }
                    ],
                },
            }),
            json.dumps({
                "type": "assistant",
                "sessionId": "test",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "read2",
                            "name": "Read",
                            "input": {"file_path": "/tmp/test.rs"},
                        }
                    ],
                },
            }),
            json.dumps({
                "type": "user",
                "sessionId": "test",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "read2",
                            "content": "content",
                        }
                    ],
                },
            }),
        ])
        trace = load_trace_from_string(jsonl)
        count = _count_unique_files_touched(trace)
        assert count == 1, f"Expected 1 unique file (duplicate access), got {count}"


# ============================================================================
# Tests for extract_efficiency
# ============================================================================


class TestExtractEfficiency:
    """Tests for extracting metrics from records."""

    def test_extract_basic_metrics(self, sample_record, sample_trace_jsonl):
        """Test extracting basic metrics from record."""
        sample_record["raw_output"] = sample_trace_jsonl

        with tempfile.TemporaryDirectory() as tmpdir:
            record_path = Path(tmpdir) / "test.json"
            with open(record_path, "w") as f:
                json.dump(sample_record, f)

            metrics = extract_efficiency(str(record_path))

            assert metrics.wall_clock_seconds == 120.0
            assert metrics.input_tokens == 5000
            assert metrics.output_tokens == 3000
            assert metrics.total_tokens == 9000
            assert metrics.cost_usd == 0.25
            assert metrics.num_turns == 5
            assert metrics.checks_passed == 7

    def test_extract_with_zero_tokens(self, sample_record, sample_trace_jsonl):
        """Test handling of zero token counts."""
        sample_record["raw_output"] = sample_trace_jsonl
        sample_record["input_tokens"] = 0
        sample_record["output_tokens"] = 0
        sample_record["cache_creation_tokens"] = 0
        sample_record["cache_read_tokens"] = 0

        with tempfile.TemporaryDirectory() as tmpdir:
            record_path = Path(tmpdir) / "test.json"
            with open(record_path, "w") as f:
                json.dump(sample_record, f)

            metrics = extract_efficiency(str(record_path))

            assert metrics.total_tokens == 0
            # With 0 tokens, per-check should be inf (unavailable metric)
            assert metrics.tokens_per_check_passed == float("inf")

    def test_extract_with_no_checks_passed(self, sample_record, sample_trace_jsonl):
        """Test handling when no checks are passed (division by zero)."""
        sample_record["raw_output"] = sample_trace_jsonl
        sample_record["passed"] = 0
        sample_record["pass_rate"] = 0.0

        with tempfile.TemporaryDirectory() as tmpdir:
            record_path = Path(tmpdir) / "test.json"
            with open(record_path, "w") as f:
                json.dump(sample_record, f)

            metrics = extract_efficiency(str(record_path))

            assert metrics.checks_passed == 0
            assert metrics.tokens_per_check_passed == float("inf")
            assert metrics.seconds_per_check_passed == float("inf")
            assert metrics.cost_per_check_passed == float("inf")

    def test_extract_tool_call_breakdown(self, sample_record, sample_trace_jsonl):
        """Test that tool breakdown is computed correctly."""
        sample_record["raw_output"] = sample_trace_jsonl

        with tempfile.TemporaryDirectory() as tmpdir:
            record_path = Path(tmpdir) / "test.json"
            with open(record_path, "w") as f:
                json.dump(sample_record, f)

            metrics = extract_efficiency(str(record_path))

            assert metrics.total_tool_calls >= 3  # Read, Write, Bash
            assert "Read" in metrics.tool_call_breakdown
            assert "Write" in metrics.tool_call_breakdown
            assert "Bash" in metrics.tool_call_breakdown
            assert metrics.tool_call_breakdown["Read"] >= 1
            assert metrics.tool_call_breakdown["Write"] >= 1
            assert metrics.tool_call_breakdown["Bash"] >= 1


# ============================================================================
# Tests for summarize_efficiency
# ============================================================================


class TestSummarizeEfficiency:
    """Tests for aggregating metrics across runs."""

    def test_summarize_empty(self):
        """Test summarizing empty list."""
        summary = summarize_efficiency([], "test")
        assert summary.format == "test"
        assert summary.n_runs == 0
        assert summary.mean_tool_calls == 0.0

    def test_summarize_single_metric(self, sample_record, sample_trace_jsonl):
        """Test summarizing a single metric."""
        sample_record["raw_output"] = sample_trace_jsonl

        with tempfile.TemporaryDirectory() as tmpdir:
            record_path = Path(tmpdir) / "test.json"
            with open(record_path, "w") as f:
                json.dump(sample_record, f)

            metrics = extract_efficiency(str(record_path))
            summary = summarize_efficiency([metrics], "ape")

            assert summary.format == "ape"
            assert summary.n_runs == 1
            assert summary.mean_wall_clock == 120.0
            assert summary.mean_tokens == 9000.0
            assert summary.mean_cost == 0.25

    def test_summarize_multiple_metrics(self, sample_record, sample_trace_jsonl):
        """Test summarizing multiple metrics."""
        sample_record["raw_output"] = sample_trace_jsonl

        metrics_list = []
        for i in range(3):
            record = sample_record.copy()
            record["wall_clock_ms"] = 100000 + (i * 10000)
            record["input_tokens"] = 5000 + (i * 500)
            record["total_tokens"] = 9000 + (i * 1000)

            with tempfile.TemporaryDirectory() as tmpdir:
                record_path = Path(tmpdir) / f"test{i}.json"
                with open(record_path, "w") as f:
                    json.dump(record, f)
                metrics_list.append(extract_efficiency(str(record_path)))

        summary = summarize_efficiency(metrics_list, "markdown")

        assert summary.format == "markdown"
        assert summary.n_runs == 3
        assert summary.mean_wall_clock > 0
        assert summary.std_wall_clock >= 0  # std should be non-negative
        assert summary.std_tokens >= 0


# ============================================================================
# Tests for format_efficiency_comparison
# ============================================================================


class TestFormatEfficiencyComparison:
    """Tests for human-readable formatting."""

    def test_format_empty(self):
        """Test formatting empty summaries."""
        output = format_efficiency_comparison([])
        assert output == ""

    def test_format_single_summary(self, sample_record, sample_trace_jsonl):
        """Test formatting a single summary."""
        sample_record["raw_output"] = sample_trace_jsonl

        with tempfile.TemporaryDirectory() as tmpdir:
            record_path = Path(tmpdir) / "test.json"
            with open(record_path, "w") as f:
                json.dump(sample_record, f)

            metrics = extract_efficiency(str(record_path))
            summary = summarize_efficiency([metrics], "ape")
            output = format_efficiency_comparison([summary])

            assert "EFFICIENCY COMPARISON" in output
            assert "ape" in output
            assert "Total Tool Calls" in output
            assert "Tokens" in output
            assert "Cost" in output

    def test_format_multiple_summaries(self, sample_record, sample_trace_jsonl):
        """Test formatting multiple summaries for comparison."""
        sample_record["raw_output"] = sample_trace_jsonl

        summaries = []
        for format_name in ["ape", "markdown"]:
            with tempfile.TemporaryDirectory() as tmpdir:
                record_path = Path(tmpdir) / f"{format_name}.json"
                record = sample_record.copy()
                record["format"] = format_name
                with open(record_path, "w") as f:
                    json.dump(record, f)
                metrics = extract_efficiency(str(record_path))
                summaries.append(summarize_efficiency([metrics], format_name))

        output = format_efficiency_comparison(summaries)

        assert "EFFICIENCY COMPARISON" in output
        assert "ape" in output
        assert "markdown" in output


# ============================================================================
# Edge Cases and Integration Tests
# ============================================================================


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_missing_raw_output(self, sample_record):
        """Test handling when raw_output is missing."""
        sample_record["raw_output"] = ""

        with tempfile.TemporaryDirectory() as tmpdir:
            record_path = Path(tmpdir) / "test.json"
            with open(record_path, "w") as f:
                json.dump(sample_record, f)

            metrics = extract_efficiency(str(record_path))

            # Should still extract basic metrics
            assert metrics.wall_clock_seconds == 120.0
            assert metrics.total_tokens == 9000
            # But tool counts should be 0
            assert metrics.total_tool_calls == 0

    def test_invalid_json(self):
        """Test handling of invalid record JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            record_path = Path(tmpdir) / "invalid.json"
            with open(record_path, "w") as f:
                f.write("not valid json")

            with pytest.raises(json.JSONDecodeError):
                extract_efficiency(str(record_path))

    def test_pre_parsed_trace(self, sample_record, sample_trace_jsonl):
        """Test providing pre-parsed trace to extract_efficiency."""
        sample_record["raw_output"] = sample_trace_jsonl
        trace = load_trace_from_string(sample_trace_jsonl)

        with tempfile.TemporaryDirectory() as tmpdir:
            record_path = Path(tmpdir) / "test.json"
            with open(record_path, "w") as f:
                json.dump(sample_record, f)

            # Pass pre-parsed trace
            metrics = extract_efficiency(str(record_path), trace=trace)

            assert metrics.total_tool_calls >= 3
            assert metrics.tool_call_breakdown["Read"] >= 1

    def test_inf_and_nan_handling(self):
        """Test proper handling of inf and NaN values."""
        # Create metrics with no checks passed
        metrics = EfficiencyMetrics(
            checks_passed=0,
            total_tokens=1000,
            tokens_per_check_passed=float("inf"),
            wall_clock_seconds=60.0,
            seconds_per_check_passed=float("inf"),
        )

        summary = summarize_efficiency([metrics], "test")

        # Should not crash and should mark as inf
        assert summary.mean_tokens_per_pass == float("inf")
        assert summary.mean_seconds_per_pass == float("inf")

    def test_per_check_calculations(self, sample_record, sample_trace_jsonl):
        """Test that per-check metrics are calculated correctly."""
        sample_record["raw_output"] = sample_trace_jsonl
        sample_record["input_tokens"] = 500
        sample_record["output_tokens"] = 500
        sample_record["cache_creation_tokens"] = 0
        sample_record["cache_read_tokens"] = 0
        sample_record["passed"] = 10  # 10 checks passed
        sample_record["wall_clock_ms"] = 50000  # 50 seconds
        sample_record["cost_usd"] = 2.50

        with tempfile.TemporaryDirectory() as tmpdir:
            record_path = Path(tmpdir) / "test.json"
            with open(record_path, "w") as f:
                json.dump(sample_record, f)

            metrics = extract_efficiency(str(record_path))

            assert metrics.total_tokens == 1000
            assert metrics.checks_passed == 10
            assert metrics.tokens_per_check_passed == 100.0
            assert metrics.seconds_per_check_passed == 5.0
            assert metrics.cost_per_check_passed == 0.25


# ============================================================================
# Run tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])

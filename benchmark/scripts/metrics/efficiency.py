"""
Efficiency metrics for benchmark runs.

This module extracts and aggregates efficiency metrics that the runner already
captures but the evaluator doesn't factor into adherence scoring:
- Token usage (input, output, cache)
- Tool call counts and patterns
- Iteration/cycle patterns (implement→test loops, fix→retry loops)
- Time efficiency
- Cost efficiency

Public API
----------
EfficiencyMetrics        — efficiency metrics for a single run.
EfficiencySummary        — aggregated metrics across multiple runs.
extract_efficiency()     — extract metrics from a stored run record.
summarize_efficiency()   — aggregate metrics across runs.
format_efficiency_comparison() — human-readable table across formats.
"""

from __future__ import annotations

import json
import sys
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

# Wire up runner module so trace.py is importable
_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.join(_HERE, "..", "runner")
if _RUNNER not in sys.path:
    sys.path.insert(0, _RUNNER)

from trace import parse_trace_jsonl, Trace


@dataclass
class EfficiencyMetrics:
    """Efficiency metrics for a single benchmark run."""

    # Tool usage
    total_tool_calls: int = 0
    tool_call_breakdown: dict[str, int] = field(default_factory=dict)
    unique_files_touched: int = 0

    # Iteration patterns
    num_turns: int = 0
    impl_test_cycles: int = 0  # count of implement→test loops
    fix_retry_cycles: int = 0  # count of fix→rerun loops

    # Token efficiency
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_per_check_passed: float = float("inf")

    # Time efficiency
    wall_clock_seconds: float = 0.0
    seconds_per_check_passed: float = float("inf")

    # Cost
    cost_usd: float = 0.0
    cost_per_check_passed: float = float("inf")

    # Metadata
    checks_passed: int = 0


@dataclass
class EfficiencySummary:
    """Aggregated efficiency metrics across multiple runs."""

    format: str
    n_runs: int = 0
    mean_tool_calls: float = 0.0
    mean_turns: float = 0.0
    mean_tokens: float = 0.0
    mean_wall_clock: float = 0.0
    mean_cost: float = 0.0
    mean_tokens_per_pass: float = float("inf")
    mean_seconds_per_pass: float = float("inf")
    mean_cost_per_pass: float = float("inf")
    # Standard deviations
    std_tool_calls: float = 0.0
    std_turns: float = 0.0
    std_tokens: float = 0.0
    std_wall_clock: float = 0.0
    std_cost: float = 0.0


def _count_impl_test_cycles(trace: Trace) -> int:
    """
    Count implement→test cycles.

    A cycle is detected when a Write/Edit operation is followed by a cargo test
    invocation. We count the number of distinct cargo test runs that came after
    at least one file modification.
    """
    write_edits = trace.all_tool_calls("Write") + trace.all_tool_calls("Edit")
    cargo_tests = trace.cargo_test_results()

    if not write_edits or not cargo_tests:
        return 0

    # Get event indices for writes/edits
    write_edit_indices = set(tc.event_index for tc in write_edits)

    # Count cargo tests that have a write/edit before them
    cycles = 0
    for test in cargo_tests:
        test_index = test["event_index"]
        # Check if there's a write/edit before this test
        if any(idx < test_index for idx in write_edit_indices):
            cycles += 1

    return cycles


def _count_fix_retry_cycles(trace: Trace) -> int:
    """
    Count fix→rerun cycles.

    A cycle is when an Edit operation is followed by re-running a previously-failed
    command. We detect this by looking for Edit calls followed by Bash commands
    that have already appeared earlier and failed.
    """
    edits = trace.all_tool_calls("Edit")
    bash_results = trace.bash_commands_with_results()

    if not edits or not bash_results:
        return 0

    # Track failed commands by command text and their indices
    failed_by_cmd = {}
    for result in bash_results:
        cmd = result["command"]
        if result["succeeded"] is False or (
            result["exit_code"] is not None and result["exit_code"] != 0
        ):
            if cmd not in failed_by_cmd:
                failed_by_cmd[cmd] = []
            failed_by_cmd[cmd].append(result["event_index"])

    # Count how many times an Edit is followed by a re-run of a failed command
    cycles = 0
    for edit in edits:
        edit_index = edit.event_index
        # Look at bash commands after this edit
        for result in bash_results:
            cmd = result["command"]
            result_index = result["event_index"]
            # Check if this is a re-run of a previously-failed command
            if (
                result_index > edit_index
                and cmd in failed_by_cmd
                and any(failed_idx < edit_index for failed_idx in failed_by_cmd[cmd])
            ):
                cycles += 1
                break  # Count at most one cycle per edit

    return cycles


def _count_unique_files_touched(trace: Trace) -> int:
    """Count unique file paths that were read, written, or edited."""
    files = set()
    files.update(trace.file_paths_read())
    files.update(trace.file_paths_written())
    files.update(trace.file_paths_edited())
    return len(files)


def _load_raw_output_from_stream(record_path: str) -> str:
    """Try to load raw output from stream.json in the same run directory.

    Works for the new per-run directory format where record_path points
    to summary.json and stream.json sits alongside it.
    """
    record_dir = Path(record_path).parent
    stream_path = record_dir / "stream.json"
    if not stream_path.exists():
        return ""
    try:
        with open(stream_path) as f:
            stream_data = json.load(f)
        return "\n".join(json.dumps(obj) for obj in stream_data)
    except (json.JSONDecodeError, OSError):
        return ""


def extract_efficiency(record_path: str, trace: Optional[Trace] = None) -> EfficiencyMetrics:
    """
    Extract efficiency metrics from a stored run record.

    Parameters
    ----------
    record_path : str
        Path to the JSON file containing the run record (legacy flat file
        or new-format summary.json).
    trace : Trace, optional
        Pre-parsed Trace object. If not provided, will parse from raw_output
        or stream.json.

    Returns
    -------
    EfficiencyMetrics
        Efficiency metrics for the run.
    """
    with open(record_path) as f:
        record = json.load(f)

    # Parse trace from raw_output if not provided
    if trace is None:
        raw_output = record.get("raw_output", "")
        # Fall back to stream.json in the same run directory
        if not raw_output:
            raw_output = _load_raw_output_from_stream(record_path)
        if raw_output:
            try:
                trace = parse_trace_jsonl(raw_output)
            except ValueError:
                trace = None
        else:
            trace = None

    # Extract basic metrics from record
    wall_clock_ms = record.get("wall_clock_ms", 0.0)
    wall_clock_seconds = wall_clock_ms / 1000.0 if wall_clock_ms else 0.0
    input_tokens = record.get("input_tokens", 0)
    output_tokens = record.get("output_tokens", 0)
    cache_creation_tokens = record.get("cache_creation_tokens", 0)
    cache_read_tokens = record.get("cache_read_tokens", 0)
    cost_usd = record.get("cost_usd", 0.0)
    num_turns = record.get("num_turns", 0)

    # Calculate totals
    total_tokens = input_tokens + output_tokens + cache_creation_tokens
    checks_passed = record.get("passed", 0)

    # Tool call breakdown and counts
    tool_call_breakdown = {}
    total_tool_calls = 0
    unique_files_touched = 0
    impl_test_cycles = 0
    fix_retry_cycles = 0

    if trace:
        all_calls = trace.all_tool_calls()
        total_tool_calls = len(all_calls)

        # Breakdown by tool
        for tc in all_calls:
            tool_call_breakdown[tc.name] = tool_call_breakdown.get(tc.name, 0) + 1

        unique_files_touched = _count_unique_files_touched(trace)
        impl_test_cycles = _count_impl_test_cycles(trace)
        fix_retry_cycles = _count_fix_retry_cycles(trace)

    # Calculate per-check metrics
    tokens_per_check = float("inf")
    seconds_per_check = float("inf")
    cost_per_check = float("inf")

    if checks_passed > 0:
        if total_tokens > 0:
            tokens_per_check = total_tokens / checks_passed
        if wall_clock_seconds > 0:
            seconds_per_check = wall_clock_seconds / checks_passed
        if cost_usd > 0:
            cost_per_check = cost_usd / checks_passed

    return EfficiencyMetrics(
        total_tool_calls=total_tool_calls,
        tool_call_breakdown=tool_call_breakdown,
        unique_files_touched=unique_files_touched,
        num_turns=num_turns,
        impl_test_cycles=impl_test_cycles,
        fix_retry_cycles=fix_retry_cycles,
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tokens_per_check_passed=tokens_per_check,
        wall_clock_seconds=wall_clock_seconds,
        seconds_per_check_passed=seconds_per_check,
        cost_usd=cost_usd,
        cost_per_check_passed=cost_per_check,
        checks_passed=checks_passed,
    )


def summarize_efficiency(
    metrics: list[EfficiencyMetrics], format_name: str
) -> EfficiencySummary:
    """
    Aggregate efficiency metrics across runs for one format.

    Parameters
    ----------
    metrics : list[EfficiencyMetrics]
        List of efficiency metrics from individual runs.
    format_name : str
        Name of the format (e.g., "ape", "markdown").

    Returns
    -------
    EfficiencySummary
        Aggregated metrics.
    """
    if not metrics:
        return EfficiencySummary(format=format_name, n_runs=0)

    # Extract arrays for each metric
    tool_calls = [m.total_tool_calls for m in metrics]
    turns = [m.num_turns for m in metrics]
    tokens = [m.total_tokens for m in metrics]
    wall_clocks = [m.wall_clock_seconds for m in metrics]
    costs = [m.cost_usd for m in metrics]

    # Filter out inf values for per-pass metrics
    tokens_per_pass = [
        m.tokens_per_check_passed
        for m in metrics
        if not np.isinf(m.tokens_per_check_passed) and m.tokens_per_check_passed > 0
    ]
    seconds_per_pass = [
        m.seconds_per_check_passed
        for m in metrics
        if not np.isinf(m.seconds_per_check_passed) and m.seconds_per_check_passed > 0
    ]
    cost_per_pass = [
        m.cost_per_check_passed
        for m in metrics
        if not np.isinf(m.cost_per_check_passed) and m.cost_per_check_passed > 0
    ]

    def safe_mean(values):
        """Return mean or inf if no valid values."""
        if not values:
            return float("inf")
        arr = np.array(values)
        return float(np.mean(arr))

    def safe_std(values):
        """Return std or 0 if fewer than 2 values."""
        if len(values) < 2:
            return 0.0
        arr = np.array(values)
        return float(np.std(arr))

    return EfficiencySummary(
        format=format_name,
        n_runs=len(metrics),
        mean_tool_calls=round(float(np.mean(tool_calls)), 2) if tool_calls else 0.0,
        mean_turns=round(float(np.mean(turns)), 2) if turns else 0.0,
        mean_tokens=round(float(np.mean(tokens)), 2) if tokens else 0.0,
        mean_wall_clock=round(float(np.mean(wall_clocks)), 2) if wall_clocks else 0.0,
        mean_cost=round(float(np.mean(costs)), 4) if costs else 0.0,
        mean_tokens_per_pass=round(safe_mean(tokens_per_pass), 2),
        mean_seconds_per_pass=round(safe_mean(seconds_per_pass), 2),
        mean_cost_per_pass=round(safe_mean(cost_per_pass), 4),
        std_tool_calls=round(safe_std(tool_calls), 2),
        std_turns=round(safe_std(turns), 2),
        std_tokens=round(safe_std(tokens), 2),
        std_wall_clock=round(safe_std(wall_clocks), 2),
        std_cost=round(safe_std(costs), 4),
    )


def format_efficiency_comparison(summaries: list[EfficiencySummary]) -> str:
    """
    Create a human-readable comparison table across formats.

    Parameters
    ----------
    summaries : list[EfficiencySummary]
        Summaries for each format to compare.

    Returns
    -------
    str
        Formatted table for console output.
    """
    if not summaries:
        return ""

    lines = []
    lines.append("")
    lines.append("=" * 100)
    lines.append("EFFICIENCY COMPARISON")
    lines.append("=" * 100)

    # Header
    header = f"{'Metric':<30} " + " ".join(f"{s.format:>15}" for s in summaries)
    lines.append(header)
    lines.append("-" * len(header))

    # Tool calls
    row = "Total Tool Calls (mean)" + " " * (30 - len("Total Tool Calls (mean)"))
    row += " ".join(
        f"{s.mean_tool_calls:>15.1f}" if s.mean_tool_calls != float("inf") else f"{'N/A':>15}"
        for s in summaries
    )
    lines.append(row)

    row = "  └─ std dev" + " " * (30 - len("  └─ std dev"))
    row += " ".join(f"{s.std_tool_calls:>15.2f}" for s in summaries)
    lines.append(row)

    # Turns
    row = "Turns (mean)" + " " * (30 - len("Turns (mean)"))
    row += " ".join(
        f"{s.mean_turns:>15.1f}" if s.mean_turns != float("inf") else f"{'N/A':>15}"
        for s in summaries
    )
    lines.append(row)

    row = "  └─ std dev" + " " * (30 - len("  └─ std dev"))
    row += " ".join(f"{s.std_turns:>15.2f}" for s in summaries)
    lines.append(row)

    # Tokens
    row = "Total Tokens (mean)" + " " * (30 - len("Total Tokens (mean)"))
    row += " ".join(
        f"{s.mean_tokens:>15.0f}" if s.mean_tokens != float("inf") else f"{'N/A':>15}"
        for s in summaries
    )
    lines.append(row)

    row = "  └─ std dev" + " " * (30 - len("  └─ std dev"))
    row += " ".join(f"{s.std_tokens:>15.0f}" for s in summaries)
    lines.append(row)

    row = "Tokens per Check Passed" + " " * (30 - len("Tokens per Check Passed"))
    row += " ".join(
        f"{s.mean_tokens_per_pass:>15.1f}"
        if s.mean_tokens_per_pass != float("inf")
        else f"{'N/A':>15}"
        for s in summaries
    )
    lines.append(row)

    # Time
    row = "Wall Clock Time (mean, sec)" + " " * (30 - len("Wall Clock Time (mean, sec)"))
    row += " ".join(
        f"{s.mean_wall_clock:>15.1f}" if s.mean_wall_clock != float("inf") else f"{'N/A':>15}"
        for s in summaries
    )
    lines.append(row)

    row = "  └─ std dev" + " " * (30 - len("  └─ std dev"))
    row += " ".join(f"{s.std_wall_clock:>15.2f}" for s in summaries)
    lines.append(row)

    row = "Seconds per Check Passed" + " " * (30 - len("Seconds per Check Passed"))
    row += " ".join(
        f"{s.mean_seconds_per_pass:>15.1f}"
        if s.mean_seconds_per_pass != float("inf")
        else f"{'N/A':>15}"
        for s in summaries
    )
    lines.append(row)

    # Cost
    row = "Cost (mean, USD)" + " " * (30 - len("Cost (mean, USD)"))
    row += " ".join(
        f"${s.mean_cost:>14.4f}" if s.mean_cost != float("inf") else f"{'N/A':>15}"
        for s in summaries
    )
    lines.append(row)

    row = "  └─ std dev" + " " * (30 - len("  └─ std dev"))
    row += " ".join(f"{s.std_cost:>15.4f}" for s in summaries)
    lines.append(row)

    row = "Cost per Check Passed" + " " * (30 - len("Cost per Check Passed"))
    row += " ".join(
        f"${s.mean_cost_per_pass:>14.4f}"
        if s.mean_cost_per_pass != float("inf")
        else f"{'N/A':>15}"
        for s in summaries
    )
    lines.append(row)

    # Sample size
    lines.append("-" * len(header))
    row = "N (runs)" + " " * (30 - len("N (runs)"))
    row += " ".join(f"{s.n_runs:>15d}" for s in summaries)
    lines.append(row)

    lines.append("=" * 100)
    lines.append("")

    return "\n".join(lines)

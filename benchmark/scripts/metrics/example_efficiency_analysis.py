#!/usr/bin/env python3
"""
Example: Analyzing efficiency metrics across benchmark formats.

This script demonstrates how to use the efficiency module to:
1. Extract efficiency metrics from individual run records
2. Summarize metrics across multiple runs per format
3. Generate human-readable comparison tables
"""

import sys
import os
from pathlib import Path

# Wire up modules
_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.join(_HERE, "..", "runner")
if _RUNNER not in sys.path:
    sys.path.insert(0, _RUNNER)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from efficiency import extract_efficiency, summarize_efficiency, format_efficiency_comparison


def main():
    """Run efficiency analysis on benchmark results."""
    # Example: collect results from one test scenario
    benchmark_root = Path(__file__).parent.parent.parent
    raw_output_dir = benchmark_root / "output" / "raw" / "bivvy"

    if not raw_output_dir.exists():
        print(f"Error: {raw_output_dir} not found")
        print("Please run benchmarks first: python -m runner ...")
        return 1

    # Collect metrics by format
    metrics_by_format = {}

    for format_dir in raw_output_dir.glob("*/"):
        format_name = format_dir.name
        metrics_by_format[format_name] = []

        # Collect all JSON files for this format
        for json_file in format_dir.rglob("*.json"):
            try:
                metrics = extract_efficiency(str(json_file))
                metrics_by_format[format_name].append(metrics)
            except Exception as e:
                print(f"Warning: Could not extract metrics from {json_file}: {e}")

        print(f"Extracted {len(metrics_by_format[format_name])} runs for {format_name}")

    # Summarize per format
    summaries = []
    for format_name, metrics_list in sorted(metrics_by_format.items()):
        if metrics_list:
            summary = summarize_efficiency(metrics_list, format_name)
            summaries.append(summary)

    # Generate comparison table
    if summaries:
        print("\n" + format_efficiency_comparison(summaries))

        # Print per-metric insights
        print("INSIGHTS")
        print("=" * 100)

        # Tool efficiency
        tool_counts = [s.mean_tool_calls for s in summaries]
        min_tools = min(tool_counts)
        min_format = summaries[tool_counts.index(min_tools)].format
        print(f"Most efficient tool usage: {min_format} ({min_tools:.1f} mean tool calls)")

        # Time efficiency
        time_counts = [s.mean_wall_clock for s in summaries]
        min_time = min(time_counts)
        min_time_format = summaries[time_counts.index(min_time)].format
        print(f"Fastest format: {min_time_format} ({min_time:.1f} mean seconds)")

        # Per-pass efficiency
        tokens_per = [
            s.mean_tokens_per_pass
            for s in summaries
            if s.mean_tokens_per_pass != float("inf")
        ]
        if tokens_per:
            min_tokens_per = min(tokens_per)
            min_tokens_format = [
                s.format for s in summaries
                if s.mean_tokens_per_pass == min_tokens_per
            ][0]
            print(f"Most token-efficient per check: {min_tokens_format} ({min_tokens_per:.1f} tokens)")

        print("=" * 100)
    else:
        print("No metrics found to compare")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

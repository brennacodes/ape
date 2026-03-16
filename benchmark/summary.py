#!/usr/bin/env python3
"""
Summarise benchmark results into two tables:

1. Per-scenario pass rates across workflow formats
2. Aggregate metrics (completion rate, avg pass rate, cost, turns)

Usage:
    python3 benchmark/summary.py                        # summarise all results
    python3 benchmark/summary.py --results-dir output/  # custom results path
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

console = Console()

CATEGORY_ORDER = [
    "architectural_issues",
    "bugs",
    "new_features",
    "untested_code",
]

CATEGORY_LABELS = {
    "architectural_issues": "Architectural Issues",
    "bugs": "Bugs",
    "new_features": "New Features",
    "untested_code": "Untested Code",
}

FORMAT_ORDER = ["adhoc-xml", "ape", "markdown", "no-workflow", "plain-text"]


def load_summaries(results_dir: Path) -> list[dict]:
    """Find and load all summary.json files under the results directory."""
    summaries = []
    for path in sorted(results_dir.rglob("summary.json")):
        # Only grab run-level summaries (inside numbered run dirs like 000/)
        if not path.parent.name.isdigit():
            continue
        with open(path) as f:
            summaries.append(json.load(f))
    return summaries


def build_scenario_data(
    summaries: list[dict],
) -> dict[str, dict[str, list[dict]]]:
    """Group summaries by (prompt_id, format).

    Returns {prompt_id: {format: [summary, ...]}}.
    """
    data: dict[str, dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for s in summaries:
        prompt_id = s.get("prompt_id", "")
        fmt = s.get("format", "")
        if prompt_id and fmt:
            data[prompt_id][fmt].append(s)
    return data


def scenario_pass_rate(runs: list[dict]) -> tuple[float | None, bool]:
    """Compute average pass rate across successful runs.

    Returns (avg_pass_rate_or_None, any_timeout).
    A None rate means every run timed out.
    """
    succeeded = [r for r in runs if r.get("succeeded")]
    any_timeout = any(not r.get("succeeded") for r in runs)
    if not succeeded:
        return None, any_timeout
    avg = sum(r["pass_rate"] for r in succeeded) / len(succeeded)
    return avg, any_timeout


def format_rate(rate: float | None, any_timeout: bool) -> Text:
    """Format a pass rate cell with colour coding."""
    if rate is None:
        return Text("TIMEOUT", style="red")

    pct = round(rate * 100)
    label = f"{pct}%"
    if any_timeout:
        label += "*"

    if pct >= 80:
        style = "green"
    elif pct >= 65:
        style = "yellow"
    else:
        style = "red"
    return Text(label, style=style)


def discover_formats(summaries: list[dict]) -> list[str]:
    """Return format names in canonical order, limited to those present."""
    present = {s.get("format") for s in summaries}
    return [f for f in FORMAT_ORDER if f in present]


def print_scenario_table(
    scenario_data: dict[str, dict[str, list[dict]]],
    formats: list[str],
) -> None:
    """Print the per-scenario pass-rate table."""
    table = Table(
        title="Per-Scenario Pass Rates",
        title_style="bold",
        show_lines=True,
    )
    table.add_column("Scenario", style="bold", min_width=30)
    for fmt in formats:
        table.add_column(fmt, justify="center", min_width=10)

    # Group scenarios by category
    scenarios_by_cat: dict[str, list[str]] = defaultdict(list)
    for prompt_id in scenario_data:
        cat = prompt_id.split("/")[0]
        scenarios_by_cat[cat].append(prompt_id)
    for cat in scenarios_by_cat:
        scenarios_by_cat[cat].sort()

    for cat in CATEGORY_ORDER:
        if cat not in scenarios_by_cat:
            continue
        # Category header row
        table.add_row(
            Text(CATEGORY_LABELS.get(cat, cat), style="bold italic cyan"),
            *["" for _ in formats],
        )
        for prompt_id in scenarios_by_cat[cat]:
            scenario_name = prompt_id.split("/", 1)[1]
            cells = []
            for fmt in formats:
                runs = scenario_data[prompt_id].get(fmt, [])
                if not runs:
                    cells.append(Text("-", style="dim"))
                else:
                    rate, any_to = scenario_pass_rate(runs)
                    cells.append(format_rate(rate, any_to))
            table.add_row(scenario_name, *cells)

    console.print(table)
    console.print(
        "[dim]* = some runs timed out; rate is from successful runs only[/dim]"
    )


def print_aggregate_table(
    scenario_data: dict[str, dict[str, list[dict]]],
    formats: list[str],
) -> None:
    """Print the aggregate summary table."""
    table = Table(
        title="Aggregate Summary",
        title_style="bold",
        show_lines=True,
    )
    table.add_column("Metric", style="bold", min_width=28)
    for fmt in formats:
        table.add_column(fmt, justify="center", min_width=10)

    # Collect all runs per format
    all_runs: dict[str, list[dict]] = defaultdict(list)
    for prompt_id in scenario_data:
        for fmt, runs in scenario_data[prompt_id].items():
            all_runs[fmt].extend(runs)

    # --- Completed / total ---
    row_completed = []
    row_timeout = []
    row_avg_completed = []
    row_avg_all = []
    row_cost = []
    row_avg_cost = []
    row_avg_turns = []
    row_avg_time = []

    for fmt in formats:
        runs = all_runs.get(fmt, [])
        total = len(runs)
        succeeded = [r for r in runs if r.get("succeeded")]
        n_ok = len(succeeded)

        row_completed.append(f"{n_ok}/{total}" if total else "-")

        if total:
            row_timeout.append(f"{round((total - n_ok) / total * 100)}%")
        else:
            row_timeout.append("-")

        if succeeded:
            avg_pass = sum(r["pass_rate"] for r in succeeded) / len(succeeded)
            row_avg_completed.append(
                format_rate(avg_pass, any_timeout=False)
            )
        else:
            row_avg_completed.append(Text("-", style="dim"))

        if total:
            avg_all = sum(r["pass_rate"] for r in runs) / total
            row_avg_all.append(format_rate(avg_all, any_timeout=False))
        else:
            row_avg_all.append(Text("-", style="dim"))

        total_cost = sum(r.get("cost_usd", 0) for r in runs)
        row_cost.append(f"${total_cost:.2f}")

        if total:
            row_avg_cost.append(f"${total_cost / total:.2f}")
        else:
            row_avg_cost.append("-")

        if succeeded:
            avg_turns = sum(r.get("num_turns", 0) for r in succeeded) / len(
                succeeded
            )
            row_avg_turns.append(str(round(avg_turns)))
        else:
            row_avg_turns.append("-")

        if succeeded:
            avg_ms = sum(
                r.get("wall_clock_ms", 0) for r in succeeded
            ) / len(succeeded)
            mins = int(avg_ms // 60_000)
            secs = int((avg_ms % 60_000) // 1000)
            row_avg_time.append(f"{mins}m {secs:02d}s")
        else:
            row_avg_time.append("-")

    table.add_row("Completed runs", *row_completed)
    table.add_row("Timeout rate", *row_timeout)
    table.add_row("Avg pass rate (completed)", *row_avg_completed)
    table.add_row("Avg pass rate (all, timeout=0)", *row_avg_all)
    table.add_row("Total cost", *row_cost)
    table.add_row("Avg cost/run", *row_avg_cost)
    table.add_row("Avg turns (completed)", *row_avg_turns)
    table.add_row("Avg time (completed)", *row_avg_time)

    console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarise benchmark results."
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="Root of the results directory (default: benchmark/output/)",
    )
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    results_dir = args.results_dir or here / "output"

    if not results_dir.is_dir():
        console.print(
            f"[red]Results directory not found:[/red] {results_dir}"
        )
        sys.exit(1)

    summaries = load_summaries(results_dir)
    if not summaries:
        console.print(
            f"[red]No summary.json files found under[/red] {results_dir}"
        )
        sys.exit(1)

    console.print()
    scenario_data = build_scenario_data(summaries)
    formats = discover_formats(summaries)

    print_scenario_table(scenario_data, formats)
    console.print()
    print_aggregate_table(scenario_data, formats)
    console.print()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Summarise benchmark results with flexible views and filters.

Views (pick one, default is scenario + aggregate):
  --checks         Per-check pass rates across formats
  --phase          Per-phase pass rates across formats

Filters (combine with any view):
  --category CAT   Filter to category (comma-separated)
  --format FMT     Show only specific formats (comma-separated)
  --scenario NAME  Filter to a specific scenario name

Examples:
    bin/summary                                    # default tables
    bin/summary --checks                           # which checks pass per format
    bin/summary --checks --category bugs           # check detail for bugs only
    bin/summary --phase                            # phase-level comparison
    bin/summary --category bugs                    # scenario table, bugs only
    bin/summary --format ape,markdown              # compare two formats
    bin/summary --checks --scenario dry_run_mode   # check detail for one scenario
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

PHASE_ORDER = [
    "workflow",
    "specification",
    "implementation",
    "documentation",
    "linting",
    "testing",
    "build",
    "commit",
    "post-commit",
    "failure_recovery",
]

PHASE_LABELS = {
    "workflow": "Workflow",
    "specification": "Specification",
    "implementation": "Implementation",
    "documentation": "Documentation",
    "linting": "Linting",
    "testing": "Testing",
    "build": "Build",
    "commit": "Commit",
    "post-commit": "Post-Commit",
    "failure_recovery": "Failure Recovery",
}


# ---------------------------------------------------------------------------
# Data loading and filtering
# ---------------------------------------------------------------------------


def load_summaries(results_dir: Path) -> list[dict]:
    """Find and load all summary.json files under the results directory."""
    summaries = []
    for path in sorted(results_dir.rglob("summary.json")):
        if not path.parent.name.isdigit():
            continue
        with open(path) as f:
            summaries.append(json.load(f))
    return summaries


def filter_summaries(
    summaries: list[dict],
    categories: list[str] | None,
    formats: list[str] | None,
    scenario: str | None,
) -> list[dict]:
    """Apply category, format, and scenario filters to summaries."""
    result = summaries
    if categories:
        result = [
            s
            for s in result
            if s.get("prompt_id", "").split("/")[0] in categories
        ]
    if formats:
        result = [s for s in result if s.get("format") in formats]
    if scenario:
        result = [
            s
            for s in result
            if s.get("prompt_id", "").split("/", 1)[-1] == scenario
        ]
    return result


def discover_formats(summaries: list[dict]) -> list[str]:
    """Return format names in canonical order, limited to those present."""
    present = {s.get("format") for s in summaries}
    return [f for f in FORMAT_ORDER if f in present]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def format_rate(rate: float | None, any_timeout: bool = False) -> Text:
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


def build_title_suffix(
    categories: list[str] | None,
    scenario: str | None,
) -> str:
    """Build a descriptive suffix for table titles based on active filters."""
    parts = []
    if categories:
        labels = [CATEGORY_LABELS.get(c, c) for c in categories]
        parts.append(", ".join(labels))
    if scenario:
        parts.append(scenario)
    return " / ".join(parts)


# ---------------------------------------------------------------------------
# Scenario view (default)
# ---------------------------------------------------------------------------


def build_scenario_data(
    summaries: list[dict],
) -> dict[str, dict[str, list[dict]]]:
    """Group summaries by (prompt_id, format)."""
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
    """Compute average pass rate across successful runs."""
    succeeded = [r for r in runs if r.get("succeeded")]
    any_timeout = any(not r.get("succeeded") for r in runs)
    if not succeeded:
        return None, any_timeout
    avg = sum(r["pass_rate"] for r in succeeded) / len(succeeded)
    return avg, any_timeout


def print_scenario_table(
    scenario_data: dict[str, dict[str, list[dict]]],
    formats: list[str],
    title_suffix: str = "",
) -> None:
    """Print the per-scenario pass-rate table."""
    title = "Per-Scenario Pass Rates"
    if title_suffix:
        title += f" \u2014 {title_suffix}"

    table = Table(title=title, title_style="bold", show_lines=True)
    table.add_column("Scenario", style="bold", min_width=30)
    for fmt in formats:
        table.add_column(fmt, justify="center", min_width=10)

    scenarios_by_cat: dict[str, list[str]] = defaultdict(list)
    for prompt_id in scenario_data:
        cat = prompt_id.split("/")[0]
        scenarios_by_cat[cat].append(prompt_id)
    for cat in scenarios_by_cat:
        scenarios_by_cat[cat].sort()

    for cat in CATEGORY_ORDER:
        if cat not in scenarios_by_cat:
            continue
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
    title_suffix: str = "",
) -> None:
    """Print the aggregate summary table."""
    title = "Aggregate Summary"
    if title_suffix:
        title += f" \u2014 {title_suffix}"

    table = Table(title=title, title_style="bold", show_lines=True)
    table.add_column("Metric", style="bold", min_width=28)
    for fmt in formats:
        table.add_column(fmt, justify="center", min_width=10)

    all_runs: dict[str, list[dict]] = defaultdict(list)
    for prompt_id in scenario_data:
        for fmt, runs in scenario_data[prompt_id].items():
            all_runs[fmt].extend(runs)

    row_completed: list[str | Text] = []
    row_timeout: list[str | Text] = []
    row_avg_completed: list[str | Text] = []
    row_avg_all: list[str | Text] = []
    row_cost: list[str | Text] = []
    row_avg_cost: list[str | Text] = []
    row_avg_turns: list[str | Text] = []
    row_avg_time: list[str | Text] = []

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
            row_avg_completed.append(format_rate(avg_pass))
        else:
            row_avg_completed.append(Text("-", style="dim"))

        if total:
            avg_all = sum(r["pass_rate"] for r in runs) / total
            row_avg_all.append(format_rate(avg_all))
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


# ---------------------------------------------------------------------------
# Check-level view (--checks)
# ---------------------------------------------------------------------------


def build_check_data(
    summaries: list[dict],
) -> dict[str, dict[str, dict[str, int]]]:
    """Aggregate per-check pass/fail/skip counts by format.

    Returns {check_id: {format: {"passed": n, "failed": n, "skipped": n}}}.
    Only includes successful runs (timed-out runs have empty checks).
    """
    data: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0})
    )
    for s in summaries:
        if not s.get("succeeded"):
            continue
        fmt = s.get("format", "")
        for check in s.get("checks", []):
            cid = check["check_id"]
            passed = check.get("passed")
            if passed is True:
                data[cid][fmt]["passed"] += 1
            elif passed is False:
                data[cid][fmt]["failed"] += 1
            else:
                data[cid][fmt]["skipped"] += 1
    return data


def check_phase_map(summaries: list[dict]) -> dict[str, str]:
    """Build a mapping from check_id to phase from the data."""
    mapping: dict[str, str] = {}
    for s in summaries:
        for check in s.get("checks", []):
            mapping[check["check_id"]] = check.get("phase", "unknown")
    return mapping


def print_checks_table(
    summaries: list[dict],
    formats: list[str],
    title_suffix: str = "",
) -> None:
    """Print per-check pass rates across formats."""
    check_data = build_check_data(summaries)
    phases = check_phase_map(summaries)

    if not check_data:
        console.print(
            "[red]No check data found (all runs may have timed out).[/red]"
        )
        return

    title = "Per-Check Pass Rates"
    if title_suffix:
        title += f" \u2014 {title_suffix}"

    table = Table(title=title, title_style="bold", show_lines=True)
    table.add_column("Check", style="bold", min_width=34)
    for fmt in formats:
        table.add_column(fmt, justify="center", min_width=10)

    # Group checks by phase
    checks_by_phase: dict[str, list[str]] = defaultdict(list)
    for cid in check_data:
        phase = phases.get(cid, "unknown")
        if cid not in checks_by_phase[phase]:
            checks_by_phase[phase].append(cid)
    for phase in checks_by_phase:
        checks_by_phase[phase].sort()

    for phase in PHASE_ORDER:
        if phase not in checks_by_phase:
            continue
        checks = checks_by_phase[phase]

        # Skip phases where every check in every format is skipped
        any_evaluated = False
        for cid in checks:
            for fmt in formats:
                counts = check_data[cid].get(
                    fmt, {"passed": 0, "failed": 0, "skipped": 0}
                )
                if counts["passed"] + counts["failed"] > 0:
                    any_evaluated = True
                    break
            if any_evaluated:
                break
        if not any_evaluated:
            continue

        # Phase header row
        table.add_row(
            Text(PHASE_LABELS.get(phase, phase), style="bold italic cyan"),
            *["" for _ in formats],
        )

        for cid in checks:
            cells = []
            for fmt in formats:
                counts = check_data[cid].get(
                    fmt, {"passed": 0, "failed": 0, "skipped": 0}
                )
                evaluated = counts["passed"] + counts["failed"]
                if evaluated == 0:
                    cells.append(Text("skip", style="dim"))
                else:
                    rate = counts["passed"] / evaluated
                    cells.append(format_rate(rate))
            table.add_row(cid, *cells)

    console.print(table)

    # Show completed run counts for context
    completed: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    for s in summaries:
        fmt = s.get("format", "")
        if fmt in formats:
            total[fmt] += 1
            if s.get("succeeded"):
                completed[fmt] += 1

    parts = [f"{fmt}: {completed[fmt]}/{total[fmt]}" for fmt in formats]
    console.print(f"[dim]Completed runs: {', '.join(parts)}[/dim]")


# ---------------------------------------------------------------------------
# Phase-level view (--phase)
# ---------------------------------------------------------------------------


def print_phase_table(
    summaries: list[dict],
    formats: list[str],
    title_suffix: str = "",
) -> None:
    """Print per-phase pass rates across formats."""
    check_data = build_check_data(summaries)
    phases = check_phase_map(summaries)

    if not check_data:
        console.print("[red]No check data found.[/red]")
        return

    title = "Per-Phase Pass Rates"
    if title_suffix:
        title += f" \u2014 {title_suffix}"

    table = Table(title=title, title_style="bold", show_lines=True)
    table.add_column("Phase", style="bold", min_width=20)
    table.add_column("Checks", justify="right", min_width=6)
    for fmt in formats:
        table.add_column(fmt, justify="center", min_width=10)

    for phase in PHASE_ORDER:
        phase_checks = [
            cid
            for cid, p in phases.items()
            if p == phase and cid in check_data
        ]
        if not phase_checks:
            continue

        any_evaluated = False
        cells = []
        for fmt in formats:
            p_total = 0
            f_total = 0
            for cid in phase_checks:
                counts = check_data[cid].get(
                    fmt, {"passed": 0, "failed": 0, "skipped": 0}
                )
                p_total += counts["passed"]
                f_total += counts["failed"]
            evaluated = p_total + f_total
            if evaluated == 0:
                cells.append(Text("skip", style="dim"))
            else:
                any_evaluated = True
                rate = p_total / evaluated
                cells.append(format_rate(rate))

        if not any_evaluated:
            continue

        table.add_row(
            PHASE_LABELS.get(phase, phase),
            str(len(phase_checks)),
            *cells,
        )

    console.print(table)

    # Show completed run counts for context
    completed: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    for s in summaries:
        fmt = s.get("format", "")
        if fmt in formats:
            total[fmt] += 1
            if s.get("succeeded"):
                completed[fmt] += 1

    parts = [f"{fmt}: {completed[fmt]}/{total[fmt]}" for fmt in formats]
    console.print(f"[dim]Completed runs: {', '.join(parts)}[/dim]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarise benchmark results.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
views (pick one, default is scenario + aggregate):
  --checks         Per-check pass rates across formats
  --phase          Per-phase pass rates across formats

filters (combine with any view):
  --category CAT   Filter to category (comma-separated, e.g. bugs,new_features)
  --format FMT     Show only specific formats (comma-separated)
  --scenario NAME  Filter to a specific scenario name

examples:
  bin/summary                                    # default tables
  bin/summary --checks                           # which checks pass per format
  bin/summary --checks --category bugs           # check detail for bugs only
  bin/summary --phase                            # phase-level comparison
  bin/summary --category bugs                    # scenario table, bugs only
  bin/summary --format ape,markdown              # compare two formats
  bin/summary --checks --scenario dry_run_mode   # check detail for one scenario
""",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="Root of the results directory (default: benchmark/output/)",
    )
    parser.add_argument(
        "--checks",
        action="store_true",
        help="Show per-check pass rates across formats",
    )
    parser.add_argument(
        "--phase",
        action="store_true",
        help="Show per-phase pass rates across formats",
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Filter to category (comma-separated)",
    )
    parser.add_argument(
        "--format",
        type=str,
        default=None,
        dest="fmt_filter",
        help="Show only specific formats (comma-separated)",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Filter to a specific scenario name",
    )
    args = parser.parse_args()

    if args.checks and args.phase:
        console.print("[red]--checks and --phase are mutually exclusive[/red]")
        sys.exit(1)

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

    # Parse filters
    categories = (
        [c.strip() for c in args.category.split(",")]
        if args.category
        else None
    )
    fmt_filter = (
        [f.strip() for f in args.fmt_filter.split(",")]
        if args.fmt_filter
        else None
    )

    filtered = filter_summaries(
        summaries, categories, fmt_filter, args.scenario
    )
    if not filtered:
        console.print("[red]No results match the given filters.[/red]")
        sys.exit(1)

    if fmt_filter:
        formats = [
            f
            for f in fmt_filter
            if f in {s.get("format") for s in filtered}
        ]
    else:
        formats = discover_formats(filtered)

    title_suffix = build_title_suffix(categories, args.scenario)

    console.print()

    if args.checks:
        print_checks_table(filtered, formats, title_suffix)
    elif args.phase:
        print_phase_table(filtered, formats, title_suffix)
    else:
        scenario_data = build_scenario_data(filtered)
        print_scenario_table(scenario_data, formats, title_suffix)
        console.print()
        print_aggregate_table(scenario_data, formats, title_suffix)

    console.print()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Summarise benchmark results with flexible views and filters.

Tables surface each workflow format as one column per source
(claude-md, prompt). no-workflow has no source layer and shows as a
single baseline column.

Views (pick one, default is scenario + aggregate):
  --checks         Per-check pass rates across (format, source) columns
  --phase          Per-phase pass rates across (format, source) columns
  --timeouts      Which scenarios/runs timed out per (format, source) column

Filters (combine with any view):
  --category CAT   Filter to category (comma-separated)
  --format FMT     Show only specific formats (comma-separated)
  --source SRC     Show only specific sources: claude-md | prompt
                   (comma-separated). no-workflow always appears as a
                   baseline regardless of this filter.
  --scenario NAME  Filter to a specific scenario name
  --since WINDOW   Only include runs started within the window
                   (e.g. 15d, 36h, 2026-04-24)

Examples:
    bin/summary                                    # default tables
    bin/summary --checks                           # which checks pass per column
    bin/summary --checks --category bugs           # check detail for bugs only
    bin/summary --phase                            # phase-level comparison
    bin/summary --timeouts                         # which runs timed out
    bin/summary --category bugs                    # scenario table, bugs only
    bin/summary --format ape,markdown              # compare two formats (both sources)
    bin/summary --source prompt                    # only prompt-delivered runs
    bin/summary --format markdown --source claude-md
    bin/summary --checks --scenario dry_run_mode   # check detail for one scenario
    bin/summary --since 15d                        # only runs from the last 15 days
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

sys.path.insert(
    0, str(Path(__file__).resolve().parent / "scripts" / "results")
)
from since_filter import filter_by_since, parse_since  # noqa: E402

console = Console()

CATEGORY_ORDER = [
    "architectural_issues",
    "bugs",
    "new_features"
]

CATEGORY_LABELS = {
    "architectural_issues": "Architectural Issues",
    "bugs": "Bugs",
    "new_features": "New Features"
}

FORMAT_ORDER = ["adhoc-xml", "ape", "markdown", "no-workflow", "plain-text"]
SOURCE_ORDER = ["claude-md", "prompt"]

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


def _summary_label(summary: dict) -> str:
    """Short identifier for a summary used in --since warning output.

    For source-bound runs the source segment is inserted after the format
    (e.g. ``bivvy/ape/claude-md/bugs/silent_yaml_failure/3``). Pre-source
    summaries with no source field render in the older shape.
    """
    fixture = summary.get("fixture_id", "?")
    fmt = summary.get("format", "?")
    src = summary.get("source", "")
    prompt = summary.get("prompt_id", "?")
    run = summary.get("run_id", "?")
    if src:
        return f"{fixture}/{fmt}/{src}/{prompt}/{run}"
    return f"{fixture}/{fmt}/{prompt}/{run}"


def column_key(summary: dict) -> str:
    """Composite (format, source) column key used by cross-format tables.

    Source-bound rows surface as ``"<format>:<source>"`` so claude-md and
    prompt runs of the same format render as separate columns. no-workflow
    stays as a bare format name because the source layer doesn't apply.
    """
    fmt = summary.get("format", "")
    src = summary.get("source", "")
    if src:
        return f"{fmt}:{src}"
    return fmt


def _column_sort_key(key: str) -> tuple[int, int, str, str]:
    """Sort columns by canonical format order, then source order."""
    if ":" in key:
        fmt, src = key.split(":", 1)
    else:
        fmt, src = key, ""
    fmt_idx = FORMAT_ORDER.index(fmt) if fmt in FORMAT_ORDER else len(FORMAT_ORDER)
    src_idx = SOURCE_ORDER.index(src) if src in SOURCE_ORDER else -1
    return (fmt_idx, src_idx, fmt, src)


def filter_summaries(
    summaries: list[dict],
    categories: list[str] | None,
    formats: list[str] | None,
    scenario: str | None,
    sources: list[str] | None = None,
) -> list[dict]:
    """Apply category, format, scenario, and source filters to summaries.

    ``sources`` is ignored for no-workflow runs (source-agnostic baseline -
    always included regardless of the source filter).
    """
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
    if sources:
        result = [
            s
            for s in result
            if s.get("format") == "no-workflow"
            or s.get("source", "") in sources
        ]
    return result


def discover_columns(summaries: list[dict]) -> list[str]:
    """Return composite ``(format[:source])`` column keys in canonical order."""
    present = {column_key(s) for s in summaries}
    return sorted(present, key=_column_sort_key)


# Back-compat alias: callers that still want format-only discovery.
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
    """Group summaries by (prompt_id, column).

    Column is the composite ``(format, source)`` key — claude-md and
    prompt runs of the same format stay in separate buckets.
    """
    data: dict[str, dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for s in summaries:
        prompt_id = s.get("prompt_id", "")
        col = column_key(s)
        if prompt_id and col:
            data[prompt_id][col].append(s)
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
    columns: list[str],
    title_suffix: str = "",
) -> None:
    """Print the per-scenario pass-rate table.

    ``columns`` are composite ``(format[:source])`` keys produced by
    ``discover_columns``.
    """
    title = "Per-Scenario Pass Rates"
    if title_suffix:
        title += f" \u2014 {title_suffix}"

    table = Table(title=title, title_style="bold", show_lines=True)
    table.add_column("Scenario", style="bold", min_width=30)
    for col in columns:
        table.add_column(col, justify="center", min_width=10)

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
            *["" for _ in columns],
        )
        for prompt_id in scenarios_by_cat[cat]:
            scenario_name = prompt_id.split("/", 1)[1]
            cells = []
            for col in columns:
                runs = scenario_data[prompt_id].get(col, [])
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
    columns: list[str],
    title_suffix: str = "",
) -> None:
    """Print the aggregate summary table."""
    title = "Aggregate Summary"
    if title_suffix:
        title += f" \u2014 {title_suffix}"

    table = Table(title=title, title_style="bold", show_lines=True)
    table.add_column("Metric", style="bold", min_width=28)
    for col in columns:
        table.add_column(col, justify="center", min_width=10)

    all_runs: dict[str, list[dict]] = defaultdict(list)
    for prompt_id in scenario_data:
        for col, runs in scenario_data[prompt_id].items():
            all_runs[col].extend(runs)

    row_completed: list[str | Text] = []
    row_timeout: list[str | Text] = []
    row_avg_completed: list[str | Text] = []
    row_avg_all: list[str | Text] = []
    row_cost: list[str | Text] = []
    row_avg_cost: list[str | Text] = []
    row_avg_turns: list[str | Text] = []
    row_avg_time: list[str | Text] = []

    for col in columns:
        runs = all_runs.get(col, [])
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
    """Aggregate per-check pass/fail/skip counts by column.

    Returns ``{check_id: {column: {"passed": n, "failed": n, "skipped": n}}}``
    where ``column`` is the composite ``(format[:source])`` key. Only
    includes successful runs (timed-out runs have empty checks).
    """
    data: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0})
    )
    for s in summaries:
        if not s.get("succeeded"):
            continue
        col = column_key(s)
        for check in s.get("checks", []):
            cid = check["check_id"]
            passed = check.get("passed")
            if passed is True:
                data[cid][col]["passed"] += 1
            elif passed is False:
                data[cid][col]["failed"] += 1
            else:
                data[cid][col]["skipped"] += 1
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
    columns: list[str],
    title_suffix: str = "",
) -> None:
    """Print per-check pass rates across (format, source) columns."""
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
    for col in columns:
        table.add_column(col, justify="center", min_width=10)

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

        # Skip phases where every check in every column is skipped
        any_evaluated = False
        for cid in checks:
            for col in columns:
                counts = check_data[cid].get(
                    col, {"passed": 0, "failed": 0, "skipped": 0}
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
            *["" for _ in columns],
        )

        for cid in checks:
            cells = []
            for col in columns:
                counts = check_data[cid].get(
                    col, {"passed": 0, "failed": 0, "skipped": 0}
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
        col = column_key(s)
        if col in columns:
            total[col] += 1
            if s.get("succeeded"):
                completed[col] += 1

    parts = [f"{col}: {completed[col]}/{total[col]}" for col in columns]
    console.print(f"[dim]Completed runs: {', '.join(parts)}[/dim]")


# ---------------------------------------------------------------------------
# Phase-level view (--phase)
# ---------------------------------------------------------------------------


def print_phase_table(
    summaries: list[dict],
    columns: list[str],
    title_suffix: str = "",
) -> None:
    """Print per-phase pass rates across (format, source) columns."""
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
    for col in columns:
        table.add_column(col, justify="center", min_width=10)

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
        for col in columns:
            p_total = 0
            f_total = 0
            for cid in phase_checks:
                counts = check_data[cid].get(
                    col, {"passed": 0, "failed": 0, "skipped": 0}
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
        col = column_key(s)
        if col in columns:
            total[col] += 1
            if s.get("succeeded"):
                completed[col] += 1

    parts = [f"{col}: {completed[col]}/{total[col]}" for col in columns]
    console.print(f"[dim]Completed runs: {', '.join(parts)}[/dim]")


# ---------------------------------------------------------------------------
# Timeout view (--timeouts)
# ---------------------------------------------------------------------------


def print_timeouts_table(
    summaries: list[dict],
    columns: list[str],
    title_suffix: str = "",
) -> None:
    """Print tables showing which scenarios/runs timed out per column."""
    # Index: (prompt_id, column, run_id) -> timed out?
    timed_out: dict[tuple[str, str, int], bool] = {}
    all_run_ids: set[int] = set()
    for s in summaries:
        prompt_id = s.get("prompt_id", "")
        col = column_key(s)
        run_id = s.get("run_id", 0)
        if not prompt_id or not col:
            continue
        timed_out[(prompt_id, col, run_id)] = not s.get("succeeded")
        all_run_ids.add(run_id)

    run_ids = sorted(all_run_ids)

    # Only show scenarios that have at least one timeout
    scenario_timeout_counts: dict[str, int] = defaultdict(int)
    for (pid, col, rid), is_timeout in timed_out.items():
        if is_timeout and col in columns:
            scenario_timeout_counts[pid] += 1

    timed_out_scenarios = sorted(
        scenario_timeout_counts, key=scenario_timeout_counts.get, reverse=True
    )
    if not timed_out_scenarios:
        console.print("[green]No timeouts found![/green]")
        return

    # -- Per-scenario grids: run# rows x (format, source) columns ----------
    for prompt_id in timed_out_scenarios:
        scenario_name = prompt_id.split("/", 1)[1]
        n = scenario_timeout_counts[prompt_id]
        table = Table(
            title=f"{scenario_name} ({n} timeout{'s' if n != 1 else ''})",
            title_style="bold",
            show_lines=True,
        )
        table.add_column("Run", justify="center", min_width=5, style="bold")
        for col in columns:
            table.add_column(col, justify="center", min_width=10)

        for rid in run_ids:
            # Skip run rows where no column has data for this scenario
            if not any(
                (prompt_id, col, rid) in timed_out for col in columns
            ):
                continue
            cells = []
            for col in columns:
                key = (prompt_id, col, rid)
                if key not in timed_out:
                    cells.append(Text("", style="dim"))
                elif timed_out[key]:
                    cells.append(Text("X", style="bold red"))
                else:
                    cells.append(Text("-", style="dim"))
            table.add_row(f"#{rid}", *cells)

        console.print(table)
        console.print()

    # -- Summary: totals by scenario ----------------------------------------
    summary = Table(
        title="Timeout Totals by Scenario",
        title_style="bold",
        show_lines=True,
    )
    summary.add_column("Scenario", style="bold", min_width=30)
    for col in columns:
        summary.add_column(col, justify="center", min_width=10)
    summary.add_column("Total", justify="center", min_width=7, style="bold")

    col_totals: dict[str, int] = defaultdict(int)
    grand_total = 0

    for prompt_id in timed_out_scenarios:
        scenario_name = prompt_id.split("/", 1)[1]
        cells = []
        row_total = 0
        for col in columns:
            n = sum(
                1
                for rid in run_ids
                if timed_out.get((prompt_id, col, rid))
            )
            if n:
                cells.append(Text(str(n), style="red"))
                row_total += n
                col_totals[col] += n
            else:
                cells.append(Text("-", style="dim"))
        grand_total += row_total
        cells.append(Text(str(row_total), style="bold red"))
        summary.add_row(scenario_name, *cells)

    total_cells = []
    for col in columns:
        n = col_totals.get(col, 0)
        total_cells.append(
            Text(str(n), style="bold red" if n else "dim")
        )
    total_cells.append(Text(str(grand_total), style="bold red"))
    summary.add_row(
        Text("TOTAL", style="bold"), *total_cells, end_section=True
    )

    console.print(summary)

    # -- Summary: totals by run number --------------------------------------
    console.print()
    run_table = Table(
        title="Timeouts by Run Number",
        title_style="bold",
        show_lines=True,
    )
    run_table.add_column("Run", justify="center", min_width=6)
    run_table.add_column("Timeouts", justify="center", min_width=10)
    run_table.add_column("Total Runs", justify="center", min_width=10)
    run_table.add_column("Rate", justify="center", min_width=8)

    for rid in run_ids:
        n_to = sum(
            1
            for (pid, col, r), v in timed_out.items()
            if r == rid and v and col in columns
        )
        n_all = sum(
            1
            for (pid, col, r) in timed_out
            if r == rid and col in columns
        )
        rate = n_to / n_all if n_all else 0
        pct = f"{round(rate * 100)}%"
        style = "red" if n_to else "dim"
        run_table.add_row(
            f"#{rid}",
            Text(str(n_to), style=style),
            str(n_all),
            Text(pct, style=style),
        )

    console.print(run_table)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarise benchmark results.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
columns:
  Source-bound formats render as one column per source (e.g.
  "markdown:claude-md", "markdown:prompt"). no-workflow has no source
  layer and shows as a single baseline column.

views (pick one, default is scenario + aggregate):
  --checks         Per-check pass rates across (format, source) columns
  --phase          Per-phase pass rates across (format, source) columns
  --timeouts      Which scenarios/runs timed out per (format, source) column

filters (combine with any view):
  --category CAT   Filter to category (comma-separated, e.g. bugs,new_features)
  --format FMT     Show only specific formats (comma-separated)
  --source SRC     Show only specific sources: claude-md | prompt
                   (comma-separated). no-workflow appears regardless.
  --scenario NAME  Filter to a specific scenario name
  --since WINDOW   Only include runs started within the window
                   (e.g. 15d, 36h, 2026-04-24, 2026-04-24T12:00:00)

examples:
  bin/summary                                    # default tables
  bin/summary --checks                           # which checks pass per column
  bin/summary --checks --category bugs           # check detail for bugs only
  bin/summary --phase                            # phase-level comparison
  bin/summary --timeouts                         # which runs timed out
  bin/summary --category bugs                    # scenario table, bugs only
  bin/summary --format ape,markdown              # compare two formats (both sources)
  bin/summary --source prompt                    # only prompt-delivered runs
  bin/summary --format markdown --source claude-md
  bin/summary --checks --scenario dry_run_mode   # check detail for one scenario
  bin/summary --since 15d                        # only runs from the last 15 days
  bin/summary --since 2026-04-24                 # only runs on/after 2026-04-24
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
        "--timeouts",
        action="store_true",
        help="Show which scenarios/runs timed out per format",
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
        "--source",
        type=str,
        default=None,
        dest="source_filter",
        help=(
            "Show only specific workflow sources (claude-md | prompt, "
            "comma-separated). no-workflow is always shown as a baseline."
        ),
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Filter to a specific scenario name",
    )
    parser.add_argument(
        "--since",
        type=parse_since,
        default=None,
        help=(
            "Only include runs started within this window "
            "(e.g. 15d, 36h, 2026-04-24, 2026-04-24T12:00:00)"
        ),
    )
    args = parser.parse_args()

    views = sum([args.checks, args.phase, args.timeouts])
    if views > 1:
        console.print(
            "[red]--checks, --phase, and --timeouts are mutually exclusive[/red]"
        )
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

    if args.since is not None:
        summaries = filter_by_since(
            summaries,
            args.since,
            get_started_at=lambda s: s.get("started_at") or s.get("timestamp"),
            label_for=_summary_label,
            warn=lambda msg: console.print(f"[yellow][summary] {msg}[/yellow]"),
        )
        if not summaries:
            console.print(
                f"[red]No runs started on or after[/red] {args.since.isoformat()}"
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
    source_filter = (
        [s.strip() for s in args.source_filter.split(",")]
        if args.source_filter
        else None
    )
    if source_filter:
        unknown = [s for s in source_filter if s not in SOURCE_ORDER]
        if unknown:
            console.print(
                f"[red]Unknown source(s):[/red] {', '.join(unknown)}. "
                f"Valid choices: {', '.join(SOURCE_ORDER)}."
            )
            sys.exit(1)

    filtered = filter_summaries(
        summaries, categories, fmt_filter, args.scenario, source_filter,
    )
    if not filtered:
        console.print("[red]No results match the given filters.[/red]")
        sys.exit(1)

    columns = discover_columns(filtered)

    title_suffix = build_title_suffix(categories, args.scenario)

    console.print()

    if args.checks:
        print_checks_table(filtered, columns, title_suffix)
    elif args.phase:
        print_phase_table(filtered, columns, title_suffix)
    elif args.timeouts:
        print_timeouts_table(filtered, columns, title_suffix)
    else:
        scenario_data = build_scenario_data(filtered)
        print_scenario_table(scenario_data, columns, title_suffix)
        console.print()
        print_aggregate_table(scenario_data, columns, title_suffix)

    console.print()


if __name__ == "__main__":
    main()

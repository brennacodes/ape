"""
Table formatting and CSV export for benchmark reports.

Public API
----------
format_summary_table(results) -> str
format_per_task_table(results) -> str
export_csv(results, path)
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def _simple_table(headers: list[str], rows: list[list[str]]) -> str:
    """
    Render a Unicode box-drawing table.

    Parameters
    ----------
    headers : list[str]
        Column headers.
    rows : list[list[str]]
        Table data (each row is a list of cell strings).

    Returns
    -------
    str
        Formatted table string.
    """
    # Compute column widths
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))

    def _row_str(cells: list[str]) -> str:
        parts = []
        for i, cell in enumerate(cells):
            w = widths[i] if i < len(widths) else len(cell)
            parts.append(f" {cell:<{w}} ")
        return "\u2502" + "\u2502".join(parts) + "\u2502"

    def _separator(left: str, mid: str, right: str, fill: str = "\u2500") -> str:
        parts = [fill * (w + 2) for w in widths]
        return left + mid.join(parts) + right

    lines = []
    lines.append(_separator("\u250c", "\u252c", "\u2510"))
    lines.append(_row_str(headers))
    lines.append(_separator("\u251c", "\u253c", "\u2524"))
    for row in rows:
        # Pad row to header length
        padded = row + [""] * (len(headers) - len(row))
        lines.append(_row_str(padded))
    lines.append(_separator("\u2514", "\u2534", "\u2518"))

    return "\n".join(lines)


def format_summary_table(results: dict[str, Any]) -> str:
    """
    Format a summary comparison table.

    Parameters
    ----------
    results : dict
        Mapping from metric name to result dict with keys:
        ape_mean, md_mean, delta, ci, p_value, effect_size

    Returns
    -------
    str
        Unicode box-drawing table.
    """
    headers = ["Metric", "APE", "MD", "Delta", "95% CI", "p-value", "d"]

    rows = []
    for name, r in results.items():
        ape = f"{r.get('ape_mean', 0):.3f}"
        md = f"{r.get('md_mean', 0):.3f}"
        delta = f"{r.get('delta', 0):+.3f}"
        ci = r.get("ci", (0, 0))
        ci_str = f"[{ci[0]:.3f}, {ci[1]:.3f}]"
        p_val = f"{r.get('p_value', 1):.4f}"
        d = f"{r.get('effect_size', 0):.2f}"

        sig = "*" if r.get("significant", False) else ""
        rows.append([name, ape, md, delta, ci_str, p_val + sig, d])

    return _simple_table(headers, rows)


def format_per_task_table(results: dict[str, dict[str, Any]]) -> str:
    """
    Format a per-task breakdown table.

    Parameters
    ----------
    results : dict
        Mapping from task name to dict with keys:
        ape_pass_rate, md_pass_rate, delta, n_runs

    Returns
    -------
    str
        Unicode box-drawing table.
    """
    headers = ["Task", "APE Rate", "MD Rate", "Delta", "N"]

    rows = []
    for task, r in results.items():
        ape = f"{r.get('ape_pass_rate', 0):.1%}"
        md = f"{r.get('md_pass_rate', 0):.1%}"
        delta = f"{r.get('delta', 0):+.1%}"
        n = str(r.get("n_runs", 0))
        rows.append([task, ape, md, delta, n])

    return _simple_table(headers, rows)


def export_csv(
    results: dict[str, Any],
    path: Path | str,
    per_task: dict[str, dict[str, Any]] | None = None,
) -> None:
    """
    Export results to CSV.

    Parameters
    ----------
    results : dict
        Summary results (metric -> values dict).
    path : Path
        Output CSV path.
    per_task : dict, optional
        Per-task results. If provided, writes a companion file at
        {path.stem}_per_task.csv.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Summary CSV
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "ape_mean", "md_mean", "delta", "ci_lower", "ci_upper", "p_value", "effect_size", "significant"])
        for name, r in results.items():
            ci = r.get("ci", (0, 0))
            writer.writerow([
                name,
                r.get("ape_mean", 0),
                r.get("md_mean", 0),
                r.get("delta", 0),
                ci[0],
                ci[1],
                r.get("p_value", 1),
                r.get("effect_size", 0),
                r.get("significant", False),
            ])

    # Per-task CSV
    if per_task:
        task_path = path.parent / f"{path.stem}_per_task.csv"
        with open(task_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["task", "ape_pass_rate", "md_pass_rate", "delta", "n_runs"])
            for task, r in per_task.items():
                writer.writerow([
                    task,
                    r.get("ape_pass_rate", 0),
                    r.get("md_pass_rate", 0),
                    r.get("delta", 0),
                    r.get("n_runs", 0),
                ])

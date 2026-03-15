#!/usr/bin/env python3
"""
End-to-end report generation from stored RunRecords.

Usage:
    python3 benchmark/scripts/report/generate_report.py
    python3 benchmark/scripts/report/generate_report.py --results-dir benchmark/output
    python3 benchmark/scripts/report/generate_report.py --output-dir benchmark/output/reports
"""

from __future__ import annotations

import argparse
import logging
import sys
import os
from collections import defaultdict
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel

console = Console()
logger = logging.getLogger("bench.report")

# Wire up modules
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(_HERE, "..")
for subdir in ("results", "metrics", "stats", "report"):
    p = os.path.join(_SCRIPTS, subdir)
    if p not in sys.path:
        sys.path.insert(0, p)

from recorder import Recorder, RunRecord  # noqa: E402

import numpy as np


def _group_runs(recorder: Recorder) -> dict[str, dict[str, list[RunRecord]]]:
    """
    Group runs by fixture/prompt and then by format.

    Returns {fixture_prompt_key: {format: [RunRecord, ...]}}
    """
    groups: dict[str, dict[str, list[RunRecord]]] = defaultdict(lambda: defaultdict(list))

    for record in recorder.all_runs():
        key = f"{record.fixture_id}/{record.prompt_id}"
        groups[key][record.format].append(record)

    return dict(groups)


def _compute_metrics_for_group(runs: list[RunRecord], recorder: Recorder | None = None) -> dict:
    """Compute metrics for a list of runs in the same condition."""
    from tokens import summarize_token_data
    from latency import compute_latency_metrics
    from consistency import compute_consistency
    from reliability import compute_reliability

    token_summary = summarize_token_data(runs)
    wall_times = [r.wall_clock_ms for r in runs if r.wall_clock_ms > 0]
    latency = compute_latency_metrics(wall_times)

    # Load stream outputs for consistency analysis
    outputs: list[str] = []
    for r in runs:
        if recorder:
            try:
                raw = recorder.load_raw_output(r.fixture_id, r.format, r.prompt_id, r.run_id)
                if raw:
                    outputs.append(raw)
                    continue
            except (FileNotFoundError, OSError):
                pass
        # Fallback: no raw_output available
    consistency = compute_consistency(outputs)
    reliability = compute_reliability(runs)

    return {
        "n_runs": len(runs),
        "pass_rates": [r.pass_rate for r in runs],
        "mean_pass_rate": float(np.mean([r.pass_rate for r in runs])) if runs else 0.0,
        "tokens": token_summary,
        "latency": {
            "p50_ms": latency.p50_ms,
            "p95_ms": latency.p95_ms,
            "mean_ms": latency.mean_ms,
        },
        "consistency": {
            "mean_similarity": consistency.mean_similarity,
            "identical_structure_rate": consistency.identical_structure_rate,
        },
        "reliability": {
            "completion_rate": reliability.completion_rate,
        },
    }


def _run_paired_analysis(ape_runs: list[RunRecord], md_runs: list[RunRecord]) -> dict:
    """Run paired statistical analysis between two conditions."""
    from bootstrap import paired_analysis
    from effect_size import cohens_d, odds_ratio_ci
    from corrections import apply_corrections

    results = {}

    # Pass rate comparison
    ape_rates = [r.pass_rate for r in ape_runs]
    md_rates = [r.pass_rate for r in md_runs]

    if ape_rates and md_rates:
        pr = paired_analysis(ape_rates, md_rates)
        results["pass_rate"] = {
            "ape_mean": float(np.mean(ape_rates)),
            "md_mean": float(np.mean(md_rates)),
            "delta": pr.mean_delta,
            "ci": (pr.ci_lower, pr.ci_upper),
            "p_value": pr.p_value,
            "effect_size": pr.effect_size,
            "significant": pr.significant,
        }

    # Latency comparison
    ape_times = [r.wall_clock_ms for r in ape_runs if r.wall_clock_ms > 0]
    md_times = [r.wall_clock_ms for r in md_runs if r.wall_clock_ms > 0]

    if ape_times and md_times:
        lr = paired_analysis(ape_times, md_times)
        results["latency"] = {
            "ape_mean": float(np.mean(ape_times)),
            "md_mean": float(np.mean(md_times)),
            "delta": lr.mean_delta,
            "ci": (lr.ci_lower, lr.ci_upper),
            "p_value": lr.p_value,
            "effect_size": lr.effect_size,
            "significant": lr.significant,
        }

    # Token comparison
    ape_tokens = [r.input_tokens + r.output_tokens for r in ape_runs if r.input_tokens > 0]
    md_tokens = [r.input_tokens + r.output_tokens for r in md_runs if r.input_tokens > 0]

    if ape_tokens and md_tokens:
        tr = paired_analysis(ape_tokens, md_tokens)
        results["total_tokens"] = {
            "ape_mean": float(np.mean(ape_tokens)),
            "md_mean": float(np.mean(md_tokens)),
            "delta": tr.mean_delta,
            "ci": (tr.ci_lower, tr.ci_upper),
            "p_value": tr.p_value,
            "effect_size": tr.effect_size,
            "significant": tr.significant,
        }

    # Apply Holm-Bonferroni correction
    if results:
        raw_p = {name: r["p_value"] for name, r in results.items()}
        corrected = apply_corrections(raw_p, method="holm")
        for name, (corrected_p, sig) in corrected.items():
            results[name]["p_value_corrected"] = corrected_p
            results[name]["significant_corrected"] = sig

    return results


def generate_report(results_dir: Path, output_dir: Path) -> int:
    """Generate the full benchmark report."""
    recorder = Recorder(results_dir)
    groups = _group_runs(recorder)

    if not groups:
        console.print("[yellow]Warning:[/yellow] No runs found. Nothing to report.")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    # Import report modules
    from summary import generate_summary
    from tables import format_summary_table, format_per_task_table, export_csv

    total_groups = len(groups)
    console.print(Panel.fit(
        f"[bold]Benchmark Report Generation[/bold]\n"
        f"  Results dir: {results_dir}\n"
        f"  Output dir:  {output_dir}\n"
        f"  Groups:      {total_groups}",
        title="bench-report",
        border_style="blue",
    ))

    all_analyses = {}
    per_task_results = {}

    for i, (group_key, format_runs) in enumerate(groups.items(), 1):
        logger.info(
            "[%d/%d] Analyzing [cyan]%s[/cyan] (%d format(s))",
            i, total_groups, group_key, len(format_runs),
        )

        # Compute per-condition metrics
        for fmt, runs in format_runs.items():
            metrics = _compute_metrics_for_group(runs, recorder=recorder)
            logger.info(
                "  %s: %d runs, mean_pass_rate=%.1f%%",
                fmt, metrics["n_runs"], metrics["mean_pass_rate"] * 100,
            )
            per_task_results[f"{group_key}/{fmt}"] = {
                "ape_pass_rate": metrics["mean_pass_rate"] if "ape" in fmt.lower() else 0,
                "md_pass_rate": metrics["mean_pass_rate"] if "ape" not in fmt.lower() else 0,
                "delta": 0,
                "n_runs": metrics["n_runs"],
            }

        # Paired analysis if we have exactly 2 formats
        formats = list(format_runs.keys())
        if len(formats) == 2:
            fmt_a, fmt_b = formats
            analysis = _run_paired_analysis(format_runs[fmt_a], format_runs[fmt_b])
            for metric_name, result in analysis.items():
                all_analyses[f"{group_key}/{metric_name}"] = result

    # Generate narrative summary
    narrative = generate_summary(all_analyses)

    # Generate tables
    summary_table = format_summary_table(all_analyses) if all_analyses else "No paired analyses available."

    # Write report files
    report_text = f"{narrative}\n\n{summary_table}\n"
    report_path = output_dir / "report.txt"
    report_path.write_text(report_text, encoding="utf-8")
    logger.info("Report written to %s", report_path)

    # Export CSV
    if all_analyses:
        csv_path = output_dir / "results.csv"
        export_csv(all_analyses, csv_path, per_task_results)
        logger.info("CSV written to %s", csv_path)

    console.print()
    console.rule("[bold]Report Complete[/bold]", style="green")
    console.print(f"  Report: [green]{report_path}[/green]")
    if all_analyses:
        console.print(f"  CSV:    [green]{output_dir / 'results.csv'}[/green]")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench-report",
        description="Generate benchmark analysis report.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path(_HERE).parent.parent / "output",
        help="Structured results directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(_HERE).parent.parent / "output" / "reports",
        help="Report output directory.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="Enable debug-level logging.",
    )
    args = parser.parse_args(argv)

    # Set up structured logging with Rich
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(
            console=console,
            rich_tracebacks=True,
            markup=True,
            show_path=False,
        )],
    )

    return generate_report(args.results_dir, args.output_dir)


if __name__ == "__main__":
    sys.exit(main())

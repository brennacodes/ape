#!/usr/bin/env python3
"""Re-evaluate stored benchmark results using the current evaluator and display summaries.

Reconstructs proper evaluation context from prompt and app-config YAML files
so that re-evaluation produces identical results to the original run. Supports
both new per-run directory format (summary.json + stream.json) and legacy
flat JSON format (raw_output embedded in record).

Usage:
    # Re-evaluate the most recent run only
    python3 benchmark/re_evaluate.py --last

    # Re-evaluate a specific run by number
    python3 benchmark/re_evaluate.py --run 3

    # Re-evaluate runs matching filters
    python3 benchmark/re_evaluate.py --format ape --prompt bugs/silent_yaml_failure

    # Re-evaluate a specific run directory
    python3 benchmark/re_evaluate.py benchmark/output/bivvy/ape/bugs/silent_yaml_failure/003

    # Re-evaluate and write corrected results back to disk
    python3 benchmark/re_evaluate.py --last --save

    # Re-evaluate everything (original behavior)
    python3 benchmark/re_evaluate.py --all
"""

import argparse, json, sys, os, glob, yaml, re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts", "runner"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts", "evaluator"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts", "results"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts", "coordinator"))

from trace import parse_trace_jsonl
from evaluator import evaluate
from coordinator import (
    load_prompt, load_app_config, get_app_config_variables, build_context,
)
from results import (
    RunMetadata, CheckOutcome,
    summarize_run, format_run_summary, format_comparison, summarize_comparison,
)

try:
    from rich.console import Console
    console = Console()
    def printout(text):
        console.print(text)
except ImportError:
    def printout(text):
        print(re.sub(r'\[/?[^\]]*\]', '', text))


def _load_context_for_record(record, root, config):
    """Reconstruct the evaluation context that was used during the original run.

    Uses stored context if available (new format), otherwise rebuilds from
    prompt and app-config YAML files to match what the runner originally built.
    """
    # If the record already stores context (new format), use it directly
    stored_conditions = record.get("eval_conditions")
    stored_variables = record.get("eval_variables")
    if stored_conditions is not None and stored_variables is not None:
        return {
            "conditions": stored_conditions,
            "variables": stored_variables,
            "phase_tool_mapping": config.get("phase_tool_mapping", {}),
            "phase_classification": config.get("phase_classification", {}),
        }

    # Otherwise reconstruct from YAML files
    prompt_id = record.get("prompt_id", "")
    fixture_id = record.get("fixture_id", "bivvy")

    # prompt_id is "category/item_id" (e.g. "bugs/silent_yaml_failure")
    parts = prompt_id.split("/", 1) if prompt_id else []
    category = parts[0] if len(parts) >= 1 else ""
    item_id = parts[1] if len(parts) >= 2 else ""

    # Load the prompt template YAML
    prompt_path = os.path.join(root, "prompts", f"{category}.yml")
    if os.path.exists(prompt_path):
        prompt_data = yaml.safe_load(open(prompt_path))
    else:
        prompt_data = {"conditions": {}, "variables": {}}

    # Load the app-config YAML and extract item variables
    app_config_path = os.path.join(root, "prompts", "app-configs", f"{fixture_id}.yml")
    app_config_variables = None
    if os.path.exists(app_config_path) and category and item_id:
        ac_data = yaml.safe_load(open(app_config_path))
        app_config_variables = get_app_config_variables(ac_data, category, item_id)

    ctx = build_context(prompt_data, config, app_config_variables=app_config_variables)
    return ctx


def _load_raw_output_for_run(run_dir):
    """Load raw output as JSONL string from a per-run directory's stream.json."""
    stream_path = os.path.join(run_dir, "stream.json")
    if not os.path.exists(stream_path):
        return ""
    with open(stream_path) as f:
        stream_data = json.load(f)
    return "\n".join(json.dumps(obj) for obj in stream_data)


def _discover_runs(results_dir):
    """Discover all run records, supporting both new and legacy formats.

    Returns list of (record_dict, source_path) tuples.
    """
    runs = []

    # New format: scan for summary.json files
    for summary_path in sorted(glob.glob(os.path.join(results_dir, "**", "summary.json"), recursive=True)):
        # Skip paths under raw/ or logs/
        rel = os.path.relpath(summary_path, results_dir)
        parts = rel.split(os.sep)
        if parts and parts[0] in ("raw", "logs", "reports"):
            continue

        run_dir = os.path.dirname(summary_path)
        with open(summary_path) as fh:
            record = json.load(fh)

        # Load raw_output from stream.json
        raw_output = _load_raw_output_for_run(run_dir)
        if raw_output:
            record["raw_output"] = raw_output

        # Load workspace_state from state.json if not in summary
        if "workspace_state" not in record or not record["workspace_state"]:
            state_path = os.path.join(run_dir, "state.json")
            if os.path.exists(state_path):
                with open(state_path) as f:
                    record["workspace_state"] = json.load(f)

        runs.append((record, summary_path))

    # Legacy format: scan raw/ for flat JSON files
    legacy_dir = os.path.join(results_dir, "raw")
    if os.path.isdir(legacy_dir):
        for fpath in sorted(glob.glob(os.path.join(legacy_dir, "**", "*.json"), recursive=True)):
            with open(fpath) as fh:
                record = json.load(fh)
            runs.append((record, fpath))

    return runs


def _filter_runs(runs, args):
    """Filter discovered runs based on CLI arguments.

    Returns a subset of (record, source_path) tuples matching the filters.
    """
    # If a specific path was given as positional arg, match only that
    if args.path:
        target = os.path.abspath(args.path)
        filtered = []
        for record, fpath in runs:
            run_dir = os.path.dirname(fpath) if fpath.endswith("summary.json") else os.path.dirname(fpath)
            if os.path.abspath(run_dir) == target or os.path.abspath(fpath) == target:
                filtered.append((record, fpath))
        if not filtered:
            # Try matching as a prefix (e.g. user gave the prompt dir, not run dir)
            for record, fpath in runs:
                if os.path.abspath(fpath).startswith(target):
                    filtered.append((record, fpath))
        return filtered

    filtered = runs

    # Apply field filters
    if args.fixture:
        filtered = [(r, p) for r, p in filtered if r.get("fixture_id") == args.fixture]
    if args.format:
        filtered = [(r, p) for r, p in filtered if r.get("format") == args.format]
    if args.prompt:
        filtered = [(r, p) for r, p in filtered if r.get("prompt_id") == args.prompt]
    if args.run is not None:
        filtered = [(r, p) for r, p in filtered if r.get("run_id") == args.run]

    # --last: keep only the most recent run (highest run_id per unique case,
    # then the single latest by timestamp/path)
    if args.last:
        if not filtered:
            return []
        # Sort by timestamp descending, fall back to path order
        def sort_key(item):
            r, p = item
            return (r.get("completed_at") or r.get("started_at") or r.get("timestamp") or "", p)
        filtered.sort(key=sort_key, reverse=True)
        return [filtered[0]]

    return filtered


def _save_reeval_results(record, outcomes, run_dir, config):
    """Write corrected evaluation results back to the run directory."""
    from dataclasses import asdict

    # Overwrite per-check files
    check_files = []
    for outcome in outcomes:
        outcome_dict = asdict(outcome)
        check_id = outcome_dict.get("check_id", "unknown")
        safe_id = check_id.replace("/", "_").replace("\\", "_")
        check_path = os.path.join(run_dir, f"{safe_id}.json")
        with open(check_path, "w", encoding="utf-8") as f:
            json.dump(outcome_dict, f, indent=2)
        check_files.append({
            "check_id": check_id,
            "passed": outcome_dict.get("passed", False),
            "phase": outcome_dict.get("phase", ""),
            "file": f"{safe_id}.json",
        })

    # Update summary.json with corrected grades
    summary_path = os.path.join(run_dir, "summary.json")
    if os.path.exists(summary_path):
        with open(summary_path) as f:
            summary = json.load(f)
    else:
        summary = dict(record)

    passed = sum(1 for o in outcomes if o.passed)
    failed = sum(1 for o in outcomes if not o.passed and not o.skip_reason)
    skipped = sum(1 for o in outcomes if o.skip_reason)
    total = passed + failed + skipped
    pass_rate = passed / total if total > 0 else 0.0

    summary["checks"] = check_files
    summary["total"] = total
    summary["passed"] = passed
    summary["failed"] = failed
    summary["skipped"] = skipped
    summary["pass_rate"] = pass_rate
    summary["grade"] = f"{pass_rate * 100:.0f}%"

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Re-evaluate stored benchmark results with the current evaluator.",
        epilog="Examples:\n"
               "  %(prog)s --last                  Re-evaluate the most recent run\n"
               "  %(prog)s --last --save            Re-evaluate and save corrected results\n"
               "  %(prog)s --run 3                  Re-evaluate run 003\n"
               "  %(prog)s --format ape             Re-evaluate all runs with format 'ape'\n"
               "  %(prog)s output/bivvy/ape/.../003  Re-evaluate a specific run directory\n"
               "  %(prog)s --all                    Re-evaluate everything\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("path", nargs="?", default=None,
                        help="Path to a specific run directory to re-evaluate")
    parser.add_argument("--last", action="store_true",
                        help="Re-evaluate only the most recent run")
    parser.add_argument("--all", action="store_true",
                        help="Re-evaluate all runs (original behavior)")
    parser.add_argument("--run", type=int, default=None,
                        help="Re-evaluate a specific run number (e.g. 3)")
    parser.add_argument("--fixture", default=None,
                        help="Filter by fixture ID (e.g. 'bivvy')")
    parser.add_argument("--format", default=None,
                        help="Filter by format (e.g. 'ape', 'markdown')")
    parser.add_argument("--prompt", default=None,
                        help="Filter by prompt ID (e.g. 'bugs/silent_yaml_failure')")
    parser.add_argument("--save", action="store_true",
                        help="Write corrected results back to disk (summary.json + check files)")

    args = parser.parse_args()

    # Require at least one targeting option
    has_filter = any([args.path, args.last, args.all, args.run is not None,
                      args.fixture, args.format, args.prompt])
    if not has_filter:
        parser.error("specify a target. Use --last for the most recent run, or --all for everything.")

    return args


def main():
    args = _parse_args()

    root = os.path.dirname(__file__)
    config_path = os.path.join(root, "test-configs", "bivvy.yml")
    results_dir = os.path.join(root, "output")

    with open(config_path) as f:
        config = yaml.safe_load(f)

    checks = config["checks"]

    runs = _discover_runs(results_dir)
    if not runs:
        print(f"No result files found in {results_dir}")
        return

    runs = _filter_runs(runs, args)
    if not runs:
        print("No runs matched the specified filters.")
        return

    printout(f"[bold]Re-evaluating {len(runs)} run(s)...[/bold]\n")

    summaries = []
    for record, fpath in runs:
        raw_output = record.get("raw_output", "")
        if not raw_output:
            continue

        try:
            trace = parse_trace_jsonl(raw_output)
        except Exception as e:
            print(f"SKIP {fpath}: {e}")
            continue

        # Reconstruct proper evaluation context
        context = _load_context_for_record(record, root, config)
        context["workspace_state"] = record.get("workspace_state", {})
        context["workspace_path"] = trace.workspace_path

        results = evaluate(trace, checks, context)

        # Extract case category from prompt_id (e.g. "bugs/silent_yaml_failure" → "bugs")
        prompt_id = record.get("prompt_id", "")
        case_category = prompt_id.split("/", 1)[0] if "/" in prompt_id else None

        outcomes = [
            CheckOutcome(
                check_id=r.check_id, phase=r.phase, passed=r.passed,
                skip_reason=r.skip_reason, detail=r.detail,
                category=case_category,
                metric_value=r.metric_value, target_value=r.target_value,
                operator=r.operator,
            )
            for r in results
        ]

        meta = RunMetadata(
            fixture_id=record.get("fixture_id", "bivvy"),
            format=record.get("format", "?"),
            prompt_id=record.get("prompt_id", "?"),
            model=record.get("model", ""),
            session_id=record.get("session_id", ""),
            timestamp=record.get("timestamp", ""),
        )

        summary = summarize_run(outcomes, meta)
        summaries.append((fpath, summary, record, outcomes))

        # Save corrected results back to disk if requested
        if args.save:
            run_dir = os.path.dirname(fpath)
            _save_reeval_results(record, outcomes, run_dir, config)

    if args.save:
        printout(f"[green bold]Saved corrected results for {len(summaries)} run(s).[/green bold]\n")

    # Print each run
    for fpath, summary, record, _ in summaries:
        class R:
            pass
        r = R()
        r.wall_clock_ms = record.get("wall_clock_ms", 0)
        r.input_tokens = record.get("input_tokens", 0)
        r.output_tokens = record.get("output_tokens", 0)
        r.cost_usd = record.get("cost_usd", 0)
        r.num_turns = record.get("num_turns", 0)
        printout(format_run_summary(summary, record=r))
        printout("")

    # Cross-format comparison (first run per format)
    seen = {}
    for _, summary, _, _ in summaries:
        fmt = summary.metadata.format
        if fmt not in seen:
            seen[fmt] = summary

    if len(seen) > 1:
        comparison = summarize_comparison(list(seen.values()))
        printout(format_comparison(comparison))


if __name__ == "__main__":
    main()

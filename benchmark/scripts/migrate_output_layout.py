#!/usr/bin/env python3
"""
Reorganise benchmark output to the source-aware layout.

The new layout groups source-bound runs by the workflow source they were
delivered under:

  output/{source}/{app}/{format}/{prompt_id}/{run_id}/

``no-workflow`` is source-agnostic and stays at
``output/{app}/no-workflow/...``. Per-fixture ``baseline.json`` files also
stay in place because they are independent of source.

Mapping per the per-format source each case was actually delivered under
in the pre-source-dimension code:

  output/{app}/plain-text/...   ->  output/prompt/{app}/plain-text/...
  output/{app}/markdown/...     ->  output/claude-md/{app}/markdown/...
  output/{app}/ape/...          ->  output/claude-md/{app}/ape/...
  output/{app}/adhoc-xml/...    ->  output/claude-md/{app}/adhoc-xml/...
  output/{app}/no-workflow/...  ->  (unchanged)
  output/{app}/baseline.json    ->  (unchanged)

Also rewrites every migrated ``summary.json`` to record the matching
``source`` field. ``no-workflow`` summaries are rewritten in place with
``source=""`` so downstream tooling sees the same data shape as fresh runs.

Idempotent: moves whose destination already exists are skipped.

Usage
-----
  python3 benchmark/scripts/migrate_output_layout.py --root benchmark/output
  python3 benchmark/scripts/migrate_output_layout.py --root benchmark/output --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# Maps the per-format directory layout pre-migration to the source it was
# actually delivered under.
_FORMAT_TO_SOURCE = {
    "plain-text": "prompt",
    "markdown": "claude-md",
    "ape": "claude-md",
    "adhoc-xml": "claude-md",
}

# Formats and dirs that stay in place (source-agnostic or non-result).
_PRESERVED_TOP_LEVEL = {"no-workflow"}


def _is_app_dir(path: Path) -> bool:
    """Return True if *path* looks like an app subtree under output/."""
    if not path.is_dir():
        return False
    # Skip already-migrated source layers.
    if path.name in _FORMAT_TO_SOURCE.values():
        return False
    return True


def _stamp_run_json(path: Path, source: str) -> None:
    """Rewrite a single summary.json to record ``source``.

    No-op if the file is missing or unparseable.
    """
    if not path.is_file():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    if data.get("source") == source:
        return
    data["source"] = source
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        return


def _stamp_summaries_under(run_root: Path, source: str, *, apply: bool) -> int:
    """Stamp every ``summary.json`` beneath *run_root* with ``source``.

    Returns the count visited. Performs no writes when ``apply`` is False.
    """
    if not run_root.is_dir():
        return 0
    count = 0
    for summary_path in run_root.rglob("summary.json"):
        if apply:
            _stamp_run_json(summary_path, source)
        count += 1
    return count


def _move_tree(src: Path, dest: Path, *, apply: bool) -> tuple[bool, str]:
    """Move *src* to *dest*.

    Returns (moved, message). Skips if dest already exists.
    """
    if dest.exists():
        return False, f"SKIP (dest exists): {dest}"
    if apply:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        return True, f"MOVED: {src} -> {dest}"
    return True, f"WOULD MOVE: {src} -> {dest}"


def migrate(root: Path, *, apply: bool) -> int:
    """Migrate the tree at *root*. Returns 0 on success."""
    if not root.is_dir():
        print(f"Root not found: {root}", file=sys.stderr)
        return 1

    actions: list[str] = []
    summary_count = 0

    for app_dir in sorted(root.iterdir()):
        if not _is_app_dir(app_dir):
            continue
        app_name = app_dir.name

        for fmt_dir in sorted(app_dir.iterdir()):
            if not fmt_dir.is_dir():
                continue
            fmt = fmt_dir.name
            if fmt in _PRESERVED_TOP_LEVEL:
                # No-workflow stays in place; stamp source="" so the field
                # is present on every summary.json.
                touched = _stamp_summaries_under(fmt_dir, source="", apply=apply)
                summary_count += touched
                if touched:
                    verb = "STAMP" if apply else "WOULD STAMP"
                    actions.append(
                        f"{verb} (source=''): {touched} summary.json under {fmt_dir}"
                    )
                continue
            source = _FORMAT_TO_SOURCE.get(fmt)
            if source is None:
                # Unknown format directory — leave it alone.
                continue
            dest = root / source / app_name / fmt
            moved, msg = _move_tree(fmt_dir, dest, apply=apply)
            actions.append(msg)
            if moved:
                # When applying, stamp the moved tree at its new location.
                # When dry-running, stamp counts come from the source tree.
                target = dest if apply else fmt_dir
                touched = _stamp_summaries_under(target, source=source, apply=apply)
                summary_count += touched
                verb = "STAMP" if apply else "WOULD STAMP"
                actions.append(
                    f"{verb} (source={source!r}): {touched} summary.json under {target}"
                )

    for line in actions:
        print(line)

    if apply:
        print(f"\nApplied {sum(1 for a in actions if a.startswith('MOVED'))} moves, "
              f"stamped {summary_count} summary.json files.")
    else:
        print(f"\nDry run. Re-run with --apply to perform the moves.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate benchmark output to the source-aware layout.",
    )
    parser.add_argument(
        "--root", type=Path,
        default=Path(__file__).resolve().parent.parent / "output",
        help="Root of the results directory (default: benchmark/output).",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Perform moves and rewrites. Default is dry-run.",
    )
    args = parser.parse_args(argv)
    return migrate(args.root, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())

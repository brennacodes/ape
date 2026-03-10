#!/usr/bin/env python3
"""
Migrate legacy flat-file summaries to structured storage.

Usage:
    python3 benchmark/scripts/migrate_results.py
    python3 benchmark/scripts/migrate_results.py --input benchmark/output/summaries --output benchmark/output
"""

from __future__ import annotations

import argparse
import json
import sys
import os
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_RESULTS = os.path.join(_HERE, "results")
if _RESULTS not in sys.path:
    sys.path.insert(0, _RESULTS)

from recorder import Recorder, RunRecord


def migrate(input_dir: Path, output_dir: Path) -> int:
    """Migrate all JSON summaries from input_dir into structured storage."""
    recorder = Recorder(output_dir)
    migrated = 0

    if not input_dir.is_dir():
        print(f"Input directory does not exist: {input_dir}")
        return 1

    for json_path in sorted(input_dir.glob("*.json")):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  Skipping {json_path.name}: {exc}")
            continue

        metadata = data.get("metadata", {})
        fixture_id = metadata.get("fixture_id", "")
        fmt = metadata.get("format", "")
        prompt_id = metadata.get("prompt_id", "")

        if not (fixture_id and fmt and prompt_id):
            print(f"  Skipping {json_path.name}: missing identity fields")
            continue

        run_id = recorder.next_run_id(fixture_id, fmt, prompt_id)

        record = RunRecord(
            fixture_id=fixture_id,
            format=fmt,
            prompt_id=prompt_id,
            run_id=run_id,
            outcomes=data.get("outcomes", []),
            total=data.get("total", 0),
            passed=data.get("passed", 0),
            failed=data.get("failed", 0),
            skipped=data.get("skipped", 0),
            pass_rate=data.get("pass_rate", 0.0),
            model=metadata.get("model", ""),
            session_id=metadata.get("session_id", ""),
            timestamp=metadata.get("timestamp", ""),
        )

        path = recorder.save_run(record)
        print(f"  Migrated {json_path.name} -> {path}")
        migrated += 1

    print(f"\nMigrated {migrated} file(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate flat summaries to structured storage.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(_HERE).parent / "output" / "summaries",
        help="Directory containing legacy JSON summaries.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(_HERE).parent / "output",
        help="Structured results directory.",
    )
    args = parser.parse_args(argv)
    return migrate(args.input, args.output)


if __name__ == "__main__":
    sys.exit(main())

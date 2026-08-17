"""Tests for the small label helpers used by --since warnings.

These live in the CLI scripts (benchmark/summary.py and
benchmark/re_evaluate.py) and feed the filter_by_since `label_for`
callback. Loaded via importlib from explicit paths so this test file
does not itself insert benchmark/ onto sys.path - doing so would shadow
benchmark/scripts/report/summary.py for other test modules. (The CLI
modules themselves still insert their own subpaths during exec_module,
which is harmless because none of those subpaths shadow names used by
other test modules.)
"""

import importlib.util
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_summary_mod = _load("_cli_summary_for_test", BENCHMARK_DIR / "summary.py")
_reeval_mod = _load("_cli_reeval_for_test", BENCHMARK_DIR / "re_evaluate.py")
_summary_label = _summary_mod._summary_label
_run_label = _reeval_mod._run_label


def test_summary_label_uses_all_fields():
    summary = {
        "fixture_id": "bivvy",
        "format": "ape",
        "prompt_id": "bugs/silent_yaml_failure",
        "run_id": 3,
    }
    assert _summary_label(summary) == "bivvy/ape/bugs/silent_yaml_failure/3"


def test_summary_label_includes_source_when_present():
    summary = {
        "fixture_id": "bivvy",
        "format": "ape",
        "source": "claude-md",
        "prompt_id": "bugs/silent_yaml_failure",
        "run_id": 3,
    }
    assert _summary_label(summary) == (
        "bivvy/ape/claude-md/bugs/silent_yaml_failure/3"
    )


def test_summary_label_omits_empty_source():
    summary = {
        "fixture_id": "bivvy",
        "format": "no-workflow",
        "source": "",
        "prompt_id": "bugs/silent_yaml_failure",
        "run_id": 0,
    }
    assert _summary_label(summary) == (
        "bivvy/no-workflow/bugs/silent_yaml_failure/0"
    )


def test_summary_label_uses_question_marks_for_missing_fields():
    assert _summary_label({}) == "?/?/?/?"


def test_run_label_uses_record_fields_not_path():
    record = {
        "fixture_id": "bivvy",
        "format": "markdown",
        "prompt_id": "bugs/race_condition_in_cache",
        "run_id": 7,
    }
    label = _run_label(record, "/some/ignored/path/summary.json")
    assert label == "bivvy/markdown/bugs/race_condition_in_cache/7"


def test_run_label_uses_question_marks_for_missing_fields():
    assert _run_label({}, "ignored") == "?/?/?/?"

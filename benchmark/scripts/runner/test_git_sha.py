"""Tests for capture_git_sha in benchmark/scripts/runner/runner.py."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_COORD = os.path.join(_HERE, "..", "coordinator")
_RESULTS = os.path.join(_HERE, "..", "results")
_EVAL = os.path.join(_HERE, "..", "evaluator")
for _dir in (_COORD, _RESULTS, _EVAL):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

from runner import capture_git_sha


SHA12 = re.compile(r"^[0-9a-f]{12}$")


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@t"], check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "t"], check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "commit.gpgsign", "false"], check=True,
    )


def _commit(path: Path, name: str, content: str, msg: str) -> None:
    (path / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", name], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "-m", msg], check=True,
    )


class TestCaptureGitSha:

    def test_clean_repo_returns_short_sha(self, tmp_path):
        _init_repo(tmp_path)
        _commit(tmp_path, "a.txt", "hello", "init")

        sha = capture_git_sha(tmp_path)

        assert SHA12.match(sha), f"expected 12-char SHA, got {sha!r}"

    def test_dirty_tracked_file_marks_dirty(self, tmp_path):
        _init_repo(tmp_path)
        _commit(tmp_path, "a.txt", "hello", "init")
        (tmp_path / "a.txt").write_text("changed", encoding="utf-8")

        sha = capture_git_sha(tmp_path)

        assert sha.endswith("-dirty"), f"expected dirty suffix, got {sha!r}"
        assert SHA12.match(sha[: -len("-dirty")])

    def test_staged_change_marks_dirty(self, tmp_path):
        _init_repo(tmp_path)
        _commit(tmp_path, "a.txt", "hello", "init")
        (tmp_path / "a.txt").write_text("changed", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "a.txt"], check=True,
        )

        sha = capture_git_sha(tmp_path)

        assert sha.endswith("-dirty"), f"expected dirty suffix, got {sha!r}"

    def test_untracked_file_marks_dirty(self, tmp_path):
        _init_repo(tmp_path)
        _commit(tmp_path, "a.txt", "hello", "init")
        (tmp_path / "new.txt").write_text("x", encoding="utf-8")

        sha = capture_git_sha(tmp_path)

        assert sha.endswith("-dirty"), f"expected dirty suffix, got {sha!r}"

    def test_non_git_dir_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="Failed to capture git SHA"):
            capture_git_sha(tmp_path)

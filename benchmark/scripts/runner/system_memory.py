"""Swap ~/.claude/CLAUDE.md with a blank file for the duration of a benchmark run.

Benchmark runs read the user's global CLAUDE.md and inherit rules that bias the
agent toward deferral instead of editing files. This module isolates the run
from that file by renaming it aside, replacing it with an empty file, and
restoring it on exit (success, failure, or signal).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("bench")


class SystemClaudeMdSwap:
    """Swaps ~/.claude/CLAUDE.md with a blank file for the duration of a context.

    Rename original to CLAUDE.md.example on enter; restore on exit. Aborts if
    a stale CLAUDE.md.example exists at startup (indicates a prior crash with
    no restore). Safe to use as a no-op when no system CLAUDE.md exists.
    """

    def __init__(self, claude_dir: Path | None = None):
        self._claude_dir = claude_dir if claude_dir is not None else Path.home() / ".claude"
        self._original = self._claude_dir / "CLAUDE.md"
        self._aside = self._claude_dir / "CLAUDE.md.example"
        self._swapped = False

    def __enter__(self) -> "SystemClaudeMdSwap":
        self._assert_safe_to_swap()
        self._swap_in()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._swap_out()
        except Exception as restore_exc:
            logger.error(
                "Failed to restore %s: %s. Inspect %s manually.",
                self._original, restore_exc, self._claude_dir,
            )

    def _assert_safe_to_swap(self) -> None:
        if self._aside.exists():
            raise RuntimeError(
                f"Stale {self._aside} exists. A prior benchmark run may have crashed "
                f"without restoring the system CLAUDE.md. Inspect manually: if "
                f"{self._original} contains the expected user content, delete "
                f"{self._aside}; otherwise rename {self._aside} back to "
                f"{self._original} before retrying."
            )

    def _swap_in(self) -> None:
        if not self._original.exists():
            logger.info("No %s present; skipping swap.", self._original)
            return
        self._original.rename(self._aside)
        self._original.write_bytes(b"")
        self._swapped = True
        logger.info("Swapped %s aside to %s for benchmark run.", self._original, self._aside)

    def _swap_out(self) -> None:
        if not self._swapped:
            return
        if self._original.exists():
            self._original.unlink()
        else:
            logger.warning(
                "Blank %s missing on restore; proceeding to rename %s back.",
                self._original, self._aside,
            )
        if self._aside.exists():
            self._aside.rename(self._original)
            logger.info("Restored %s from %s.", self._original, self._aside)
        else:
            logger.error(
                "Aside file %s missing on restore; cannot recover original CLAUDE.md.",
                self._aside,
            )

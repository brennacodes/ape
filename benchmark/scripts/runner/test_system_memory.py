"""Tests for SystemClaudeMdSwap in system_memory.py."""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from system_memory import SystemClaudeMdSwap


ORIGINAL_CONTENT = b"# user CLAUDE.md\n\nrules go here\n"


def test_swap_and_restore_happy_path(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_bytes(ORIGINAL_CONTENT)
    aside = tmp_path / "CLAUDE.md.example"

    with SystemClaudeMdSwap(claude_dir=tmp_path):
        assert claude_md.exists()
        assert claude_md.read_bytes() == b""
        assert aside.exists()
        assert aside.read_bytes() == ORIGINAL_CONTENT

    assert claude_md.exists()
    assert claude_md.read_bytes() == ORIGINAL_CONTENT
    assert not aside.exists()


def test_no_claude_md_is_noop(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    aside = tmp_path / "CLAUDE.md.example"

    with SystemClaudeMdSwap(claude_dir=tmp_path):
        assert not claude_md.exists()
        assert not aside.exists()

    assert not claude_md.exists()
    assert not aside.exists()


def test_stale_example_aborts(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_bytes(ORIGINAL_CONTENT)
    aside = tmp_path / "CLAUDE.md.example"
    aside.write_bytes(b"stale content")

    with pytest.raises(RuntimeError, match="Stale"):
        with SystemClaudeMdSwap(claude_dir=tmp_path):
            pytest.fail("Body should not execute when stale aside exists")

    assert claude_md.read_bytes() == ORIGINAL_CONTENT
    assert aside.read_bytes() == b"stale content"


def test_exception_in_body_still_restores(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_bytes(ORIGINAL_CONTENT)
    aside = tmp_path / "CLAUDE.md.example"

    class BoomError(Exception):
        pass

    with pytest.raises(BoomError):
        with SystemClaudeMdSwap(claude_dir=tmp_path):
            assert claude_md.read_bytes() == b""
            assert aside.read_bytes() == ORIGINAL_CONTENT
            raise BoomError("body failed")

    assert claude_md.read_bytes() == ORIGINAL_CONTENT
    assert not aside.exists()


def test_blank_file_is_zero_bytes(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_bytes(ORIGINAL_CONTENT)

    with SystemClaudeMdSwap(claude_dir=tmp_path):
        assert claude_md.stat().st_size == 0


def test_restore_idempotent_when_blank_missing(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_bytes(ORIGINAL_CONTENT)
    aside = tmp_path / "CLAUDE.md.example"

    with SystemClaudeMdSwap(claude_dir=tmp_path):
        claude_md.unlink()
        assert not claude_md.exists()
        assert aside.read_bytes() == ORIGINAL_CONTENT

    assert claude_md.read_bytes() == ORIGINAL_CONTENT
    assert not aside.exists()

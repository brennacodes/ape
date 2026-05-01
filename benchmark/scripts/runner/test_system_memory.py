"""Tests for SystemClaudeMdSwap in system_memory.py."""

import importlib.util
import os
import signal
import sys
from unittest.mock import patch

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from system_memory import SystemClaudeMdSwap


def _load_run_benchmark():
    """Import benchmark/run_benchmark.py without running it."""
    benchmark_root = os.path.normpath(os.path.join(_HERE, "..", ".."))
    path = os.path.join(benchmark_root, "run_benchmark.py")
    spec = importlib.util.spec_from_file_location("run_benchmark_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


@pytest.mark.parametrize("exc_cls", [SystemExit, KeyboardInterrupt])
def test_signal_style_exceptions_still_restore(tmp_path, exc_cls):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_bytes(ORIGINAL_CONTENT)
    aside = tmp_path / "CLAUDE.md.example"

    with pytest.raises(exc_cls):
        with SystemClaudeMdSwap(claude_dir=tmp_path):
            assert claude_md.read_bytes() == b""
            assert aside.read_bytes() == ORIGINAL_CONTENT
            raise exc_cls()

    assert claude_md.read_bytes() == ORIGINAL_CONTENT
    assert not aside.exists()


def test_signal_handler_raises_systemexit_and_resets_handlers():
    """The shutdown handler must raise SystemExit so `with` blocks unwind.

    Resetting to SIG_DFL + os.kill terminated the process synchronously and
    skipped __exit__, leaving CLAUDE.md.example aside. SystemExit propagates
    through context managers cleanly.
    """
    rb = _load_run_benchmark()

    prior_int = signal.getsignal(signal.SIGINT)
    prior_term = signal.getsignal(signal.SIGTERM)
    try:
        rb._install_signal_handlers()
        assert signal.getsignal(signal.SIGINT) is rb._handle_shutdown_signal
        assert signal.getsignal(signal.SIGTERM) is rb._handle_shutdown_signal

        with patch.object(rb, "shutdown_all") as mock_shutdown:
            with pytest.raises(SystemExit) as excinfo:
                rb._handle_shutdown_signal(signal.SIGINT, None)
            assert excinfo.value.code == 128 + signal.SIGINT
            mock_shutdown.assert_called_once()

        # Handler resets itself to SIG_DFL so a second signal terminates raw.
        assert signal.getsignal(signal.SIGINT) == signal.SIG_DFL
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
    finally:
        signal.signal(signal.SIGINT, prior_int)
        signal.signal(signal.SIGTERM, prior_term)


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

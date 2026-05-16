"""
Workspace isolation for benchmark runs.

Each benchmark run gets an isolated workspace containing:
- A copy of the app fixture (the codebase Claude works in)
- The workflow document injected based on (format, source):
    - no-workflow:                no workflow placed or prepended (baseline,
                                  source-agnostic — emitted once per case).
    - {plain-text,markdown,ape} + source="claude-md":
                                  workflow content placed as ``CLAUDE.md`` in
                                  the workspace root.
    - {plain-text,markdown,ape} + source="prompt":
                                  workflow content prepended to the prompt;
                                  nothing placed in the workspace.
    - adhoc-xml + source="claude-md":
                                  fixture's own ``CLAUDE.md`` and
                                  ``.claude/bivvy-dev-workflow.md`` are kept
                                  untouched.
    - adhoc-xml + source="prompt":
                                  fixture's ``CLAUDE.md`` is stripped;
                                  ``.claude/bivvy-dev-workflow.md`` is kept;
                                  the runner prepends the literal preamble
                                  ``Use @.claude/bivvy-dev-workflow.md while
                                  working on the following:`` to the prompt.
- Fixture workflow files stripped for formats/sources that need a clean slate.
- A clean git repo with one initial commit.
- A scrubbed environment preventing context leakage.

Public API
----------
BenchmarkEnvironment  — manages workspace lifecycle and isolation.
WorkspaceState        — frozen snapshot of workspace after a run.
PromptInjection       — value object describing prompt-side workflow injection.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


# Environment variables safe to pass through to the isolated session.
_PASSTHROUGH_VARS = {
    "PATH",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "SHELL",
    "USER",
    "LOGNAME",
    # Auth
    "ANTHROPIC_API_KEY",
    "CLAUDE_API_KEY",
    "CLAUDE_CODE_API_KEY",
    # Runtime (needed if fixture apps use them)
    "NODE_PATH",
    "NVM_DIR",
    "PYENV_ROOT",
}

# Environment variables explicitly excluded — even if present, never pass through.
_EXCLUDED_VARS = {
    "CLAUDECODE",
    "PWD",
}

# Formats whose workflow content can be placed in CLAUDE.md when
# source="claude-md". For adhoc-xml, the fixture's own files are kept
# instead — see _kept_fixture_files() below.
_CLAUDE_MD_CONTENT_FORMATS = {
    "plain-text", "markdown", "structured-md", "ape",
}

# Hardcoded preamble inserted into the prompt for adhoc-xml + source="prompt".
# The path is hardcoded by design — adhoc-xml's workflow_files entry is just
# a path reference, not content to read.
_ADHOC_XML_PROMPT_PREAMBLE = (
    "Use @.claude/bivvy-dev-workflow.md while working on the following:"
)


def _place_claude_md(workflow_format: str, workflow_source: str) -> bool:
    """Return True when the runner should write the workflow into ``CLAUDE.md``.

    Source ``"prompt"`` never writes to ``CLAUDE.md`` — the workflow lives in
    the prompt instead. ``no-workflow`` never writes. ``adhoc-xml`` is special:
    it keeps the fixture's own ``CLAUDE.md`` rather than overwriting it (see
    ``_kept_fixture_files``).
    """
    if workflow_source != "claude-md":
        return False
    return workflow_format in _CLAUDE_MD_CONTENT_FORMATS


def _kept_fixture_files(
    workflow_format: str,
    workflow_source: str,
    fixture_workflow_files: list[str] | None,
) -> list[str]:
    """Return the subset of fixture workflow files that should be preserved.

    All other fixture workflow files are stripped from the workspace before
    injection.

    - For ``adhoc-xml`` + ``claude-md``: keep everything (today's behaviour).
    - For ``adhoc-xml`` + ``prompt``:    keep everything except ``CLAUDE.md``
                                         (the prompt preamble replaces it).
    - For every other (format, source):  keep nothing.
    """
    if not fixture_workflow_files:
        return []
    if workflow_format != "adhoc-xml":
        return []
    if workflow_source == "claude-md":
        return list(fixture_workflow_files)
    if workflow_source == "prompt":
        return [p for p in fixture_workflow_files if p != "CLAUDE.md"]
    return []


@dataclass(frozen=True)
class PromptInjection:
    """Workflow content destined for the user-facing prompt.

    The runner concatenates ``preamble`` with the user prompt. When
    ``divider`` is true, the standard ``\\n\\n---\\n\\nUser task:\\n`` divider
    is inserted between the preamble and the prompt; otherwise the two are
    joined by a single blank line because the preamble already provides its
    own framing (used for adhoc-xml + source="prompt").
    """
    preamble: str
    divider: bool

# ---------------------------------------------------------------------------
# Isolation guard script
# ---------------------------------------------------------------------------
# Written to .claude/scripts/guard.py at setup time and invoked by
# PreToolUse hooks.  Blocks tool calls that would let the agent discover
# benchmark infrastructure (git history, guard script, settings files, env vars).
#
# Exit 0 = allow, exit 2 + stderr message = block.

_GUARD_SCRIPT = r'''#!/usr/bin/env python3
"""Benchmark isolation guard — blocks tool calls that expose benchmark context."""
import json, re, sys

def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool = data.get("tool_name", "")
    inp = data.get("tool_input", {})
    reason = None

    if tool == "Bash":
        reason = _check_bash(inp.get("command", ""))
    elif tool == "Read":
        reason = _check_path(inp.get("file_path", ""))
    elif tool in ("Grep", "Glob"):
        reason = _check_path(inp.get("path", ""))

    if reason:
        print(reason, file=sys.stderr)
        sys.exit(2)

def _check_bash(cmd):
    c = cmd.strip()

    # Block environment variable inspection (reveals HOME override, etc.)
    if re.search(r'(?:^|[;&|]\s*)(?:env|printenv)\s*(?:$|[|;&>\s])', c):
        return "Environment inspection is not available in this workspace"

    # Block guard script access
    if ".claude/scripts/guard.py" in c:
        return "Access to .claude/scripts/guard.py is restricted"

    # Block .claude/settings access via bash
    if re.search(r"\.claude/settings", c):
        return "Access to .claude/settings files is restricted"

    # Block git show (can view any commit content)
    if re.search(r"\bgit\s+show\b", c):
        return "git show is not available — use git diff for working tree changes"

    # Block git reflog (reveals history manipulation)
    if re.search(r"\bgit\s+reflog\b", c):
        return "git reflog is not available"

    # git log: allow only single-commit views
    if re.search(r"\bgit\s+log\b", c):
        has_limit_1 = bool(re.search(r"-(?:1|n\s*1)\b|--max-count[=\s]+1\b", c))
        if not has_limit_1:
            return "git log is restricted to a single commit — use 'git log -1'"
        if "--all" in c:
            return "git log --all is not available"

    # git diff with historical refs
    m = re.search(r"\bgit\s+diff\b(.*)", c)
    if m:
        rest = m.group(1)
        if re.search(r"HEAD[~^]", rest):
            return "git diff with historical refs is restricted"
        if "initial-state" in rest:
            return "git diff against initial-state is restricted"

    # Block .git/ internal access via shell commands
    if re.search(r"(?:cat|less|head|tail|ls|find|tree)\s+.*\.git/", c):
        return "Direct .git/ access is restricted"

    # Block parent directory traversal
    if re.search(r"\.\.[/\\]", c):
        return "Parent directory traversal is restricted"

    return None

def _check_path(path):
    if not path:
        return None
    if ".claude/scripts/guard.py" in path:
        return "Access to .claude/scripts/guard.py is restricted"
    if re.search(r"(?:^|/)\.claude/settings", path):
        return "Access to .claude/settings files is restricted"
    if re.search(r"(?:^|/)\.git/", path):
        return "Direct .git/ access is restricted"
    if path.startswith("..") or "/../" in path:
        return "Parent directory traversal is restricted"
    return None

if __name__ == "__main__":
    main()
'''

# Hook configuration added to .claude/settings.local.json so PreToolUse
# hooks invoke the guard script for Bash, Read, Grep, and Glob calls.
_HOOK_ENTRY = {"type": "command", "command": "python3 .claude/scripts/guard.py"}

_HOOKS_CONFIG = {
    "PreToolUse": [
        {"matcher": "Bash", "hooks": [_HOOK_ENTRY]},
        {"matcher": "Read", "hooks": [_HOOK_ENTRY]},
        {"matcher": "Grep", "hooks": [_HOOK_ENTRY]},
        {"matcher": "Glob", "hooks": [_HOOK_ENTRY]},
    ],
}


@dataclass(frozen=True)
class BaselineMetrics:
    """Functional baseline metrics captured by running cargo commands at setup time.

    These establish the fixture's starting state so evaluators can distinguish
    "the agent barely improved coverage" from "the fixture started low."
    """
    cargo_test_exit_code: int | None = None
    cargo_test_stdout: str = ""
    cargo_test_stderr: str = ""
    cargo_build_exit_code: int | None = None
    cargo_build_stderr: str = ""
    cargo_fmt_exit_code: int | None = None
    cargo_fmt_stdout: str = ""       # non-empty = unformatted files
    cargo_clippy_exit_code: int | None = None
    cargo_clippy_stderr: str = ""
    cargo_llvm_cov_exit_code: int | None = None
    cargo_llvm_cov_stdout: str = ""
    cargo_llvm_cov_stderr: str = ""
    test_count: int | None = None    # parsed from cargo test output
    coverage_pct: float | None = None  # parsed from llvm-cov output


@dataclass(frozen=True)
class SetupSnapshot:
    """Workspace state immediately after setup, before the LLM runs."""
    file_list: tuple[str, ...] = ()
    git_log: str = ""
    git_status: str = ""
    claude_md_content: str | None = None
    baseline: BaselineMetrics | None = None


@dataclass(frozen=True)
class WorkspaceState:
    """Snapshot of workspace state after a benchmark run."""
    git_log: str = ""
    modified_files: list[str] = ()  # type: ignore[assignment]
    git_status: str = ""
    committed_files: list[str] = ()  # type: ignore[assignment]
    full_diff: str = ""
    before: SetupSnapshot | None = None


def _truncate(s: str, max_len: int = 0) -> str:
    """Return the string as-is (no truncation).

    The *max_len* parameter is kept for call-site compatibility but is
    ignored — we always store the complete output so that state.json
    is never missing data.
    """
    return s


def _parse_test_count(cargo_test_stdout: str) -> int | None:
    """Extract total test count from ``cargo test`` output.

    Looks for the summary line: ``test result: ok. 42 passed; 0 failed; ...``
    """
    m = re.search(r"(\d+)\s+passed", cargo_test_stdout)
    if m:
        return int(m.group(1))
    return None


def _parse_coverage_pct(llvm_cov_stdout: str) -> float | None:
    """Extract line coverage percentage from ``cargo llvm-cov`` output.

    Looks for patterns like ``Total: 36.54%`` or ``TOTAL ... 36.54%``.
    """
    # Try "TOTAL" line format (e.g., "TOTAL  100  200  36.54%")
    m = re.search(r"TOTAL\s+.*?([\d.]+)%", llvm_cov_stdout)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    # Fallback: any "XX.XX%" at end of a line
    m = re.search(r"([\d.]+)%\s*$", llvm_cov_stdout, re.MULTILINE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


class BaselineCache:
    """Persistent cache for fixture baseline metrics.

    Baselines are a property of the fixture's source code, not the workflow
    format or prompt.  This cache computes them once per fixture and stores
    the result in the benchmark output directory.  Subsequent benchmark
    runs load the cached result instead of re-running 5 cargo commands
    per variant.

    Invalidation is by git HEAD — if the fixture's HEAD commit changes, the
    cache is stale and will be recomputed.
    """

    CACHE_VERSION = 1

    def __init__(self, output_dir: Path | None = None, timeout: int = 300):
        self._output_dir = output_dir
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Fingerprinting
    # ------------------------------------------------------------------

    @staticmethod
    def fingerprint(app_path: Path) -> str:
        """Return a content fingerprint for invalidation.

        Uses git HEAD if the fixture is a git repo; otherwise hashes
        Cargo.toml + sorted list of .rs file paths and sizes.
        """
        git_dir = app_path / ".git"
        if git_dir.exists():
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=app_path,
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        # Fallback: hash key files
        h = hashlib.sha256()
        cargo_toml = app_path / "Cargo.toml"
        if cargo_toml.is_file():
            h.update(cargo_toml.read_bytes())
        for rs_file in sorted(app_path.rglob("*.rs")):
            try:
                rel = rs_file.relative_to(app_path)
                # Skip target/ directory
                if str(rel).startswith("target"):
                    continue
                h.update(str(rel).encode())
                h.update(str(rs_file.stat().st_size).encode())
            except (OSError, ValueError):
                continue
        return h.hexdigest()

    # ------------------------------------------------------------------
    # Cache I/O
    # ------------------------------------------------------------------

    def cache_path(self, app_path: Path) -> Path:
        if self._output_dir is not None:
            return self._output_dir / app_path.name / "baseline.json"
        # Fallback: benchmark output directory relative to app fixture
        return app_path.parent.parent.parent / "output" / app_path.name / "baseline.json"

    def load(self, app_path: Path) -> BaselineMetrics | None:
        """Load cached baseline if it exists and the fingerprint matches."""
        path = self.cache_path(app_path)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        if data.get("version") != self.CACHE_VERSION:
            return None
        if data.get("fingerprint") != self.fingerprint(app_path):
            return None

        metrics = data.get("baseline")
        if metrics is None:
            return None

        return BaselineMetrics(**metrics)

    def save(self, app_path: Path, baseline: BaselineMetrics) -> None:
        """Write baseline and fingerprint to the cache file."""
        data = {
            "version": self.CACHE_VERSION,
            "fingerprint": self.fingerprint(app_path),
            "baseline": asdict(baseline),
        }
        path = self.cache_path(app_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------

    def compute(self, app_path: Path) -> BaselineMetrics | None:
        """Run baseline commands in a temporary workspace and cache the result.

        Returns None if the fixture has no Cargo.toml.  Otherwise runs the
        full cargo baseline suite in a disposable clone, caches the result,
        and returns it.
        """
        if not (app_path / "Cargo.toml").is_file():
            return None

        logger = logging.getLogger("bench.baseline_cache")
        logger.info("computing baseline for %s", app_path.name)

        # Create a temporary workspace to run cargo commands in.
        # We clone (or copy) the fixture so we don't pollute it with
        # build artifacts.
        workspace = Path(tempfile.mkdtemp(prefix="baseline-")).resolve()
        try:
            git_dir = app_path / ".git"
            if git_dir.exists():
                subprocess.run(
                    ["git", "clone", "--local", str(app_path), str(workspace)],
                    capture_output=True, text=True, timeout=300, check=True,
                )
            else:
                shutil.copytree(
                    app_path, workspace,
                    symlinks=False, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git"),
                )

            baseline = self._run_baseline(workspace)
            if baseline is not None:
                self.save(app_path, baseline)
                logger.info(
                    "baseline cached for %s: tests=%s (exit %s), build exit %s, "
                    "fmt exit %s, clippy exit %s, coverage=%s",
                    app_path.name,
                    baseline.test_count, baseline.cargo_test_exit_code,
                    baseline.cargo_build_exit_code,
                    baseline.cargo_fmt_exit_code,
                    baseline.cargo_clippy_exit_code,
                    baseline.coverage_pct,
                )
            return baseline
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def load_or_compute(self, app_path: Path) -> BaselineMetrics | None:
        """Load from cache if valid, otherwise compute and cache."""
        cached = self.load(app_path)
        if cached is not None:
            logger = logging.getLogger("bench.baseline_cache")
            logger.info(
                "loaded cached baseline for %s: tests=%s, coverage=%s",
                app_path.name, cached.test_count, cached.coverage_pct,
            )
            return cached
        return self.compute(app_path)

    def _run_baseline(self, workspace: Path) -> BaselineMetrics:
        """Run cargo commands in *workspace* and return metrics."""
        test_result = _run_cargo_cmd(workspace, "test", "--no-fail-fast", timeout=self._timeout)
        build_result = _run_cargo_cmd(workspace, "build", "--all-targets", "--all-features", timeout=self._timeout)
        fmt_result = _run_cargo_cmd(workspace, "fmt", "--check", timeout=self._timeout)
        clippy_result = _run_cargo_cmd(workspace, "clippy", "--all-targets", "--", "-D", "warnings", timeout=self._timeout)
        cov_result = _run_cargo_cmd(workspace, "llvm-cov", "--summary-only", timeout=self._timeout)

        test_count = _parse_test_count(test_result.stdout) if test_result else None
        coverage_pct = _parse_coverage_pct(cov_result.stdout) if cov_result else None

        return BaselineMetrics(
            cargo_test_exit_code=test_result.returncode if test_result else None,
            cargo_test_stdout=test_result.stdout if test_result else "",
            cargo_test_stderr=test_result.stderr if test_result else "",
            cargo_build_exit_code=build_result.returncode if build_result else None,
            cargo_build_stderr=build_result.stderr if build_result else "",
            cargo_fmt_exit_code=fmt_result.returncode if fmt_result else None,
            cargo_fmt_stdout=fmt_result.stdout if fmt_result else "",
            cargo_clippy_exit_code=clippy_result.returncode if clippy_result else None,
            cargo_clippy_stderr=clippy_result.stderr if clippy_result else "",
            cargo_llvm_cov_exit_code=cov_result.returncode if cov_result else None,
            cargo_llvm_cov_stdout=cov_result.stdout if cov_result else "",
            cargo_llvm_cov_stderr=cov_result.stderr if cov_result else "",
            test_count=test_count,
            coverage_pct=coverage_pct,
        )


def _run_cargo_cmd(
    workspace: Path, *args: str, timeout: int = 300,
) -> subprocess.CompletedProcess | None:
    """Run a cargo subcommand, returning the CompletedProcess or None on error.

    Module-level helper shared by BaselineCache and BenchmarkEnvironment.
    """
    import signal as _signal
    proc = None
    try:
        proc = subprocess.Popen(
            ["cargo", *args],
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(
            args=proc.args,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
        )
    except subprocess.TimeoutExpired:
        if proc is not None:
            try:
                os.killpg(proc.pid, _signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            proc.wait(timeout=5)
        return None
    except (FileNotFoundError, OSError):
        return None


class BenchmarkEnvironment:
    """Manages isolated workspace directories for benchmark runs."""

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        skip_baseline: bool = False,
        precomputed_baselines: dict[str, BaselineMetrics] | None = None,
    ):
        self.base_dir = base_dir
        self.skip_baseline = skip_baseline
        # Map of app name -> pre-loaded BaselineMetrics.
        # When set, capture_setup_state uses these instead of re-running
        # cargo commands per variant.
        self._baselines: dict[str, BaselineMetrics] = precomputed_baselines or {}

    def setup(
        self,
        app_path: Path,
        workflow_path: Path,
        workflow_format: str,
        workflow_source: str,
        fixture_workflow_files: list[str] | None = None,
        *,
        case_id: str = "",
    ) -> Path:
        """Create an isolated workspace for a benchmark run.

        If the app fixture is a git repo, uses ``git clone --local`` to
        create the workspace with hardlinked objects — orders of magnitude
        faster than copying the entire tree for large fixtures.

        Falls back to ``shutil.copytree`` + ``git init`` for fixtures that
        are not git repos.

        *fixture_workflow_files* lists paths (relative to workspace root)
        that belong to the app fixture's own workflow instructions. Which
        of those files are kept depends on (format, source) — see
        ``_kept_fixture_files``.

        Returns the workspace path.
        """
        logger = logging.getLogger("bench.environment")
        label = case_id or app_path.name
        if self._is_git_repo(app_path):
            logger.info("%s: setting up workspace via git clone", label)
            workspace = self._setup_via_clone(
                app_path, workflow_path, workflow_format, workflow_source,
                fixture_workflow_files,
            )
        else:
            logger.info("%s: setting up workspace via copy", label)
            workspace = self._setup_via_copy(
                app_path, workflow_path, workflow_format, workflow_source,
                fixture_workflow_files,
            )
        logger.info("%s: workspace ready", label)
        return workspace

    def _is_git_repo(self, path: Path) -> bool:
        """Return True if *path* is the root of a git repository."""
        if not path.is_dir():
            return False
        result = self._git(path, "rev-parse", "--git-dir")
        return result == ".git"

    def _setup_via_clone(
        self,
        app_path: Path,
        workflow_path: Path,
        workflow_format: str,
        workflow_source: str,
        fixture_workflow_files: list[str] | None = None,
    ) -> Path:
        """Fast path: workspace from ``git clone --local``.

        Hardlinks the object store so the clone is nearly free on disk,
        then squashes all history into a single orphan commit so the LLM
        cannot discover fixture context through ``git log``, ``git show``,
        or ``git reflog``.  Layers benchmark-specific files on top and
        commits everything as a single ``Initial commit``.
        """
        workspace = Path(tempfile.mkdtemp(
            prefix="bench-",
            dir=self.base_dir,
        )).resolve()
        shutil.rmtree(workspace)

        subprocess.run(
            ["git", "clone", "--local", str(app_path), str(workspace)],
            capture_output=True, text=True, timeout=300, check=True,
        )

        # --- Squash fixture history into a single orphan commit ----------
        self._git(workspace, "remote", "remove", "origin")

        # Delete ALL tags — git clone copies them and they keep old
        # history reachable even after the orphan branch trick.
        tags = self._git(workspace, "tag", "-l")
        for tag in tags.splitlines():
            tag = tag.strip()
            if tag:
                self._git(workspace, "tag", "-d", tag)

        # Delete ALL branches except the current one.
        current = self._git(workspace, "rev-parse", "--abbrev-ref", "HEAD")
        branches = self._git(workspace, "branch", "--format=%(refname:short)")
        for branch in branches.splitlines():
            branch = branch.strip()
            if branch and branch != current:
                self._git(workspace, "branch", "-D", branch)

        self._git(workspace, "checkout", "--orphan", "_squashed")

        # Remove the old branch ref (skip if we were on a detached HEAD).
        if current and current != "HEAD":
            self._git(workspace, "branch", "-D", current)

        # --- Set git identity in the repo --------------------------------
        self._git(workspace, "config", "user.name", "benchmark")
        self._git(workspace, "config", "user.email", "benchmark@localhost")

        # --- Layer benchmark files before committing ---------------------
        self._inject_benchmark_files(
            workspace, workflow_path, workflow_format, workflow_source,
            fixture_workflow_files,
        )

        # Stage removals of stripped fixture workflow files (must use git rm
        # for tracked files that were deleted from the worktree)
        if fixture_workflow_files:
            kept = set(_kept_fixture_files(
                workflow_format, workflow_source, fixture_workflow_files,
            ))
            for rel in fixture_workflow_files:
                if rel in kept:
                    continue
                if not (workspace / rel).exists():
                    self._git(workspace, "rm", "--quiet", "--ignore-unmatch", rel)

        staged = []
        if _place_claude_md(workflow_format, workflow_source):
            staged.append("CLAUDE.md")
        staged.append(".claude/settings.local.json")
        staged.append(".claude/scripts/guard.py")
        if (workspace / ".gitignore").exists():
            staged.append(".gitignore")
        self._git(workspace, "add", "--force", "--", *staged)

        # Single commit: all fixture files + benchmark files together.
        self._git(workspace, "commit", "-m", "Initial commit",
                  "--allow-empty")

        self._git(workspace, "branch", "-m", "main")
        self._git(workspace, "tag", "initial-state")

        # Purge reflog and GC to remove unreachable fixture history.
        self._git(workspace, "reflog", "expire", "--expire=now", "--all")
        self._git(workspace, "gc", "--prune=now")

        return workspace

    def _setup_via_copy(
        self,
        app_path: Path,
        workflow_path: Path,
        workflow_format: str,
        workflow_source: str,
        fixture_workflow_files: list[str] | None = None,
    ) -> Path:
        """Slow fallback: copy the fixture tree and create a fresh repo."""
        workspace = Path(tempfile.mkdtemp(
            prefix="bench-",
            dir=self.base_dir,
        )).resolve()

        if app_path.is_dir():
            shutil.copytree(
                app_path, workspace,
                symlinks=False,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".git"),
            )
        else:
            shutil.copy2(app_path, workspace / app_path.name)

        self._inject_benchmark_files(
            workspace, workflow_path, workflow_format, workflow_source,
            fixture_workflow_files,
        )

        self._git(workspace, "init")
        self._git(workspace, "config", "user.name", "benchmark")
        self._git(workspace, "config", "user.email", "benchmark@localhost")
        self._git(workspace, "add", "-A")
        self._git(workspace, "commit", "-m", "Initial commit")
        self._git(workspace, "tag", "initial-state")

        return workspace

    def _inject_benchmark_files(
        self,
        workspace: Path,
        workflow_path: Path,
        workflow_format: str,
        workflow_source: str,
        fixture_workflow_files: list[str] | None = None,
    ) -> None:
        """Write workflow, settings, and guard script into *workspace*.

        Strips any fixture workflow files that the (format, source) pair does
        not preserve, then optionally writes the workflow content into
        ``CLAUDE.md``.
        """
        kept = set(_kept_fixture_files(
            workflow_format, workflow_source, fixture_workflow_files,
        ))
        if fixture_workflow_files:
            for rel_path in fixture_workflow_files:
                if rel_path in kept:
                    continue
                target = workspace / rel_path
                if target.is_file():
                    target.unlink()
                    # Remove parent dir if now empty (e.g. .claude/)
                    parent = target.parent
                    if parent != workspace and parent.is_dir() and not any(parent.iterdir()):
                        parent.rmdir()

        if _place_claude_md(workflow_format, workflow_source):
            workflow_content = workflow_path.read_text(encoding="utf-8")
            (workspace / "CLAUDE.md").write_text(workflow_content, encoding="utf-8")

        settings_dir = workspace / ".claude"
        settings_dir.mkdir(exist_ok=True)
        (settings_dir / "settings.local.json").write_text(json.dumps({
            "permissions": {
                "allow": [
                    "Bash(*)", "Read(*)", "Write(*)", "Edit(*)",
                    "Glob(*)", "Grep(*)",
                ],
                "deny": [],
            },
            "hooks": _HOOKS_CONFIG,
        }, indent=2), encoding="utf-8")

        # Write the isolation guard script — invoked by PreToolUse hooks
        scripts_dir = settings_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        guard_path = scripts_dir / "guard.py"
        guard_path.write_text(_GUARD_SCRIPT, encoding="utf-8")
        os.chmod(guard_path, 0o755)

    def build_env(self, workspace: Path) -> dict[str, str]:
        """Build a scrubbed environment dict for the CLI subprocess.

        The resulting env contains only whitelisted passthrough vars plus
        overrides for git config and memory isolation.
        """
        env: dict[str, str] = {}

        for key in _PASSTHROUGH_VARS:
            if key in _EXCLUDED_VARS:
                continue
            val = os.environ.get(key)
            if val is not None:
                env[key] = val

        # Block system-level git config (/etc/gitconfig) from affecting runs
        env["GIT_CONFIG_NOSYSTEM"] = "1"

        # Disable auto-memory so the agent cannot persist or load memories
        # across benchmark runs.  Without this, Claude Code's -p mode loads
        # auto memory by default and could contaminate future results.
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"

        return env

    def get_session_dir(self, workspace: Path) -> Path:
        """Return the Claude Code session directory for this workspace."""
        encoded = str(workspace.resolve()).replace("/", "-").replace(" ", "-")
        return Path.home() / ".claude" / "projects" / encoded

    def get_workflow_content(
        self,
        workflow_path: Path,
        workflow_format: str,
        workflow_source: str,
    ) -> PromptInjection | None:
        """Return prompt-side workflow injection for this (format, source).

        Returns ``None`` for ``source="claude-md"`` (workflow lives in
        ``CLAUDE.md``) and for ``no-workflow`` (baseline, no injection).

        For ``source="prompt"``:
          - markdown/ape/plain-text/structured-md: returns the fixture
            workflow content with the standard ``---``/``User task:`` divider.
          - adhoc-xml: returns the hardcoded preamble that points the agent
            at ``.claude/bivvy-dev-workflow.md`` (no divider — the preamble
            already frames the prompt).
        """
        if workflow_format == "no-workflow":
            return None
        if workflow_source != "prompt":
            return None
        if workflow_format == "adhoc-xml":
            return PromptInjection(
                preamble=_ADHOC_XML_PROMPT_PREAMBLE,
                divider=False,
            )
        return PromptInjection(
            preamble=workflow_path.read_text(encoding="utf-8"),
            divider=True,
        )

    def capture_setup_state(self, workspace: Path, *, case_id: str = "", app_name: str = "") -> SetupSnapshot:
        """Capture a snapshot of the workspace after all setup, before the LLM runs.

        This must be called AFTER ``setup()`` completes and BEFORE the prompt
        is submitted.  The file list is a full filesystem walk — not
        ``git ls-tree`` — so it reflects every file on disk that the LLM
        can encounter.

        If a pre-computed baseline exists for *app_name*, it is used directly
        instead of re-running cargo commands.  This avoids redundant 5-minute
        compilations across variants that share the same fixture code.
        """
        logger = logging.getLogger("bench.environment")
        label = case_id or workspace.name
        logger.info("%s: capturing setup state", label)

        # Use pre-loaded baseline if available; otherwise fall back to
        # computing it in the workspace (legacy path).
        baseline: BaselineMetrics | None = None
        if app_name and app_name in self._baselines:
            baseline = self._baselines[app_name]
            logger.info(
                "%s: using cached baseline: tests=%s (exit %s), build exit %s, "
                "fmt exit %s, clippy exit %s, coverage=%s",
                label,
                baseline.test_count, baseline.cargo_test_exit_code,
                baseline.cargo_build_exit_code,
                baseline.cargo_fmt_exit_code,
                baseline.cargo_clippy_exit_code,
                baseline.coverage_pct,
            )
        else:
            baseline = self._capture_baseline(workspace, case_id=label)
            if baseline is not None:
                logger.info(
                    "%s: baseline tests=%s (exit %s), build exit %s, fmt exit %s, clippy exit %s, coverage=%s",
                    label,
                    baseline.test_count, baseline.cargo_test_exit_code,
                    baseline.cargo_build_exit_code,
                    baseline.cargo_fmt_exit_code,
                    baseline.cargo_clippy_exit_code,
                    baseline.coverage_pct,
                )
            else:
                logger.info("%s: no baseline (no Cargo.toml)", label)

        # Filesystem walk — captures every file on disk so the snapshot
        # reflects exactly what the LLM can encounter.
        file_list: list[str] = []
        for dirpath, dirnames, filenames in os.walk(workspace):
            rel_dir = os.path.relpath(dirpath, workspace)
            for fname in filenames:
                if rel_dir == ".":
                    file_list.append(fname)
                else:
                    file_list.append(os.path.join(rel_dir, fname))
        file_list.sort()

        git_log = self._git(workspace, "log", "--oneline", "--all")
        git_status = self._git(workspace, "status", "--porcelain")

        claude_md = workspace / "CLAUDE.md"
        claude_md_content = claude_md.read_text(encoding="utf-8") if claude_md.is_file() else None

        logger.info("%s: setup state captured, %d files on disk", label, len(file_list))

        return SetupSnapshot(
            file_list=tuple(file_list),
            git_log=git_log,
            git_status=git_status,
            claude_md_content=claude_md_content,
            baseline=baseline,
        )

    def _capture_baseline(self, workspace: Path, *, case_id: str = "") -> BaselineMetrics | None:
        """Run cargo commands to capture the fixture's functional baseline.

        Returns None if the workspace has no Cargo.toml (not a Rust project).

        This is the legacy per-workspace path.  Prefer pre-loading baselines
        via ``BaselineCache`` and passing them as ``precomputed_baselines``
        to the constructor — that avoids redundant compilations.
        """
        if self.skip_baseline or not (workspace / "Cargo.toml").is_file():
            return None

        logger = logging.getLogger("bench.environment")
        label = case_id or workspace.name
        logger.info("%s: capturing baseline metrics (uncached — consider using BaselineCache)", label)

        test_result = _run_cargo_cmd(workspace, "test", "--no-fail-fast")
        build_result = _run_cargo_cmd(workspace, "build", "--all-targets", "--all-features")
        fmt_result = _run_cargo_cmd(workspace, "fmt", "--check")
        clippy_result = _run_cargo_cmd(workspace, "clippy", "--all-targets", "--", "-D", "warnings")
        cov_result = _run_cargo_cmd(workspace, "llvm-cov", "--summary-only")

        test_count = _parse_test_count(test_result.stdout) if test_result else None
        coverage_pct = _parse_coverage_pct(cov_result.stdout) if cov_result else None

        return BaselineMetrics(
            cargo_test_exit_code=test_result.returncode if test_result else None,
            cargo_test_stdout=test_result.stdout if test_result else "",
            cargo_test_stderr=test_result.stderr if test_result else "",
            cargo_build_exit_code=build_result.returncode if build_result else None,
            cargo_build_stderr=build_result.stderr if build_result else "",
            cargo_fmt_exit_code=fmt_result.returncode if fmt_result else None,
            cargo_fmt_stdout=fmt_result.stdout if fmt_result else "",
            cargo_clippy_exit_code=clippy_result.returncode if clippy_result else None,
            cargo_clippy_stderr=clippy_result.stderr if clippy_result else "",
            cargo_llvm_cov_exit_code=cov_result.returncode if cov_result else None,
            cargo_llvm_cov_stdout=cov_result.stdout if cov_result else "",
            cargo_llvm_cov_stderr=cov_result.stderr if cov_result else "",
            test_count=test_count,
            coverage_pct=coverage_pct,
        )

    def capture_state(self, workspace: Path, setup_snapshot: SetupSnapshot | None = None, *, case_id: str = "") -> WorkspaceState:
        """Capture a snapshot of the workspace state after a run."""
        logger = logging.getLogger("bench.environment")
        label = case_id or workspace.name
        logger.info("%s: capturing post-run workspace state", label)
        git_log = self._git(workspace, "log", "--oneline", "--all")
        git_status = self._git(workspace, "status", "--porcelain")

        modified = []
        for line in git_status.splitlines():
            line = line.strip()
            if line:
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    modified.append(parts[1])

        committed_files_raw = self._git(
            workspace, "diff", "--name-only", "--diff-filter=ACMR",
            "initial-state", "HEAD",
        )
        committed = [
            f.strip() for f in committed_files_raw.splitlines() if f.strip()
        ] if committed_files_raw else []

        # Capture the full diff of everything the LLM changed since initialization
        full_diff = self._git(workspace, "diff", "initial-state")

        logger.info(
            "%s: post-run state — %d committed files, %d modified files",
            label, len(committed), len(modified),
        )

        return WorkspaceState(
            git_log=git_log,
            modified_files=modified,
            git_status=git_status,
            committed_files=committed,
            full_diff=full_diff,
            before=setup_snapshot,
        )

    def check_memory_leak(self, workspace: Path) -> list[str]:
        """Return paths to any memory files created during the run.

        Should be called after the CLI exits but before teardown.  If the
        list is non-empty, memory isolation failed and the run should be
        flagged.
        """
        memory_files: list[str] = []

        # Check under the real HOME's project memory directory
        encoded = str(workspace.resolve()).replace("/", "-").replace(" ", "-")
        projects_dir = Path.home() / ".claude" / "projects" / encoded
        if projects_dir.is_dir():
            for p in projects_dir.rglob("memory*"):
                if p.is_file() or p.is_dir():
                    memory_files.append(str(p.relative_to(Path.home())))

        # Check workspace-level .claude for any memory files
        workspace_claude = workspace / ".claude"
        if workspace_claude.is_dir():
            for p in workspace_claude.rglob("memory*"):
                if p.is_file() or p.is_dir():
                    memory_files.append(str(p.relative_to(workspace)))

        return memory_files

    def teardown(self, workspace: Path) -> None:
        """Remove the workspace directory."""
        try:
            shutil.rmtree(workspace)
        except OSError:
            pass

    def _git(self, workspace: Path, *args: str) -> str:
        """Run a git command in the workspace directory."""
        try:
            result = subprocess.run(
                ["git"] + list(args),
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

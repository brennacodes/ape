"""
Workspace isolation for benchmark runs.

Each benchmark run gets an isolated workspace containing:
- A copy of the app fixture (the codebase Claude works in)
- The workflow document injected based on format:
    - no-workflow: no workflow placed or prepended (baseline)
    - plain-text: not placed in workspace (prepended to prompt by the runner)
    - markdown, ape: placed as CLAUDE.md in the workspace root
    - adhoc-xml: fixture's own workflow files are kept untouched
- Fixture workflow files stripped for formats that need a clean slate
- A clean git repo with one initial commit
- A scrubbed environment preventing context leakage

Public API
----------
BenchmarkEnvironment  — manages workspace lifecycle and isolation.
WorkspaceState        — frozen snapshot of workspace after a run.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
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

# Workflow formats that replace the fixture's CLAUDE.md with a benchmark workflow.
# plain-text is excluded because it gets prepended to the prompt instead.
# adhoc-xml is excluded because the fixture's own workflow files are kept as-is.
_CLAUDE_MD_FORMATS = {"markdown", "structured-md", "ape"}

# Formats that keep the fixture's own workflow files untouched.
# All other formats strip fixture workflow files before injecting benchmark files.
_KEEP_FIXTURE_WORKFLOWS = {"adhoc-xml"}

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


class BenchmarkEnvironment:
    """Manages isolated workspace directories for benchmark runs."""

    def __init__(self, base_dir: Optional[Path] = None, skip_baseline: bool = False):
        self.base_dir = base_dir
        self.skip_baseline = skip_baseline

    def setup(
        self,
        app_path: Path,
        workflow_path: Path,
        workflow_format: str,
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
        that belong to the app fixture's own workflow instructions.  These
        are stripped for formats that need a clean slate, and kept only for
        formats in ``_KEEP_FIXTURE_WORKFLOWS``.

        Returns the workspace path.
        """
        logger = logging.getLogger("bench.environment")
        label = case_id or app_path.name
        if self._is_git_repo(app_path):
            logger.info("%s: setting up workspace via git clone", label)
            workspace = self._setup_via_clone(
                app_path, workflow_path, workflow_format, fixture_workflow_files,
            )
        else:
            logger.info("%s: setting up workspace via copy", label)
            workspace = self._setup_via_copy(
                app_path, workflow_path, workflow_format, fixture_workflow_files,
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
            capture_output=True, text=True, timeout=120, check=True,
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
            workspace, workflow_path, workflow_format, fixture_workflow_files,
        )

        # Stage removals of stripped fixture workflow files (must use git rm
        # for tracked files that were deleted from the worktree)
        if fixture_workflow_files and workflow_format not in _KEEP_FIXTURE_WORKFLOWS:
            for rel in fixture_workflow_files:
                if not (workspace / rel).exists():
                    self._git(workspace, "rm", "--quiet", "--ignore-unmatch", rel)

        staged = []
        if workflow_format in _CLAUDE_MD_FORMATS:
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
            workspace, workflow_path, workflow_format, fixture_workflow_files,
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
        fixture_workflow_files: list[str] | None = None,
    ) -> None:
        """Write workflow, settings, and guard script into *workspace*.

        For formats not in ``_KEEP_FIXTURE_WORKFLOWS``, any files listed in
        *fixture_workflow_files* are deleted first to prevent the fixture's
        own workflow instructions from conflicting with the benchmark workflow.
        """
        # Strip fixture workflow files for formats that need a clean slate
        if fixture_workflow_files and workflow_format not in _KEEP_FIXTURE_WORKFLOWS:
            for rel_path in fixture_workflow_files:
                target = workspace / rel_path
                if target.is_file():
                    target.unlink()
                    # Remove parent dir if now empty (e.g. .claude/)
                    parent = target.parent
                    if parent != workspace and parent.is_dir() and not any(parent.iterdir()):
                        parent.rmdir()

        if workflow_format in _CLAUDE_MD_FORMATS:
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

    def get_workflow_content(self, workflow_path: Path, workflow_format: str) -> str | None:
        """Return workflow content if it should be prepended to the prompt.

        Returns the content for plain-text format, None for formats that
        are placed as CLAUDE.md in the workspace, use the fixture's own
        workflow files, or need no workflow at all.
        """
        if workflow_format in ("no-workflow", *_CLAUDE_MD_FORMATS, *_KEEP_FIXTURE_WORKFLOWS):
            return None
        return workflow_path.read_text(encoding="utf-8")

    def capture_setup_state(self, workspace: Path, *, case_id: str = "") -> SetupSnapshot:
        """Capture a snapshot of the workspace after all setup, before the LLM runs.

        This must be called AFTER ``setup()`` completes (including any
        baseline capture) and BEFORE the prompt is submitted.  The file
        list is a full filesystem walk — not ``git ls-tree`` — so it
        reflects every file on disk that the LLM can encounter.
        """
        logger = logging.getLogger("bench.environment")
        label = case_id or workspace.name
        logger.info("%s: capturing setup state", label)

        # Run baseline FIRST so the file list includes any side-effects
        # (e.g. Cargo.lock updates, generated code) from baseline commands.
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
        """
        if self.skip_baseline or not (workspace / "Cargo.toml").is_file():
            return None

        logger = logging.getLogger("bench.environment")
        label = case_id or workspace.name
        logger.info("%s: capturing baseline metrics", label)

        test_result = self._run_cargo(workspace, "test", "--", "--no-fail-fast")
        build_result = self._run_cargo(workspace, "build", "--all-targets", "--all-features")
        fmt_result = self._run_cargo(workspace, "fmt", "--check")
        clippy_result = self._run_cargo(workspace, "clippy", "--all-targets", "--", "-D", "warnings")
        cov_result = self._run_cargo(workspace, "llvm-cov", "--summary-only")

        test_count = _parse_test_count(test_result.stdout) if test_result else None
        coverage_pct = _parse_coverage_pct(cov_result.stdout) if cov_result else None

        return BaselineMetrics(
            cargo_test_exit_code=test_result.returncode if test_result else None,
            cargo_test_stdout=_truncate(test_result.stdout, 4000) if test_result else "",
            cargo_test_stderr=_truncate(test_result.stderr, 2000) if test_result else "",
            cargo_build_exit_code=build_result.returncode if build_result else None,
            cargo_build_stderr=_truncate(build_result.stderr, 2000) if build_result else "",
            cargo_fmt_exit_code=fmt_result.returncode if fmt_result else None,
            cargo_fmt_stdout=_truncate(fmt_result.stdout, 2000) if fmt_result else "",
            cargo_clippy_exit_code=clippy_result.returncode if clippy_result else None,
            cargo_clippy_stderr=_truncate(clippy_result.stderr, 2000) if clippy_result else "",
            cargo_llvm_cov_exit_code=cov_result.returncode if cov_result else None,
            cargo_llvm_cov_stdout=_truncate(cov_result.stdout, 2000) if cov_result else "",
            cargo_llvm_cov_stderr=_truncate(cov_result.stderr, 2000) if cov_result else "",
            test_count=test_count,
            coverage_pct=coverage_pct,
        )

    def _run_cargo(
        self, workspace: Path, *args: str, timeout: int = 120,
    ) -> subprocess.CompletedProcess | None:
        """Run a cargo subcommand, returning the CompletedProcess or None on error.

        Uses start_new_session so the entire process tree (compiler, test
        binaries, etc.) can be killed on timeout.  Without this, killing
        only the parent cargo process leaves children holding pipes open,
        which causes subprocess.run's pipe-drain to block indefinitely.
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

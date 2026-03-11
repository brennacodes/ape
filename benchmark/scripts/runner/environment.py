"""
Workspace isolation for benchmark runs.

Each benchmark run gets an isolated workspace containing:
- A copy of the app fixture (the codebase Claude works in)
- The workflow document injected based on format:
    - plain-text: not placed in workspace (prepended to prompt by the runner)
    - markdown, adhoc-xml, structured-md, ape: placed as CLAUDE.md in the workspace root
- A clean git repo with one initial commit
- A scrubbed environment preventing context leakage

Public API
----------
BenchmarkEnvironment  — manages workspace lifecycle and isolation.
WorkspaceState        — frozen snapshot of workspace after a run.
"""

from __future__ import annotations

import json
import os
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
    # XDG dirs are overridden to workspace-local paths in build_env().
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
}

# Workflow formats that get placed as CLAUDE.md in the workspace.
# plain-text is excluded because it gets prepended to the prompt instead.
_CLAUDE_MD_FORMATS = {"markdown", "adhoc-xml", "structured-md", "ape"}


@dataclass(frozen=True)
class WorkspaceState:
    """Snapshot of workspace state after a benchmark run."""
    git_log: str = ""
    modified_files: list[str] = ()  # type: ignore[assignment]
    git_status: str = ""
    committed_files: list[str] = ()  # type: ignore[assignment]


class BenchmarkEnvironment:
    """Manages isolated workspace directories for benchmark runs."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir

    def setup(
        self,
        app_path: Path,
        workflow_path: Path,
        workflow_format: str,
    ) -> Path:
        """Create an isolated workspace for a benchmark run.

        1. Create a temp directory.
        2. Copy the app fixture into the workspace root.
        3. Inject the workflow based on format:
           - plain-text: skip (runner prepends to prompt)
           - others: place as CLAUDE.md
        4. Initialize a clean git repo.
        5. Write .claude/settings.local.json.
        6. Create isolated HOME directory.

        Returns the workspace path.
        """
        workspace = Path(tempfile.mkdtemp(
            prefix="bench-",
            dir=self.base_dir,
        ))

        # Copy app fixture into workspace root (excluding .git)
        if app_path.is_dir():
            shutil.copytree(
                app_path, workspace,
                symlinks=False,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".git"),
            )
        else:
            shutil.copy2(app_path, workspace / app_path.name)

        # Inject workflow based on format
        if workflow_format in _CLAUDE_MD_FORMATS:
            workflow_content = workflow_path.read_text(encoding="utf-8")
            (workspace / "CLAUDE.md").write_text(workflow_content, encoding="utf-8")

        # Write Claude project settings
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
        }, indent=2), encoding="utf-8")

        # Create isolated HOME — copy only auth credentials from real HOME
        home_dir = workspace / ".bench-home"
        home_dir.mkdir()
        home_claude = home_dir / ".claude"
        home_claude.mkdir()

        real_creds = Path.home() / ".claude" / ".credentials.json"
        if real_creds.exists():
            shutil.copy2(real_creds, home_claude / ".credentials.json")

        (home_claude / "settings.json").write_text(json.dumps({
            "permissions": {
                "allow": [
                    "Bash(*)", "Read(*)", "Write(*)", "Edit(*)",
                    "Glob(*)", "Grep(*)",
                ],
                "deny": [],
            },
        }, indent=2), encoding="utf-8")

        # Per-workspace tmp dir so parallel runs don't collide in system TMPDIR
        workspace_tmp = home_dir / "tmp"
        workspace_tmp.mkdir()

        # Git identity — prevents failures on systems requiring user.name/email
        (home_dir / ".gitconfig").write_text(
            "[user]\n    name = benchmark\n    email = benchmark@localhost\n",
            encoding="utf-8",
        )

        # Gitignore benchmark infrastructure so runtime writes
        # (session logs, project metadata) don't pollute post-run diffs
        (workspace / ".gitignore").write_text(".bench-home/\n", encoding="utf-8")

        # Git init — commit everything (app + infrastructure) as initial state
        self._git(workspace, "init")
        self._git(workspace, "add", "-A")
        self._git(workspace, "commit", "-m", "Initial fixture state")

        return workspace

    def build_env(self, workspace: Path) -> dict[str, str]:
        """Build a scrubbed environment dict for the CLI subprocess.

        The resulting env contains only whitelisted passthrough vars plus
        workspace-local overrides for HOME, TMPDIR, XDG dirs, and git config.
        """
        home_dir = workspace / ".bench-home"
        env: dict[str, str] = {}

        for key in _PASSTHROUGH_VARS:
            if key in _EXCLUDED_VARS:
                continue
            val = os.environ.get(key)
            if val is not None:
                env[key] = val

        home_str = str(home_dir)
        env["HOME"] = home_str

        # Per-workspace TMPDIR prevents collisions between parallel runs
        env["TMPDIR"] = str(home_dir / "tmp")

        # XDG dirs — point into isolated home so no subprocess reads/writes
        # shared locations like ~/.config or ~/.local/share
        env["XDG_CONFIG_HOME"] = str(home_dir / ".config")
        env["XDG_DATA_HOME"] = str(home_dir / ".local" / "share")
        env["XDG_CACHE_HOME"] = str(home_dir / ".cache")
        env["XDG_RUNTIME_DIR"] = str(home_dir / "run")

        # Block system-level git config (/etc/gitconfig) from affecting runs
        env["GIT_CONFIG_NOSYSTEM"] = "1"

        return env

    def get_session_dir(self, workspace: Path) -> Path:
        """Return the Claude Code session directory for this workspace."""
        home_dir = workspace / ".bench-home"
        encoded = str(workspace.resolve()).replace("/", "-").replace(" ", "-")
        return home_dir / ".claude" / "projects" / encoded

    def get_workflow_content(self, workflow_path: Path, workflow_format: str) -> str | None:
        """Return workflow content if it should be prepended to the prompt.

        Returns the content for plain-text format, None for formats that
        are placed as CLAUDE.md in the workspace.
        """
        if workflow_format not in _CLAUDE_MD_FORMATS:
            return workflow_path.read_text(encoding="utf-8")
        return None

    def capture_state(self, workspace: Path) -> WorkspaceState:
        """Capture a snapshot of the workspace state after a run."""
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
            workspace, "diff", "--name-only", "--diff-filter=ACDMR",
            "HEAD~1", "HEAD",
        )
        committed = [
            f.strip() for f in committed_files_raw.splitlines() if f.strip()
        ] if committed_files_raw else []

        return WorkspaceState(
            git_log=git_log,
            modified_files=modified,
            git_status=git_status,
            committed_files=committed,
        )

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

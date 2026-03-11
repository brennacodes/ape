"""
Evaluate a list of checks from a test-config against a parsed session Trace.

Public API
----------
evaluate(trace, checks, context) -> list[CheckResult]
    The main entry point. `checks` is the list of check dicts from a test-config
    YAML. `context` supplies prompt-level conditions, variables, and optionally
    workspace state and phase configuration.

CheckResult
    Dataclass holding the outcome for one check.

MetricNotResolvable
    Raised internally when a metric path cannot be resolved from the available
    data. The evaluator catches this and marks the result as skipped.

Design notes
------------
Metric resolution is the most complex part. Many metrics map directly to
tool calls in the trace (tool_call.file_read → Read tool calls). Some require
per-path bookkeeping (precedes_per_path). Phase metrics (phase.*) are resolved
by detecting phases from tool call patterns using the phase_tool_mapping
config. External state metrics (diff.*, git.*, workspace.*) are resolved from
workspace state captured after a run, with trace-based fallbacks.

The tool name mapping lives here (not in YAML) as it is runtime implementation
detail, not authoring vocabulary.
"""

from __future__ import annotations

import re
import sys
import os
from dataclasses import dataclass
from typing import Any, Optional

# Allow running from benchmark/scripts/evaluator directly (picks up trace.py
# from the sibling runner directory) as well as from the repo root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.join(_HERE, "..", "runner")
if _RUNNER not in sys.path:
    sys.path.insert(0, _RUNNER)

from trace import Trace, ToolCall  # noqa: E402 (after sys.path update)
from operators import (  # noqa: E402
    apply_transform,
    op_eq, op_neq, op_gt, op_gte, op_lt, op_lte,
    op_exists_before, op_exists_after, op_exists_between,
    op_strictly_precedes, op_strictly_ordered_subset,
    op_subset_of, op_each_preceded_by_within_N_steps,
    op_precedes_per_path, op_not_contains, op_regex_not_match,
    op_has_key, op_has_key_any, op_contains, op_contains_count_gte,
    op_first_search_broader_than_final,
)


# ---------------------------------------------------------------------------
# Tool name mapping  (implementation detail — lives in code, not YAML)
# ---------------------------------------------------------------------------
# Maps abstract metric prefixes to concrete Claude Code tool names.
# tool_call.file_create is treated as Write in v1 (distinguishing new-file vs
# existing-file writes requires pre-call workspace state — deferred to v2).

TOOL_NAME_MAP: dict[str, str] = {
    "tool_call.file_read": "Read",
    "tool_call.file_write": "Write",
    "tool_call.file_edit": "Edit",
    "tool_call.file_create": "Write",      # v1: same as file_write
    "tool_call.execute_command": "Bash",
    "tool_call.search": "Grep",
    "tool_call.glob": "Glob",
    "tool_call.ask_user": "AskUserQuestion",
    "tool_call.skill": "Skill",
}

# Sentinel used as the "target" index for task_completed (always after all events)
_TASK_COMPLETED: int = 10_000_000

# Human-readable labels for metric names used in detail messages.
_METRIC_LABELS: dict[str, str] = {
    "tool_call.file_read": "a file read (Read)",
    "tool_call.file_write": "a file write (Write)",
    "tool_call.file_edit": "a file edit (Edit)",
    "tool_call.file_create": "a file creation (Write)",
    "tool_call.execute_command": "a shell command (Bash)",
    "tool_call.search": "a search (Grep)",
    "tool_call.glob": "a file search (Glob)",
    "tool_call.ask_user": "a clarifying question",
    "tool_call.skill": "a skill invocation",
    "task_completed": "task completion",
}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    """Outcome of evaluating one check against a trace."""

    check_id: str
    phase: str
    description: str
    passed: Optional[bool]    # None → check was skipped
    skip_reason: Optional[str]  # set when passed is None
    detail: Optional[str] = None  # explanation of why the check failed/passed


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class MetricNotResolvable(Exception):
    """Raised when a metric cannot be resolved from the Trace alone."""


class UnknownOperator(Exception):
    """Raised when a condition references an operator not in the vocabulary."""


# ---------------------------------------------------------------------------
# Variable interpolation
# ---------------------------------------------------------------------------

def interpolate(value: Any, variables: dict[str, Any]) -> Any:
    """
    Replace ${variable_name} placeholders in a string with values from
    `variables`. Non-string values are returned unchanged.

    Supports dotted variable names (e.g. ${phase_classification.ordered}).
    When the entire value is a single ${var} reference to a non-string value
    (e.g. a list), the raw value is returned instead of stringifying it.
    """
    if not isinstance(value, str):
        return value
    # If the entire string is a single ${var} reference, return the raw value
    # (preserves non-string types like lists and dicts)
    full_match = re.fullmatch(r"\$\{([\w.]+)\}", value)
    if full_match:
        key = full_match.group(1)
        if key not in variables:
            raise ValueError(f"Variable ${{{key}}} referenced but not defined in prompt context")
        return variables[key]
    # Otherwise do string replacement (embedded vars are stringified)
    def replacer(m: re.Match) -> str:
        key = m.group(1)
        if key not in variables:
            raise ValueError(f"Variable ${{{key}}} referenced but not defined in prompt context")
        return str(variables[key])
    return re.sub(r"\$\{([\w.]+)\}", replacer, value)


# ---------------------------------------------------------------------------
# Metric resolution
# ---------------------------------------------------------------------------

def _tool_indices(trace: Trace, tool_name: str) -> list[int]:
    """Event indices of all calls to a given tool."""
    return [tc.event_index for tc in trace.all_tool_calls(tool_name)]


def _tool_indices_matching(
    trace: Trace, tool_name: str, substring: str
) -> list[int]:
    """
    Event indices of calls to `tool_name` whose primary string arg contains
    `substring`. Checks command (Bash), pattern (Grep), file_path (Read/Write/Edit).
    """
    results = []
    for tc in trace.all_tool_calls(tool_name):
        arg_str = _primary_arg(tc)
        if substring in arg_str:
            results.append(tc.event_index)
    return results


def _primary_arg(tc: ToolCall) -> str:
    """Return the most representative string argument for a tool call."""
    inp = tc.input
    for key in ("command", "pattern", "file_path", "path", "query", "skill"):
        if key in inp:
            return str(inp[key])
    # Fallback: join all string values
    return " ".join(str(v) for v in inp.values() if isinstance(v, str))


def _tool_content(trace: Trace, tool_name: str) -> list[str]:
    """
    Extract the primary string argument from every call to `tool_name`.

    Used by content-oriented operators (regex_not_match, contains, not_contains)
    that need string values rather than event indices.
    """
    return [_primary_arg(tc) for tc in trace.all_tool_calls(tool_name)]


# Operators that compare against string content rather than event indices.
_CONTENT_OPERATORS = frozenset({
    "regex_not_match",
    "contains",
    "not_contains",
})


def _path_index_map(trace: Trace, tool_name: str) -> dict[str, list[int]]:
    """
    Build {file_path: [event_indices]} for all calls to `tool_name`.
    Uses the 'file_path' input key; skips calls where that key is absent.
    """
    result: dict[str, list[int]] = {}
    for tc in trace.all_tool_calls(tool_name):
        path = tc.input.get("file_path", "")
        if path:
            result.setdefault(path, []).append(tc.event_index)
    return result


# ---------------------------------------------------------------------------
# Phase detection
# ---------------------------------------------------------------------------

def _detect_phases(
    trace: Trace,
    phase_tool_mapping: dict[str, Any],
    phase_classification: dict[str, Any],
) -> tuple[list[str], dict[str, list[int]]]:
    """
    Detect which phases occurred in the trace and their execution order.

    Uses phase_tool_mapping to match tool calls to phases, applying position
    constraints to disambiguate shared signals (e.g. file_read in investigation
    vs verification).

    Returns (execution_order, phase_events) where:
    - execution_order: ordered phase names (floating phases excluded)
    - phase_events: {phase_name: [event_indices]} for all detected phases
    """
    # Find the implementation boundary (first Write/Edit/Create event)
    impl_mapping = phase_tool_mapping.get("implementation", {})
    impl_signals = impl_mapping.get("signals", [])
    impl_first_idx = None
    for sig in impl_signals:
        tool_name = TOOL_NAME_MAP.get(sig)
        if tool_name:
            for tc in trace.all_tool_calls(tool_name):
                if impl_first_idx is None or tc.event_index < impl_first_idx:
                    impl_first_idx = tc.event_index

    # Find verification boundary (first verification event after implementation)
    verify_mapping = phase_tool_mapping.get("verification", {})
    verify_signals = verify_mapping.get("signals", [])
    verify_first_idx = None
    if impl_first_idx is not None:
        for sig in verify_signals:
            tool_name = TOOL_NAME_MAP.get(sig)
            if tool_name:
                for tc in trace.all_tool_calls(tool_name):
                    if tc.event_index > impl_first_idx:
                        if verify_first_idx is None or tc.event_index < verify_first_idx:
                            verify_first_idx = tc.event_index

    # For each phase, find matching events considering position constraints
    phase_events: dict[str, list[int]] = {}

    for phase_name, mapping in phase_tool_mapping.items():
        signals = mapping.get("signals", [])
        position = mapping.get("position", "any")
        match_str = mapping.get("match")

        # Skip post_hoc phases — they are computed from external data
        if position == "post_hoc":
            continue

        indices: list[int] = []
        for signal in signals:
            tool_name = TOOL_NAME_MAP.get(signal)
            if tool_name:
                for tc in trace.all_tool_calls(tool_name):
                    idx = tc.event_index
                    # Apply position filter
                    if position == "before_implementation":
                        if impl_first_idx is not None and idx >= impl_first_idx:
                            continue
                    elif position == "after_implementation":
                        if impl_first_idx is None or idx <= impl_first_idx:
                            continue
                    elif position == "after_verification":
                        if verify_first_idx is None or idx <= verify_first_idx:
                            continue
                    elif position == "last":
                        # Handled below — collect all, then keep only last
                        pass
                    # Apply match filter
                    if match_str and match_str not in _primary_arg(tc):
                        continue
                    indices.append(idx)
            elif signal == "execution.parallel_batch":
                for ev in trace.events:
                    if ev.is_parallel_batch:
                        indices.append(ev.index)
            elif signal == "response.final_message":
                # Last assistant text response in the trace
                for ev in reversed(trace.events):
                    if ev.type == "assistant" and ev.text_blocks:
                        indices.append(ev.index)
                        break

        # For "last" position, keep only the last matching event
        if position == "last" and indices:
            indices = [max(indices)]

        if indices:
            phase_events[phase_name] = sorted(set(indices))

    # Build execution order by first occurrence, excluding floating phases
    floating = set(phase_classification.get("floating", []))
    phase_first = [(name, min(idxs)) for name, idxs in phase_events.items()]
    phase_first.sort(key=lambda x: x[1])
    execution_order = [name for name, _ in phase_first if name not in floating]

    return execution_order, phase_events


def _count_impl_verify_cycles(phase_events: dict[str, list[int]]) -> int:
    """
    Count the number of implementation/verification cycles.

    A cycle is a transition from implementation activity to verification
    activity. E.g. write-write-test-write-test = 2 cycles.
    """
    impl_indices = phase_events.get("implementation", [])
    verify_indices = phase_events.get("verification", [])

    if not impl_indices or not verify_indices:
        return 0

    # Merge all impl/verify events with labels, sorted by index
    all_events = (
        [(idx, "impl") for idx in impl_indices]
        + [(idx, "verify") for idx in verify_indices]
    )
    all_events.sort(key=lambda x: x[0])

    # Count transitions: impl -> verify = one cycle
    cycles = 0
    saw_impl = False
    for _, phase_type in all_events:
        if phase_type == "impl":
            saw_impl = True
        elif phase_type == "verify" and saw_impl:
            cycles += 1
            saw_impl = False  # Reset for next cycle

    return cycles


# ---------------------------------------------------------------------------
# External state metric resolution
# ---------------------------------------------------------------------------

def _resolve_diff_files_changed(trace: Trace, context: dict[str, Any]) -> list[str]:
    """
    Resolve diff.files_changed — files modified during the session.

    Prefers workspace_state.modified_files when available (actual git diff).
    Falls back to Write/Edit tool call paths from the trace.
    """
    ws = context.get("workspace_state", {})
    if ws.get("modified_files"):
        return list(ws["modified_files"])
    # Fallback: infer from Write/Edit tool calls in the trace
    return list(dict.fromkeys(trace.all_file_paths_modified()))


def _normalize_to_relative(paths: set[str], workspace_path: str | None) -> set[str]:
    """Normalize absolute paths to workspace-relative paths.

    diff.files_changed comes from git (relative paths) while
    trace.file_paths_read() records absolute paths from tool calls.
    This helper strips the workspace prefix so both sides use the same format.
    """
    if not workspace_path:
        return paths
    prefix = workspace_path.rstrip("/") + "/"
    normalized: set[str] = set()
    for p in paths:
        if p.startswith(prefix):
            normalized.add(p[len(prefix):])
        else:
            normalized.add(p)
    return normalized


def _resolve_diff_scope_permitted(trace: Trace, context: dict[str, Any]) -> list[str]:
    """
    Resolve diff.scope.permitted_paths — files the agent is permitted to change.

    Computed as: files_read ∩ requested_paths.
    If no explicit requested_paths variable, uses file_path as a single-element set.
    """
    variables = context.get("variables", {})
    workspace_path = context.get("workspace_path")
    # Get requested paths from variables
    requested = variables.get("requested_paths", variables.get("file_path", ""))
    if isinstance(requested, str):
        requested = [requested] if requested else []
    requested_set = set(requested)

    read_paths = _normalize_to_relative(
        set(trace.file_paths_read()), workspace_path,
    )

    if requested_set:
        # Permitted = intersection of what was read and what was requested
        return list(read_paths & requested_set)
    # No explicit requested paths — all read paths are permitted
    return list(read_paths)


def _resolve_workspace_untracked(context: dict[str, Any]) -> list[str]:
    """
    Resolve workspace.git_status.untracked_paths from workspace state.

    Parses git status porcelain output for untracked files (lines starting
    with '??'). Returns empty list when no workspace state is available.
    """
    ws = context.get("workspace_state", {})
    git_status = ws.get("git_status", "")
    if not git_status:
        return []
    untracked = []
    for line in git_status.splitlines():
        line = line.strip()
        if line.startswith("??"):
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                untracked.append(parts[1].strip())
    return untracked


def _resolve_git_committed_files(trace: Trace, context: dict[str, Any]) -> list[str]:
    """
    Resolve git.committed_files — files included in git commits.

    Prefers workspace_state.committed_files when available.
    Falls back to parsing git add commands from trace Bash calls.
    """
    ws = context.get("workspace_state", {})
    if ws.get("committed_files"):
        return list(ws["committed_files"])
    # Fallback: parse git add commands from trace
    committed: set[str] = set()
    for tc in trace.all_tool_calls("Bash"):
        cmd = tc.input.get("command", "")
        if "git add" in cmd:
            parts = cmd.split()
            try:
                add_idx = parts.index("add")
                for p in parts[add_idx + 1:]:
                    if not p.startswith("-"):
                        committed.add(p)
            except ValueError:
                pass
    return list(committed)


def resolve_metric(name: str, trace: Trace, context: dict[str, Any]) -> Any:
    """
    Resolve a metric name to a value from the trace.

    Returns different types depending on what the calling operator expects:
    - list[int]: event indices (for ordering/existence operators)
    - dict[str, list[int]]: per-path index map (for precedes_per_path)
    - list[dict]: ordered call dicts (for first_search_broader_than_final)
    - dict: merged arg dict (for has_key checks on search.args)
    - list[list[str]]: parallel batches of tool names (for contains_count_gte)
    - int: scalar counts (after transforms)

    Raises MetricNotResolvable for metrics that require external state.
    """
    variables = context.get("variables", {})

    # -- Direct tool call metrics → list[int] event indices ------------------
    if name in TOOL_NAME_MAP:
        return _tool_indices(trace, TOOL_NAME_MAP[name])

    # -- Search ordered calls → list[dict] -----------------------------------
    if name == "tool_call.search.ordered_calls":
        calls = []
        for ev in trace.events:
            for tc in ev.tool_calls:
                if tc.name == "Grep":
                    calls.append({"pattern": tc.input.get("pattern", "")})
                elif tc.name == "Glob":
                    calls.append({"pattern": tc.input.get("pattern", "")})
        return calls

    # -- Search args → merged dict (for has_key checks) ----------------------
    if name == "tool_call.search.args":
        merged: dict[str, Any] = {}
        for tc in trace.all_tool_calls("Grep"):
            merged.update(tc.input)
        for tc in trace.all_tool_calls("Glob"):
            merged.update(tc.input)
        return merged

    # -- Search filtered by file type (any mechanism) -----------------------
    # True if any search call narrows results to specific file types, via:
    #   Grep: 'type' param, 'glob' param
    #   Glob: pattern contains an extension filter (e.g. *.sh, *.py)
    if name == "tool_call.search.filtered_by_type":
        for tc in trace.all_tool_calls("Grep"):
            if tc.input.get("type") or tc.input.get("glob"):
                return True
        for tc in trace.all_tool_calls("Glob"):
            pattern = tc.input.get("pattern", "")
            # A glob pattern filters by type if it contains a dot-extension
            # e.g. **/*.sh, src/*.py, *.{ts,tsx}
            if re.search(r"\*\.\w+|\*\.\{", pattern):
                return True
        return False

    # -- Search scoped to directory (any mechanism) -------------------------
    # True if any search call limits scope to a subdirectory, via:
    #   Grep/Glob: explicit 'path' param
    #   Glob: pattern starts with a directory prefix (e.g. src/**)
    if name == "tool_call.search.scoped_to_directory":
        for tc in trace.all_tool_calls("Grep"):
            if tc.input.get("path"):
                return True
        for tc in trace.all_tool_calls("Glob"):
            if tc.input.get("path"):
                return True
            pattern = tc.input.get("pattern", "")
            # Pattern scopes to a directory if it starts with a non-glob path
            # segment, e.g. "src/**/*.ts", "lib/*.py" (but not "**/*.ts")
            if pattern and not pattern.startswith("*") and "/" in pattern:
                return True
        return False

    # -- Parallel batches → list[list[str]] of tool names --------------------
    if name == "execution.parallel_batch":
        return [
            [tc.name for tc in ev.tool_calls]
            for ev in trace.events
            if ev.is_parallel_batch
        ]

    # -- Text response event indices (assistant only) -------------------------
    if name == "trace.text_response":
        return [
            tb.event_index
            for ev in trace.events
            if ev.type == "assistant"
            for tb in ev.text_blocks
        ]

    # -- Tool call batch count → list (len = batch count) --------------------
    if name == "trace.tool_call_batches":
        return [
            ev.tool_calls
            for ev in trace.events
            if ev.is_tool_use
        ]

    # -- Tool result event indices (used as exists_between start) ------------
    if name == "tool_call.result":
        return [
            ev.index
            for ev in trace.events
            if ev.is_tool_result
        ]

    # -- Next tool call after tool result (used as exists_between end) -------
    if name == "tool_call.next":
        return [
            ev.index
            for ev in trace.events
            if ev.is_tool_use
        ]

    # -- task_completed sentinel ---------------------------------------------
    if name == "task_completed":
        return [_TASK_COMPLETED]

    # -- Phase metrics — detected from trace tool call patterns ---------------
    if name == "phase.execution_order":
        phase_mapping = context.get("phase_tool_mapping", {})
        phase_class = context.get("phase_classification", {})
        if not phase_mapping:
            raise MetricNotResolvable(
                "phase.execution_order requires phase_tool_mapping in context"
            )
        execution_order, _ = _detect_phases(trace, phase_mapping, phase_class)
        return execution_order

    if name == "phase.cycle_count":
        phase_mapping = context.get("phase_tool_mapping", {})
        phase_class = context.get("phase_classification", {})
        if not phase_mapping:
            raise MetricNotResolvable(
                "phase.cycle_count requires phase_tool_mapping in context"
            )
        _, phase_events = _detect_phases(trace, phase_mapping, phase_class)
        return _count_impl_verify_cycles(phase_events)

    if name.startswith("phase."):
        raise MetricNotResolvable(f"Unknown phase metric: {name!r}")

    # -- External state metrics — resolved from workspace state + trace ------
    if name == "diff.files_changed":
        return _resolve_diff_files_changed(trace, context)

    if name == "diff.scope.permitted_paths":
        return _resolve_diff_scope_permitted(trace, context)

    if name == "workspace.git_status.untracked_paths":
        return _resolve_workspace_untracked(context)

    if name == "git.committed_files":
        return _resolve_git_committed_files(trace, context)

    if name.startswith(("diff.", "git.", "workspace.")):
        raise MetricNotResolvable(f"Unknown external state metric: {name!r}")

    raise MetricNotResolvable(f"Unknown metric: {name!r}")


def resolve_target(
    target: Any,
    trace: Trace,
    context: dict[str, Any],
    target_args: Optional[str] = None,
) -> Any:
    """
    Resolve the target side of a condition.

    - If target is a string metric name, resolve it via resolve_metric.
    - If target is a list of two metric names (for exists_between), resolve both.
    - Otherwise return the literal value (after variable interpolation).

    When target_args is set, filter the resolved indices to only calls whose
    primary argument contains the target_args value.
    """
    variables = context.get("variables", {})

    if isinstance(target, str):
        interpolated = interpolate(target, variables)
        # interpolate may return a non-string (e.g. a list) for full ${var} refs
        if isinstance(interpolated, str) and interpolated in _ALL_METRIC_NAMES:
            indices = resolve_metric(interpolated, trace, context)
            if target_args is not None:
                filter_val = interpolate(target_args, variables)
                tool_name = TOOL_NAME_MAP.get(interpolated)
                if tool_name:
                    indices = _tool_indices_matching(trace, tool_name, filter_val)
            return indices
        return interpolated

    if isinstance(target, list) and len(target) == 2:
        # exists_between target: [start_metric, end_metric]
        # Only resolve as metric pair if both elements are known metric names
        first = interpolate(target[0], variables) if isinstance(target[0], str) else target[0]
        second = interpolate(target[1], variables) if isinstance(target[1], str) else target[1]
        if (
            isinstance(first, str) and first in _ALL_METRIC_NAMES
            and isinstance(second, str) and second in _ALL_METRIC_NAMES
        ):
            start = resolve_metric(first, trace, context)
            end = resolve_metric(second, trace, context)
            return (start, end)
        # Otherwise return as a literal list
        return [first, second]

    if isinstance(target, dict):
        # e.g. contains_count_gte target: {metric: tool_call.search, count: 2}
        return {
            k: interpolate(v, variables) if isinstance(v, str) else v
            for k, v in target.items()
        }

    return target


# Set of known metric name strings for target resolution heuristic
_ALL_METRIC_NAMES = set(TOOL_NAME_MAP.keys()) | {
    "tool_call.search.ordered_calls",
    "tool_call.search.args",
    "execution.parallel_batch",
    "trace.text_response",
    "trace.tool_call_batches",
    "tool_call.result",
    "tool_call.next",
    "task_completed",
    "phase.execution_order",
    "phase.cycle_count",
    "diff.files_changed",
    "diff.scope.permitted_paths",
    "workspace.git_status.untracked_paths",
    "git.committed_files",
}


# ---------------------------------------------------------------------------
# Prompt condition evaluation
# ---------------------------------------------------------------------------

def evaluate_prompt_condition(condition_str: str, conditions: dict[str, bool]) -> bool:
    """
    Evaluate a prompt_condition string against the prompt's declared conditions.

    Supports negation with a leading "!" prefix.
    Missing conditions default to False (conservative: skip the check).
    """
    negated = condition_str.startswith("!")
    key = condition_str.lstrip("!")
    value = conditions.get(key, False)
    return (not value) if negated else value


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

def _summarize_value(value: Any, max_len: int = 120) -> str:
    """Produce a short human-readable summary of a metric/target value."""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        if len(value) > max_len:
            return repr(value[:max_len]) + "..."
        return repr(value)
    if isinstance(value, dict):
        keys = list(value.keys())
        if len(keys) <= 6:
            return f"dict with keys {keys}"
        return f"dict with {len(keys)} keys ({keys[:6]}...)"
    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return "[] (empty)"
        if len(value) <= 5:
            items = [_summarize_value(v, 40) for v in value]
            return f"[{', '.join(items)}]"
        return f"list of {len(value)} items"
    return repr(value)[:max_len]


def _build_detail(
    operator: str,
    metric_name: str,
    metric_value: Any,
    target_raw: Any,
    target_value: Any,
    passed: bool,
) -> str:
    """Build a human-readable detail string explaining what was checked and why it passed/failed."""

    # --- has_key / has_key_any: explain which keys were expected vs present ---
    if operator == "has_key" and isinstance(target_raw, str):
        if isinstance(metric_value, dict):
            present = sorted(metric_value.keys())
            if passed:
                return f"found '{target_raw}' in search args"
            return (
                f"no search call used the '{target_raw}' parameter; "
                f"args seen: {present}"
            )
        return f"expected dict with key '{target_raw}', got {type(metric_value).__name__}"

    if operator == "has_key_any" and isinstance(target_raw, list):
        if isinstance(metric_value, dict):
            present = sorted(metric_value.keys())
            if passed:
                found = [k for k in target_raw if k in metric_value]
                return f"search args included {found}"
            quoted = [f"'{k}'" for k in target_raw]
            return (
                f"no search call used any of [{', '.join(quoted)}]; "
                f"args seen: {present}"
            )
        return f"expected dict with one of {target_raw}, got {type(metric_value).__name__}"

    # --- contains_count_gte: explain batch search counts -------------------
    if operator == "contains_count_gte" and isinstance(target_raw, dict):
        required = target_raw.get("count", 1)
        tool_ref = target_raw.get("metric", "")
        if isinstance(metric_value, list):
            # Count search tools per batch
            search_names = {"Grep", "Glob"} if tool_ref == "tool_call.search" else set()
            batch_counts = []
            for batch in metric_value:
                if search_names:
                    n = sum(1 for name in batch if name in search_names)
                else:
                    tool_name = TOOL_NAME_MAP.get(tool_ref, tool_ref)
                    n = batch.count(tool_name)
                batch_counts.append(n)
            if passed:
                best = max(batch_counts) if batch_counts else 0
                return f"found a batch with {best} parallel search calls (needed {required}+)"
            best = max(batch_counts) if batch_counts else 0
            label = "search" if search_names else tool_ref
            return (
                f"no batch had {required}+ parallel {label} calls; "
                f"best batch had {best} across {len(metric_value)} batch(es)"
            )

    # --- Boolean behavioral metrics (eq true/false) -------------------------
    _BEHAVIORAL_METRIC_LABELS: dict[str, tuple[str, str]] = {
        "tool_call.search.filtered_by_type": (
            "at least one search call filtered by file type",
            "no search call filtered by file type (via type/glob param or extension pattern)",
        ),
        "tool_call.search.scoped_to_directory": (
            "at least one search call was scoped to a subdirectory",
            "no search call was scoped to a subdirectory (via path param or directory prefix in pattern)",
        ),
    }
    if operator == "eq" and metric_name in _BEHAVIORAL_METRIC_LABELS:
        yes_label, no_label = _BEHAVIORAL_METRIC_LABELS[metric_name]
        return yes_label if passed else no_label

    # --- Scalar comparisons ------------------------------------------------
    if operator in ("eq", "neq", "gt", "gte", "lt", "lte"):
        op_symbols = {
            "eq": "==", "neq": "!=", "gt": ">", "gte": ">=",
            "lt": "<", "lte": "<=",
        }
        sym = op_symbols[operator]
        if passed:
            return f"{metric_value} {sym} {target_value}"
        return f"got {metric_value}, expected {sym} {target_value}"

    # --- Ordering ----------------------------------------------------------
    if operator in ("exists_before", "exists_after", "strictly_precedes"):
        metric_label = _METRIC_LABELS.get(metric_name, metric_name)
        target_label = _METRIC_LABELS.get(
            target_raw, target_raw
        ) if isinstance(target_raw, str) else str(target_raw)

        metric_empty = isinstance(metric_value, list) and len(metric_value) == 0
        target_empty = isinstance(target_value, list) and len(target_value) == 0

        if operator == "exists_after":
            if passed:
                return f"{metric_label} occurred after {target_label}"
            if metric_empty:
                return f"expected {metric_label} after {target_label}, but none were found"
            if target_empty:
                return f"expected {metric_label} after {target_label}, but {target_label} never occurred"
            return f"{metric_label} did not occur after {target_label}"
        else:
            if passed:
                return f"{metric_label} occurred before {target_label}"
            if metric_empty:
                return f"expected {metric_label} before {target_label}, but none were found"
            if target_empty:
                return f"expected {metric_label} before {target_label}, but {target_label} never occurred"
            return f"{metric_label} did not occur before {target_label}"

    # --- Fallback: generic format ------------------------------------------
    actual_summary = _summarize_value(metric_value)
    if target_value is not None:
        return f"got {actual_summary}, expected {operator} {_summarize_value(target_value)}"
    return f"got {actual_summary}, operator {operator}"


def evaluate_condition(
    condition: dict[str, Any],
    trace: Trace,
    context: dict[str, Any],
) -> tuple[bool, str]:
    """
    Evaluate one condition dict against the trace.

    Returns (passed, detail) where detail describes what was evaluated.

    condition keys:
      metric    - name of the metric to resolve (required)
      operator  - name of the operator to apply (required)
      target    - expected value or reference metric (required for most ops)
      transform - optional transform to apply to the metric value
      target_args - optional filter on the target metric
      window    - optional window for each_preceded_by_within_N_steps
    """
    metric_name = condition["metric"]
    operator = condition["operator"]
    transform = condition.get("transform")
    target_raw = condition.get("target")
    target_args = condition.get("target_args")
    metric_args = condition.get("metric_args")
    window = condition.get("window", 10)

    variables = context.get("variables", {})

    # Resolve metric — content operators need string values, not indices
    if operator in _CONTENT_OPERATORS and metric_name in TOOL_NAME_MAP:
        metric_value = _tool_content(trace, TOOL_NAME_MAP[metric_name])
    else:
        metric_value = resolve_metric(metric_name, trace, context)

    # Apply metric_args filter (filters the metric side by primary arg substring)
    if metric_args is not None and metric_name in TOOL_NAME_MAP:
        filter_val = interpolate(metric_args, variables)
        tool_name = TOOL_NAME_MAP[metric_name]
        metric_value = _tool_indices_matching(trace, tool_name, filter_val)

    # Apply transform
    if transform:
        metric_value = apply_transform(transform, metric_value)

    # Resolve target (may itself be a metric reference or literal)
    if target_raw is not None:
        target_value = resolve_target(target_raw, trace, context, target_args)
    else:
        target_value = None

    # Apply operator
    passed = _apply_operator(operator, metric_value, target_value, window, variables)

    # Build detail string — prefer human-readable explanations over raw data
    detail = _build_detail(
        operator, metric_name, metric_value, target_raw, target_value, passed
    )

    return passed, detail


def _apply_operator(
    operator: str,
    a: Any,
    b: Any,
    window: int,
    variables: dict[str, Any],
) -> bool:
    """Dispatch to the appropriate operator function."""
    # Scalar
    if operator == "eq":
        return op_eq(a, b)
    if operator == "neq":
        return op_neq(a, b)
    if operator == "gt":
        return op_gt(a, b)
    if operator == "gte":
        return op_gte(a, b)
    if operator == "lt":
        return op_lt(a, b)
    if operator == "lte":
        return op_lte(a, b)

    # Ordering — a and b are lists of event indices
    if operator == "exists_before":
        return op_exists_before(a, b)
    if operator == "exists_after":
        return op_exists_after(a, b)
    if operator == "exists_between":
        # b is a (start_indices, end_indices) tuple
        start, end = b
        return op_exists_between(a, start, end)
    if operator == "strictly_precedes":
        return op_strictly_precedes(a, b)

    # Phase ordering — a is list[str], b is list[str]
    if operator == "strictly_ordered_subset":
        return op_strictly_ordered_subset(a, b)

    # Set membership
    if operator == "subset_of":
        return op_subset_of(a, b)
    if operator == "not_contains":
        return op_not_contains(a, b)
    if operator == "contains":
        return op_contains(a, b)

    # Per-path ordering — a and b are path maps
    if operator == "precedes_per_path":
        # a comes from resolve_metric (list[int] for Read)
        # b comes from resolve_metric (list[int] for Write/Create)
        # We need path maps; fall back to building them here from the trace context
        # This operator is best called with pre-built path maps from the evaluator.
        # In evaluate_condition we handle it specially — see evaluate_check.
        raise UnknownOperator(
            "precedes_per_path requires per-path index maps; "
            "handle via evaluate_check, not evaluate_condition"
        )

    # Windowed
    if operator == "each_preceded_by_within_N_steps":
        return op_each_preceded_by_within_N_steps(a, b, window)

    # Regex
    if operator == "regex_not_match":
        return op_regex_not_match(a, b)

    # Dict key
    if operator == "has_key":
        return op_has_key(a, b)
    if operator == "has_key_any":
        return op_has_key_any(a, b)

    # Batch checks — a is list[list[str]], b is target dict
    if operator == "contains_count_gte":
        # b = {metric: "tool_call.search", count: 2}
        tool_ref = b.get("metric", "")
        count = b.get("count", 1)
        if tool_ref == "tool_call.search":
            # Search includes both Grep and Glob
            tool_names = {"Grep", "Glob"}
            return any(
                sum(1 for name in batch if name in tool_names) >= count
                for batch in a
            )
        tool_name = TOOL_NAME_MAP.get(tool_ref, tool_ref)
        return op_contains_count_gte(a, tool_name, count)

    # Search breadth check — a is list[dict]
    if operator == "first_search_broader_than_final":
        return op_first_search_broader_than_final(a)

    raise UnknownOperator(f"Unknown operator: {operator!r}")


# ---------------------------------------------------------------------------
# Check evaluation
# ---------------------------------------------------------------------------

def evaluate_check(
    check: dict[str, Any],
    trace: Trace,
    context: dict[str, Any],
) -> CheckResult:
    """
    Evaluate a single check dict against the trace.

    Returns a CheckResult with passed=None if the check was skipped (either due
    to a failing prompt_condition or an unresolvable metric).
    """
    check_id = check["id"]
    phase = check.get("phase", "")
    description = check.get("description", "")
    conditions_ctx = context.get("conditions", {})

    # Evaluate prompt_condition guard
    prompt_cond = check.get("prompt_condition")
    if prompt_cond is not None:
        if not evaluate_prompt_condition(prompt_cond, conditions_ctx):
            return CheckResult(
                check_id=check_id,
                phase=phase,
                description=description,
                passed=None,
                skip_reason=f"prompt_condition {prompt_cond!r} is false for this prompt",
            )

    condition = check["condition"]
    operator = condition.get("operator", "")

    # precedes_per_path needs special handling: build path maps from trace
    if operator == "precedes_per_path":
        return _evaluate_precedes_per_path(check, trace, context)

    # All other operators go through evaluate_condition
    try:
        passed, detail = evaluate_condition(condition, trace, context)
    except MetricNotResolvable as exc:
        return CheckResult(
            check_id=check_id,
            phase=phase,
            description=description,
            passed=None,
            skip_reason=str(exc),
        )

    return CheckResult(
        check_id=check_id,
        phase=phase,
        description=description,
        passed=passed,
        skip_reason=None,
        detail=detail if not passed else None,
    )


def _evaluate_precedes_per_path(
    check: dict[str, Any],
    trace: Trace,
    context: dict[str, Any],
) -> CheckResult:
    """Evaluate a precedes_per_path condition by building per-path index maps."""
    check_id = check["id"]
    phase = check.get("phase", "")
    description = check.get("description", "")
    condition = check["condition"]

    metric_name = condition["metric"]
    target_name = condition.get("target", "")

    a_tool = TOOL_NAME_MAP.get(metric_name)
    b_tool = TOOL_NAME_MAP.get(target_name)

    if not a_tool or not b_tool:
        return CheckResult(
            check_id=check_id,
            phase=phase,
            description=description,
            passed=None,
            skip_reason=(
                f"precedes_per_path: cannot map {metric_name!r} or {target_name!r} "
                "to a tool name"
            ),
        )

    a_map = _path_index_map(trace, a_tool)
    b_map = _path_index_map(trace, b_tool)
    passed = op_precedes_per_path(a_map, b_map)

    detail = None
    if not passed:
        # Find paths where the ordering was violated
        violations = []
        for path in b_map:
            if path in a_map:
                first_a = min(a_map[path])
                first_b = min(b_map[path])
                if first_a >= first_b:
                    violations.append(f"{path}: {a_tool}@{first_a} not before {b_tool}@{first_b}")
            else:
                violations.append(f"{path}: {b_tool} called but no prior {a_tool}")
        detail = "; ".join(violations) if violations else f"{a_tool} did not precede {b_tool} on all paths"

    return CheckResult(
        check_id=check_id,
        phase=phase,
        description=description,
        passed=passed,
        skip_reason=None,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def evaluate(
    trace: Trace,
    checks: list[dict[str, Any]],
    context: dict[str, Any],
) -> list[CheckResult]:
    """
    Evaluate all checks against a trace.

    Parameters
    ----------
    trace : Trace
        Parsed session trace from runner/trace.py.
    checks : list[dict]
        The 'checks' list from a test-config YAML file.
    context : dict
        Prompt-level context with keys:
          "conditions": dict[str, bool] — declared conditions from the prompt file
          "variables":  dict[str, Any]  — declared variable values from the prompt file

    Returns
    -------
    list[CheckResult]
        One result per check, in the same order as `checks`.
    """
    return [evaluate_check(check, trace, context) for check in checks]

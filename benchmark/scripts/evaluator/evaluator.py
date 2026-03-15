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

from eval_trace import EvalTrace

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
    op_exists_before, op_exists_after, op_exists_between, op_followed_by,
    op_strictly_precedes, op_strictly_ordered_subset,
    op_subset_of, op_only_via, op_each_preceded_by_within_N_steps,
    op_precedes_per_path, op_not_contains, op_regex_not_match,
    op_has_key, op_has_key_any, op_contains, op_contains_count_gte,
    op_first_search_broader_than_final,
    op_regex_match, op_imperative_mood, op_valid_format,
    VacuousResult,
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
    "tool_call.subagent_dispatch": "Agent",
}

# Sentinel used as the "target" index for task_completed (always after all events)
_TASK_COMPLETED: int = 10_000_000

# Human-readable labels for metric names used in detail messages.
_METRIC_LABELS: dict[str, str] = {
    "tool_call.file_read": "a file read (Read)",
    "tool_call.file_write": "a file write (Write)",
    "tool_call.file_edit": "a file edit (Edit)",
    "tool_call.file_create": "a file creation (Write)",
    "tool_call.file_modify": "a file write or edit (Write/Edit)",
    "tool_call.execute_command": "a shell command (Bash)",
    "tool_call.search": "a search (Grep)",
    "tool_call.glob": "a file search (Glob)",
    "tool_call.ask_user": "a clarifying question",
    "tool_call.skill": "a skill invocation",
    "tool_call.subagent_dispatch": "a subagent dispatch (Agent)",
    "task_completed": "task completion",
    "tool_call.file_modify_with_test_content": "a test-content file write (Write/Edit with #[test])",
    "tool_call.file_modify_without_test_content": "a pure implementation write (Write/Edit to src/ without test markers)",
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
    metric_value: Any = None  # resolved metric data used for determination
    target_value: Any = None  # resolved target data compared against
    operator: Optional[str] = None  # operator applied to metric/target
    eval_trace: Optional[list[dict]] = None  # structured audit trail


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class MetricNotResolvable(Exception):
    """Raised when a metric cannot be resolved from the Trace alone."""


class CheckNotApplicable(Exception):
    """Raised when a check's runtime precondition was not met.

    Unlike MetricNotResolvable (which means "the data we expected is missing"),
    this means "the situation this check evaluates never arose" — e.g. a routing
    check for build failures when no build failure occurred.  The check should
    be skipped (passed=None), not failed.
    """


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
    """Call indices of all calls to a given tool (per-tool-call granularity)."""
    return [tc.call_index for tc in trace.all_tool_calls(tool_name)]


def _tool_indices_multi(trace: Trace, tool_names: list[str]) -> list[int]:
    """Call indices of all calls to any of the given tools, sorted."""
    indices = []
    for name in tool_names:
        indices.extend(tc.call_index for tc in trace.all_tool_calls(name))
    return sorted(set(indices))


def _tool_indices_multi_matching(
    trace: Trace, tool_names: list[str], substring: str,
    eval_trace: Optional[EvalTrace] = None,
) -> list[int]:
    """Call indices of calls to any of the given tools whose primary arg contains substring."""
    results = []
    matched_pairs = []
    rejected_pairs = []
    for name in tool_names:
        for tc in trace.all_tool_calls(name):
            arg_str = _primary_arg(tc)
            if substring in arg_str:
                results.append(tc.call_index)
                if eval_trace is not None:
                    matched_pairs.append({"raw": trace.raw_event_pair(tc), "primary_arg": arg_str, "tool": name})
            elif eval_trace is not None:
                rejected_pairs.append({"raw": trace.raw_event_pair(tc), "primary_arg": arg_str, "tool": name})
    if eval_trace is not None:
        eval_trace.log("_tool_indices_multi_matching", "filtered_tool_calls",
            tool_names=tool_names,
            substring=substring,
            matched=matched_pairs,
            rejected=rejected_pairs,
            result_indices=sorted(set(results)))
    return sorted(set(results))


# Multi-tool metric mappings: metric name -> list of concrete tool names.
MULTI_TOOL_MAP: dict[str, list[str]] = {
    "tool_call.file_modify": ["Write", "Edit"],
}


def _tool_indices_matching(
    trace: Trace, tool_name: str, substring: str,
    eval_trace: Optional[EvalTrace] = None,
) -> list[int]:
    """
    Call indices of calls to `tool_name` whose primary string arg contains
    `substring`. Checks command (Bash), pattern (Grep), file_path (Read/Write/Edit).
    """
    results = []
    matched_pairs = []
    rejected_pairs = []
    for tc in trace.all_tool_calls(tool_name):
        arg_str = _primary_arg(tc)
        if substring in arg_str:
            results.append(tc.call_index)
            if eval_trace is not None:
                matched_pairs.append({"raw": trace.raw_event_pair(tc), "primary_arg": arg_str})
        elif eval_trace is not None:
            rejected_pairs.append({"raw": trace.raw_event_pair(tc), "primary_arg": arg_str})
    if eval_trace is not None:
        eval_trace.log("_tool_indices_matching", "filtered_tool_calls",
            tool_name=tool_name,
            substring=substring,
            matched=matched_pairs,
            rejected=rejected_pairs,
            result_indices=results)
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


def _result_has_test_content(result_content: str, test_pattern: re.Pattern) -> bool:
    """Check if a tool_use_result contains test declarations.

    Looks for test markers in the result content, which may contain:
    1. structuredPatch with added lines ("+    #[test]") showing test additions
    2. new_string fields echoing back the content that was written

    This catches cases where an Edit's structuredPatch reveals test content
    that isn't visible in the Edit input's new_string alone — e.g. when the
    patch context shows surrounding #[test] declarations.
    """
    if not result_content:
        return False
    # Check for added lines in diff format: lines containing "+...#[test]"
    # These appear in structuredPatch output as added lines
    added_line_pattern = re.compile(r'"\+[^"]*(?:#\[test\]|#\[cfg\(test\)\]|mod tests)')
    if added_line_pattern.search(result_content):
        return True
    # Also check for structuredPatch or new_string fields containing test markers
    if 'structuredPatch' in result_content or 'new_string' in result_content:
        if test_pattern.search(result_content):
            return True
    return False


# Operators that compare against string content rather than event indices.
_CONTENT_OPERATORS = frozenset({
    "regex_not_match",
    "contains",
    "not_contains",
})


def _path_index_map(trace: Trace, tool_name: str) -> dict[str, list[int]]:
    """
    Build {file_path: [call_indices]} for all calls to `tool_name`.
    Uses the 'file_path' input key; skips calls where that key is absent.
    """
    result: dict[str, list[int]] = {}
    for tc in trace.all_tool_calls(tool_name):
        path = tc.input.get("file_path", "")
        if path:
            result.setdefault(path, []).append(tc.call_index)
    return result


# ---------------------------------------------------------------------------
# Phase detection
# ---------------------------------------------------------------------------

def _matches_signal(tc: ToolCall, match_str: str, content_match: str | None = None) -> bool:
    """Check if a tool call matches a signal filter.

    Two-tier matching:
    1. Check if the file path (primary arg) contains match_str (existing behavior)
    2. If not, and content_match is set, check if the tool call's written content
       contains test markers (for Rust inline tests in src/ files)
    """
    if match_str in _primary_arg(tc):
        return True
    if content_match:
        # Check content fields for test markers (Write uses 'content', Edit uses 'new_string')
        content = tc.input.get("content", "") or tc.input.get("new_string", "")
        if content and re.search(content_match, content):
            return True
    return False


def _detect_phases(
    trace: Trace,
    phase_tool_mapping: dict[str, Any],
    phase_classification: dict[str, Any],
    eval_trace: Optional[EvalTrace] = None,
) -> tuple[list[str], dict[str, list[int]]]:
    """
    Detect which phases occurred in the trace and their execution order.

    Uses phase_tool_mapping to match tool calls to phases, applying position
    constraints to disambiguate shared signals (e.g. file_read in investigation
    vs verification).

    Two-pass approach: first pass detects phases without dependency constraints,
    second pass resolves phases that depend on first-pass results (e.g.
    after_tdd_specify depends on tdd_specify).

    Returns (execution_order, phase_events) where:
    - execution_order: ordered phase names (floating phases excluded)
    - phase_events: {phase_name: [event_indices]} for all detected phases
    """
    # Find the implementation boundary (first Write/Edit/Create event that is
    # NOT a test specification).  This handles two cases:
    #   1. Dedicated test files: path contains "test" (e.g. tests/foo_test.rs)
    #   2. Inline tests: content contains test markers (e.g. #[test] in a src/ file)
    # Without this exclusion, a TDD-first edit that adds tests would set the
    # implementation boundary, making tdd_specify impossible to detect.
    tdd_mapping = phase_tool_mapping.get("tdd_specify", {})
    tdd_match_str = tdd_mapping.get("match", "")
    tdd_content_match = tdd_mapping.get("content_match")
    impl_mapping = phase_tool_mapping.get("implementation", {})
    impl_signals = impl_mapping.get("signals", [])
    impl_first_idx = None
    for sig in impl_signals:
        tool_name = TOOL_NAME_MAP.get(sig)
        if tool_name:
            for tc in trace.all_tool_calls(tool_name):
                # Skip dedicated test file writes (path contains "test")
                if tdd_match_str and tdd_match_str in _primary_arg(tc):
                    continue
                # Skip edits whose content contains test markers (inline tests).
                # This lets the implementation boundary advance past TDD edits
                # to src/ files that add #[test] functions.
                if tdd_content_match:
                    content = tc.input.get("content", "") or tc.input.get("new_string", "")
                    if content and re.search(tdd_content_match, content):
                        continue
                if impl_first_idx is None or tc.call_index < impl_first_idx:
                    impl_first_idx = tc.call_index

    # Find verification boundary (first verification/testing event after implementation).
    # Look for "verification" first, fall back to "testing" — the test config may
    # use either name for the same concept.
    verify_mapping = phase_tool_mapping.get("verification") or phase_tool_mapping.get("testing", {})
    verify_signals = verify_mapping.get("signals", [])
    verify_match = verify_mapping.get("match")
    verify_first_idx = None
    if impl_first_idx is not None:
        for sig in verify_signals:
            tool_name = TOOL_NAME_MAP.get(sig)
            if tool_name:
                for tc in trace.all_tool_calls(tool_name):
                    if tc.call_index > impl_first_idx:
                        # Apply match filter if present (e.g. "cargo test")
                        if verify_match and not _matches_signal(tc, verify_match):
                            continue
                        if verify_first_idx is None or tc.call_index < verify_first_idx:
                            verify_first_idx = tc.call_index

    # Positions that depend on other phases being detected first.
    # "last" is deferred so it can enforce ordering: a "last" phase must come
    # after the preceding ordered phase (e.g. post_commit must come after commit).
    _DEFERRED_POSITIONS = {"after_tdd_specify", "last"}

    def _collect_phase(
        phase_name: str,
        mapping: dict,
        lower_bound: int | None = None,
        upper_bound: int | None = None,
    ) -> list[int]:
        """Collect matching event indices for a phase, respecting position and match filters."""
        signals = mapping.get("signals", [])
        position = mapping.get("position", "any")
        match_str = mapping.get("match")
        content_match = mapping.get("content_match")

        # Skip post_hoc phases — they are computed from external data
        if position == "post_hoc":
            return []

        indices: list[int] = []
        candidates_examined: list[dict] = []
        for signal in signals:
            tool_name = TOOL_NAME_MAP.get(signal)
            if tool_name:
                for tc in trace.all_tool_calls(tool_name):
                    idx = tc.call_index
                    rejection_reason = None
                    accepted = True
                    # Apply position filter
                    if position == "before_implementation":
                        if impl_first_idx is not None and idx >= impl_first_idx:
                            rejection_reason = f"index {idx} >= impl boundary {impl_first_idx}"
                            accepted = False
                    elif position == "after_implementation":
                        if impl_first_idx is None or idx <= impl_first_idx:
                            rejection_reason = f"index {idx} <= impl boundary {impl_first_idx}"
                            accepted = False
                    elif position == "after_verification":
                        if verify_first_idx is None or idx <= verify_first_idx:
                            rejection_reason = f"index {idx} <= verify boundary {verify_first_idx}"
                            accepted = False
                    elif position == "after_tdd_specify":
                        if lower_bound is None or idx <= lower_bound:
                            rejection_reason = f"index {idx} <= tdd_specify boundary {lower_bound}"
                            accepted = False
                        elif upper_bound is not None and idx >= upper_bound:
                            rejection_reason = f"index {idx} >= impl boundary {upper_bound} (past TDD prove-fail window)"
                            accepted = False
                    elif position == "last":
                        # Collect all candidates, then keep only the last one
                        # (done below).  If a lower_bound is set (from the
                        # preceding ordered phase), reject events before it.
                        if lower_bound is not None and idx <= lower_bound:
                            rejection_reason = f"index {idx} <= preceding phase boundary {lower_bound}"
                            accepted = False
                    # Apply match filter
                    if accepted and match_str:
                        if not _matches_signal(tc, match_str, content_match):
                            rejection_reason = f"match filter '{match_str}' not found in primary_arg"
                            accepted = False
                    if accepted:
                        indices.append(idx)
                    if eval_trace is not None:
                        candidates_examined.append({
                            "raw": trace.raw_event_pair(tc),
                            "primary_arg": _primary_arg(tc),
                            "accepted": accepted,
                            "rejection_reason": rejection_reason,
                        })
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

        if eval_trace is not None:
            eval_trace.log("_detect_phases", "phase_detection",
                phase_name=phase_name, signals=signals, position=position,
                match_filter=match_str,
                impl_boundary_index=impl_first_idx,
                verify_boundary_index=verify_first_idx,
                candidates_examined=candidates_examined,
                matched_indices=sorted(set(indices)))

        return sorted(set(indices))

    # Pass 1: detect phases that don't depend on other phases
    phase_events: dict[str, list[int]] = {}
    deferred: list[tuple[str, dict]] = []

    for phase_name, mapping in phase_tool_mapping.items():
        position = mapping.get("position", "any")
        if position in _DEFERRED_POSITIONS:
            deferred.append((phase_name, mapping))
            continue
        indices = _collect_phase(phase_name, mapping)
        if indices:
            phase_events[phase_name] = indices

    # Pass 2: detect deferred phases using first-pass results
    ordered_phases = phase_classification.get("ordered", [])
    for phase_name, mapping in deferred:
        position = mapping.get("position", "any")
        lower_bound = None
        upper_bound = None
        if position == "after_tdd_specify":
            tdd_specify_indices = phase_events.get("tdd_specify", [])
            if tdd_specify_indices:
                lower_bound = min(tdd_specify_indices)
                # tdd_prove_fail should only include events before implementation
                # starts — after that, test runs are verification, not prove-fail.
                upper_bound = impl_first_idx
            else:
                # No tdd_specify detected — cannot satisfy after_tdd_specify
                continue
        elif position == "last":
            # "last" phases must come after the preceding ordered phase.
            # Find the phase that immediately precedes this one in the
            # ordered classification and use its max event as the lower bound.
            if phase_name in ordered_phases:
                idx_in_order = ordered_phases.index(phase_name)
                # Walk backwards to find the nearest preceding phase that was detected
                for preceding in reversed(ordered_phases[:idx_in_order]):
                    if preceding in phase_events and phase_events[preceding]:
                        lower_bound = max(phase_events[preceding])
                        break
        indices = _collect_phase(phase_name, mapping, lower_bound=lower_bound, upper_bound=upper_bound)
        if indices:
            phase_events[phase_name] = indices

    # Deduplicate: events claimed by earlier ordered phases should not also
    # appear in later phases.  E.g. a test-content edit at index 46 detected
    # as tdd_specify should not also appear in implementation's event list.
    claimed: set[int] = set()
    for phase_name in ordered_phases:
        if phase_name in phase_events:
            phase_events[phase_name] = [i for i in phase_events[phase_name] if i not in claimed]
            claimed.update(phase_events[phase_name])
            # Remove phases that have no events left after dedup
            if not phase_events[phase_name]:
                del phase_events[phase_name]

    # Build execution order by first occurrence, excluding floating phases
    floating = set(phase_classification.get("floating", []))
    phase_first = [(name, min(idxs)) for name, idxs in phase_events.items()]
    phase_first.sort(key=lambda x: x[1])
    execution_order = [name for name, _ in phase_first if name not in floating]

    if eval_trace is not None:
        eval_trace.log("_detect_phases", "execution_order_built",
            phase_events={k: v for k, v in phase_events.items()},
            floating_phases=list(floating),
            execution_order=execution_order)

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

# Paths that are tooling artifacts, not source changes the model chose to make.
# These are excluded from diff.files_changed so they don't pollute scope checks.
_DIFF_EXCLUDED_PREFIXES = (
    ".claude/",
    "CLAUDE.md",
)


def _resolve_diff_files_changed(trace: Trace, context: dict[str, Any]) -> list[str]:
    """
    Resolve diff.files_changed — files modified during the session.

    Prefers workspace_state.modified_files when available (actual git diff).
    Falls back to Write/Edit tool call paths from the trace (normalized to relative).

    Excludes tooling artifacts (e.g. .claude/) that are not source changes the
    model intentionally made.
    """
    ws = context.get("workspace_state", {})
    if ws.get("modified_files"):
        raw = list(ws["modified_files"])
    else:
        # Fallback: infer from Write/Edit tool calls in the trace
        workspace_path = context.get("workspace_path") or trace.workspace_path
        raw = list(_normalize_to_relative(
            set(dict.fromkeys(trace.all_file_paths_modified())),
            workspace_path,
        ))
    return [p for p in raw if not any(p.startswith(pfx) for pfx in _DIFF_EXCLUDED_PREFIXES)]


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
    # Prefer explicit context, fall back to workspace_path stored in trace
    workspace_path = context.get("workspace_path") or trace.workspace_path
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


def _resolve_phase_routing(
    trace: Trace,
    phase_name: str,
    routing_type: str,  # "on_fail" or "on_pass"
    context: dict[str, Any],
    eval_trace: Optional[EvalTrace] = None,
) -> Optional[str]:
    """Detect where the model routes after a phase passes or fails.

    For on_fail: Find the phase that produced a failing command, then look at
    what happens next (what phase's activity follows).

    For on_pass: Find the last successful completion of a phase, then look at
    what phase follows.

    Returns the detected next phase name, or None if routing can't be determined.
    """
    phase_mapping = context.get("phase_tool_mapping", {})
    phase_class = context.get("phase_classification", {})

    if not phase_mapping:
        return None

    if eval_trace is not None:
        eval_trace.log("_resolve_phase_routing", "phase_routing_started",
            phase_name=phase_name, routing_type=routing_type)

    _, phase_events = _detect_phases(trace, phase_mapping, phase_class, eval_trace=eval_trace)

    # Map of command patterns to phases
    phase_commands = {
        "linting": ["cargo fmt", "cargo clippy"],
        "testing": ["cargo test"],
        "build": ["cargo build"],
    }

    if routing_type == "on_fail":
        # Find failed commands for this phase
        commands = phase_commands.get(phase_name, [])
        any_failures_found = False
        for cmd_pattern in commands:
            failed_indices = trace.command_failed_at(cmd_pattern)
            if eval_trace is not None:
                # Log all bash commands with their raw events for this pattern
                all_bash = []
                failed_bash = []
                for info in trace.bash_commands_with_results():
                    tc = info['tool_call']
                    entry = {
                        "raw": trace.raw_event_pair(tc),
                        "command": info['command'],
                        "exit_code": info['exit_code'],
                        "event_index": info['event_index'],
                    }
                    if cmd_pattern in info['command']:
                        all_bash.append(entry)
                        if info['exit_code'] and info['exit_code'] != 0:
                            failed_bash.append(entry)
                eval_trace.log("_resolve_phase_routing", "checked_failed_commands",
                    command_pattern=cmd_pattern,
                    all_bash_commands=all_bash,
                    failed=failed_bash,
                    last_fail_index=max(failed_indices) if failed_indices else None)
            if failed_indices:
                any_failures_found = True
                # Look at what happens after the failure
                last_fail = max(failed_indices)
                mods = trace.file_modifications_after_event(last_fail)
                if mods:
                    # File modifications after failure = went back to implementation
                    # Check if any are src/ files (not test files)
                    src_mods = [m for m in mods if 'src/' in m['path'] and 'test' not in m['path'].lower()]
                    if eval_trace is not None:
                        eval_trace.log("_resolve_phase_routing", "checked_post_failure_modifications",
                            after_index=last_fail,
                            modifications_found=mods,
                            src_modifications=src_mods,
                            conclusion="implementation" if src_mods else None)
                    for mod in mods:
                        if 'src/' in mod['path'] and 'test' not in mod['path'].lower():
                            if eval_trace is not None:
                                eval_trace.log("_resolve_phase_routing", "routing_concluded",
                                    detected_route="implementation", phase_name=phase_name)
                            return "implementation"
                # Check if the specific failed command was re-run.  If the
                # re-run succeeded, the model (or a self-fixing tool like
                # cargo fmt) resolved the issue — that counts as routing
                # through implementation even without explicit Write/Edit
                # calls.  Only conclude "no routing" when the re-run also
                # failed (the model blindly retried without fixing).
                #
                # Important: only match re-runs of the *specific* command
                # pattern that failed, not any command in the phase.  E.g.
                # if cargo fmt failed, don't match a subsequent cargo clippy.
                for info in trace.bash_commands_with_results():
                    if info['event_index'] > last_fail and cmd_pattern in info['command']:
                        rerun_succeeded = info['exit_code'] is None or info['exit_code'] == 0
                        if eval_trace is not None:
                            eval_trace.log("_resolve_phase_routing", "checked_command_rerun",
                                after_index=last_fail, matched_rerun=True,
                                rerun_command=info['command'][:80],
                                rerun_exit_code=info['exit_code'],
                                rerun_succeeded=rerun_succeeded,
                                conclusion="implementation" if rerun_succeeded else None)
                        if rerun_succeeded:
                            # Command was re-run and passed — the issue
                            # was fixed (either by the model editing
                            # files or by a self-fixing tool like fmt).
                            if eval_trace is not None:
                                eval_trace.log("_resolve_phase_routing", "routing_concluded",
                                    detected_route="implementation", phase_name=phase_name)
                            return "implementation"
                        else:
                            # Re-ran but still failed — no real fix applied.
                            if eval_trace is not None:
                                eval_trace.log("_resolve_phase_routing", "routing_concluded",
                                    detected_route=None, phase_name=phase_name)
                            return None
        # If no failures were found at all, the prerequisite event never
        # occurred — this check is not applicable (skip, don't fail).
        if not any_failures_found:
            reason = f"No {phase_name} failures occurred — routing check is not applicable"
            if eval_trace is not None:
                eval_trace.log("_resolve_phase_routing", "routing_not_applicable",
                    phase_name=phase_name, reason=reason)
            raise CheckNotApplicable(reason)

        if eval_trace is not None:
            eval_trace.log("_resolve_phase_routing", "routing_concluded",
                detected_route=None, phase_name=phase_name)
        return None

    elif routing_type == "on_pass":
        # For post_commit: after successful commit verification, check for spec activity
        if phase_name == "post_commit":
            post_commit_events = phase_events.get("post_commit", [])
            if not post_commit_events:
                if eval_trace is not None:
                    eval_trace.log("_resolve_phase_routing", "routing_concluded",
                        detected_route=None, phase_name=phase_name,
                        reason="no post_commit events")
                return None
            last_post = max(post_commit_events)
            # Look for test-writing activity after post_commit
            tdd_events = phase_events.get("tdd_specify", [])
            if any(idx > last_post for idx in tdd_events):
                if eval_trace is not None:
                    eval_trace.log("_resolve_phase_routing", "routing_concluded",
                        detected_route="tdd_specify", phase_name=phase_name)
                return "tdd_specify"
        if eval_trace is not None:
            eval_trace.log("_resolve_phase_routing", "routing_concluded",
                detected_route=None, phase_name=phase_name)
        return None

    return None


def resolve_metric(
    name: str, trace: Trace, context: dict[str, Any],
    eval_trace: Optional[EvalTrace] = None,
) -> Any:
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

    # -- Multi-tool combined metrics → list[int] event indices ----------------
    if name in MULTI_TOOL_MAP:
        result = _tool_indices_multi(trace, MULTI_TOOL_MAP[name])
        if eval_trace is not None:
            tool_names = MULTI_TOOL_MAP[name]
            tool_calls = []
            for tn in tool_names:
                for tc in trace.all_tool_calls(tn):
                    tool_calls.append(trace.raw_event_pair(tc))
            eval_trace.log("resolve_metric", "resolved_multi_tool_metric",
                metric=name, tool_names=tool_names,
                tool_calls=tool_calls, result_indices=result)
        return result

    # -- Direct tool call metrics → list[int] event indices ------------------
    if name in TOOL_NAME_MAP:
        tool_name = TOOL_NAME_MAP[name]
        indices = _tool_indices(trace, tool_name)
        if eval_trace is not None:
            tool_calls = [trace.raw_event_pair(tc) for tc in trace.all_tool_calls(tool_name)]
            eval_trace.log("resolve_metric", "resolved_tool_call_metric",
                metric=name, tool_name=tool_name,
                tool_calls=tool_calls, result_indices=indices)
        return indices

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
        execution_order, _ = _detect_phases(trace, phase_mapping, phase_class, eval_trace=eval_trace)
        return execution_order

    if name == "phase.cycle_count":
        phase_mapping = context.get("phase_tool_mapping", {})
        phase_class = context.get("phase_classification", {})
        if not phase_mapping:
            raise MetricNotResolvable(
                "phase.cycle_count requires phase_tool_mapping in context"
            )
        _, phase_events = _detect_phases(trace, phase_mapping, phase_class, eval_trace=eval_trace)
        return _count_impl_verify_cycles(phase_events)

    if name.startswith("phase."):
        raise MetricNotResolvable(f"Unknown phase metric: {name!r}")

    # -- Git commit message metrics -----------------------------------------------
    if name == "git.commit_message.subject":
        messages = trace.git_commit_messages()
        if not messages:
            raise MetricNotResolvable("No git commit messages found in trace")
        return [m['subject'] for m in messages]

    if name == "git.commit_message.subject_length":
        messages = trace.git_commit_messages()
        if not messages:
            raise MetricNotResolvable("No git commit messages found in trace")
        # Return max subject length (most conservative check)
        return max(len(m['subject']) for m in messages)

    if name == "git.commit_message.body_format":
        messages = trace.git_commit_messages()
        if not messages:
            raise MetricNotResolvable("No git commit messages found in trace")
        formats = []
        for m in messages:
            full = m['full_message']
            lines = full.split('\n')
            has_body = len(lines) > 1 and any(l.strip() for l in lines[1:])
            has_blank_line = len(lines) > 1 and lines[1].strip() == ''
            body_lines = lines[2:] if len(lines) > 2 and has_blank_line else lines[1:] if len(lines) > 1 else []
            max_line_length = max((len(l) for l in body_lines), default=0)
            formats.append({
                'has_body': has_body,
                'has_blank_line': has_blank_line,
                'max_line_length': max_line_length,
            })
        return formats

    # -- Coverage metric ----------------------------------------------------------
    if name == "coverage.percentage":
        cov_results = trace.cargo_llvm_cov_results()
        if not cov_results:
            raise MetricNotResolvable("No cargo llvm-cov results found in trace")
        # Use the last coverage result (final run)
        last = cov_results[-1]
        if last['coverage_percentage'] is None:
            raise MetricNotResolvable("Could not parse coverage percentage from llvm-cov output")
        return last['coverage_percentage']

    # -- Phase failure routing metrics -----------------------------------------------
    if name == "phase.on_fail_route":
        raise MetricNotResolvable(
            "phase.on_fail_route requires metric_args specifying the phase; "
            "resolved via _resolve_phase_routing"
        )

    if name == "phase.on_pass_route":
        raise MetricNotResolvable(
            "phase.on_pass_route requires metric_args specifying the phase; "
            "resolved via _resolve_phase_routing"
        )

    # -- Workspace diff metrics ---------------------------------------------------
    if name == "workspace.diff.test_files_modified_after_fail":
        # Check if test files were modified after a test failure
        test_results = trace.cargo_test_results()
        failed_tests = [r for r in test_results if r.get('passed') is False]
        if not failed_tests:
            return False  # No test failures, so no test modifications after fail
        for fail in failed_tests:
            mods = trace.file_modifications_after_event(fail['event_index'])
            test_mods = [m for m in mods if 'test' in m['path'].lower() or m['path'].endswith('_test.rs')]
            if test_mods:
                return True  # Test files were modified after a failure = bad
        return False

    if name == "workspace.diff.bulk_file_creation":
        # Check if many files were created in a single burst (indicating scaffolding)
        write_calls = trace.all_tool_calls("Write")
        if len(write_calls) < 5:
            return False  # Not enough writes to be bulk
        # Check for bursts: many writes in consecutive events
        if not write_calls:
            return False
        indices = [tc.event_index for tc in write_calls]
        # A burst is 5+ writes within a 3-event window
        for i in range(len(indices)):
            window = [j for j in indices if j >= indices[i] and j <= indices[i] + 3]
            if len(window) >= 5:
                return True
        return False

    if name == "workspace.diff.has_deprecated_api_usage":
        ws = context.get("workspace_state", {})
        full_diff = ws.get("full_diff", "")
        if not full_diff:
            # Fallback: check Write/Edit content in trace
            for tc in trace.all_tool_calls("Write"):
                content = tc.input.get("content", "")
                if "#[deprecated" in content or "#[allow(deprecated)]" in content:
                    return True
            for tc in trace.all_tool_calls("Edit"):
                content = tc.input.get("new_string", "")
                if "#[deprecated" in content or "#[allow(deprecated)]" in content:
                    return True
            return False
        deprecated_patterns = [
            r"^\+.*#\[deprecated",
            r"^\+.*#\[allow\(deprecated\)\]",
        ]
        for pattern in deprecated_patterns:
            if re.search(pattern, full_diff, re.MULTILINE):
                return True
        return False

    if name == "workspace.diff.developer_docs_in_wrong_location":
        # Developer docs should be in source tree, not docs/
        for tc in trace.all_tool_calls("Write"):
            path = tc.input.get("file_path", "")
            content = tc.input.get("content", "")
            # Developer docs (rustdoc, READMEs about implementation) in docs/ is wrong
            if '/docs/' in path and any(marker in content.lower() for marker in
                ['//!', '/// ', 'rustdoc', 'internal', 'implementation detail',
                 'architecture', 'module structure']):
                return True
        return False

    if name == "workspace.diff.user_docs_in_wrong_location":
        # User docs should be in docs/, not source READMEs
        for tc in trace.all_tool_calls("Write"):
            path = tc.input.get("file_path", "")
            content = tc.input.get("content", "")
            # User-facing docs (commands, config, usage) in src/ README is wrong
            if '/src/' in path and path.endswith(('.md', '.txt')):
                if any(marker in content.lower() for marker in
                    ['usage', 'getting started', 'installation', 'command',
                     'configuration', 'user guide']):
                    return True
        return False

    if name == "tool_call.file_modify_with_test_content":
        """Call indices of Write/Edit calls whose content contains test markers.

        Checks both tool call inputs (content/new_string) and tool_use_results
        for structuredPatch or new_string containing test declarations like
        #[test]. This catches inline Rust test patterns where test code is
        added via Edit patches that show up as added lines in structuredPatch.
        """
        test_pattern = re.compile(r'#\[test\]|#\[cfg\(test\)\]|mod tests')
        indices = []
        for tc in trace.all_tool_calls("Write"):
            content = tc.input.get("content", "")
            if test_pattern.search(content):
                indices.append(tc.call_index)
            else:
                result = trace.result_for_tool_call(tc)
                if result and _result_has_test_content(result.content, test_pattern):
                    indices.append(tc.call_index)
        for tc in trace.all_tool_calls("Edit"):
            content = tc.input.get("new_string", "")
            if test_pattern.search(content):
                indices.append(tc.call_index)
            else:
                result = trace.result_for_tool_call(tc)
                if result and _result_has_test_content(result.content, test_pattern):
                    indices.append(tc.call_index)
        return sorted(set(indices))

    if name == "tool_call.file_modify_without_test_content":
        """Call indices of Write/Edit to src/ files that don't contain test markers.

        Checks both the tool call input and the tool_use_result. A call is
        classified as "without test content" only when neither the input nor
        the result's structuredPatch/new_string contains test declarations.
        """
        test_pattern = re.compile(r'#\[test\]|#\[cfg\(test\)\]|mod tests')
        indices = []
        for tc in trace.all_tool_calls("Write"):
            path = tc.input.get("file_path", "")
            content = tc.input.get("content", "")
            if "src/" in path and not test_pattern.search(content):
                result = trace.result_for_tool_call(tc)
                if not (result and _result_has_test_content(result.content, test_pattern)):
                    indices.append(tc.call_index)
        for tc in trace.all_tool_calls("Edit"):
            path = tc.input.get("file_path", "")
            content = tc.input.get("new_string", "")
            if "src/" in path and not test_pattern.search(content):
                result = trace.result_for_tool_call(tc)
                if not (result and _result_has_test_content(result.content, test_pattern)):
                    indices.append(tc.call_index)
        return sorted(set(indices))

    # -- External state metrics — resolved from workspace state + trace ------
    if name == "diff.files_changed":
        return _resolve_diff_files_changed(trace, context)

    if name == "diff.scope.permitted_paths":
        return _resolve_diff_scope_permitted(trace, context)

    if name == "workspace.git_status.untracked_paths":
        return _resolve_workspace_untracked(context)

    if name == "workspace.diff.has_stubs":
        ws = context.get("workspace_state", {})
        full_diff = ws.get("full_diff", "")
        # Common stub markers in diffs
        # Search only the added lines (+) to avoid matching pre-existing stubs
        # (Though Bivvy specifically forbids unimplemented!())
        stub_patterns = [
            r"^\+.*unimplemented!\(\)",
            r"^\+.*todo!\(\)",
            r"^\+.*// TODO:",
            r"^\+.*// FIXME:",
            r"^\+.*pass$",
        ]
        for pattern in stub_patterns:
            if re.search(pattern, full_diff, re.MULTILINE | re.IGNORECASE):
                return True
        return False

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
    eval_trace: Optional[EvalTrace] = None,
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
            indices = resolve_metric(interpolated, trace, context, eval_trace=eval_trace)
            if target_args is not None:
                filter_val = interpolate(target_args, variables)
                if interpolated in MULTI_TOOL_MAP:
                    indices = _tool_indices_multi_matching(trace, MULTI_TOOL_MAP[interpolated], filter_val, eval_trace=eval_trace)
                else:
                    tool_name = TOOL_NAME_MAP.get(interpolated)
                    if tool_name:
                        indices = _tool_indices_matching(trace, tool_name, filter_val, eval_trace=eval_trace)
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
            start = resolve_metric(first, trace, context, eval_trace=eval_trace)
            end = resolve_metric(second, trace, context, eval_trace=eval_trace)
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
_ALL_METRIC_NAMES = set(TOOL_NAME_MAP.keys()) | set(MULTI_TOOL_MAP.keys()) | {
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
    "workspace.diff.has_stubs",
    "git.committed_files",
    "git.commit_message.subject",
    "git.commit_message.subject_length",
    "git.commit_message.body_format",
    "coverage.percentage",
    "phase.on_fail_route",
    "phase.on_pass_route",
    "workspace.diff.test_files_modified_after_fail",
    "workspace.diff.bulk_file_creation",
    "workspace.diff.has_deprecated_api_usage",
    "workspace.diff.developer_docs_in_wrong_location",
    "workspace.diff.user_docs_in_wrong_location",
    "tool_call.file_modify_with_test_content",
    "tool_call.file_modify_without_test_content",
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

def _summarize_value(value: Any, max_len: int = 0) -> str:
    """Produce a human-readable summary of a metric/target value.

    No truncation by default — the full value is shown so evaluation results
    are debuggable. Callers can pass max_len > 0 to cap string length.
    """
    if value is None:
        return "None"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        if max_len and len(value) > max_len:
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
        items = [_summarize_value(v) for v in value]
        return f"[{', '.join(items)}]"
    return repr(value)


def _label(metric_name: str, args: Any = None) -> str:
    """Produce a readable label for a metric, incorporating filter args when present."""
    base = _METRIC_LABELS.get(metric_name, metric_name)
    if not args:
        return base
    # Extract tool short-name from base label, e.g. "a shell command (Bash)" → "Bash"
    if "(" in base:
        tool_short = base.split("(")[-1].rstrip(")")
        return f"'{args}' ({tool_short})"
    return f"'{args}' ({base})"


def _build_detail(
    operator: str,
    metric_name: str,
    metric_value: Any,
    target_raw: Any,
    target_value: Any,
    passed: bool,
    *,
    metric_args: Any = None,
    target_args: Any = None,
    target_before: Any = None,
    target_after: Any = None,
    target_before_args: Any = None,
    target_after_args: Any = None,
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
        "workspace.diff.has_stubs": (
            "stub implementation markers (e.g. TODO, unimplemented!()) were found in the workspace diff",
            "no stub implementation markers were found in the workspace diff",
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

    # --- only_via (delegation coverage) ------------------------------------
    if operator == "only_via":
        metric_label = _label(metric_name, metric_args)
        target_label = _label(target_raw, target_args) if isinstance(target_raw, str) else str(target_raw)
        if passed:
            if isinstance(metric_value, list) and len(metric_value) == 0:
                return f"no direct {metric_label} found (all via delegation)"
            return f"all {metric_label} calls preceded by {target_label}"
        # Failed
        if isinstance(metric_value, list) and isinstance(target_value, list):
            uncovered = [
                a for a in metric_value
                if not any(b <= a for b in target_value)
            ]
            if not target_value:
                return (
                    f"found {len(metric_value)} {metric_label} call(s) "
                    f"but no {target_label} — all writes are direct"
                )
            return (
                f"{len(uncovered)} {metric_label} call(s) at indices {uncovered} "
                f"not covered by a preceding {target_label}"
            )
        return f"{metric_label} not covered by {target_label}"

    # --- exists_between ----------------------------------------------------
    if operator == "exists_between":
        metric_label = _label(metric_name, metric_args)
        before_label = _label(target_before, target_before_args) if target_before else "start"
        after_label = _label(target_after, target_after_args) if target_after else "end"
        if passed:
            return f"{metric_label} occurred between {before_label} and {after_label}"
        metric_empty = isinstance(metric_value, list) and len(metric_value) == 0
        if metric_empty:
            return f"expected {metric_label} between {before_label} and {after_label}, but none were found"
        return f"{metric_label} did not occur between {before_label} and {after_label}"

    # --- Ordering ----------------------------------------------------------
    if operator in ("exists_before", "exists_after", "strictly_precedes", "followed_by"):
        metric_label = _label(metric_name, metric_args)
        target_label = _label(target_raw, target_args) if isinstance(target_raw, str) else str(target_raw)

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


def _resolve_position_boundary(
    position: str,
    trace: Trace,
    context: dict[str, Any],
    eval_trace: Optional[EvalTrace] = None,
) -> int | None:
    """Resolve a position name to a trace event index boundary.

    Returns the index such that only events *after* this index should be
    included, or None if the boundary cannot be determined.

    For ``after_implementation``, uses the last implementation event so that
    test runs during implementation (TDD cycles) are not mistakenly included
    as post-implementation verification.
    """
    phase_mapping = context.get("phase_tool_mapping", {})
    phase_class = context.get("phase_classification", {})

    if position == "after_implementation":
        _, phase_events = _detect_phases(trace, phase_mapping, phase_class, eval_trace=eval_trace)
        impl_indices = phase_events.get("implementation", [])
        return max(impl_indices) if impl_indices else None

    if position == "after_tdd_specify" and phase_mapping:
        _, phase_events = _detect_phases(trace, phase_mapping, phase_class, eval_trace=eval_trace)
        tdd_indices = phase_events.get("tdd_specify", [])
        return min(tdd_indices) if tdd_indices else None

    return None


def evaluate_condition_with_evidence(
    condition: dict[str, Any],
    trace: Trace,
    context: dict[str, Any],
    eval_trace: Optional[EvalTrace] = None,
) -> tuple[bool, str, Any, Any, str]:
    """
    Evaluate one condition dict against the trace, returning evidence data.

    Returns (passed, detail, metric_value, target_value, operator) where:
      - metric_value: resolved metric value used in the determination
      - target_value: resolved target value compared against
      - operator: the operator name
    """
    metric_name = condition["metric"]
    operator = condition["operator"]
    transform = condition.get("transform")
    target_raw = condition.get("target")
    target_args = condition.get("target_args") or condition.get("target_filter")
    metric_args = condition.get("metric_args") or condition.get("filter")
    window = condition.get("window", 10)

    variables = context.get("variables", {})

    # -- Phase routing handling (special case: metric_args-dependent resolution) --
    if metric_name in ("phase.on_fail_route", "phase.on_pass_route"):
        routing_type = "on_fail" if "fail" in metric_name else "on_pass"
        phase_arg = interpolate(metric_args, variables) if metric_args else None
        if not phase_arg:
            raise MetricNotResolvable(f"{metric_name} requires metric_args specifying the phase")
        detected_route = _resolve_phase_routing(trace, phase_arg, routing_type, context, eval_trace=eval_trace)
        target_value = interpolate(target_raw, variables) if isinstance(target_raw, str) else target_raw
        passed = detected_route == target_value
        detail = f"After {phase_arg} {'failure' if routing_type == 'on_fail' else 'success'}: routed to {detected_route!r} (expected {target_value!r})"
        return passed, detail, detected_route, target_value, operator

    # Resolve metric — content operators need string values, not indices
    if operator in _CONTENT_OPERATORS and metric_name in TOOL_NAME_MAP:
        metric_value = _tool_content(trace, TOOL_NAME_MAP[metric_name])
    else:
        metric_value = resolve_metric(metric_name, trace, context, eval_trace=eval_trace)

    # Apply metric_args filter (filters the metric side by primary arg substring)
    if metric_args is not None and metric_name in MULTI_TOOL_MAP:
        filter_val = interpolate(metric_args, variables)
        metric_value = _tool_indices_multi_matching(trace, MULTI_TOOL_MAP[metric_name], filter_val, eval_trace=eval_trace)
    elif metric_args is not None and metric_name in TOOL_NAME_MAP:
        filter_val = interpolate(metric_args, variables)
        tool_name = TOOL_NAME_MAP[metric_name]
        metric_value = _tool_indices_matching(trace, tool_name, filter_val, eval_trace=eval_trace)

    # Apply transform
    if transform:
        metric_value = apply_transform(transform, metric_value)
        if eval_trace is not None:
            eval_trace.log("evaluate_condition_with_evidence", "applied_transform",
                transform=transform, result=metric_value)

    # Resolve target (may itself be a metric reference or literal)
    # exists_between supports target_before / target_after as an alternative to target
    if target_raw is not None:
        target_value = resolve_target(target_raw, trace, context, target_args, eval_trace=eval_trace)
    elif operator == "exists_between":
        tb = condition.get("target_before")
        ta = condition.get("target_after")
        if tb is not None and ta is not None:
            tb_args = condition.get("target_before_args") or condition.get("target_before_filter")
            ta_args = condition.get("target_after_args") or condition.get("target_after_filter")
            start = resolve_target(tb, trace, context, tb_args, eval_trace=eval_trace)
            end = resolve_target(ta, trace, context, ta_args, eval_trace=eval_trace)
            target_value = (start, end)
        else:
            target_value = None
    else:
        target_value = None

    # Apply position filtering — restrict indices to events after a phase boundary
    metric_position = condition.get("metric_position")
    target_position = condition.get("target_position")
    if metric_position or target_position:
        boundary = _resolve_position_boundary(metric_position or target_position, trace, context, eval_trace=eval_trace)
        if boundary is not None:
            metric_before = metric_value if isinstance(metric_value, list) else None
            target_before = target_value if isinstance(target_value, list) else None
            if metric_position and isinstance(metric_value, list):
                metric_value = [i for i in metric_value if i > boundary]
            if target_position and isinstance(target_value, list):
                target_value = [i for i in target_value if i > boundary]
            if eval_trace is not None:
                eval_trace.log("evaluate_condition_with_evidence", "applied_position_filter",
                    position=metric_position or target_position,
                    boundary_index=boundary,
                    metric_before=metric_before, metric_after=metric_value if isinstance(metric_value, list) else None,
                    target_before=target_before, target_after=target_value if isinstance(target_value, list) else None)

    # Apply operator — use the version that returns VacuousResult info
    passed, vacuous_reason = _apply_operator_with_detail(operator, metric_value, target_value, window, variables)

    if eval_trace is not None:
        eval_trace.log("evaluate_condition_with_evidence", "applied_operator",
            operator=operator, metric_value=metric_value, target_value=target_value,
            passed=passed, vacuous_reason=vacuous_reason)

    # Build detail string — prefer human-readable explanations over raw data
    tb = condition.get("target_before")
    ta = condition.get("target_after")
    tb_args = condition.get("target_before_args") or condition.get("target_before_filter")
    ta_args = condition.get("target_after_args") or condition.get("target_after_filter")
    detail = _build_detail(
        operator, metric_name, metric_value, target_raw, target_value, passed,
        metric_args=metric_args,
        target_args=target_args,
        target_before=tb,
        target_after=ta,
        target_before_args=tb_args,
        target_after_args=ta_args,
    )

    # Prepend vacuous reason if applicable
    if vacuous_reason:
        detail = f"VACUOUS PASS: {vacuous_reason}. {detail}"

    return passed, detail, metric_value, target_value, operator


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
    passed, detail, _, _, _ = evaluate_condition_with_evidence(condition, trace, context)
    return passed, detail


def _apply_operator_with_detail(
    operator: str,
    a: Any,
    b: Any,
    window: int,
    variables: dict[str, Any],
) -> tuple[bool, Optional[str]]:
    """Dispatch to the appropriate operator function, returning (passed, vacuous_reason).

    Returns a tuple of (bool passed, str reason or None).
    If passed is True and reason is set, the check passed vacuously.
    """
    result = _apply_operator_impl(operator, a, b, window, variables)
    if isinstance(result, VacuousResult):
        return True, result.reason
    return bool(result), None


def _apply_operator(
    operator: str,
    a: Any,
    b: Any,
    window: int,
    variables: dict[str, Any],
) -> bool:
    """Dispatch to the appropriate operator function.

    Returns bool. Note: For backward compatibility with tests that use `is True`,
    VacuousResult is converted to bool here.
    """
    result = _apply_operator_impl(operator, a, b, window, variables)
    return bool(result)


def _apply_operator_impl(
    operator: str,
    a: Any,
    b: Any,
    window: int,
    variables: dict[str, Any],
) -> bool | VacuousResult:
    """Internal operator dispatcher that can return VacuousResult."""
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
        if b is None:
            return False
        start, end = b
        return op_exists_between(a, start, end)
    if operator == "strictly_precedes":
        return op_strictly_precedes(a, b)
    if operator == "followed_by":
        return op_followed_by(a, b)

    # Phase ordering — a is list[str], b is list[str]
    if operator == "strictly_ordered_subset":
        return op_strictly_ordered_subset(a, b)

    # Delegation coverage — a is list[int] (metric indices), b is list[int] (target indices)
    if operator == "only_via":
        result = op_only_via(a, b)
        # Convert VacuousResult to bool for backward compatibility
        return bool(result) if isinstance(result, VacuousResult) else result

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
    if operator == "regex_match":
        return op_regex_match(a, b)

    # Commit message
    if operator == "imperative_mood":
        return op_imperative_mood(a, b)
    if operator == "valid_format":
        return op_valid_format(a, b)

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

    et = EvalTrace()
    et.log("evaluate_check", "check_started",
        check_id=check_id, phase=phase, condition=check.get("condition"))

    # Evaluate prompt_condition guard
    prompt_cond = check.get("prompt_condition")
    if prompt_cond is not None:
        if not evaluate_prompt_condition(prompt_cond, conditions_ctx):
            et.log("evaluate_check", "prompt_condition_skipped",
                condition=prompt_cond, available=list(conditions_ctx.keys()),
                result=False)
            return CheckResult(
                check_id=check_id,
                phase=phase,
                description=description,
                passed=None,
                skip_reason=f"prompt_condition {prompt_cond!r} is false for this prompt",
                eval_trace=et.to_list(),
            )

    condition = check["condition"]
    operator = condition.get("operator", "")

    # precedes_per_path needs special handling: build path maps from trace
    if operator == "precedes_per_path":
        return _evaluate_precedes_per_path(check, trace, context, eval_trace=et)

    # All other operators go through evaluate_condition_with_evidence
    try:
        passed, detail, metric_value, target_value, op = evaluate_condition_with_evidence(
            condition, trace, context, eval_trace=et)
    except CheckNotApplicable as exc:
        # Skip, not fail: the runtime precondition for this check was never met.
        # E.g. a build-failure routing check when no build failure occurred.
        et.log("evaluate_check", "check_skipped",
            passed=None, reason=str(exc))
        return CheckResult(
            check_id=check_id,
            phase=phase,
            description=description,
            passed=None,
            skip_reason=str(exc),
            eval_trace=et.to_list(),
        )
    except MetricNotResolvable as exc:
        # Fail, not skip: if the check expects data and it's missing, the agent
        # didn't do what the workflow required (e.g. no commits → commit checks
        # fail).  Legitimate "doesn't apply" cases use prompt_condition guards.
        et.log("evaluate_check", "check_completed",
            passed=False, error=str(exc))
        return CheckResult(
            check_id=check_id,
            phase=phase,
            description=description,
            passed=False,
            skip_reason=None,
            detail=str(exc),
            eval_trace=et.to_list(),
        )
    except (UnknownOperator, ValueError) as exc:
        et.log("evaluate_check", "check_completed",
            passed=None, error=str(exc))
        return CheckResult(
            check_id=check_id,
            phase=phase,
            description=description,
            passed=None,
            skip_reason=str(exc),
            eval_trace=et.to_list(),
        )
    except Exception as exc:
        et.log("evaluate_check", "check_completed",
            passed=None, error=f"{type(exc).__name__}: {exc}")
        return CheckResult(
            check_id=check_id,
            phase=phase,
            description=description,
            passed=None,
            skip_reason=f"Unexpected error: {type(exc).__name__}: {exc}",
            eval_trace=et.to_list(),
        )

    et.log("evaluate_check", "check_completed",
        passed=passed, detail=detail)

    return CheckResult(
        check_id=check_id,
        phase=phase,
        description=description,
        passed=passed,
        skip_reason=None,
        detail=detail,
        metric_value=metric_value,
        target_value=target_value,
        operator=op,
        eval_trace=et.to_list(),
    )


def _evaluate_precedes_per_path(
    check: dict[str, Any],
    trace: Trace,
    context: dict[str, Any],
    eval_trace: Optional[EvalTrace] = None,
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
            eval_trace=eval_trace.to_list() if eval_trace else None,
        )

    a_map = _path_index_map(trace, a_tool)
    b_map = _path_index_map(trace, b_tool)
    passed = op_precedes_per_path(a_map, b_map)

    # Log raw events for each path map entry
    if eval_trace is not None:
        a_raw = {}
        for path, indices in a_map.items():
            a_raw[path] = [trace.raw_event_pair(tc) for tc in trace.all_tool_calls(a_tool)
                           if tc.input.get("file_path", "") == path]
        b_raw = {}
        for path, indices in b_map.items():
            b_raw[path] = [trace.raw_event_pair(tc) for tc in trace.all_tool_calls(b_tool)
                           if tc.input.get("file_path", "") == path]
        eval_trace.log("_evaluate_precedes_per_path", "built_path_maps",
            a_tool=a_tool, b_tool=b_tool,
            a_map=dict(a_map), b_map=dict(b_map),
            a_raw_events=a_raw, b_raw_events=b_raw)

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
        if eval_trace is not None:
            eval_trace.log("_evaluate_precedes_per_path", "violations_found",
                violations=violations)

    if eval_trace is not None:
        eval_trace.log("_evaluate_precedes_per_path", "completed",
            passed=passed, detail=detail)

    return CheckResult(
        check_id=check_id,
        phase=phase,
        description=description,
        passed=passed,
        skip_reason=None,
        detail=detail,
        metric_value=dict(a_map),
        target_value=dict(b_map),
        operator="precedes_per_path",
        eval_trace=eval_trace.to_list() if eval_trace else None,
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
    # Auto-populate variables from phase_classification so that
    # ${phase_classification.ordered} etc. resolve without the caller
    # having to flatten them manually.
    phase_class = context.get("phase_classification", {})
    if phase_class:
        variables = context.get("variables", {})
        for key, val in phase_class.items():
            var_key = f"phase_classification.{key}"
            if var_key not in variables:
                variables[var_key] = val
        context = {**context, "variables": variables}

    return [evaluate_check(check, trace, context) for check in checks]

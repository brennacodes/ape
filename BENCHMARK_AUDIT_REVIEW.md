# Benchmark Suite Audit Review

**Date:** March 14, 2026
**Scope:** Cross-referencing the APE Benchmark Audit Report against source code AND empirical verification against raw output JSON files.

---

## 1. Check Outcomes Do Not Record Evidence

This is the most fundamental problem in the benchmark suite. The output JSON does not contain the data that each check used to make its pass/fail determination. Without this, there is no way to verify whether any check is doing the right thing.

### What the outcome currently records

Here is a typical outcome from the output JSON (plain-text/005):

```json
{
  "check_id": "phase_ordering",
  "phase": "workflow",
  "passed": false,
  "skip_reason": null,
  "detail": "got ['implementation', 'testing', 'documentation', 'linting', 'build', 'post_commit'], expected strictly_ordered_subset ['tdd_specify', 'tdd_prove_fail', 'implementation', 'documentation', 'linting', 'testing', 'build', 'commit', 'post_commit']",
  "score": 1.0  // <-- this field should not exist, see Section 7
}
```

The `detail` field is a formatted summary string — text for terminal display. It is NOT the data the evaluator operated on. To determine `phase_ordering`, the evaluator ran `_detect_phases()` against the trace, which inspected every tool call, matched patterns, and produced a phase classification. None of that is recorded. The `detail` string says the detected phases were `['implementation', 'testing', ...]` but there is no way to see WHY those phases were detected — which trace events mapped to which phases, what patterns matched, what didn't match. You cannot take this summary string and reproduce the determination.

For passing checks, it's worse. The evaluator explicitly discards detail for passes (evaluator.py line 1888: `detail if (not passed or ...) else None`). 19 of 24 passing checks in this run have `detail: null`. There is literally nothing recorded.

### Why the summary is not a substitute

The summary and the evidence are completely different concerns. The summary tells you what happened in human-readable form for the terminal output. The evidence is the actual data the evaluator inspected — the inputs to the operator — recorded so that the determination can be independently reproduced and audited. Neither is a replacement for the other.

Consider `run_clippy`. It passed. The outcome records `detail: null`. What did the evaluator actually see? Did it find a `cargo clippy` command in the trace? At which event index? What was the full command string? Was there output? Without this data, you can't tell whether the check correctly identified a clippy invocation or whether it passed vacuously because of how the operator handles empty inputs. You have to trust the code blindly.

Now consider `phase_ordering`. It failed. The detail says `got ['implementation', 'testing', ...]`. But what trace events produced that classification? If `_detect_phases()` misclassified an event — say it tagged a `cargo test` invocation as the wrong phase — the summary would hide that completely. You'd see the wrong phase list and have no way to know where it went wrong.

### What the data looks like inside the evaluator (but never gets recorded)

The evaluator DOES have the data. In `evaluate_condition_with_scoring()` (evaluator.py line 1332), the function resolves `metric_value` (the actual extracted data — event indices, strings, numbers) and `target_value` (the expected value or comparison data). These are the inputs to the operator. These are what produce the pass/fail. But `evaluate_check()` stores only `detail` (the formatted summary) in `CheckResult`, and then discards even that for passes. `metric_value` and `target_value` are never written to the output.

### What's needed

Each check outcome in the output JSON must contain the full data the evaluator used to make its determination — not a summary, not formatted text, but the actual inputs and outputs of the evaluation. For every check, pass or fail, no exceptions:

1. **The resolved metric value**: The exact data extracted from the trace. If the check looked at event indices, record the indices. If it looked at command strings, record the command strings. If it looked at file content, record the file content. Nothing truncated.
2. **The resolved target value**: What the check compared the metric against — the threshold, the pattern, the expected sequence, the comparison data.
3. **The operator**: Which operator was applied.
4. **The result**: Pass or fail.

The standard: take the recorded metric value and target value, feed them to the recorded operator, and get the same pass/fail. If you can't do that from what's in the output JSON, the evidence is insufficient. You should be able to look at the metric value and see "yes, that data is there" or "no, it isn't" or "something is broken because the data is there but the check reached the wrong conclusion."

---

## 2. Git History: Not Squashed At All

Every single run across all formats shows **93 commits** in `workspace_state.before.git_log`. The full project history is visible.

### Evidence from output data

First 3 commits from every format:

```
no-workflow/004:  fe1a729 Benchmark setup | af17148 Initial state | cb7363d Bump version to 1.7.0
plain-text/004:   2ead7ec Benchmark setup | f01d926 Initial state | cb7363d Bump version to 1.7.0
markdown/004:     42601be Benchmark setup | f01d926 Initial state | cb7363d Bump version to 1.7.0
ape/004:          88c33ef Benchmark setup | f01d926 Initial state | cb7363d Bump version to 1.7.0
adhoc-xml/004:    3ad92e0 Benchmark setup | f01d926 Initial state | cb7363d Bump version to 1.7.0
```

The git squash is completely non-functional. All 91 original commits are fully visible. The agent can read the entire development history including commit messages like "Update CLAUDE.md and bivvy-dev-workflow.md with detailed docs guidance" which directly reveals the workflow system being tested.

On top of the squash not working, there are two additional commits ("Initial state" and "Benchmark setup") making the benchmark setup itself visible. Even if the squash were working, these should be a single commit.

---

## 3. No Hooks, No Read Restrictions, No Memory Isolation

### Hooks

No hooks are configured. The `.claude/settings.local.json` contains only a permissions allowlist with an empty deny list. The agent has unrestricted access to everything.

### Tool restrictions (Read, Grep, Glob, Bash)

Without hooks restricting tool use, the agent can use any tool to crawl around and discover context that would taint the test. This isn't limited to Read — Grep can search file contents across directories, Glob can discover file structure, and Bash can run arbitrary commands like `find`, `ls -la`, `cat`, or anything else that exposes the benchmark context. Any tool that allows the agent to gain knowledge of things outside the intended operating context compromises the sanctity of the test.

What the agent can currently discover through unrestricted tool use:

- `.claude/settings.local.json` — exposes the benchmark permissions structure
- `.git/` internals — full reflog, config, hooks directory (empty), etc.
- The full git history via `git log`, `git show`, `git diff` (see Section 2 — 93 commits visible)
- Parent directory structure via `ls ../`, `find / -name "benchmark"`, or similar
- Environment variables via `env` or `printenv` that could reveal the benchmark runner
- The `.home/` directory and its contents

The agent in plain-text/005 actually ran `git log --oneline -5` (trace line 252) during its session, proving it does access git history. If the agent discovers it's operating inside a benchmark, its behavior is compromised — it's no longer behaving as it would in a real development context.

Hooks should restrict all tools that could expose benchmark context: `git log` (beyond `git log --oneline -1`), `git show`, `git reflog`, `git diff HEAD~`, Bash commands that access `.git/` internals or parent directories, Read/Grep/Glob access to `.claude/settings.local.json`, `.home/`, and any path outside the project workspace.

### Memory

The CLI is invoked as `claude -p <prompt>` (runner.py line 117). There is no `--no-memory` CLI flag — memory is controlled either through the `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` environment variable or the `"autoMemoryEnabled": false` setting in `.claude/settings.json`. Neither mechanism is used anywhere in the benchmark runner. The `build_env()` method in environment.py does not set `CLAUDE_CODE_DISABLE_AUTO_MEMORY`, and the settings files written to the workspace do not include `autoMemoryEnabled`.

Claude Code's `-p` mode loads auto memory by default. Memories are stored at `~/.claude/projects/<project>/memory/` — which in the benchmark's case would be under the fake HOME at `.home/.claude/projects/`. Since each run gets a fresh workspace with a new `.home/`, memories from one run SHOULD NOT persist to the next. However, this relies on the workspace teardown actually removing the directory, and project-level memories (stored in the workspace itself rather than under HOME) could persist if the workspace is reused.

Two things must happen:

1. **Purge existing memories.** Previous benchmark runs executed without any memory restriction. Any memories created during those runs may still exist on the host machine — under `~/.claude/projects/<encoded-workspace-path>/memory/`, or anywhere else Claude Code stores project-indexed memories. These must be found and deleted before any new runs, because they would be loaded at session start and contaminate future results. Every prior run is suspect. Check the host machine directly for any memory files associated with benchmark workspace paths.

2. **Disable memory for all future runs.** The runner must set `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in the subprocess environment (via `build_env()`). After each run completes, before teardown, the runner should verify that no memory files were created — neither under `.home/.claude/projects/` nor anywhere in the workspace itself. If any are found, the run result should be flagged.

### Workspace state capture

`SetupSnapshot` captures only `file_list`, `git_log`, and `git_status`. This is insufficient to verify that isolation is working. The full workspace state should be captured comprehensively — CLAUDE.md content (not just existence), settings file content, hook configuration, memory state, and anything else that forms the agent's operating context. Without this, isolation failures are invisible unless someone manually inspects output files, which nobody does.

---

## 4. CLAUDE.md: Handling Correct but Unverified

### Evidence from output data

Checked `workspace_state.before.file_list` across all 5 formats:

| Format | CLAUDE.md in file_list | .claude/bivvy-dev-workflow.md in file_list |
|--------|----------------------|------------------------------------------|
| no-workflow | **No** | No |
| plain-text | **No** | No |
| markdown | **Yes** | No |
| ape | **Yes** | No |
| adhoc-xml | **Yes** | **Yes** |

CLAUDE.md is correctly absent for no-workflow and plain-text. It is correctly present for markdown, ape, and adhoc-xml. The fixture's `.claude/bivvy-dev-workflow.md` is only retained for adhoc-xml.

### What's missing

The data is captured but **never asserted.** `capture_setup_state()` stores `file_list` in `SetupSnapshot` which ends up in `workspace_state.before` in the output JSON. But nothing in the pipeline checks these values. If a future code change broke the deletion logic, the benchmark would silently run with the fixture's original CLAUDE.md and the only way to notice would be manual inspection of output JSON.

We also don't know if the CLAUDE.md **content** is correct for markdown and ape. The file is present, but its content could be the fixture's original 2000+ line CLAUDE.md instead of the benchmark workflow. The file list proves existence, not content. A content hash (or the full content) should be captured and asserted.

---

## 5. Commit Message Checks Skip Instead of Fail

### Evidence from output data

In no-workflow/004 (which has outcomes but no git commits):

```
commit_msg_imperative_mood:       SKIP - "No git commit messages found in trace"
commit_msg_subject_length:        SKIP - "No git commit messages found in trace"
commit_msg_capitalize_no_period:  SKIP - "No git commit messages found in trace"
commit_msg_no_type_prefix:        SKIP - "No git commit messages found in trace"
commit_msg_body_format:           SKIP - "No git commit messages found in trace"
```

Plus `coverage_threshold` skips with "No cargo llvm-cov results found in trace."

These 6 skipped checks are excluded from the denominator. The run reports 17/26 = 65.4% instead of 17/32 = 53.1%. The workflow mandates commits. Not committing should fail those checks, not skip them. Checks should not decide on their own whether to skip based on another check's outcome — if the workflow says "make commits", and there are no commits, that is a failure of every commit-related check.

### The commit message parsing bug (separate issue)

In plain-text/005 (the only run with commits in this batch), `commit_msg_capitalize_no_period` fails with:

```
got ['$(cat <<'], expected regex_match '^[A-Z].*[^.]$'
```

The agent used a heredoc for the commit message:
```
git commit -m "$(cat <<'EOF'
Reject unknown fields in config instead of silently defaulting
...
EOF
)"
```

The trace parser extracted `$(cat <<` as the commit message subject instead of the actual message content. This is a parsing bug in `trace.git_commit_messages()`.

---

## 6. tdd_prove_tests_fail: Works for Cross-File Patterns Only

### Evidence from output data

Checked all runs with outcomes across all formats:

| Run | Result | Detail |
|-----|--------|--------|
| no-workflow/000 | FAIL | cargo test not between test write and src/ write |
| no-workflow/001 | FAIL | cargo test not between test write and src/ write |
| no-workflow/004 | FAIL | cargo test not between test-content write and implementation |
| plain-text/000 | FAIL | `exists_between [[] (empty), [] (empty)]` — no test writes found |
| plain-text/001 | FAIL | cargo test not between test write and src/ write |
| plain-text/002 | FAIL | cargo test not between test write and src/ write |
| **plain-text/005** | **PASS** | (no detail recorded) |
| **markdown/005** | **PASS** | (no detail recorded) |
| **ape/005** | **PASS** | (no detail recorded) |
| **adhoc-xml/005** | **PASS** | (no detail recorded) |
| ape/000 | FAIL | `exists_between [[134], [134]]` — same event in both lists |

The check CAN pass — it passed in 4/20 runs. But it only works when tests and implementation go to different files. In ape/000, the detail shows `exists_between [[134], [134]]` — the same event index appears in both the test-content write list and the implementation write list. This happens when a single Write/Edit to a `src/` file contains both test code AND implementation code (Rust's inline test pattern). The `exists_between` window collapses to `lo=134, hi=134`, and `lo >= hi` returns false.

Note that the 4 passing runs record NO detail (see Section 1). There is no way to verify from the output JSON what evidence the evaluator used to determine these passed.

---

## 7. Graduated Scoring Must Be Removed

Scoring is binary pass/fail. There is no graduated scoring. All code related to graduated scoring must be removed. Locations to target:

**evaluator/evaluator.py:**
- `CheckResult.score` field (line 117)
- `_compute_graduated_score()` function (lines 1650–1799)
- Call to `_compute_graduated_score()` in `evaluate_check()` (line 1880)
- `score=score` assignment in `CheckResult` construction (line 1889)
- Return signature of `evaluate_condition_with_scoring()` that returns scoring data (lines 1336–1343)

**results/results.py:**
- `CheckOutcome.score` field (line 58)
- `RunSummary.mean_score` field (line 97)
- `score` parameter in `make_outcome()` (line 131)
- Mean score computation in `summarize_run()` (lines 160–163, 177)

**evaluator/test_evaluator.py:**
- Any tests for `_compute_graduated_score()` or graduated scoring behavior

**runner/runner.py:**
- `check_results_to_outcomes()` (line 296) — currently doesn't pass `score` through, but after removal the field shouldn't exist at all

---

## 8. Baseline Metrics Not Captured

### Evidence from output data

`SetupSnapshot` contains only:

- `file_list`: 266 files (git ls-tree)
- `git_log`: 93 commits (unsquashed history)
- `git_status`: clean working tree

No baseline coverage, test counts, build status, or lint status.

The `coverage_threshold` check expects >= 90%. In the one run where coverage was actually measured (plain-text/005), it got 36.54%. Without knowing the fixture's starting coverage, we can't distinguish "the agent barely improved coverage" from "the fixture started at 35% and the agent got it to 36.54%". The check target of 90% may be unreachable for this fixture/task combination.

Baseline metrics should be captured at setup time: run `cargo test`, `cargo llvm-cov`, `cargo build`, `cargo fmt --check`, `cargo clippy` and store the results. Use these to validate that check targets are achievable and to establish meaningful deltas.

---

## Summary

| # | Issue | Evidence |
|---|-------|----------|
| 1 | **Check outcomes do not record the data used to determine pass/fail** | `metric_value` and `target_value` are never written to output; `detail` is a summary string, not evidence; passes explicitly discard even the summary (line 1888) |
| 2 | **Git history completely unsquashed — 93 commits visible** | `workspace_state.before.git_log` in every output file shows full history |
| 3 | **No hooks, no tool restrictions (Read/Grep/Glob/Bash), no memory isolation** | Empty deny list in settings; `CLAUDE_CODE_DISABLE_AUTO_MEMORY` not set; `autoMemoryEnabled` not configured; agent runs `git log` during sessions |
| 4 | **CLAUDE.md handling correct but unverified** | file_list data shows correct presence/absence but nothing asserts it; content not captured |
| 5 | **Commit checks skip instead of fail when no commits** | 6 checks skipped in no-workflow/004, inflating pass rate from 53% to 65% |
| 5b | **Commit message parser broken (captures heredoc syntax)** | `got ['$(cat <<']` in plain-text/005 outcome |
| 6 | **tdd_prove_tests_fail works only for cross-file test patterns** | Passes 4/20 runs; fails with `[[134],[134]]` for inline tests; passing runs record no evidence |
| 7 | **Graduated scoring must be removed — binary pass/fail only** | Removal targets listed in evaluator.py, results.py, test_evaluator.py, runner.py |
| 8 | **Baseline functional metrics not captured** | SetupSnapshot has only file_list, git_log, git_status; coverage target may be unreachable |

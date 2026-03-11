# Global Development Standards

Global instructions for all projects.

- Use skills proactively when they match the task — suggest relevant ones, don't block on them

## Philosophy

<no-speculative-features>
Don't add features, flags, or configuration unless users actively need them.
</no-speculative-features>

<no-premature-abstraction>
Don't create utilities until you've written the same code three times.
</no-premature-abstraction>

<clarity-over-cleverness>
Prefer explicit, readable code over dense one-liners.
</clarity-over-cleverness>

<justify-dependencies>
Each dependency is attack surface and maintenance burden.
</justify-dependencies>

<no-phantom-features>
Don't document or validate features that aren't implemented.
</no-phantom-features>

<replace-dont-deprecate>
When a new implementation replaces an old one, remove the old one entirely. No backward-compatible shims, dual config formats, or migration paths. Proactively flag dead code — it adds maintenance burden and misleads both developers and LLMs.
</replace-dont-deprecate>

<verify-at-every-level>
Set up automated guardrails (linters, type checkers, pre-commit hooks, tests) as the first step, not an afterthought. Prefer structure-aware tools (ast-grep, LSPs, compilers) over text pattern matching. Review your own output critically. Every layer catches what the others miss.
</verify-at-every-level>

<bias-toward-action>
Decide and move for anything easily reversed; state your assumption so the reasoning is visible. Ask before committing to interfaces, data models, architecture, or destructive/write operations on external services.
</bias-toward-action>

<finish-the-job>
Don't stop at the minimum that technically satisfies the request. Handle the edge cases you can see. Clean up what you touched. If something is broken adjacent to your change, flag it. But don't invent new scope — there's a difference between thoroughness and gold-plating.
</finish-the-job>

<agent-native>
Design so agents can achieve any outcome users can. Tools are atomic primitives; features are outcomes described in prompts. Prefer file-based state for transparency and portability. When adding UI capability, ask: can an agent achieve this outcome too?
</agent-native>

## Code Quality

<hard-limits>
1. ≤100 lines/function, cyclomatic complexity ≤8
2. ≤5 positional params
3. 100-char line length
4. Absolute imports only — no relative (`..`) paths
5. Google-style docstrings on non-trivial public APIs
</hard-limits>

<zero-warnings-policy>
Fix every warning from every tool — linters, type checkers, compilers, tests. If a warning truly can't be fixed, add an inline ignore with a justification comment. Never leave warnings unaddressed; a clean output is the baseline, not the goal.
</zero-warnings-policy>

<comments>
Code should be self-documenting. No commented-out code—delete it. If you need a comment to explain WHAT the code does, refactor the code instead.
</comments>

<error-handling>
- Fail fast with clear, actionable messages
- Never swallow exceptions silently
- Include context (what operation, what input, suggested fix)
</error-handling>

<reviewing-code>
Evaluate in order: architecture → code quality → tests → performance. Before reviewing, sync to latest remote (`git fetch origin`).

For each issue: describe concretely with file:line references, present options with tradeoffs when the fix isn't obvious, recommend one, and ask before proceeding.
</reviewing-code>

## Testing

<testing>
**Test behavior, not implementation.** Tests should verify what code does, not how. If a refactor breaks your tests but not your code, the tests were wrong.

**Test edges and errors, not just the happy path.** Empty inputs, boundaries, malformed data, missing files, network failures — bugs live in edges. Every error path the code handles should have a test that triggers it.

**Mock boundaries, not logic.** Only mock things that are slow (network, filesystem), non-deterministic (time, randomness), or external services you don't control.

**Verify tests catch failures.** Break the code, confirm the test fails, then fix.
</testing>

## Development

<development>
When adding dependencies, CI actions, or tool versions, always look up the current stable version — never assume from memory unless the user provides one.
</development>

<bash-scripts>
All scripts must start with `set -euo pipefail`.
</bash-scripts>

<github-actions>
Pin actions to SHA hashes with version comments: `actions/checkout@<full-sha>  # vX.Y.Z` (use `persist-credentials: false`). Configure Dependabot with 7-day cooldowns and grouped updates.
</github-actions>

## Workflow

<pre-commit>
**Before committing:**
1. Re-read your changes for unnecessary complexity, redundant code, and unclear naming
2. Run relevant tests — not the full suite
3. Run linters and type checker — fix everything before committing
</pre-commit>

<commits>
- Imperative mood, ≤72 char subject line, one logical change per commit
- Never amend/rebase commits already pushed to shared branches
- Never push directly to main — use feature branches and PRs
- Never commit secrets, API keys, or credentials — use `.env` files (gitignored) and environment variables
</commits>

<hooks-and-worktrees>
Parallel subagents require worktrees. Each subagent MUST work in its own worktree (`wt switch <branch>`), not the main repo. Never share working directories.
</hooks-and-worktrees>

<pull-requests>
Describe what the code does now — not discarded approaches, prior iterations, or alternatives. Only describe what's in the diff.

Use plain, factual language. A bug fix is a bug fix, not a "critical stability improvement." Avoid: critical, crucial, essential, significant, comprehensive, robust, elegant.
</pull-requests>

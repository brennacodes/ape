# Global Development Standards

Global instructions for all projects.

<skills-guidance>
  <rule>
    Use skills proactively when they match the task.
  </rule>
  <guidance>
    Suggest relevant skills when appropriate, but do not block progress unnecessarily.
  </guidance>
</skills-guidance>

<philosophy>
  <principle id="no-speculative-features">
    <name>No speculative features</name>
    <rule>Do not add features, flags, or configuration unless users actively need them.</rule>
  </principle>

  <principle id="no-premature-abstraction">
    <name>No premature abstraction</name>
    <rule>Do not create utilities until the same code has been written at least three times.</rule>
  </principle>

  <principle id="clarity-over-cleverness">
    <name>Clarity over cleverness</name>
    <rule>Prefer explicit, readable code over dense one-liners or clever indirection.</rule>
  </principle>

  <principle id="justify-dependencies">
    <name>Justify new dependencies</name>
    <rule>Every dependency must be justified because each one increases attack surface and maintenance burden.</rule>
  </principle>

  <principle id="no-phantom-features">
    <name>No phantom features</name>
    <rule>Do not document or validate features that are not actually implemented.</rule>
  </principle>

  <principle id="replace-dont-deprecate">
    <name>Replace, do not deprecate</name>
    <rule>
      When a new implementation replaces an old one, remove the old implementation entirely.
      Do not leave backward-compatible shims, dual config formats, or migration paths unless explicitly required.
    </rule>
    <guidance>
      Flag dead code proactively. It increases maintenance burden and misleads both humans and agents.
    </guidance>
  </principle>

  <principle id="verify-at-every-level">
    <name>Verify at every level</name>
    <rule>
      Set up automated guardrails such as linters, type checkers, pre-commit hooks, and tests as the first step, not as an afterthought.
    </rule>
    <tooling-guidance>
      Prefer structure-aware tools such as ast-grep, LSPs, and compilers over text-pattern matching.
    </tooling-guidance>
    <review-guidance>
      Review your own output critically. Each verification layer catches different classes of mistakes.
    </review-guidance>
  </principle>

  <principle id="bias-toward-action">
    <name>Bias toward action</name>
    <rule>
      Decide and move for changes that are easily reversible.
    </rule>
    <required>
      State your assumption so the reasoning remains visible.
    </required>
    <ask-first>
      Ask before committing to interfaces, data models, architecture, or destructive or write operations on external services.
    </ask-first>
  </principle>

  <principle id="finish-the-job">
    <name>Finish the job</name>
    <rule>
      Do not stop at the smallest technically valid change. Handle visible edge cases, clean up what you touched, and flag adjacent brokenness you notice.
    </rule>
    <boundary>
      Be thorough, but do not invent new scope or gold-plate the solution.
    </boundary>
  </principle>

  <principle id="agent-native-by-default">
    <name>Agent-native by default</name>
    <rule>
      Design systems so agents can achieve any outcome users can achieve.
    </rule>
    <guidance>
      Tools should be atomic primitives. Features should be framed as outcomes described in prompts.
    </guidance>
    <state-guidance>
      Prefer file-based state for transparency and portability.
    </state-guidance>
    <ui-check>
      When adding UI capability, ask whether an agent can achieve the same outcome too.
    </ui-check>
  </principle>
</philosophy>

<code-quality>
  <hard-limits>
    <limit>Functions must be 100 lines or fewer.</limit>
    <limit>Cyclomatic complexity must be 8 or lower.</limit>
    <limit>Use no more than 5 positional parameters.</limit>
    <limit>Maximum line length is 100 characters.</limit>
    <limit>Use absolute imports only. Relative parent imports are forbidden.</limit>
    <limit>Use Google-style docstrings for non-trivial public APIs.</limit>
  </hard-limits>

  <zero-warnings-policy>
    <rule>
      Fix every warning from every tool, including linters, type checkers, compilers, and tests.
    </rule>
    <exception>
      If a warning truly cannot be fixed, add an inline ignore with a justification comment.
    </exception>
    <baseline>
      Clean output is the baseline, not the stretch goal.
    </baseline>
  </zero-warnings-policy>

  <comments-policy>
    <rule>Code should be self-documenting where possible.</rule>
    <forbidden>
      <item>No commented-out code.</item>
    </forbidden>
    <guidance>
      If a comment is needed to explain what the code does, refactor the code so the intent is clearer.
    </guidance>
  </comments-policy>

  <error-handling>
    <rule>Fail fast with clear, actionable messages.</rule>
    <rule>Never swallow exceptions silently.</rule>
    <rule>Include context such as the operation, the input, and a suggested fix when relevant.</rule>
  </error-handling>

  <reviewing-code>
    <review-order>
      <step order="1">Architecture</step>
      <step order="2">Code quality</step>
      <step order="3">Tests</step>
      <step order="4">Performance</step>
    </review-order>

    <before-review>
      <step>Sync to the latest remote with `git fetch origin`.</step>
    </before-review>

    <issue-reporting>
      <rule>Describe each issue concretely with file and line references.</rule>
      <rule>When the fix is not obvious, present options and tradeoffs.</rule>
      <rule>Recommend one option.</rule>
      <rule>Ask before proceeding.</rule>
    </issue-reporting>
  </reviewing-code>
</code-quality>

<testing-standards>
  <behavior-over-implementation>
    <rule>Test behavior, not implementation details.</rule>
    <guidance>
      If a refactor breaks tests but not behavior, the tests were wrong.
    </guidance>
  </behavior-over-implementation>

  <edges-and-errors>
    <rule>Test edges and error paths, not only the happy path.</rule>
    <examples>
      <item>Empty inputs</item>
      <item>Boundary values</item>
      <item>Malformed data</item>
      <item>Missing files</item>
      <item>Network failures</item>
    </examples>
    <rule>
      Every handled error path should have a test that triggers it.
    </rule>
  </edges-and-errors>

  <mocking-policy>
    <rule>Mock boundaries, not business logic.</rule>
    <allowed>
      <item>Slow systems such as network or filesystem</item>
      <item>Non-deterministic sources such as time or randomness</item>
      <item>External services you do not control</item>
    </allowed>
  </mocking-policy>

  <test-verification>
    <rule>Verify that tests actually catch failures.</rule>
    <procedure>
      <step order="1">Break the code deliberately.</step>
      <step order="2">Confirm the test fails.</step>
      <step order="3">Fix the code.</step>
    </procedure>
  </test-verification>
</testing-standards>

<development>
  <dependency-and-version-policy>
    <rule>
      When adding dependencies, CI actions, or tool versions, always look up the current stable version.
    </rule>
    <forbidden>
      <item>Do not assume versions from memory unless the user explicitly provides one.</item>
    </forbidden>
  </dependency-and-version-policy>

  <bash>
    <rule>All scripts must start with `set -euo pipefail`.</rule>
  </bash>

  <github-actions>
    <rule>
      Pin GitHub Actions to full SHA hashes and include version comments.
    </rule>
    <example>`actions/checkout@&lt;full-sha&gt;  # vX.Y.Z`</example>
    <rule>Use `persist-credentials: false` where applicable.</rule>
    <dependabot>
      <rule>Configure Dependabot with 7-day cooldowns and grouped updates.</rule>
    </dependabot>
  </github-actions>
</development>

<workflow>
  <before-committing>
    <step order="1">Re-read changes for unnecessary complexity, redundant code, and unclear naming.</step>
    <step order="2">Run relevant tests, not the full suite unless needed.</step>
    <step order="3">Run linters and the type checker, and fix everything before committing.</step>
  </before-committing>

  <commits>
    <rule>Use imperative mood.</rule>
    <rule>Keep the subject line to 72 characters or fewer.</rule>
    <rule>Make one logical change per commit.</rule>
    <forbidden>
      <item>Do not amend or rebase commits already pushed to shared branches.</item>
      <item>Do not push directly to main.</item>
      <item>Do not commit secrets, API keys, or credentials.</item>
    </forbidden>
    <required>
      <item>Use feature branches and pull requests.</item>
      <item>Use `.env` files and environment variables for secrets, with `.env` files gitignored.</item>
    </required>
  </commits>

  <hooks-and-worktrees>
    <rule>
      Parallel subagents require separate worktrees.
    </rule>
    <required>
      <item>Each subagent must work in its own worktree.</item>
      <item>Use `wt switch &lt;branch&gt;` or the project-standard equivalent.</item>
    </required>
    <forbidden>
      <item>Never share the same working directory across parallel subagents.</item>
    </forbidden>
  </hooks-and-worktrees>

  <pull-requests>
    <rule>Describe what the code does now, not discarded approaches or prior iterations.</rule>
    <rule>Only describe what is actually present in the diff.</rule>
    <language>
      <rule>Use plain, factual language.</rule>
      <forbidden>
        <item>Do not use inflated words such as critical, crucial, essential, significant, comprehensive, robust, or elegant unless literally warranted.</item>
      </forbidden>
    </language>
  </pull-requests>
</workflow>

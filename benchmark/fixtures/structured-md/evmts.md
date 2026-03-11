# CLAUDE.md

## Mission Context

**⚠️ WARNING: Mission-critical infrastructure. Bugs cause business loss.**

Every line of code must be correct. Zero error tolerance.

<mission-critical>
  <rule>
    Treat all changes as high-consequence. Correctness takes priority over speed.
  </rule>
  <rule>
    If uncertain, stop and surface the uncertainty rather than guessing.
  </rule>
</mission-critical>

<working-directory>
  <rule>
    Always run commands from the repository root directory.
  </rule>
  <forbidden>
    <item>Do not use `cd` during normal work.</item>
  </forbidden>
  <exception>
    `cd` is allowed only when debugging a submodule.
  </exception>
  <rationale>
    All commands, builds, and tests are designed to run from the repository root.
  </rationale>
</working-directory>

<security>
  <sensitive-data>
    <trigger>API keys, passwords, tokens, or other secrets detected</trigger>
    <required-actions>
      <step order="1">Abort the task.</step>
      <step order="2">Explain why execution stopped.</step>
      <step order="3">Request a sanitized prompt or sanitized data.</step>
    </required-actions>
  </sensitive-data>

  <verification-rule>
    Every change must be tested and verified.
  </verification-rule>

  <crash-policy>
    <rule>
      Unhandled crashes are severe bugs.
    </rule>
    <rule>
      Any unhandled crash indicates missing validation or error handling.
    </rule>
    <required-behavior>
      The application must always return errors gracefully and must never crash.
    </required-behavior>
    <fix-order>
      <step order="1">Fix the validation or error handling that allowed the crash.</step>
      <step order="2">Fix the bug that triggered the crash.</step>
    </fix-order>
  </crash-policy>
</security>

<build-verification>
  <trigger>Every code change</trigger>
  <required-actions>
    <step order="1">Run the build.</step>
    <step order="2">Run the tests.</step>
    <step order="3">Verify results before finishing.</step>
  </required-actions>
  <exception>
    Markdown-only changes are exempt from build and test execution.
  </exception>
  <development-style>
    Follow TDD.
  </development-style>
</build-verification>

<debugging>
  <rule>
    If the cause of a bug is not obvious, improve visibility first before attempting a fix.
  </rule>
  <examples>
    <item>Add logging</item>
    <item>Inspect intermediate state</item>
    <item>Improve assertions</item>
    <item>Increase observability of failing conditions</item>
  </examples>
</debugging>

<zero-tolerance>
  <forbidden>
    <item>Broken builds</item>
    <item>Broken tests</item>
    <item>Stub implementations</item>
    <item>Placeholder implementations that pretend to work</item>
    <item>Commented-out code</item>
    <item>Skipping tests</item>
    <item>Commenting out tests</item>
    <item>Fallback implementations that hide missing behavior</item>
    <item>Swallowing errors</item>
  </forbidden>

  <stub-examples>
    <item>`raise NotImplementedError`</item>
    <item>`throw new Error("not implemented")`</item>
    <item>Fake success states</item>
    <item>"Coming soon" behavior in code paths that should be complete</item>
  </stub-examples>

  <swallowed-error-examples>
    <item>empty `catch {}` blocks</item>
    <item>`except: pass`</item>
    <item>`rescue => nil`</item>
  </swallowed-error-examples>

  <required-response>
    Stop and ask for help rather than stubbing, faking, skipping, or hiding failure.
  </required-response>

  <placeholder-rationale>
    Placeholder implementations create ambiguity. They make it unclear whether the feature is unfinished, blocked, abandoned, or falsely presented as working. This wastes debugging time and erodes trust.
  </placeholder-rationale>

  <error-handling-rule>
    Every error must be explicitly handled or explicitly propagated. Never ignore errors silently.
  </error-handling-rule>
</zero-tolerance>

<coding-standards>
  <principles>
    <item>Prefer minimal use of `else` statements.</item>
    <item>Use direct imports. Do not alias imports unless absolutely required.</item>
    <item>Use descriptive variable names.</item>
    <item>Assertions must include descriptive messages.</item>
    <item>Always pair resource acquisition with cleanup or release.</item>
  </principles>

  <naming-guidance>
    Prefer names like `top`, `value`, and `operand` rather than `a` or `b`.
  </naming-guidance>
</coding-standards>

<testing-philosophy>
  <test-structure>
    <rule>No abstractions in tests.</rule>
    <rule>No helpers in tests.</rule>
    <rule>Prefer copy/paste setup over indirection.</rule>
    <rule>Tests must be self-contained.</rule>
  </test-structure>

  <debugging-standard>
    Evidence-based debugging only.
  </debugging-standard>

  <failure-policy>
    Test failures must be fixed immediately.
  </failure-policy>

  <output-expectations>
    <rule>Always print test results.</rule>
    <rule>If tests produce no output, they passed successfully.</rule>
    <rule>Only failed tests should produce output.</rule>
  </output-expectations>
</testing-philosophy>

<collaboration>
  <proposal-policy>
    Present proposals first, then wait for approval before proceeding with significant changes.
  </proposal-policy>

  <failure-boundary>
    If the plan fails, stop, explain what happened, and wait for guidance.
  </failure-boundary>
</collaboration>

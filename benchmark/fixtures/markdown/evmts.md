# CLAUDE.md

## MISSION CRITICAL SOFTWARE

**⚠️ WARNING: Mission-critical infrastructure - bugs cause business loss.**

Every line of code must be correct. Zero error tolerance.

## Core Protocols

### Working Directory

**ALWAYS run commands from the repository root directory.** Never use `cd` except when debugging a submodule. All commands, builds, and tests are designed to run from root.

### Security

- Sensitive data detected (API keys/passwords/tokens): abort, explain, request sanitized prompt
- Every change must be tested and verified
- **CRITICAL: Unhandled crashes are SEVERE BUGS** - Any unhandled crash indicates missing validation. The application must ALWAYS return errors gracefully, never crash. Before fixing the bug that triggered the crash, FIRST fix the validation/error handling that allowed the crash to occur.

### Build Verification

**EVERY code change**: run build and test commands
**Exception**: .md files only

Follow TDD

### Debugging

- Bug not obvious = improve visibility first

### Zero Tolerance

❌ Broken builds/tests
❌ Stub implementations (e.g., `raise NotImplementedError`, `throw new Error("not implemented")`)
❌ Commented code (use Git)
❌ Test failures
❌ Skipping/commenting tests
❌ Any stub/fallback implementations
❌ **Swallowing errors (e.g., empty `catch {}` blocks, bare `except: pass`, `rescue => nil`)**

**STOP and ask for help rather than stubbing.**

**WHY PLACEHOLDERS ARE BANNED**: Placeholder implementations create ambiguity - the human cannot tell if "Coming soon!" or simplified output means:
1. The AI couldn't solve it and gave up
2. The AI is planning to implement it later
3. The feature genuinely isn't ready yet
4. There's a technical blocker

This uncertainty wastes debugging time and erodes trust. Either implement it fully, explain why it can't be done, or ask for help. Never leave placeholders that pretend to work.

**NEVER swallow errors! Every error must be explicitly handled or propagated. Ignoring errors can cause silent failures and data loss.**

## Coding Standards

### Principles

- Minimal else statements
- Direct imports (no aliases)
- Descriptive variable names (`top`, `value`, `operand` not `a`, `b`)
- Assertions must include descriptive messages
- Clean up resources: always pair acquisition with cleanup/release

## Testing Philosophy

- NO abstractions - copy/paste setup
- NO helpers - self-contained tests
- Test failures = fix immediately
- Evidence-based debugging only
- **CRITICAL**: always print test results
- If tests produce no output, they PASSED successfully
- Only failed tests produce output

## Collaboration

- Present proposals, wait for approval
- Plan fails: STOP, explain, wait for guidance



# CLAUDE.md

## MISSION CRITICAL SOFTWARE

**⚠️ WARNING: Mission-critical infrastructure - bugs cause business loss.**

Every line of code must be correct. Zero error tolerance.

## Core Protocols

### Working Directory

**ALWAYS run commands from the repository root directory.** Never use `cd` except when debugging a submodule. All commands, builds, and tests are designed to run from root.

### Security

- Sensitive data detected (API keys/passwords/tokens): abort, explain, request sanitized prompt
- Memory safety: plan ownership/deallocation for every allocation
- Every change must be tested and verified
- Use SafetyCounter for infinite loop prevention (300M instruction limit)
- **CRITICAL: Crashes are SEVERE SECURITY BUGS** - Any crash (e.g., from `std.debug.assert`) indicates memory unsafety or missing validation. The EVM must ALWAYS return errors gracefully, never crash. Before fixing the bug that triggered the crash, FIRST fix the validation/error handling that allowed the crash to occur.

### Build Verification

**EVERY code change**: run build and test commands
**Exception**: .md files only

Follow TDD

### Debugging

- Bug not obvious = improve visibility first

### Zero Tolerance

❌ Broken builds/tests
❌ Stub implementations (`error.NotImplemented`)
❌ Commented code (use Git)
❌ Test failures
❌ Skipping/commenting tests
❌ Any stub/fallback implementations
❌ **Swallowing errors with `catch` (e.g., `catch {}`, `catch &.{}`, `catch null`)**

**STOP and ask for help rather than stubbing.**

**WHY PLACEHOLDERS ARE BANNED**: Placeholder implementations create ambiguity - the human cannot tell if "Coming soon!" or simplified output means:
1. The AI couldn't solve it and gave up
2. The AI is planning to implement it later
3. The feature genuinely isn't ready yet
4. There's a technical blocker

This uncertainty wastes debugging time and erodes trust. Either implement it fully, explain why it can't be done, or ask for help. Never leave placeholders that pretend to work.

**NEVER swallow errors! Every error must be explicitly handled or propagated. Using `catch` to ignore errors can cause silent failures and fund loss.**

## Coding Standards

### Principles

- Minimal else statements
- Single word variables (`n` not `number`)
- Direct imports (`address.Address` not aliases)
- Tests in source files
- Defer patterns for cleanup
- Always follow allocations with defer/errDefer
- Descriptive variables (`top`, `value1`, `operand` not `a`, `b`)
- Assertions: `tracer.assert(condition, "message")`
- Stack semantics: LIFO order (first pop = top)

## Testing Philosophy

- NO abstractions - copy/paste setup
- NO helpers - self-contained tests
- Test failures = fix immediately
- Evidence-based debugging only
- **CRITICAL**: always print test results
- If tests produce no output, they PASSED successfully
- Only failed tests produce output

## Commands

### Basic Commands


### Test Organization

**Test Categories:**


**Test Aggregator Files:**




### Design Patterns

1. Strong error types per component
2. Unsafe ops for performance (pre-validated)
3. Cache-conscious struct layout
4. Handler tables for O(1) dispatch
5. Bytecode optimization via Dispatch

### Understanding `_unsafe` Operations

**DO NOT file bugs about `_unsafe` operations lacking runtime bounds checks - that's the entire point.**

The `_unsafe` suffix is a deliberate naming convention indicating:
- **Caller is responsible for validation** (like Rust's `unsafe` blocks)
- **Bounds checks are skipped for performance** - this is intentional, not a bug
- **Pre-validation happens elsewhere** - the dispatch system validates stack requirements at bytecode analysis time

## Collaboration

- Present proposals, wait for approval
- Plan fails: STOP, explain, wait for guidance

## GitHub Issue Management

Always disclose Claude AI assistant actions:
"*Note: This action was performed by Claude AI assistant"

Required for: creating, commenting, closing, updating issues and all GitHub API operations.


# Bivvy Development Workflow

Bivvy is a cross-language development environment setup automation tool built in Rust. This guide covers the development workflow and conventions used on the project.

## Core Principles

- **Atomic commits** — each commit should be complete and independent, containing both code and tests.
- **Test-first** — tests define behavior before implementation begins.
- **Zero warnings** — warnings are treated as errors and should be fixed immediately.
- **Small steps** — prefer many small commits over few large ones.

## The Development Pipeline

Follow these steps exactly in order. Each step must be completed before moving to the next.

### 1. Specification (Tests First)

Start by writing or updating tests that specify the intended behavior. Run the full test suite to confirm the new tests fail for the right reasons — they should fail because the functionality doesn't exist yet, not because of syntax errors or test bugs.

```sh
cargo test --all-features
```

If the tests pass unexpectedly or fail for the wrong reasons, refine them until they correctly specify the missing behavior before continuing.

### 2. Implementation

With failing tests in hand, write the minimum code needed to make them pass. If you find yourself writing more code than the tests demand, stop and reconsider. Don't use stubs, placeholders, or `unimplemented!()` macros.

Run the full test suite again:

```sh
cargo test --all-features
```

Fix bugs until all tests are green. Never skip, disable, or modify tests to make them pass, and never move on while tests are failing.

### 3. Documentation

After the implementation passes all tests, add or update documentation. Add `///` doc comments to all public items. Keep inline comments limited to non-obvious rationale.

Run the doc build to verify there are no documentation errors:

```sh
cargo doc --no-deps --all-features
```

Fix any doc errors before continuing.

### 4. Linting

Once documentation is clean, run the linter to catch style and static analysis issues:

```sh
cargo fmt -- --check && cargo clippy --all-targets --all-features -- -D warnings
```

The linter must pass with zero warnings. If it doesn't, go back and fix all issues before continuing.

### 5. Testing and Coverage

After linting, run the full test suite and verify coverage meets the minimum threshold of 90%:

```sh
cargo test --all-features
cargo llvm-cov --all-features
```

All tests must pass and coverage must be at or above 90%. If either fails, go back and fix the implementation.

### 6. Build

Run both development and release builds to confirm the code compiles cleanly:

```sh
cargo build --all-targets --all-features
cargo build --release
```

Both builds must succeed before committing.

### 7. Commit

Create one atomic commit covering both code and tests for a single logical change. If the change is too large, split it into smaller commits.

Stage files selectively — never use `git add -A` or `git add .`.

Commit message conventions:
- Use imperative mood ("Add feature" not "Added feature")
- Keep the subject line to 50 characters or less
- Capitalize the first letter
- No period at the end of the subject line
- No type prefixes (don't use "feat:", "fix:", etc.)
- Blank line between subject and body; body wraps at 72 characters

### 8. Post-Commit Verification

After committing, re-run the test suite and build to confirm the committed state is clean:

```sh
cargo test --all-features
cargo build
git log -1 --stat
```

All tests and the build must pass. If anything fails, go back and fix it.

## Test Runner Rules

Always run the full test suite with all features enabled. Never filter to a subset of tests.

```sh
# correct
cargo test --all-features

# incorrect — don't do these
cargo test config
cargo test --lib
cargo test some_module::
```

If the test output shows "X filtered out," the wrong command was used. Every verification step must run the entire suite.

## Anti-Patterns

- Large commits with multiple unrelated changes
- Over-engineering or adding features that weren't requested

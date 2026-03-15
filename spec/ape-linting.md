# APE Linting Rules

**Version:** 0.3.0
**Companion to:** `ape-spec.md` (Section 23: Validation Architecture)

---

## Purpose

The XSD validates **shape** — tag names, attribute types, parent-child relationships. It answers: "Is this valid XML that looks like APE?"

This document defines the rules that the XSD cannot express: **semantic correctness** and **taste**. It answers: "Does this APE document mean what the author thinks it means?"

Every rule has a unique ID, a severity, a description, and at least one example. These rules are the contract that authors, LLM executors, and any future tooling must follow.

---

## Severity Levels

| Severity | Meaning | Who must enforce |
|---|---|---|
| **ERROR** | The document is semantically broken. | LLM executors must halt. Authors must fix before use. |
| **WARNING** | The document is valid but likely wrong. The author probably didn't mean this. | Authors should fix. LLM executors may report but need not halt. |
| **INFO** | The document works but could be better. Taste, not correctness. | Authoring-time concern only. |

---

## Reference Resolution

### E001: Unresolvable `ref`

**Severity:** ERROR

A `ref` attribute on `<command>`, `<output>`, `<template>`, `<write>`, `<criteria>`, or `<criterion>` does not resolve to a declared `id` of the correct type. For `<criteria>` and `<criterion>`, `ref` resolves to a `<criteria>` or `<criterion>` `id` only.

```xml
<!-- ERROR: no command with id="run-tests" exists -->
<command ref="run-tests" />
```

### E002: Unresolvable `goto`

**Severity:** ERROR

A `goto` attribute or `<goto ref>` does not resolve to a `<step>` `id`.

```xml
<!-- ERROR: no step with id="cleanup" exists -->
<on-fail goto="cleanup" />
```

### E003: Unresolvable `uses`

**Severity:** ERROR

A token in a `uses` attribute does not resolve to a `<resource>` `id`.

```xml
<!-- ERROR: no resource with id="database" exists -->
<step id="migrate" uses="database">
```

### E004: Unresolvable `ref_id`

**Severity:** ERROR

A `ref_id` attribute on `<reference>` does not resolve to any element with that `id`.

### E005: Unresolvable variable interpolation

**Severity:** ERROR

A `{{ identifier }}` token does not resolve to an in-scope variable (declared via `<var>`, `<command set>`, or `<ask-user-question var>`).

---

## Identity and Uniqueness

### E010: Duplicate global ID

**Severity:** ERROR

Two elements share the same `id` in global scope. Global ID namespaces: `resource`, `command`, `sequence`, `step`, `template`, `output`, `action`, `criteria`.

```xml
<!-- ERROR: two commands with id="build" -->
<command id="build">cargo build</command>
<command id="build">npm run build</command>
```

### E011: Duplicate scoped name

**Severity:** ERROR

Two `<var>` declarations with the same `name` attribute exist in the same scope (not counting legitimate shadowing across nested scopes). Two `<param>` declarations with the same `ref` in the same parameter block.

### E012: Duplicate `<case>` value

**Severity:** ERROR

Two `<case>` elements within the same `<conditional>` share a `value` attribute.

---

## Mutual Exclusivity

### E020: `id` and `ref` on same `<command>`

**Severity:** ERROR

A `<command>` element has both `id` and `ref` attributes.

### E021: Non-empty body with `ref`

**Severity:** ERROR

A `<command>` or `<template>` with a `ref` attribute has non-empty body text (`trim(textContent) != ""`).

### E022: `ref` and `to` on same `<output>`

**Severity:** ERROR

An `<output>` element has both `ref` and `to` attributes.

### E023: `<conditional>` dual default

**Severity:** ERROR

A `<conditional>` has both a `default` attribute and a `<default>` child element.

### E024: `ref` and `check` on same `<criterion>`

**Severity:** ERROR

A `<criterion>` element has both `ref` and `check` attributes. They are mutually exclusive — exactly one must be present.

### E025: `<criterion>` missing `ref` or `check`

**Severity:** ERROR

A `<criterion>` element has neither `ref` nor `check`. Exactly one must be present.

```xml
<!-- ERROR: both ref and check -->
<criterion ref="run-tests" check="{{ test_failures == 0 }}" />

<!-- ERROR: neither ref nor check -->
<criterion />

<!-- VALID: ref to a named criteria block -->
<criterion ref="linter-checks" />

<!-- VALID: inline check -->
<criterion check="{{ test_failures == 0 }}" />
```

### E026: `<criterion>` `ref` resolving to action or command

**Severity:** ERROR

A `<criterion>` uses `ref` to point at an `<action>` or `<command>`. This leaves pass/fail semantics ambiguous — nothing in the referenced element defines what constitutes success or failure. `<criterion ref>` must resolve to a `<criteria>` or `<criterion>` `id` — elements with explicit `check` expressions that define unambiguous pass/fail.

```xml
<!-- ERROR: ref points to an action — pass/fail is undefined -->
<action id="run-tests"><command ref="run-tests" /></action>
<criterion ref="run-tests" />

<!-- ERROR: ref points to a command — same problem -->
<criterion ref="build-dev" />

<!-- VALID: ref points to a named criteria block -->
<criteria id="linter-checks" operator="and">
  <criterion check="{{ linter_errors == 0 }}" />
  <criterion check="{{ linter_warnings == 0 }}" />
</criteria>
<!-- ...later... -->
<criterion ref="linter-checks" />

<!-- VALID: ref points to a named criterion -->
<criterion id="no-test-failures" check="{{ test_failures == 0 }}" />
<!-- ...later... -->
<criterion ref="no-test-failures" />
```

---

## Required Children and Attributes

### E030: `<gate>` missing condition

**Severity:** ERROR

A `<gate>` does not contain exactly one `<criteria>` element or exactly one `<criterion>` element. A gate must have one or the other (not both, not neither).

### E031: `<gate>` missing `<on-fail>`

**Severity:** ERROR

A `<gate>` does not contain exactly one `<on-fail>` element.

### E032: `<on-fail>` missing primary flow-control

**Severity:** ERROR

An `<on-fail>` does not carry exactly one primary flow-control attribute (`goto`, `retry`, `halt`, `proceed`).

### E033: `<conditional>` missing `on`

**Severity:** ERROR

A `<conditional>` does not have a non-empty `on` attribute.

### E034: `<conditional>` missing `<case>`

**Severity:** ERROR

A `<conditional>` contains no `<case>` children.

### E035: `<var>` missing value

**Severity:** ERROR

A `<var>` declaration has no value via element content, `value` attribute, `default` attribute, or `<command>`/`<resource>` child.

---

## Flow Control

### E040: `max` without `retry`

**Severity:** ERROR

An `<on-fail>` has a `max` attribute but `retry` is not `true`.

### E041: `then` without `retry`

**Severity:** ERROR

An `<on-fail>` has a `then` attribute but `retry` is not `true`.

### E042: Invalid `<on-pass>` attribute

**Severity:** ERROR

An `<on-pass>` carries an attribute other than `goto` or `proceed` (e.g., `retry`, `halt`).

---

## Content Model

### E050: Prose inside structure container

**Severity:** ERROR

An `<action>`, `<criteria>`, `<criterion>`, `<on-fail>`, `<on-pass>`, `<case>`, `<default>`, `<each>`, or `<sequence>` contains non-whitespace text content.

```xml
<!-- ERROR: prose inside action -->
<action>
  Run the tests and check the output.
  <command ref="run-tests" />
</action>
```

### E051: Executable child inside `<instruction>`

**Severity:** ERROR

An `<instruction>` contains `<command>`, `<action>`, `<output>`, or a tool tag.

### E052: `<note>` inside `<instruction>` or `<action>`

**Severity:** ERROR

A `<note>` appears inside `<instruction>` or `<action>`. `<note>` is allowed only at step level or root `<ape>` level.

### E053: Structural semantics in prose container

**Severity:** ERROR

A prose container (`<note>`, `<instruction>`, `<constraint>`, `<rule>`, `<principle>`, `<anti-pattern>`, `<description>`) contains conditional logic or flow-control directives that should be expressed structurally.

The core principle is "structure over prose." If prose describes navigation, branching, or conditional behavior, the author is smuggling structure into a prose container — the inverse of E050.

**Patterns that trigger this rule:**

- **Conditional logic:** "if [condition], [action]" / "when [condition]" / "unless [condition]" — belongs in a `<gate>`, `<conditional>`, or `<prerequisite>`.
- **Flow-control directives:** "return to [step]" / "go back to" / "proceed to" / "skip" / "repeat" — belongs in `goto`, `retry`, `proceed`, or `halt` attributes on `<on-fail>` or `<on-pass>`.
- **Imperative navigation:** "move to [step]" / "continue with" / "start over" — same as flow-control.

```xml
<!-- ERROR: conditional logic and flow-control directive in a note.
     "If tests pass unexpectedly" is a conditional.
     "return to refine them" is a goto.
     Both are already expressed by the gate below. -->
<note>Gate logic is inverted: test failure (action fails) is the expected
  result, proving the tests specify new behavior. If tests pass
  unexpectedly, return to refine them.</note>

<gate>
  <criterion check="{{ test_failures == 0 }}" />
  <on-fail proceed="true" />
  <on-pass goto="specification" />
</gate>
```

```xml
<!-- ERROR: conditional and flow-control in a constraint -->
<constraint>If coverage drops below 90%, go back to implementation
  and add tests.</constraint>

<!-- VALID: structure expresses this -->
<gate>
  <criterion check="{{ coverage_pct >= min_coverage }}" />
  <on-fail goto="implementation" />
</gate>
```

**How to evaluate:** Scan prose for if/when/unless patterns paired with imperative actions, and for any language that directs the executor to navigate to a step or change execution flow. If the directive maps to a structural element (`<gate>`, `<conditional>`, `<prerequisite>`, `<on-fail>`, `<on-pass>`), the prose is a violation. This applies to **all** prose containers, including `<instruction>`.

**No exceptions.** If a conditional appears in prose, it can be expressed structurally. "If the user prefers dark mode, ask which theme" is a `<conditional>` on a value captured by `<ask-user-question>`. "If writing more code than tests demand, stop" is a `<gate>`. The fact that a conditional involves human judgment does not exempt it from structural expression — it means the author needs to design the structure that captures the decision (via `<ask-user-question>`, `<command set="...">`, or a gate criterion), not bury it in prose.

---

## Ordering

### E060: Prerequisite not first

**Severity:** ERROR

A `<prerequisite>` or `<prerequisites>` is not the first child element of its `<step>`.

### E061: `<default>` before `<case>`

**Severity:** ERROR

A `<default>` element appears before a `<case>` element within its `<conditional>`.

### E062: `<constraint>` after prose in `<instruction>`

**Severity:** ERROR

A `<constraint>` inside an `<instruction>` appears after prose text or executable content.

---

## Comments

### E070: XML comment present

**Severity:** ERROR

The document contains an XML comment (`<!-- -->`). Use `<note>` for author-facing context.

---

## Document Structure

### E080: `<meta>` not first

**Severity:** ERROR

A `<meta>` element exists but is not the first child of `<ape>` (or the first child after `<preamble>`, if a preamble is present).

### E081: Empty `<steps>`

**Severity:** ERROR

A `<steps>` element contains no `<step>` children.

### E082: `<description>` misplaced

**Severity:** ERROR

A `<description>` appears as a child of something other than `<meta>` or `<step>`.

### E083: Multiple `<description>` per parent

**Severity:** ERROR

A `<meta>` or `<step>` contains more than one `<description>`.

### E084: `<preamble>` not first

**Severity:** ERROR

A `<preamble>` element exists but is not the first child of `<ape>`.

### E085: Multiple `<preamble>` elements

**Severity:** ERROR

More than one `<preamble>` element exists in the document.

---

## Redundancy

These rules enforce the "Structure over prose, once" design principle. They are the rules most likely to be violated because the XSD cannot catch them and prose-first authoring habits encourage repetition.

### E090: Prose restating structural enforcement

**Severity:** ERROR

A `<note>`, `<constraint>`, `<rule>`, `<principle>`, or `<anti-pattern>` restates behavior that a `<gate>`, `<prerequisite>`, `<conditional>`, or `<command>` definition already enforces.

The test: if you deleted the prose element and the document's runtime behavior would be identical — because a gate would still block, a prerequisite would still redirect, or a command definition would still prescribe the exact invocation — the prose is redundant.

```xml
<!-- ERROR: the gate on step "testing" already prevents proceeding
     when tests fail. This constraint adds nothing. -->
<constraint>NEVER move on when tests are failing.</constraint>

<!-- ...later... -->
<step id="testing">
  <action><command ref="run-tests" /></action>
  <gate>
    <criterion check="{{ test_failures == 0 }}" />
    <on-fail goto="implementation" />
  </gate>
</step>
```

```xml
<!-- ERROR: the command definition already specifies the exact invocation.
     The constraint just narrates what the structure prescribes. -->
<command id="run-tests" note="NO FILTERS">cargo test --all-features</command>

<!-- ...later... -->
<constraint>NEVER filter tests. Always run `cargo test --all-features` without
  name filters.</constraint>
```

```xml
<!-- ERROR: the note narrates exactly what the gate already expresses.
     on-fail proceed="true" means "failure is expected, continue."
     on-pass goto="specification" means "if tests pass, go back."
     The note adds nothing the structure doesn't already say. -->
<note>Gate logic is inverted: test failure (action fails) is the expected
  result, proving the tests specify new behavior. If tests pass
  unexpectedly, return to refine them.</note>

<gate>
  <criterion check="{{ test_failures == 0 }}" />
  <on-fail proceed="true" />
  <on-pass goto="specification" />
</gate>
```

**How to evaluate:** For each prose element, ask: "What structural element enforces this?" If you can point to a gate, prerequisite, conditional, or command definition that already makes this behavior mandatory or impossible to violate, the prose is redundant.

### E091: Same guidance in multiple guidance elements

**Severity:** ERROR

The same guidance is expressed in more than one guidance element (`<note>`, `<constraint>`, `<rule>`, `<principle>`, `<anti-pattern>`). Each piece of guidance appears once, in the element whose semantics best match its nature.

```xml
<!-- ERROR: same idea expressed three times -->
<constraint>NEVER skip or disable tests.</constraint>

<principle name="Test-First">Tests define behavior before implementation.</principle>

<anti-pattern>Skipping or disabling tests to make them pass.</anti-pattern>
```

**Fix:** Choose one. "Never skip tests" is a hard prohibition — `<constraint>` is the right home. Delete the principle and anti-pattern that restate it.

**How to evaluate:** Normalize each decorator's text content to its core directive (strip negation, imperative mood, and framing). If two or more decorators reduce to the same directive, they are redundant. The selection hierarchy:

1. Can it be enforced by structure (gate, prerequisite, conditional)? Use structure. Delete all decorators.
2. Is it a hard prohibition or requirement? → `<constraint>`
3. Is it a thing to avoid (not a hard prohibition)? → `<anti-pattern>`
4. Is it a style preference? → `<rule>`
5. Is it a value or approach? → `<principle>`
6. Is it contextual information? → `<note>`

### E092: Step-level guidance duplicates document-level

**Severity:** ERROR

A `<note>`, `<constraint>`, `<rule>`, `<principle>`, or `<anti-pattern>` inside a `<step>` has the same semantic content as one at the document level or inside `<steps>`. Scope narrows; it does not echo.

```xml
<!-- Document-level constraint -->
<constraint>Warnings are errors. Zero tolerance.</constraint>

<steps>
  <step id="linting">
    <!-- ERROR: restates the document-level constraint -->
    <constraint>Warnings are errors. Zero tolerance.</constraint>
  </step>
</steps>
```

**Exception:** A step-level decorator that *narrows* a document-level one is valid. "Format with 2-space indent" inside a step is a valid narrowing of a document-level "Follow project formatting standards."

---

## Unused Declarations

### W100: Declared command never referenced

**Severity:** WARNING

A `<command>` with an `id` attribute is never referenced via `ref` anywhere in the document.

### W101: Declared resource never referenced

**Severity:** WARNING

A `<resource>` with an `id` is never referenced via `uses` or in any instruction/action.

### W102: Declared variable never interpolated

**Severity:** WARNING

A variable is declared but never appears in a `{{ }}` interpolation or as a `ref` target. Applies to `<var>` declarations and variables created by `<command set="name">`.

### W103: Declared template never referenced

**Severity:** WARNING

A `<template>` with an `id` is never referenced via `template` attribute on `<output>`.

### W104: Unreferenced element `id`

**Severity:** WARNING

An element has an `id` attribute that is never referenced anywhere in the document — not by `ref`, `goto`, `uses`, or any other referencing mechanism. Dead identifiers add noise. Either reference the `id` or remove it.

This generalizes W100–W103 to all element types (actions, criteria, gates, steps, etc.). W100–W103 remain as specific cases with targeted diagnostics; W104 catches everything else.

```xml
<!-- WARNING: action id is never referenced -->
<action id="check-docs">
  <command ref="build-docs" />
</action>

<!-- VALID: id is referenced by a prerequisite -->
<step id="build">...</step>
<prerequisite ref="build" goto="build" />
```

---

## Structural Smell

### W110: Step without gate

**Severity:** WARNING

A step contains actions or commands that can fail but has no `<gate>`. If the step is purely informational, this is fine. If it executes commands, it probably needs a gate.

### W111: Prerequisite without `goto` or `halt`

**Severity:** ERROR

A `<prerequisite>` specifies neither `goto` nor `halt`. Every prerequisite must declare a recovery path—either `goto` to redirect to a step or `halt` to stop execution.

### W111a: Prerequisite contains prose

**Severity:** ERROR

A `<prerequisite>` contains text content. `<prerequisite>` is a structure container and must not contain prose. Conditions and recovery paths are expressed through attributes (`ref`, `goto`, `halt`) and optional structural children (`<check>`).

```xml
<!-- ERROR: prose in prerequisite -->
<prerequisite ref="build">Build must pass. If not, return to build.</prerequisite>

<!-- VALID: attributes only -->
<prerequisite ref="build" goto="build" />
```

### W112: Inline command used more than once

**Severity:** WARNING

The same command text appears in two or more inline `<command>` elements (no `id`/`ref`). Declare it once with an `id` and reference it.

```xml
<!-- WARNING: same command text appears in three actions -->
<command>cargo test --all-features</command>
<!-- ...in another action... -->
<command>cargo test --all-features</command>
```

**Fix:** Declare once in `<meta>`:
```xml
<command id="run-tests">cargo test --all-features</command>
```

### W113: Hardcoded value appearing multiple times

**Severity:** WARNING

A literal value (threshold, path, URL) appears in more than one place in the document. It should be a `<var>`.

### W114: Workflow with no conditionals

**Severity:** WARNING

A `<steps>` block contains executable steps and gates but no `<conditional>` elements. Gates are appropriate for numeric thresholds (`{{ test_failures == 0 }}`). Conditionals are appropriate when the executor must evaluate, classify, or choose between meaningfully different paths.

A workflow that reduces every decision to a binary pass/fail gate is likely flattening qualitative judgments that deserve structural branching.

```xml
<!-- WARNING: qualitative judgment reduced to a numeric proxy.
     The instruction asks the executor to evaluate what to stage
     ("only the files related to this change"), but the gate
     only checks staged_count — it never captures or branches on
     whether the commit is actually atomic. -->
<step id="commit" number="7">
  <instruction>Stage only the files related to this change.
    Write the commit message explaining WHY, not just WHAT.</instruction>

  <action><command>git add -p</command></action>
  <action><command>git commit</command></action>

  <gate>
    <criterion check="{{ staged_count == 0 }}" />
    <on-fail retry="true" max="1" then="halt" />
  </gate>
</step>
```

```xml
<!-- VALID: qualitative evaluation captured and branched on.
     The executor classifies the staged changes, the conditional
     routes to the appropriate path, and the gate verifies the
     numeric outcome. -->
<step id="commit" number="7">
  <action><command>git add -p</command></action>

  <ask-user-question var="commit_scope" type="choice">
    Review the staged changes. Is this a single atomic change
    with tests and docs?
    <option value="atomic">One logical change with tests and docs</option>
    <option value="splittable">Multiple logical changes — split into
      separate commits</option>
    <option value="incomplete">Missing required tests or
      documentation</option>
  </ask-user-question>

  <conditional on="commit_scope">
    <case value="atomic">
      <action><command>git commit</command></action>
    </case>
    <case value="splittable">
      <action><command>git reset HEAD</command></action>
    </case>
    <case value="incomplete" goto="implementation" />
  </conditional>

  <gate>
    <criterion check="{{ staged_count == 0 }}" />
    <on-fail retry="true" max="1" then="halt" />
  </gate>
</step>
```

**How to evaluate:** If a workflow has three or more steps with gates but zero conditionals, check whether any instructions or rules describe qualitative evaluations, classifications, or multi-path decisions. If they do, the author is burying branching logic in prose instead of expressing it structurally.

---

## Taste

### I120: `<title>` where `id` suffices

**Severity:** INFO

A step has a `<title>` whose text content closely matches or duplicates its `id` attribute (e.g., `id="testing"` with `<title>Testing</title>`).

### I121: Single-instruction `<instructions>` wrapper

**Severity:** INFO

An `<instructions>` wrapper contains only one `<instruction>` child. Use `<instruction>` directly.

*Note: The XSD enforces `minOccurs="2"` on `<instructions>`, so this should already fail shape validation. This rule exists for defense in depth.*

### I122: Step with no meaningful work

**Severity:** INFO

A step contains no actions, no commands, and no tool tags. Consider whether it should be an instruction or constraint inside another step.

### I123: Decorator on wrong scope

**Severity:** INFO

A decorator is placed at a broad scope when it applies only to a narrow one. A constraint that mentions a specific command by name probably belongs on the action containing that command, not at the document level.

---

## Applying These Rules

### For authors

Review your document against these rules before use. Treat ERRORs as blockers — the document is broken until they are fixed. Treat WARNINGs as likely bugs. INFO-level findings are suggestions — apply judgment.

### For LLM executors

When executing an APE document, detect ERROR-level violations and halt with a diagnostic that names the rule ID and the offending element, per the execution contract in `ape-llms.md`. WARNING and INFO violations are authoring-time concerns — the executor is not expected to detect them.

### For LLM authors

When writing or converting an APE document, enforce all rules at all severity levels. A document you produce should have zero violations. When you detect a violation, report it by rule ID and fix it — do not silently pass it through.

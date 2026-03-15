# APE Conversion Guide (0.4.0)

> This file teaches you how to **convert** existing documents into APE — system prompts, runbooks, process docs, CLAUDE.md files, or any other structured text.
> For writing APE from scratch, see `ape-authoring.md`. For the full language reference, see `ape-spec.md`. For execution, see `ape-llms.md`.

---

## The Core Principle

Converting to APE is a **structural transformation**, not a formatting exercise. You are not wrapping prose in XML tags. You are decomposing prose into semantic primitives that each carry specific meaning.

If your conversion produces the original text inside `<run>` tags with no structural changes — no variables wired, no conditionals extracted, no gates defined — you have not converted anything. You have dressed prose in an XML costume.

Every element in APE exists because it does something that prose alone cannot:

- `<command>` executes. Prose describes.
- `<gate>` enforces. Prose suggests.
- `<conditional>` routes. Prose hand-waves.
- `<rule>` binds. Prose advises.
- `<var>` tracks state. Prose mentions values.

If the tag you chose does not add structural meaning beyond the prose it replaced, you chose the wrong tag — or you should not have used a tag at all.

---

## Element Selection

When reading source material, you will encounter text that needs to become APE. The question is always: **which element?**

### "Someone needs to do something"

| Source text pattern | APE element | Why |
|---|---|---|
| A literal shell command: `npm test` | `<run>npm test</run>` | Concrete, executable, copy-pasteable |
| A specific directive: "Edit the config" | `<edit path="config.yaml" />` | Tool tag, direct child of step |
| A narrative instruction: "Review the diff and decide" | `<instruction>Review the diff and decide what needs changing.</instruction>` | Prose, interpretive latitude allowed |

**The escalation:** `<run>` (atomic, executable) < tool tags (directive, no latitude) < `<instruction>` (narrative, latitude allowed).

`<run>` elements and tool tags are concrete anchors; an `<instruction>` provides the narrative context. A step's work is expressed through instructions, executed through `<run>` elements and tool tags.

### "Something must be true"

| Source text pattern | APE element | Why |
|---|---|---|
| "Before starting, make sure X is done" | `<prerequisite>` | Declares a dependency on prior work |
| "If X, do Y; otherwise do Z" | `<conditional>` | Routes based on existing state |
| "After doing the work, verify it meets the bar" | `<gate>` | Evaluates completed work — enforces quality |
| "Never do X" / "Always do Y" | `<rule>` | Non-negotiable behavioral restriction |

These are the four enforcement mechanisms. They are not interchangeable:

- **Prerequisites** declare what must be true *before* work starts. They point backward.
- **Conditionals** route *during* work based on a value. They point sideways.
- **Gates** evaluate *after* work is done. They point forward (or loop back).
- **Rules** apply *always*. They don't point anywhere — they restrict.

### "There's a value / there's a dependency"

| Source text pattern | APE element | Why |
|---|---|---|
| A configurable threshold: "coverage above 80%" | `<var name="coverage_threshold" default="80" />` | Internal state this document owns |
| A value from the caller: "the project path must be provided" | `<param ref="project-path" />` | External input — the caller provides this |
| A tool, file, or service that's needed | `<resource>` | Something consumed, not produced |

**`<var>` vs `<param>`:** Variables are values this document defines and controls — even if they have no default and need to be resolved at runtime. Parameters are values this document *requires from whoever invokes it*. If the document is standalone, you almost certainly want `<var>`. If an external system invokes your document and must provide a value, that's a `<param>`.

A `<var>` can also contain `<command>` children for computed values — the result of the command becomes the variable's value.

### "There's a rule or guideline"

| Source text pattern | APE element | Why |
|---|---|---|
| "NEVER do X" / "ALWAYS do Y" | `<rule>` | Binding requirement or prohibition |
| "Prefer X over Y" / "Aim for Z" | `<principle name="...">...</principle>` | Non-binding guidance or preference |
| "Common mistake: doing Y instead of Z" | `<anti-pattern>...</anti-pattern>` | What to avoid — the inverse of rules |
| "Note: this only works on Linux" | `<note>` | Author-facing context — not executable, not output |
| "The principle is: each commit is atomic" | `<principle name="...">...</principle>` | Overarching values for the whole document |

**Placement matters.** Place decorators as close to their subject as possible. A rule about a specific command goes in the step containing that `<run>`, not at the document level. A rule that applies to every step in the workflow goes at root level, outside `<steps>`. Note that `<principle>` can appear inside any block (step, instruction, etc.), not just at root level.

**`<reference>` uses `ref_id`:** `<reference>` uses the `ref_id` attribute to point to any element with an id. It does not use `id` or `path` attributes. Example: `<reference ref_id="some-step" />`.

### "There's output to produce"

| Source text pattern | APE element | Why |
|---|---|---|
| "Write this content to that file" (direct tool call) | `<write path="...">content</write>` | Direct tool invocation — triggers the Write tool now |
| "The PR description should look like this: ..." | `<template id="..." format="md">...</template>` | Defines the *shape* of content (reusable, with interpolation) |
| "Put the changelog entry in CHANGELOG.md under Unreleased" | `<output to="file" target="..." anchor="..." position="append">` | Defines the *destination* — what goes where and how |

**`<write>` vs `<output>`:** `<write>` is "use the Write tool right now." `<output>` is "this is what should be produced, here is where it should go." Use `<write>` for simple, immediate file writes. Use `<output>` when you need to specify format, anchor position, or when the content comes from a template.

**`<template>` vs `<output>`:** Templates define shape. Outputs define destination. A template without an output is a reusable content block. An output without a template is a direct content write. An output *with* a template reference is "fill this shape, put it there."

---

## Commonly Confused Elements

### `<instruction>` vs `<run>`

This is the most important distinction for conversion.

`<instruction>` and `<run>` are **siblings within a step**, not parent-child. They serve opposite purposes:

- `<instruction>` contains **prose only** — narrative context, interpretive latitude, guidance. Can include guidance decorators (`<rule>`, `<principle>`, `<anti-pattern>`, `<reference>`) but no executable children. `<note>` is allowed only at the step or root `<ape>` level.
- `<run>` is the **execution verb** — it executes commands in three forms: reference (`<run ref="cmd-id" />`), inline (`<run>command text</run>`), and inline+capture (`<run set="var">command text</run>`). Tool tags (`<read>`, `<write>`, `<ask-user-question>`, etc.) are also direct children of steps.

Together, they express both the narrative context and the concrete execution needed for a phase of work.

```xml
<!-- Instruction: interpretive latitude (prose only) -->
<instruction>
  Review the test output and determine what needs fixing.
  Focus on failures that indicate missing functionality rather than flaky tests.
</instruction>

<!-- Run: no latitude, execute as stated -->
<run ref="run-tests" />
```

**When converting:**
- If the source says "figure out X" or "decide Y" or "review and understand Z" — that is an `<instruction>`.
- If the source says "do X" or "run Y" or "execute Z" — that is a `<run>` element.
- If the source contains both narrative ("first understand the problem") and executable directives ("then fix it") — split them. The narrative becomes `<instruction>`, the executable parts become `<run>` elements.

An `<instruction>` and `<run>` elements can both exist in the same step, side by side. They are not alternatives; they are complementary. A step with only interpretation has just an `<instruction>`. A step with only execution has just `<run>` elements. A step with both has both.

### `<prerequisite>` vs `<conditional>` vs `<gate>`

These three handle different aspects of "what must be true."

**Prerequisite:** "This step cannot start unless X." It is a dependency declaration — it points at another step and specifies a recovery path. It is a structure container with no prose. It either blocks or it does not.

```xml
<!-- Step 4 cannot start until step 3 passes -->
<prerequisite ref="prove-failure" goto="prove-failure" />
```

**Conditional:** "Based on the value of X, do different things." It routes during work. The value already exists — the conditional just selects a path. It does not judge quality, it does not retry, it does not enforce.

```xml
<!-- Route based on an existing value -->
<conditional on="{{ request_type }}" default="investigate">
  <case value="implementation" goto="make-changes" />
  <case value="information" goto="investigate" />
</conditional>
```

**Gate:** "The work is done — does it meet the bar?" It evaluates completed work and enforces quality. It can retry (because the work can be redone), halt (because the bar was not met), or route to recovery steps. It is the bouncer at the door between steps.

```xml
<!-- Evaluate whether work meets criteria (expression-based) -->
<gate>
  <criterion check="{{ test_failures == 0 }}" />
  <on-fail retry="true" max="3" then="halt" />
</gate>
```

**Key test:** Is the value known before work starts? That is a **prerequisite** (before) or **conditional** (during). Does the value depend on the outcome of work just performed? That is a **gate**.

### `<instruction>` vs `<description>`

These both "describe" something but serve completely different purposes.

- `<instruction>` states the *work* — what to do (prose only). It is executed. It allows interpretive latitude.
- `<description>` states *what something is* — author-facing metadata. Never executed, never output. Only valid inside `<meta>` and `<step>`.

```xml
<step id="verify" number="5">
  <title>Verify Build</title>
  <description>Runs the build and test suite to confirm the implementation.</description>
  <instruction>
    Verify that the build completes without errors and all tests pass.
  </instruction>
  <run ref="build" />
  <run ref="run-tests" />
</step>
```

**When converting:** "This step does X and Y" becomes `<instruction>` (if narrative) or `<run>` (if executable). "This is the verification step" (metadata that adds nothing beyond the title) is either `<description>` if it adds real context, or omitted entirely if it restates what `<title>` already says.

### Singular vs Plural Forms

APE uses a consistent pattern: **singular** for one item, **plural wrapper** for two or more. The singular form stands alone inside its parent. The plural form is a container.

| Singular | Plural wrapper | Rule |
|---|---|---|
| `<instruction>` | `<instructions>` | A step contains bare `<instruction>` elements; use wrapper only if organizing a large group |
| `<prerequisite>` | `<prerequisites>` | One dependency inline, two or more in the wrapper |
| `<command>` | `<approved-commands>` / `<approved-gate-commands>` | Containers in `<meta>` for declaring step and gate commands |
| `<var>` | `<variables>` | Wrapper requires 2+ children; use inline `<var>` for a single variable |
| `<resource>` | `<resources>` | Wrapper for declaring multiple resources together |
| `<param>` | `<params>` | Wrapper for declaring multiple parameters together |
| `<rule>` | *(none)* | Standalone; proximity handles grouping |
| `<anti-pattern>` | *(none)* | Standalone; proximity handles grouping |
| `<principle>` | *(none)* | Standalone; proximity handles grouping |

**The key distinction:** For declarations (`<command>`, `<var>`, etc.), the plural form is a convenience wrapper — you can also place singular declarations directly in any block without a wrapper. For `<instruction>`, the `<instructions>` wrapper is optional — multiple bare `<instruction>` elements are also valid.

```xml
<!-- One instruction: standalone -->
<instruction>Do the thing.</instruction>

<!-- Two instructions: bare (no wrapper needed) -->
<instruction>Do the first thing.</instruction>
<instruction>Do the second thing.</instruction>

<!-- Two instructions: with optional wrapper -->
<instructions>
  <instruction>Do the first thing.</instruction>
  <instruction>Do the second thing.</instruction>
</instructions>
```

---

## When to Omit

Not everything in the source needs a tag. Omission is part of good conversion.

### Omit steps that are not phases

A step is a *phase of work* — it has a body of work, takes real time, and usually has a gate. If the source has a section that is really just a single rule or a single `<run>`, it is not a step. It is an instruction, a rule, or a `<run>` inside another step.

**Test:** Does this step have a meaningful pass/fail gate? If not, it is probably not a step.

```xml
<!-- WRONG: "Keep Scope Tight" is a rule, not a phase of work -->
<step id="keep-scope-tight">
  <title>Keep Scope Tight</title>
  <instruction>Do exactly what the user asked — nothing more.</instruction>
</step>

<!-- RIGHT: it's a rule that applies globally -->
<rule>Do exactly what the user asked — nothing more, nothing less.</rule>
```

### Omit variables you never wire up

If you declare a variable but never set it (via `<ask-user-question var="...">`, `<command set="...">`, `default`, or `value`) AND never reference it (via `{{ name }}`), the declaration is waste. Either wire it up or remove it.

**Do not declare empty variables.** A `<var>` must carry a value — via element content, `value` attribute, or `default` attribute. Variables created at runtime by `<ask-user-question var>` or `<command set>` do not need a `<var>` declaration. Only declare a variable when you have a value to give it.

### Do not use XML comments

XML comments (`<!-- -->`) are not permitted in APE documents. They are invisible to validators, unsearchable by tooling, and add noise without structural value. Use `<note>` for author-facing context instead.

```xml
<!-- WRONG: XML comment -->
<!-- Phase 4: Plan After Tool Results -->
<step number="4" id="plan-after-tools">

<!-- WRONG: section-header comment -->
<!-- Security and safety checks -->
<step number="1" id="security-check">

<!-- RIGHT: no comments; the step speaks for itself -->
<step number="4" id="plan-after-tools">

<!-- RIGHT: use <note> if context is needed -->
<step number="1" id="security-check">
  <note>This gate must pass before any code changes.</note>
```

### Omit duplicate guidance

If a rule says "Never create documentation files unless requested" and a principle says "Only create files when absolutely necessary" and an instruction says "Do not proactively create documentation files," you have said the same thing three times. Say it once, in the element that best fits:

- If it is a hard prohibition or requirement → `<rule>` (once)
- If it is a preference or guiding value → `<principle>` (once)
- If it is a specific directive in context → part of the `<instruction>` prose (once)

---

## Structural Extraction

The hardest part of conversion is recognizing structural patterns hiding in prose.

### Conditionals Hiding in Prose

Every "if," "when," "decide whether," or "depending on" in the source is a candidate for `<conditional>`. Do not bury these in prose.

```xml
<!-- WRONG: conditional logic buried in prose -->
<instruction>
  Decide whether the user is asking for:
  - Information/recommendations only, or
  - Actual edits/implementation.
</instruction>

<!-- RIGHT: structural conditional -->
<conditional on="{{ request_type }}" default="investigate">
  <case value="implementation" goto="make-changes" />
  <case value="information" goto="investigate" />
</conditional>
```

For the conditional to work, the value must be set somewhere — via `<ask-user-question>`, `<command set="...">`, or runtime resolution. If the conditional depends on a value that does not yet exist, you need to add the mechanism that captures it.

### Variables Hiding in Prose

If the source mentions a threshold, a path, a name, or any other value that appears more than once or could change between runs, it should be a `<var>`.

```xml
<!-- WRONG: value hardcoded in prose -->
<criteria>Coverage is above 80%</criteria>

<!-- RIGHT: value extracted to a variable, checked with expression -->
<var name="coverage_threshold" type="number" default="80" />
<var name="coverage" type="number"><command>npm test -- --coverage 2>&amp;1 | grep "Stmts" | awk '{print $4}'</command></var>
...
<criterion check="{{ coverage >= coverage_threshold }}" />
```

### Gates Hiding in Prose

"Make sure X," "verify that Y," "confirm Z before continuing" — these are gates, not instructions. If the source says something must be true before proceeding, that is enforcement. Do not leave it as prose.

`<criteria>` does not contain prose. Prefer expression-based criteria (`check`) when a measurable condition exists — it makes the pass/fail condition explicit and unambiguous. Gates can contain `<run>` children to execute measurement commands before evaluating criteria.

```xml
<!-- WRONG: quality check buried in prose -->
<instruction>
  Make sure all tests pass before moving on.
  If they don't, go back and fix them.
</instruction>

<!-- RIGHT: self-contained gate with measurement and evaluation -->
<run ref="run-tests" />
<gate>
  <run ref="test-failure-count" set="test_failures" />
  <criterion check="{{ test_failures == 0 }}" />
  <on-fail goto="implementation" />
</gate>

<!-- Compound criteria: multiple measurements -->
<gate>
  <run ref="test-failure-count" set="test_failures" />
  <run ref="coverage-pct" set="coverage" />
  <criteria operator="and">
    <criterion check="{{ test_failures == 0 }}" />
    <criterion check="{{ coverage >= coverage_threshold }}" />
  </criteria>
  <on-fail goto="implementation" />
</gate>

<!-- goto as an element (alternative to attribute) -->
<on-fail>
  <goto ref="implementation" />
</on-fail>
```

### Commands Hiding in Prose

If the source describes running something — "execute the linter," "run the build," "check the output of X" — and that something is a concrete tool invocation, it is a `<run>`, not prose inside `<instruction>`.

Distinguish clearly: narrative instruction (prose only) vs executable `<run>` element.

```xml
<!-- WRONG: command buried in prose instruction -->
<instruction>Run cargo test with all features enabled to verify.</instruction>

<!-- RIGHT: instruction for context, run for execution -->
<instruction>
  Verify all features work correctly.
</instruction>
<run ref="run-tests" />
```

If the same command appears in multiple places, declare it once in `<approved-commands>` and reference it:

```xml
<approved-commands>
  <command id="run-tests" note="NO FILTERS">cargo test --all-features</command>
</approved-commands>
...
<run ref="run-tests" />
```

---

## Conversion Anti-Patterns

### 1. The XML Costume

**Symptom:** Every section of the source becomes a `<step>`. Every sentence becomes a `<run>`. The structure is identical to the original — just wrapped in tags.

**Fix:** Steps are phases with gates. Instructions allow latitude and are prose-only. `<run>` elements execute commands. Tool tags invoke tools directly. Match the element to the *nature* of the content, not its position in the source.

### 2. Unused Declarations

**Symptom:** Variables, resources, or parameters declared in `<meta>` that are never referenced anywhere in the document.

**Fix:** Every declaration must be used. If `<var name="deliverable">` exists, `{{ deliverable }}` must appear somewhere, or `<ask-user-question var="deliverable">` must set it. If not, remove the declaration.

### 3. Prose Conditionals

**Symptom:** Text containing "if/when/decide/depending on" inside `<instruction>` with no `<conditional>` element.

**Fix:** Extract the branching logic into a `<conditional>` with `<case>` elements. Wire the `on` expression to a variable that gets set by the time the conditional is reached.

### 4. Gateless Steps

**Symptom:** A step that performs work but has no gate — meaning there is no enforcement of quality and no defined behavior on failure.

**Fix:** If the step can fail, add a gate. If the step genuinely cannot fail (purely informational, no output to validate), consider whether it is really a step or just an instruction inside another step.

### 5. Redundant Decorators

**Symptom:** The same guidance expressed as a `<rule>`, a `<principle>`, an `<anti-pattern>`, and prose in an `<instruction>`.

**Fix:** Say it once. Choose the element that best matches the nature of the guidance. Hard prohibitions and requirements are rules. Things to avoid are anti-patterns. Guidance and preferences are principles. Context is notes. Do not repeat the same message in multiple forms. This is a validation error per the spec's redundancy rules.

### 5a. Prose Restating Structure

**Symptom:** A `<rule>` says "NEVER move on when tests are failing" but a `<gate>` on the same step already makes it impossible to proceed without passing tests. Or a `<rule>` says "always run `cargo test --all-features`" but the `<command id="run-tests">` already defines that exact invocation.

**Fix:** Delete the prose. Gates enforce. Command definitions prescribe. Prerequisites block. Conditionals branch. If structure already makes a behavior mandatory or impossible, a decorator restating that behavior is redundant noise. Trust the structure you built.

### 6. Over-Stepping

**Symptom:** 13 steps where 5 would do. Steps with no gates, no prerequisites, and a single sentence of instruction.

**Fix:** Merge related work into fewer, more substantial steps. A step should represent a phase with a meaningful body of work. "Keep scope tight" is a rule, not a step. "Use parallel tool calls" is a rule, not a step. "Summarize work completed" is an instruction inside the final step, not its own step.

### 7. Section-Header Comments

**Symptom:** XML comments like `<!-- Security checks -->` or `<!-- Phase 2: Implementation -->` above steps or sections.

**Fix:** XML comments are not permitted in APE documents. The step `id` and `<title>` (if present) already name the section. If additional context is needed, use `<note>`.

### 8. Placeholder Variable Declarations

**Symptom:** `<var name="result" type="string" />` with no value, no default, and no content — declared "for later" or "for runtime resolution."

**Fix:** A `<var>` must carry a value. If a value is captured at runtime via `<ask-user-question var>` or `<command set>`, no `<var>` declaration is needed — those mechanisms create the variable implicitly.

### 9. Commands Buried in Prose

**Symptom:** Executable commands written as prose inside `<instruction>` rather than as `<run>` elements.

**Fix:** `<instruction>` is prose only — narrative context with interpretive latitude. Concrete commands belong in `<run>` elements, which are siblings of `<instruction>` within the step. Tool tags are also direct children of the step.

```xml
<!-- WRONG: command buried in prose -->
<instruction>
  Run the tests and check the output.
  Make sure they all pass before continuing.
</instruction>

<!-- RIGHT: prose in instruction, execution in run -->
<instruction>
  Ensure all tests pass before continuing to the next phase.
</instruction>
<run ref="run-tests" />
```

### 10. Criteria with Prose

**Symptom:** `<criteria>All tests must pass and coverage must be above 80%.</criteria>`

**Fix:** `<criteria>` does not contain prose. Use expression-based criteria for explicit measurable conditions. Use `<run>` inside gates to execute measurement commands, then `<criterion check>` to evaluate results.

```xml
<!-- WRONG: prose inside criteria -->
<criteria>All tests must pass and coverage must be above 80%.</criteria>

<!-- RIGHT: expression-based criteria with explicit conditions -->
<var name="test_failures" type="number"><command>npm test 2>&amp;1 | grep "failing" | wc -l</command></var>
<var name="coverage" type="number"><command>npm test -- --coverage 2>&amp;1 | grep "Stmts" | awk '{print $4}'</command></var>
<var name="coverage_threshold" type="number" default="80" />

<gate>
  <criteria operator="and">
    <criterion check="{{ test_failures == 0 }}" />
    <criterion check="{{ coverage >= coverage_threshold }}" />
  </criteria>
  <on-fail goto="implementation" />
</gate>
```

---

## Conversion Checklist

After converting, verify:

**Structure:**
- [ ] Every step represents a real phase of work (not a single rule or principle)
- [ ] Every step that can fail has a `<gate>` with `<criteria>` or `<criterion>` and `<on-fail>`
- [ ] Every "if/when/decide" in the source is a `<conditional>`, not prose
- [ ] Every concrete command is a `<run>`, not text inside `<instruction>`
- [ ] `<instruction>` contains only prose and guidance decorators (`<rule>`, `<principle>`, `<anti-pattern>`, `<reference>`) — no executable children like `<run>`, and no `<note>` (use at step or root level only)
- [ ] `<run>` uses either `ref` or body text, never both
- [ ] `<run>` inside `<gate>` only references `<approved-gate-commands>`
- [ ] `<run>` outside `<gate>` only references `<approved-commands>`
- [ ] Singular/plural forms are correct (`<instruction>` vs `<instructions>`, etc.)

**Declarations:**
- [ ] Every declared variable is both set and referenced
- [ ] Every declared resource is referenced via `uses` or in instructions
- [ ] Every declared command with `id` is referenced via `<run ref>` somewhere
- [ ] Values that appear more than once are variables, not hardcoded

**Cleanliness:**
- [ ] No XML comments anywhere in the document
- [ ] No descriptions that restate what the element's attributes already say
- [ ] No guidance repeated across multiple decorator types (rule, principle, anti-pattern, note)
- [ ] No decorator restates what a gate, prerequisite, conditional, or command definition already enforces
- [ ] No step-level decorator duplicates a document-level decorator
- [ ] No steps that should be rules, principles, or instructions in another step

**Variables:**
- [ ] Every `<var>` has a value (content, `value` attr, or `default` attr) — no empty declarations
- [ ] `<variables>` wrapper is used only with 2+ variables; single variables use inline `<var>`
- [ ] Variables created by `<ask-user-question var>` or `<command set>` are not redundantly declared with `<var>`

**Prerequisites:**
- [ ] Prerequisites are the first children in their step
- [ ] Prerequisites contain no prose—conditions and recovery paths use attributes (`ref`, `goto`, `halt`) and optional structural children (`<check>`)
- [ ] Every prerequisite specifies at least one of `goto` or `halt`

**Ordering:**
- [ ] Rules inside instructions appear before prose and executable content

**Content integrity:**
- [ ] Hard requirements from the source are `<rule>`, not just prose
- [ ] Quality checks from the source are `<gate>`, not just instructions
- [ ] Dependencies between steps are `<prerequisite>`, not just implied ordering
- [ ] Configurable values have `<var>` with sensible defaults
- [ ] Output format expectations use `<template>` and `<output>`
- [ ] Criteria use `check` for explicit measurable conditions or `ref` for reusing named criteria — never bare prose
- [ ] Named criteria (`<criteria id="...">`) are used when the same conditions appear in multiple gates

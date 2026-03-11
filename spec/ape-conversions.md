# APE Conversion Guide

> This file teaches you how to **convert** existing documents into APE — system prompts, runbooks, process docs, CLAUDE.md files, or any other structured text.
> For writing APE from scratch, see `ape-authoring.md`. For the full language reference, see `ape-spec.md`. For execution, see `ape-llms.md`.

---

## The Core Principle

Converting to APE is a **structural transformation**, not a formatting exercise. You are not wrapping prose in XML tags. You are decomposing prose into semantic primitives that each carry specific meaning.

If your conversion produces the original text inside `<action>` tags with no structural changes — no variables wired, no conditionals extracted, no gates defined — you have not converted anything. You have dressed prose in an XML costume.

Every element in APE exists because it does something that prose alone cannot:

- `<command>` executes. Prose describes.
- `<gate>` enforces. Prose suggests.
- `<conditional>` routes. Prose hand-waves.
- `<constraint>` binds. Prose advises.
- `<var>` tracks state. Prose mentions values.

If the tag you chose does not add structural meaning beyond the prose it replaced, you chose the wrong tag — or you should not have used a tag at all.

---

## Element Selection

When reading source material, you will encounter text that needs to become APE. The question is always: **which element?**

### "Someone needs to do something"

| Source text pattern | APE element | Why |
|---|---|---|
| A literal shell command: `npm test` | `<command>npm test</command>` | Concrete, executable, copy-pasteable |
| A specific directive: "Edit the config file to add X" | `<action>Edit the config file to add X</action>` | Specific and direct — the LLM should do exactly this |
| A narrative instruction: "Review the diff and decide what needs changing" | `<instruction>Review the diff and decide what needs changing</instruction>` | Allows interpretive latitude — the LLM decides *how* |
| A stated objective: "The goal is to ensure test coverage" | `<goal>Ensure test coverage</goal>` | Decorator — states the *why*, not the *what* or *how* |

**The escalation:** `<command>` (atomic, executable) < `<action>` (directive, no latitude) < `<instruction>` (narrative, latitude allowed) < `<goal>` (objective, not executable).

`<command>` and `<action>` live inside `<instruction>`. They are the concrete anchors; the instruction is the narrative wrapper. A step's work is expressed through instructions. The step's purpose is expressed through `<goal>`.

### "Something must be true"

| Source text pattern | APE element | Why |
|---|---|---|
| "Before starting, make sure X is done" | `<prerequisite>` | Declares a dependency on prior work |
| "If X, do Y; otherwise do Z" | `<conditional>` | Routes based on existing state |
| "After doing the work, verify it meets the bar" | `<gate>` | Evaluates completed work — enforces quality |
| "Never do X" / "Always do Y" | `<constraint>` | Non-negotiable behavioral restriction |

These are the four enforcement mechanisms. They are not interchangeable:

- **Prerequisites** declare what must be true *before* work starts. They point backward.
- **Conditionals** route *during* work based on a value. They point sideways.
- **Gates** evaluate *after* work is done. They point forward (or loop back).
- **Constraints** apply *always*. They don't point anywhere — they restrict.

### "There's a value / there's a dependency"

| Source text pattern | APE element | Why |
|---|---|---|
| A configurable threshold: "coverage above 80%" | `<var name="coverage_threshold" default="80" />` | Internal state this document owns |
| A value from the caller: "the project path must be provided" | `<param ref="project-path" />` | External input — the caller provides this |
| A tool, file, or service that's needed | `<resource>` | Something consumed, not produced |

**`<var>` vs `<param>`:** Variables are values this document defines and controls — even if they have no default and need to be resolved at runtime. Parameters are values this document *requires from whoever invokes it*. If the document is standalone, you almost certainly want `<var>`. If an external system invokes your document and must provide a value, that's a `<param>`.

### "There's a rule or guideline"

| Source text pattern | APE element | Why |
|---|---|---|
| "NEVER do X" / "ALWAYS do Y" | `<constraint>` | Hard, non-negotiable restriction |
| "Follow these style rules: ..." | `<rules><rule>...</rule></rules>` | Behavioral rules — the LLM should follow them |
| "Common mistake: doing Y instead of Z" | `<anti-patterns><anti-pattern>...</anti-pattern></anti-patterns>` | What to avoid — the inverse of rules |
| "Note: this only works on Linux" | `<note>` | Author-facing context — not executable, not output |
| "We do this because..." | `<rationale>` | Explains *why* — helps the LLM make better judgments |
| "The principle is: each commit is atomic" | `<principles><principle name="...">...</principle></principles>` | Overarching values for the whole document |

**`<constraint>` vs `<rule>`:** Constraints are absolute — "NEVER," "ALWAYS," hard prohibitions. Rules are behavioral guidance — "prefer X over Y," "use imperative mood." The LLM treats constraints as non-negotiable. Rules inform judgment.

**Placement matters.** Place decorators as close to their subject as possible. A constraint about a specific command goes inside the instruction containing that command, not on the step. A constraint that applies to every step in the workflow goes at root level, outside `<steps>`.

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

### `<instruction>` vs `<action>`

This is the most important distinction for conversion.

`<instruction>` allows interpretive latitude. It says "here is what needs to happen" and the LLM decides how. It can contain prose, commands, actions, conditionals, tool tags, behavioral tags — anything.

`<action>` is a direct directive. It says "do exactly this." The LLM should perform it as stated, not treat it as a suggestion. It contains things that *happen* — text, commands, tool tags, behavioral tags, outputs. It does not contain structural or decision-making elements.

```xml
<!-- Instruction: interpretive latitude -->
<instruction>
  Review the test output and determine what needs fixing.
  Focus on failures that indicate missing functionality rather than flaky tests.
</instruction>

<!-- Action: no latitude, execute as stated -->
<action>
  Run the test suite and capture the output.
  <command ref="run-tests" />
</action>
```

**When converting:** If the source says "figure out X" or "decide Y" — that is an `<instruction>`. If the source says "do X" or "run Y" — that is an `<action>` (possibly inside an `<instruction>`).

`<action>` lives *inside* `<instruction>`. It is not a sibling or alternative — it is a concrete anchor within the narrative. An instruction can contain multiple actions alongside prose. An action does not contain instructions.

### `<prerequisite>` vs `<conditional>` vs `<gate>`

These three handle different aspects of "what must be true."

**Prerequisite:** "This step cannot start unless X." It is a dependency declaration — it points at another step or describes a precondition. It does not decide anything or route anywhere. It either blocks or it does not.

```xml
<!-- Step 4 cannot start until step 3 passes -->
<prerequisite ref="prove-failure">Tests must fail before implementing.</prerequisite>
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
<!-- Evaluate whether work meets criteria -->
<gate>
  <criteria>All tests pass with no filtered tests.</criteria>
  <on-fail retry="true" max="3" then="halt">Tests failing.</on-fail>
</gate>
```

**Key test:** Is the value known before work starts? That is a **prerequisite** (before) or **conditional** (during). Does the value depend on the outcome of work just performed? That is a **gate**.

### `<goal>` vs `<instruction>` vs `<description>`

These all "describe" something but serve completely different purposes.

- `<goal>` states the *objective* — why this step/block exists. It is a decorator. Not executed, but it shapes the LLM's understanding of purpose. Can appear inside any block.
- `<instruction>` states the *work* — what to do. It is executed. It allows interpretive latitude.
- `<description>` states *what something is* — author-facing metadata. Never executed, never output. Only valid inside `<meta>`, `<step>`, and `<actor>`.

```xml
<step id="verify" number="5">
  <title>Verify Build</title>
  <description>Runs the build and test suite to confirm the implementation.</description>
  <goal>Confirm the implementation is correct before proceeding.</goal>
  <instruction>
    <action>
      Run the build and test suite.
      <command ref="run-build" />
      <command ref="run-tests" />
    </action>
  </instruction>
</step>
```

**When converting:** "The purpose of this step is..." becomes `<goal>`. "This step does X and Y" becomes `<instruction>`. "This is the verification step" (metadata that adds nothing beyond the title) is either `<description>` if it adds real context, or omitted entirely if it restates what `<title>` already says.

### Singular vs Plural Forms

APE uses a consistent pattern: **singular** for one item, **plural wrapper** for two or more. The singular form stands alone inside its parent. The plural form is a container.

| Singular | Plural wrapper | Rule |
|---|---|---|
| `<instruction>` | `<instructions>` | A step contains exactly one `<instruction>` OR one `<instructions>` (never both) |
| `<prerequisite>` | `<prerequisites>` | One dependency inline, two or more in the wrapper |
| `<command>` | `<commands>` | Wrapper for declaring multiple commands together |
| `<var>` | `<variables>` | Wrapper requires 2+ children; use inline `<var>` for a single variable |
| `<resource>` | `<resources>` | Wrapper for declaring multiple resources together |
| `<actor>` | `<actors>` | Wrapper for declaring multiple actors together |
| `<param>` | `<params>` | Wrapper for declaring multiple parameters together |
| `<rule>` | `<rules>` | Rules always need the wrapper (rules are a set) |
| `<anti-pattern>` | `<anti-patterns>` | Anti-patterns always need the wrapper |
| `<principle>` | `<principles>` | Principles always need the wrapper |

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

## When to Use Actors (and When Not To)

Actors are needed when **multiple participants interact** and you need to specify who does what. If the entire workflow is executed by a single LLM agent, actors add noise.

**Declare actors when:**
- A human approves, reviews, or provides input during the workflow
- A CI service runs commands that the LLM cannot
- Subagents handle specialized tasks
- The workflow involves handoffs between participants

**Skip actors when:**
- A single LLM agent does everything
- There is no human interaction
- The source document does not distinguish between participants

**If you declare actors, use them.** The default actor is the first one declared. Do not put `actor="claude"` on every step when `claude` is already the first actor — it is redundant. Only specify `actor` on steps or actions where the actor *differs* from the default.

```xml
<!-- WRONG: actor on every element when it's already the default -->
<actor id="claude" type="agent" />  <!-- first = default -->
<step actor="claude">               <!-- redundant -->
  <action actor="claude">           <!-- redundant -->

<!-- RIGHT: only specify when different from default -->
<actor id="claude" type="agent" />
<step>
  <action>...</action>
  <action actor="developer">Get human approval</action>
```

**Do not declare actors that are never referenced.** If you declare a `developer` actor but no step, action, gate, or ask-user-question ever uses `actor="developer"`, the declaration is waste. Either the workflow genuinely involves the developer (and you should reference them somewhere) or it does not (and you should not declare them).

---

## When to Omit

Not everything in the source needs a tag. Omission is part of good conversion.

### Omit steps that are not phases

A step is a *phase of work* — it has a body of work, takes real time, and usually has a gate. If the source has a section that is really just a single rule or a single action, it is not a step. It is an instruction, a constraint, or an action inside another step.

**Test:** Does this step have a meaningful pass/fail gate? If not, it is probably not a step.

```xml
<!-- WRONG: "Keep Scope Tight" is a constraint, not a phase of work -->
<step id="keep-scope-tight">
  <title>Keep Scope Tight</title>
  <instruction>Do exactly what the user asked — nothing more.</instruction>
</step>

<!-- RIGHT: it's a constraint that applies globally -->
<constraint>Do exactly what the user asked — nothing more, nothing less.</constraint>
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

If a constraint says "Never create documentation files unless requested" and a rule says "Only create files when absolutely necessary" and an instruction says "Do not proactively create documentation files," you have said the same thing three times. Say it once, in the element that best fits:

- If it is a hard prohibition → `<constraint>` (once)
- If it is a style preference → `<rule>` (once)
- If it is a specific directive in context → part of the `<instruction>` prose (once)

---

## Structural Extraction

The hardest part of conversion is recognizing structural patterns hiding in prose.

### Conditionals Hiding in Prose

Every "if," "when," "decide whether," or "depending on" in the source is a candidate for `<conditional>`. Do not bury these in `<action>` text.

```xml
<!-- WRONG: conditional logic buried in prose -->
<action>
  Decide whether the user is asking for:
  - Information/recommendations only, or
  - Actual edits/implementation.
</action>

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

<!-- RIGHT: value extracted to a variable -->
<var name="coverage_threshold" type="number" default="80" />
...
<criteria>Coverage is above {{ coverage_threshold }}%</criteria>
```

### Gates Hiding in Prose

"Make sure X," "verify that Y," "confirm Z before continuing" — these are gates, not instructions. If the source says something must be true before proceeding, that is enforcement. Do not leave it as prose inside an `<action>`.

```xml
<!-- WRONG: quality check buried in prose -->
<action>
  Make sure all tests pass before moving on.
  If they don't, go back and fix them.
</action>

<!-- RIGHT: structural gate -->
<gate>
  <criteria>All tests pass.</criteria>
  <on-fail goto="implementation">Tests failing — fix and retry.</on-fail>
</gate>
```

### Commands Hiding in Prose

If the source describes running something — "execute the linter," "run the build," "check the output of X" — and that something is a concrete tool invocation, it is a `<command>`, not prose inside `<action>`.

```xml
<!-- WRONG: command buried in description -->
<action>Run cargo test with all features enabled to verify.</action>

<!-- RIGHT: command extracted -->
<action>
  Verify all features work.
  <command>cargo test --all-features</command>
</action>
```

If the same command appears in multiple places, declare it once and reference it:

```xml
<command id="test-all" note="NO FILTERS">cargo test --all-features</command>
...
<command ref="test-all" />
```

---

## Conversion Anti-Patterns

### 1. The XML Costume

**Symptom:** Every section of the source becomes a `<step>`. Every sentence becomes an `<action>`. The structure is identical to the original — just wrapped in tags.

**Fix:** Steps are phases with gates. Instructions allow latitude. Actions are directives. Commands execute. Match the element to the *nature* of the content, not its position in the source.

### 2. Unused Declarations

**Symptom:** Variables, actors, or resources declared in `<meta>` that are never referenced anywhere in the document.

**Fix:** Every declaration must be used. If `<var name="deliverable">` exists, `{{ deliverable }}` must appear somewhere, or `<ask-user-question var="deliverable">` must set it. If not, remove the declaration.

### 3. Prose Conditionals

**Symptom:** Text containing "if/when/decide/depending on" inside `<action>` or `<instruction>` with no `<conditional>` element.

**Fix:** Extract the branching logic into a `<conditional>` with `<case>` elements. Wire the `on` expression to a variable that gets set by the time the conditional is reached.

### 4. Gateless Steps

**Symptom:** A step that performs work but has no gate — meaning there is no enforcement of quality and no defined behavior on failure.

**Fix:** If the step can fail, add a gate. If the step genuinely cannot fail (purely informational, no output to validate), consider whether it is really a step or just an instruction inside another step.

### 5. Redundant Decorators

**Symptom:** The same guidance expressed as a `<constraint>`, a `<rule>`, an `<anti-pattern>`, AND prose in an `<instruction>`.

**Fix:** Say it once. Choose the element that best matches the nature of the guidance. Hard prohibitions are constraints. Style preferences are rules. Things to avoid are anti-patterns. Context is notes. Do not repeat the same message in multiple forms.

### 6. Over-Stepping

**Symptom:** 13 steps where 5 would do. Steps with no gates, no prerequisites, and a single sentence of instruction.

**Fix:** Merge related work into fewer, more substantial steps. A step should represent a phase with a meaningful body of work. "Keep scope tight" is a constraint, not a step. "Use parallel tool calls" is a rule, not a step. "Summarize work completed" is an instruction inside the final step, not its own step.

### 7. Default Actor Noise

**Symptom:** `actor="claude"` on every step, every action, every command — when `claude` is the first (and therefore default) actor.

**Fix:** Only specify `actor` when it differs from the default. The default is the first actor declared.

### 8. Section-Header Comments

**Symptom:** XML comments like `<!-- Security checks -->` or `<!-- Phase 2: Implementation -->` above steps or sections.

**Fix:** XML comments are not permitted in APE documents. The step `id` and `<title>` (if present) already name the section. If additional context is needed, use `<note>`.

### 9. Placeholder Variable Declarations

**Symptom:** `<var name="result" type="string" />` with no value, no default, and no content — declared "for later" or "for runtime resolution."

**Fix:** A `<var>` must carry a value. If a value is captured at runtime via `<ask-user-question var>` or `<command set>`, no `<var>` declaration is needed — those mechanisms create the variable implicitly.

---

## Conversion Checklist

After converting, verify:

**Structure:**
- [ ] Every step represents a real phase of work (not a single rule or constraint)
- [ ] Every step that can fail has a `<gate>` with `<criteria>` and a single `<on-fail>`
- [ ] Every "if/when/decide" in the source is a `<conditional>`, not prose
- [ ] Every concrete command is a `<command>`, not text inside `<action>`
- [ ] Singular/plural forms are correct (`<instruction>` vs `<instructions>`, etc.)

**Declarations:**
- [ ] Every declared variable is both set and referenced
- [ ] Every declared resource is referenced via `uses` or in instructions
- [ ] Every declared actor is referenced via `actor` attributes somewhere
- [ ] Every declared command with `id` is referenced via `ref` somewhere
- [ ] Values that appear more than once are variables, not hardcoded

**Cleanliness:**
- [ ] No XML comments anywhere in the document
- [ ] No `actor` attributes on elements where the actor matches the default
- [ ] No descriptions that restate what the element's attributes already say
- [ ] No guidance repeated across multiple decorator types
- [ ] No steps that should be constraints, rules, or instructions in another step

**Variables:**
- [ ] Every `<var>` has a value (content, `value` attr, or `default` attr) — no empty declarations
- [ ] `<variables>` wrapper is used only with 2+ variables; single variables use inline `<var>`
- [ ] Variables created by `<ask-user-question var>` or `<command set>` are not redundantly declared with `<var>`

**Prerequisites:**
- [ ] Prerequisites are the first children in their step
- [ ] Prerequisite text describes both the condition and the consequence of it not being met

**Ordering:**
- [ ] Constraints inside instructions appear before prose and executable content

**Content integrity:**
- [ ] Constraints from the source are `<constraint>`, not just prose
- [ ] Quality checks from the source are `<gate>`, not just instructions
- [ ] Dependencies between steps are `<prerequisite>`, not just implied ordering
- [ ] Configurable values have `<var>` with sensible defaults
- [ ] Output format expectations use `<template>` and `<output>`

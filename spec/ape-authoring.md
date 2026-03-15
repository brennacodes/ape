# APE Authoring Guide

> This file teaches you how to **write** APE workflows from scratch.
> For converting existing documents into APE, see `ape-conversions.md`. For executing APE, see `ape-llms.md`. For the full language reference, see `ape-spec.md`.

---

## The Mindset

To author effective APE workflows, you must shift from writing conversational prose to declaring **execution primitives**. Markdown relies on **emphasis** to suggest importance, which is why prompts fail. APE relies on **structure** to enforce behavior.

Every instruction you write boils down to three primary categories of primitives, which map directly to what an execution runtime performs:

### 1. Execution (What to *Do*)

These are atomic directives. If you want the agent to execute a shell command, make a tool call, or get user input, it's an execution primitive. These elements produce a definitive outcome (success or failure, input data, or structured output).

**Elements:** `<run>`, `<ask-user-question>`, `<interview-mode>`, `<output>`.

### 2. State & Dependencies (What to *Know/Need*)

These define the condition under which actions can run. This category includes configurable state, prerequisites, and resource dependencies. They provide semantic weight to your execution model, ensuring necessary conditions are met before proceeding.

**Elements:** `<resource>`, `<var>`, `<param>`, `<rule>`, `<gate required="true">`.

### 3. Flow Control (How to *Navigate*)

This is the structural scaffolding that directs the LLM through the runtime contract. It manages phase transitions, decision points, and quality control. Flow control makes intent structural rather than conversational.

**Elements:** `<step>`, `<gate>`, `<conditional>`, `<on-pass>`, `<on-fail goto>`.

---

## Middle-Out Authoring

Most configuration formats force you to think **top-down** (defining structure before substance) or **bottom-up** (abstracting details before you know what varies). APE is designed to be authored **middle-out**: start with the irreducible primitives, then radiate outward through composition and into structure.

### The Three Rings

| Ring | Elements | Question |
|---|---|---|
| **Core (Middle)** | `<command>`, `<criterion>`, `<var>`, `<resource>`, `<rule>` | **What** is the work? |
| **Inner Ring** | `<run>`, `<gate>`, `<instruction>`, `<conditional>` | **How** does it compose, validate, and branch? |
| **Outer Ring** | `<step>`, `<meta>`, `<ape>` | **Where** does it live, scope, and get reused? |

### The Process

1. **Drop the meat.** Get your commands, resources, and criteria on the page. If the meat doesn't work, the structure doesn't matter.
2. **Add the guardrails.** Write `<run>` elements for commands. Protect steps with gates. Inject variables to stop hard-coding. Only add logic when the primitive requires it — if a command never fails, it doesn't need a gate.
3. **Find the pattern.** Promote repeated commands to `<approved-commands>` in `<meta>`. Group related `<run>` elements into steps. Define your document root and versioning.

### Why Middle-Out Wins

**Prevents indecision paralysis.** In top-down design, the first question is "How many steps do I need?" — a high-level architectural decision that slows you down. In middle-out, the first question is "What is the actual command I need to run?" Start with the `<command>`. The structure reveals itself as you add safety and portability around it.

**Grounds the document in reality.** Top-down tries to define the workflow before proving the implementation works. Middle-out ensures every `<step>` wraps commands that actually execute.

**Prevents premature abstraction.** You can't know what belongs in `<meta>` until you know what's reused. You can't know what's a variable until you've hard-coded it twice. Bottom-up asks you to abstract before you have evidence.

**Prevents step-bloat.** If you start with steps, you create empty structural containers and end up with 20 steps and no gates. If you start with commands, you discover you only need 5 steps with 4 `<run>` elements each.

**Matches the developer's journey.** "I need to run this" → "I need to make this safe" → "I need to share this."

> If you find yourself spending more than 5 minutes on `<meta>` before you've written a single `<command>`, stop. Go back to the middle.

---

## Step 1: Read the Source Document

Before writing any XML, read the entire source and answer these questions:

**What do they need?** Every tool, file, service, or prerequisite becomes a `<resource>`:
- "Make sure X is installed" → `<resource type="executable" />`
- "Open the config file" → `<resource type="file" path="..." />`
- "You'll need access to the staging server" → `<resource type="service" />`
- If something is optional, use `required="false"`

**What do they do?** Every action, command, or instruction becomes a `<run>`:
- Literal shell commands → `<run>command text</run>` (inline form)
- Reused commands → declare with `<command id="...">` in `<approved-commands>` and use `<run ref="..." />`
- Commands whose output you need → `<run set="var_name">command text</run>` (capture form)

**What can go wrong?** Every failure case, quality check, or approval point becomes a `<gate>`:
- "Make sure all tests pass" → `<gate>` with `<criterion check>` or `<criteria>` and `<on-fail>`
- "Get sign-off before proceeding" → `<gate>` with an `<ask-user-question>` before it
- "If the build breaks, start over from step 2" → `<on-fail goto="step-2">`

**What are the rules?** Every requirement, best practice, or anti-pattern becomes a decorator:
- Hard requirements ("never do X", "always do Y") → `<rule>`
- Soft guidance ("prefer X over Y") → `<principle>`
- Common mistakes to avoid → `<anti-pattern>`
- Context that helps understanding but isn't actionable → `<note>`

**What are the phases?** Every distinct stage, phase, or checkpoint becomes a `<step>`:
- Look for numbered lists, section headers, phase names
- Look for explicit ordering ("first... then... finally...")
- Look for decision points ("if tests pass, move on; if not, go back")

Note the order: identify the primitives (commands, resources, criteria, rules) *before* thinking about phases. The phases emerge from the primitives, not the other way around.

---

## Step 2: Classify and Write the Primitives

This is the most important judgment call, and the heart of middle-out authoring. For every piece of source content, classify it using the escalation ladder:

| Source says... | APE element |
|---|---|
| `npm test` | `<run>npm test</run>` |
| "Run the tests" | `<run ref="run-tests" />` (declared in `<approved-commands>`) |
| "Review the diff and decide what needs changing" | `<instruction>Review the diff and decide what needs changing.</instruction>` |
| "Testing phase: run tests, check coverage, fix failures" | `<step>` with `<instruction>` for context, `<run>` for execution, `<gate>` for enforcement |

**Start at the bottom of the ladder.** Write the commands first. Get the atoms on the page before worrying about how they compose:

```xml
<command>cargo test --all-features</command>
<command>cargo fmt -- --check</command>
<command>cargo clippy --all-targets --all-features -- -D warnings</command>

<resource type="executable">cargo</resource>
<resource type="file" path="Cargo.toml" />

<criterion check="{{ test_failures == 0 }}" />
<criterion check="{{ lint_errors == 0 }}" />

<rule>Never skip tests to make the build pass.</rule>
```

Don't organize yet. Don't worry about where these live. Just get them written.

### Granularity

The escalation ladder implies a size hierarchy. Use it:

**Commands are atomic operations.** A single executable thing — a shell command, a script invocation, a tool call. If you can copy-paste it into a terminal, it's a command.

**`<run>` elements are execution verbs.** A specific thing the LLM should execute. `<run>` has three forms: reference (`<run ref="cmd-id" />`), inline (`<run>command text</run>`), and inline+capture (`<run set="var">command text</run>`). Tool tags (`<read>`, `<write>`, `<ask-user-question>`, etc.) and behavioral tags (`<interview-mode>`, `<plan-mode>`, etc.) are direct children of steps — no wrapper needed.

**Steps are phases.** A *phase* of work with a clear entry condition, a body of work, and an exit condition (gate). Good steps have a title that names the phase, instructions that could take minutes or hours to complete, and a gate that determines whether to proceed. **If it takes 5 seconds and has no meaningful pass/fail, it's not a step.** It's a `<run>` element inside a step.

---

## Step 3: Compose — Run, Gates, and Logic

Now that you have your primitives, define how they interact. This is the layer of **logic and safety**. Only add composition when the primitive requires it.

### Using Run

`<run>` is the execution verb. It has three forms:

```xml
<!-- Reference form: execute a declared command -->
<run ref="run-tests" />

<!-- Reference form with capture: execute and store stdout -->
<run ref="test-failure-count" set="test_failures" />

<!-- Inline form: execute command text directly -->
<run>git add -p</run>

<!-- Inline + capture form -->
<run set="staged_count">git diff --cached --name-only | wc -l</run>
```

Declare reusable commands in `<approved-commands>` (for step-level execution) or `<approved-gate-commands>` (for gate measurement) inside `<meta>`, then reference them with `<run ref="cmd-id" />`.

**Instructions and run elements are siblings.** `<instruction>` contains only prose that provides interpretive context and guidance. It may include guidance decorators like `<rule>`, `<principle>`, `<anti-pattern>`, and `<reference>`. `<run>` elements, tool tags, and behavioral tags are direct children of the step alongside `<instruction>`. `<note>` is a prose element allowed only at the step level or root `<ape>` level, not inside `<instruction>` or other structural containers.

### Writing Good Gates

Every gate needs a condition (`<criteria>` or a single `<criterion>`) and exactly one `<on-fail>` (or both `<on-fail>` and `<on-pass>`). Gates are **purely structural** — they use attributes for flow control and must not contain bare prose text. Think about *what could go wrong* and *where to route the failure*:

```xml
<!-- Self-contained gate: run measurement, then evaluate -->
<gate>
  <run ref="test-failure-count" set="test_failures" />
  <criterion check="{{ test_failures == 0 }}" />
  <on-fail goto="debug" />
</gate>

<!-- Compound: multiple measurements, all must pass -->
<gate>
  <run ref="test-failure-count" set="test_failures" />
  <run ref="lint-error-count" set="lint_errors" />
  <criteria operator="and">
    <criterion check="{{ test_failures == 0 }}" />
    <criterion check="{{ lint_errors == 0 }}" />
  </criteria>
  <on-fail goto="implementation" />
</gate>

<!-- Named reusable criteria -->
<gate>
  <run ref="fmt-error-count" set="fmt_errors" />
  <run ref="clippy-error-count" set="clippy_errors" />
  <criteria id="quality-checks" operator="and">
    <criterion check="{{ fmt_errors == 0 }}" />
    <criterion check="{{ clippy_errors == 0 }}" />
  </criteria>
  <on-fail retry="true" max="3" then="halt" />
</gate>

<!-- Reuse named criteria from another gate -->
<gate>
  <criteria ref="quality-checks" />
  <on-fail goto="fix-code" />
</gate>
```

**Gates are self-contained.** A gate can contain `<run>` children to execute measurement commands (from `<approved-gate-commands>`), followed by `<criteria>` or `<criterion>` to evaluate the results. Use `<criterion check="{{ var == value }}" />` for explicit, unambiguous conditions. Use `<criteria operator="and|or">` with multiple `<criterion>` children for compound logic. Named criteria (`<criteria id="...">`) can be reused across gates with `<criteria ref="criteria-id" />`.

**The `<goto>` element.** You can use `<on-fail>` and `<on-pass>` with the `goto` attribute, or use the `<goto>` element as an alternative:
```xml
<on-fail>
  <goto ref="implementation" />
</on-fail>
```

**Not every step needs a gate.** If a step is purely informational or always proceeds, you can omit the gate.

### Writing Good Instructions

Each `<instruction>` is one coherent unit of prose. It can contain guidance decorators like `<rule>`, `<principle>`, `<anti-pattern>`, and `<reference>`. It must not contain executable children like `<run>` or `<output>` — those are siblings of `<instruction>` within the step. Use `<instruction>` standalone for single instructions, or wrap multiple in `<instructions>`:

```xml
<instruction>
  Narrative guidance about what this step involves and how to approach it.
</instruction>

<note>Context that helps the executor understand what's about to happen.</note>

<instructions>
  <instruction>
    Specific guidance for this part of the work.
    <rule>Focus on logic errors, not style.</rule>
  </instruction>
  <instruction>
    Another aspect to consider.
  </instruction>
</instructions>
```

### Adding Rules and Guidance

Go back through the source document and find everything that isn't an action:

| Source content | APE element | Where to put it |
|---|---|---|
| "Important: never do X" | `<rule>Never do X</rule>` | On the step or instruction it applies to |
| "Follow these style rules: ..." | `<rule>...</rule>` | On the step or instruction it applies to |
| "Common mistake: doing Y" | `<anti-pattern>Doing Y</anti-pattern>` | On the step where Y could happen |
| "Note: this only applies when Z" | `<note>This only applies when Z</note>` | At the step level or root `<ape>` level; use the `note` attribute on `<command>` for command-level annotations |
| "The output should look like this: ..." | `<template id="..." format="md">...</template>` | In `<meta>` if reused, or scoped to the step |
| "Write the result to X" | `<output to="file" target="X">...</output>` | Inside the step that produces it |

**Place decorators as close to their subject as possible.** A rule about a specific command goes in the step containing that `<run>`, not at the document level.

### Using Principles as a Decorator

`<principle>` can appear inside any block — steps, instructions, gates, and conditionals — not just at the root level. Use it to articulate the guiding values for a phase of work:

```xml
<step number="1" id="design">
  <title>Design Phase</title>
  <principle name="Simplicity">Favor simplicity over comprehensiveness.</principle>
  <principle name="Testability">Ensure the design is testable before implementation.</principle>
  <instruction>...</instruction>
  <run ref="design-review" />
</step>
```

### Handling Conditional Logic

If the source document has branching ("if X, do Y; otherwise do Z"), use `<conditional>`:

```xml
<!-- Multiple branches -->
<conditional on="{{ environment }}">
  <case value="production"><run ref="deploy-prod" /></case>
  <case value="staging"><run ref="deploy-staging" /></case>
  <default><run ref="deploy-local" /></default>
</conditional>

<!-- Binary choice -->
<conditional on="{{ has_docker }}">
  <case value="true"><run>docker compose up</run></case>
  <case value="false"><run>npm start</run></case>
</conditional>

<!-- Simple routing with attribute default -->
<conditional on="{{ request_type }}" default="investigate">
  <case value="implementation" goto="make-changes" />
  <case value="information" goto="investigate" />
</conditional>
```

One construct handles both binary and multi-branch logic. Use the `default` attribute for simple outcomes (a step ID, `"halt"`, `"proceed"`), or a `<default>` child element when the fallback needs content.

**Conditionals are purely structural.** They must contain only `<case>` and `<default>` children with structural content, not bare prose.

**Conditionals vs. gates:** Conditionals are **navigational** — they route based on a value. Gates are **evaluative** — they judge whether work meets a bar. If the source says "if tests fail, go back to step 2," that's a gate with `<on-fail goto="step-2">`, not a conditional. Quality enforcement between steps lives in gates.

---

## Step 4: Structure — Steps, Meta, and Variables

Now that you know what your `<run>` elements are and how they behave, you can decide where they live. This is the outer ring: scoping, lifecycle, and reuse.

### Building the Skeleton

`<steps>` is optional. For simple documents — a single command, a flat list of `<run>` elements — you don't need steps at all:

```xml
<ape version="0.4.0" xmlns="https://ape-lang.dev/schema/0">

  <meta><name>List Files</name></meta>
  <run>ls -la</run>
</ape>
```

For multi-phase workflows, wrap your composed `<run>` elements and gates into steps:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ape version="0.4.0" xmlns="https://ape-lang.dev/schema/0">

  <meta>
    <name>Workflow Name</name>
    <description>What this workflow does and why.</description>
  </meta>

  <steps>
    ...
  </steps>

</ape>
```

### Writing Steps

For each phase you identified, build a step:

```xml
<step number="1" id="short-kebab-id" uses="resource-a, resource-b">
  <prerequisite ref="previous-step-id" goto="previous-step-id" />

  <title>Human-Readable Phase Name</title>

  <instruction>Narrative context for the agent.</instruction>

  <run ref="some-command" />

  <gate>
    <run ref="some-measurement" set="result" />
    <criterion check="{{ result == expected }}" />
    <on-fail goto="some-step" />
  </gate>
</step>
```

**Prerequisites come first and are prose-free.** If a step has a prerequisite, it must be the first child element. Prerequisites are entry conditions — the LLM evaluates them before reading anything else in the step. `<prerequisite>` is a structure container: use `ref` to identify the dependency, `goto` or `halt` to specify the recovery path, and optional `<check>` children for runtime verification. Do not put prose inside a `<prerequisite>`.

**Titles are optional.** If the step `id` is descriptive enough (e.g., `id="verify"`, `id="plan"`), omit the `<title>`. Use `<title>` only when the step needs a human-readable name that the `id` cannot convey.

### Identifying Variables

Anything that might change between runs or that the user might want to configure becomes a `<var>`. A `<var>` declaration must carry a value — via element content, `value` attribute, or `default` attribute:

- Thresholds ("80% coverage") → `<var name="coverage_threshold" type="number" default="80" />`
- Feature flags ("if coverage tool is installed") → `<var name="has_coverage" type="boolean" default="false" />`

`<var>` can also contain children to compute or resolve values:

- Computing a value from a command → `<var name="git-hash"><command>git rev-parse HEAD</command></var>`
- Declaring resource dependencies → `<var name="config"><resource type="file" path="config.json" /></var>`

Values captured at runtime do not need `<var>` declarations — they are created implicitly:

- User input collected during the workflow → `<ask-user-question var="name">` creates the variable
- Command output → `<command set="result">...</command>` creates the variable

**Do not declare empty variables.** If a value has no default, no content, no child elements, and no `value` attribute, it should not be a `<var>`. Either give it a default or let it be created implicitly when its value becomes available.

**If a value appears in more than one place, make it a variable.** If a value might change between environments or users, make it a variable. Use inline `<var>` for a single variable; use `<variables>` only when declaring two or more.

**Params vs. vars:** Variables are internal state — values this document owns and controls. Parameters are inputs from the caller — values this document *needs* but doesn't define. If an external system invokes your document and must provide a value, that's a `<param ref="..." />`. If you're declaring a configurable default within your own document, that's a `<var>`.

### Declaration Placement

Now is when `<meta>` earns its keep. Because you wrote the primitives first and composed them second, you can see what's reused:

- Commands used in steps → declare in `<meta><approved-commands>` with an `id`, execute with `<run ref="..." />`
- Commands used in gates → declare in `<meta><approved-gate-commands>` with an `id`, execute with `<run ref="..." />` inside the gate
- Resources consumed across steps → declare in `<meta><resources>`
- Variables used in multiple places → declare in `<meta><variables>`

Add declarations to `<meta>` only when needed. Omit any section that would be empty or contain a single item (use the inline singular form for single declarations).

**Rule of thumb:** If you reference it more than once, it belongs in `<meta>`. If it only matters in one place, use inline `<run>` in that step.

---

## Common Patterns

### Linear Pipeline
Steps proceed in order. Each gate either passes to the next step or fails back.
```
[Step 1] → [Step 2] → [Step 3] → [Step 4] → Done
              ↑ on-fail ←──┘
```

### Development Cycle
The last step loops back to the first. The workflow repeats until the user stops.
```
[Spec] → [Implement] → [Test] → [Commit] → [Transition]
  ↑                                              │
  └──────────── on-pass goto="spec" ─────────────┘
```

### Tiered Failure Recovery
A single `<on-fail>` with retry handles escalation within one gate. For different recovery routes, use a conditional inside the on-fail or split into separate gates at different steps.
```
[Lint] gate:
  on-fail → goto implementation

[Build] gate:
  on-fail retry="true" max="3" then="halt"
```

### Interview-Driven Setup
Collect information before the real workflow begins.
```xml
<step number="1" id="setup">
  <title>Project Setup</title>
  <interview-mode>
    <ask-user-question var="name">Project name?</ask-user-question>
    <ask-user-question var="lang" type="choice">
      <option value="rust">Rust</option>
      <option value="python">Python</option>
    </ask-user-question>
  </interview-mode>
  <gate>
    <criterion check="{{ name != '' }}" />
    <on-fail retry="true" max="1" then="halt" />
  </gate>
</step>
```

### Structured Output with Templates
Define the shape of content, then direct it to a destination.
```xml
<template id="changelog-entry" format="md">
## {{ version }} — {{ date }}
{{ changes }}
</template>

<output template="changelog-entry" to="file" target="CHANGELOG.md"
       anchor="## Unreleased" position="append" />
```

---

## Common Mistakes

**Starting with the skeleton.** If you're writing `<meta>` and `<steps>` before you've written a single `<command>`, you're working top-down. Go back to the middle. Get the meat on the page first.

**Making everything a step.** If your workflow has 20 steps and most have no gates, you probably have 5 steps with 4 `<run>` elements each.

**Gates without explicit failure handling.** Every `<on-fail>` must carry a flow-control attribute (`goto`, `retry`, `halt`, `proceed`). There is no default — the author must be explicit about what happens on failure.

**Putting flow control in the wrong place.** "If X, do Y" inside a step → `<conditional>`. "If this step fails, go to that step" → gate `<on-fail>`. Don't use conditionals for inter-step routing or gates for intra-step branching.

**Hardcoding values that should be variables.** If you write a threshold like "Coverage above 80%" and it appears elsewhere too, make it `{{ coverage_threshold }}`.

**Declaring commands inline when they're reused.** If the same `cargo test` command appears in three inline `<run>` elements, declare it once in `<approved-commands>` with an `id` and use `<run ref="..." />` everywhere else.

**Premature promotion to `<meta>`.** Don't move a command to `<approved-commands>` because it *might* be reused. Move it when it *is* reused. Middle-out means you discover reuse patterns; you don't predict them.

**Using `<run ref>` with body text.** `<run>` takes either a `ref` attribute or body text, never both. `ref` executes a declared command; body text is an inline command.

**Missing rules.** If the source document emphasizes "NEVER do X" or "ALWAYS do Y," that's a `<rule>`. Without it, the LLM might not treat it as non-negotiable.

**Forgetting to add guidance decorators.** When instructions or steps have supporting guidance — rules, principles, anti-patterns, notes, or references — use the appropriate decorator elements. Don't bury guidance in prose.

**Restating what structure already enforces.** If a gate ensures tests pass before proceeding, a `<rule>` saying "NEVER move on when tests are failing" is redundant — the gate already makes it impossible. If a `<command>` definition specifies the exact invocation, a `<rule>` restating the same flags adds noise. Trust your structure.

**Saying the same thing in multiple decorator types.** A `<rule>` saying "never skip tests," a `<principle>` saying "tests are mandatory," and an `<anti-pattern>` saying "skipping tests" are three expressions of one idea. Pick the element that best fits (here, `<rule>`) and delete the others.

---

## Conversion Checklist

Before you're done, verify:

- [ ] Every reusable command has an `id` in `<approved-commands>` or `<approved-gate-commands>` and is referenced with `<run ref="..." />`
- [ ] Gate commands are in `<approved-gate-commands>`, step commands are in `<approved-commands>`
- [ ] Every tool/file/service mentioned is declared as a `<resource>`
- [ ] `<title>` is present only when the step `id` is not descriptive enough
- [ ] `<instruction>` contains only prose and guidance decorators (`<rule>`, `<principle>`, `<anti-pattern>`, `<reference>`) — no executable children like `<run>` or `<output>`, and no `<note>` (use at step or root level only)
- [ ] `<run>` uses either `ref` or body text, never both
- [ ] `<run>` inside `<gate>` only references `<approved-gate-commands>`
- [ ] `<run>` outside `<gate>` only references `<approved-commands>`
- [ ] `<criterion>` uses `check` for explicit measurable conditions or `ref` for reusing named criteria — no prose
- [ ] Compound criteria use `<criteria operator="and|or">` with `<criterion>` children
- [ ] Named criteria (`<criteria id="...">`) are used when the same conditions appear in multiple gates
- [ ] `<on-fail>` and `<on-pass>` use attributes or `<goto>` element — no prose text
- [ ] Every step that can fail has a `<gate>` with `<criteria>` or `<criterion>` and a single `<on-fail>`
- [ ] Prerequisites are the first children in their step
- [ ] Prerequisites describe the condition and the consequence of it not being met
- [ ] Every `goto` attribute or `<goto>` element points to a real step `id`
- [ ] Every `<run ref>` points to a real command `id` in the appropriate command section
- [ ] Every `ref_id` points to a real element `id` (used by `<reference>`)
- [ ] Every `uses` lists real resource `id`s
- [ ] Hard requirements are in `<rule>`, not just prose
- [ ] Guidance decorators (`<rule>`, `<principle>`, `<anti-pattern>`, `<reference>`, `<note>`) are used where applicable
- [ ] No guidance restates what a gate, prerequisite, or conditional already enforces
- [ ] No guidance is expressed in more than one decorator type (rule + anti-pattern, rule + principle, etc.)
- [ ] No step-level rule duplicates a document-level rule
- [ ] `<principle>` elements are nested where needed, not only at root level
- [ ] Every `<var>` has a value (content, child elements, `value` attr, or `default` attr) — no empty declarations
- [ ] Variables that compute values use `<command>` or `<resource>` children appropriately
- [ ] `<variables>` wrapper is used only with 2+ variables; single variables use inline `<var>`
- [ ] No XML comments anywhere in the document
- [ ] The workflow has a clear start and end (or an explicit loop via `<on-pass goto>`)
- [ ] Structured outputs use `<template>` for shape and `<output>` for destination
- [ ] Output format hints (`format="md"`, etc.) are specified where content has a known format

# APE Authoring Guide

> This file teaches you how to **write** APE workflows from scratch.
> For converting existing documents into APE, see `ape-conversions.md`. For executing APE, see `ape-llms.md`. For the full language reference, see `ape-spec.md`.

---

## The Mindset

APE has two primitives. Everything you're converting boils down to:

- **Commands** — things someone *does*. Shell commands, actions, decisions.
- **Resources** — things someone *needs*. Files, tools, services, executables.

Everything else in APE is flow control (steps, gates, conditionals), metadata (actors, variables), or guidance (constraints, rules, notes). Start by finding the commands and resources. The structure follows.

---

## Step 1: Read the Source Document

Before writing any XML, read the entire source and answer these questions:

**Who is involved?** If multiple participants interact and you need to specify who does what, each becomes an `<actor>`. Look for:
- Role names ("the developer," "the reviewer," "CI")
- Implied agents ("run this command" implies someone runs it; "the bot will check" implies an agent)
- Handoffs between participants

If a single LLM agent does everything with no human interaction, skip actors entirely — they add noise without value.

**What do they need?** Every tool, file, service, or prerequisite becomes a `<resource>`:
- "Make sure X is installed" → `<resource type="executable" />`
- "Open the config file" → `<resource type="file" path="..." />`
- "You'll need access to the staging server" → `<resource type="service" />`
- If something is optional, use `required="false"`

**What do they do?** Every action, command, or instruction becomes a `<command>` or `<action>`:
- Literal shell commands → `<command>` with the text as content
- Specific directives ("update the documentation") → `<action>` with inline text describing what to do
- If a command is reused, declare it with an `id` in `<meta>` and reference it with `ref` later

**What are the phases?** Every distinct stage, phase, or checkpoint becomes a `<step>`:
- Look for numbered lists, section headers, phase names
- Look for explicit ordering ("first... then... finally...")
- Look for decision points ("if tests pass, move on; if not, go back")

**What can go wrong?** Every failure case, quality check, or approval point becomes a `<gate>`:
- "Make sure all tests pass" → `<gate>` with `<criteria>` and `<on-fail>`
- "Get sign-off before proceeding" → `<gate>` with an `<ask-user-question>` before it
- "If the build breaks, start over from step 2" → `<on-fail goto="step-2">`

**What are the rules?** Every constraint, best practice, or anti-pattern becomes a decorator:
- Hard constraints ("never do X") → `<constraint>`
- Style rules ("use imperative mood") → `<rules>` with `<rule>`
- Common mistakes to avoid → `<anti-patterns>` with `<anti-pattern>`
- Context that helps understanding but isn't actionable → `<note>`

---

## Step 2: Determine Granularity

This is the most important judgment call. The question is: **what's a step vs. an action vs. a command?**

### Steps Are Phases

A step is a *phase* of work with a clear entry condition, a body of work, and an exit condition (gate). Good steps have:

- A title that names the phase ("Specification," "Linting," "Deployment")
- A goal that explains *why* this phase exists
- Instructions that could take minutes or hours to complete
- A gate that determines whether to proceed

**If it takes 5 seconds and has no meaningful pass/fail, it's not a step.** It's an action or command inside a step.

### Actions Are Directives Within a Phase

An action is a specific thing the LLM should execute inside a step. Actions can have:

- Inline prose (mixed content saying exactly what to do)
- Commands (concrete things to execute)

Unlike `<instruction>`, which allows interpretive latitude, `<action>` is a direct directive — the LLM should perform it as stated, not treat it as a guide.

**If the source document has a bullet point that says "run X and check Y," that's an action.**

### Commands Are Atomic Operations

A command is a single executable thing — a shell command, a script invocation, a tool call. If you can copy-paste it into a terminal, it's a command.

**If the source says "run `npm test`," that's a command.** If it says "make sure the tests cover edge cases," that's an action with inline prose.

### The Escalation Ladder

When reading the source document, classify each instruction:

| Source says... | APE element |
|---------------|-------------|
| `npm test` | `<command>npm test</command>` |
| "Run the tests" | `<action><command>npm test</command></action>` |
| "Run the tests and make sure coverage is above 80%" | `<action>Coverage must be above 80%.<command>npm test</command></action>` |
| "Testing phase: run tests, check coverage, fix any failures, then proceed" | `<step>` containing multiple actions and a gate |

---

## Step 3: Build the Skeleton

`<steps>` is optional. For simple documents — a single command, a flat list of actions — you don't need steps at all:

```xml
<!-- Simple: no steps needed -->
<ape version="0.2.2" xmlns="https://ape-lang.dev/schema/0">

  <meta><name>List Files</name></meta>
  <command>ls -la</command>
</ape>
```

For multi-phase workflows, start with this structure and fill in:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ape version="0.2.2" xmlns="https://ape-lang.dev/schema/0">

  <meta>
    <name>Workflow Name</name>
    <description>What this workflow does and why.</description>
  </meta>

  <steps>
    ...
  </steps>

</ape>
```

Add declarations to `<meta>` only when needed: `<actors>` when multiple participants interact, `<variables>` when there are two or more configurable values, `<resources>` when tools or files are consumed, `<commands>` when commands are reused across steps. Omit any section that would be empty or contain a single item (use the inline singular form for single declarations).

### Declaration Placement

Put things in `<meta>` if they're used across multiple steps. Put things inside a specific `<step>` if they're only relevant there.

**Rule of thumb:** If you reference it more than once, it belongs in `<meta>`. If it only matters in one place, scope it.

---

## Step 4: Write the Steps

For each phase you identified, build a step:

```xml
<step number="1" id="short-kebab-id" uses="resource-a, resource-b">
  <prerequisite ref="previous-step-id">What must be true and what to do if not met.</prerequisite>

  <title>Human-Readable Phase Name</title>
  <goal>One sentence: why this phase exists.</goal>

  <instruction>...</instruction>

  <gate>
    <criteria>What must be true to proceed</criteria>
    <on-fail goto="some-step">When and why to go there</on-fail>
  </gate>
</step>
```

**Prerequisites come first.** If a step has a prerequisite, it must be the first child element. Prerequisites are entry conditions — the LLM evaluates them before reading anything else in the step. The text should describe both what must be true and what happens if it is not.

**Titles are optional.** If the step `id` is descriptive enough (e.g., `id="verify"`, `id="plan"`), omit the `<title>`. Use `<title>` only when the step needs a human-readable name that the `id` cannot convey.

### Writing Good Gates

Every gate needs a `<criteria>` and exactly one `<on-fail>`. Think about *what could go wrong* and *where to route the failure*:

```xml
<!-- Simple: retry then give up -->
<gate>
  <criteria>All tests pass</criteria>
  <on-fail retry="true" max="3" then="halt">Tests still failing</on-fail>
</gate>

<!-- Route back to fix -->
<gate>
  <criteria>Build succeeds with no warnings</criteria>
  <on-fail goto="implementation">Build failed — fix and retry</on-fail>
</gate>

<!-- Cyclic: on-pass loops back to the beginning -->
<gate>
  <criteria>Ready for next iteration</criteria>
  <on-pass goto="first-step">Start the next cycle</on-pass>
  <on-fail halt="true">Cannot continue</on-fail>
</gate>
```

**Not every step needs a gate.** If a step is purely informational or always proceeds, you can omit the gate.

### Writing Good Instructions

Each `<instruction>` is one coherent unit. Use `<instruction>` standalone for single instructions, or wrap multiple in `<instructions>`:

```xml
<instruction>
  <action>
    What to do, specifically.
    <command ref="some-command" />
  </action>
</instruction>

<instructions>
  <instruction>
    <note>Context that helps the executor understand what's about to happen.</note>
  </instruction>
  <instruction>
    <action>
      What to do, specifically.
      <command ref="some-command" />
    </action>
  </instruction>
  <instruction>
    <ask-user-question var="ready" type="confirm">Good to proceed?</ask-user-question>
  </instruction>
</instructions>
```

---

## Step 5: Add Constraints and Guidance

Go back through the source document and find everything that isn't an action:

| Source content | APE element | Where to put it |
|---------------|-------------|-----------------|
| "Important: never do X" | `<constraint>Never do X</constraint>` | On the step or instruction it applies to |
| "Follow these style rules: ..." | `<rules><rule>...</rule></rules>` | On the step or instruction it applies to |
| "Common mistake: doing Y" | `<anti-patterns><anti-pattern>Doing Y</anti-pattern></anti-patterns>` | On the step where Y could happen |
| "Note: this only applies when Z" | `<note>This only applies when Z</note>` | Inside the relevant instructions |
| "The reason we do this is..." | `<rationale>The reason...</rationale>` | Inside the instruction it explains |
| "The output should look like this: ..." | `<template id="..." format="md">...</template>` | In `<meta>` if reused, or scoped to the step |
| "Write the result to X" | `<output to="file" target="X">...</output>` | Inside the action that produces it |

**Place decorators as close to their subject as possible.** A constraint about a specific command goes inside the instruction containing that command, not at the step level.

**Constraints come first inside instructions.** When a `<constraint>` appears inside an `<instruction>`, place it before prose and executable content. The LLM must read restrictions before executing work.

---

## Step 6: Handle Conditional Logic

If the source document has branching ("if X, do Y; otherwise do Z"), use `<conditional>`:

```xml
<!-- Multiple branches -->
<conditional on="{{ environment }}">
  <case value="production"><command ref="deploy-prod" /></case>
  <case value="staging"><command ref="deploy-staging" /></case>
  <default><command ref="deploy-local" /></default>
</conditional>

<!-- Binary choice -->
<conditional on="{{ has_docker }}">
  <case value="true"><command>docker compose up</command></case>
  <case value="false"><command>npm start</command></case>
</conditional>

<!-- Simple routing with attribute default -->
<conditional on="{{ request_type }}" default="investigate">
  <case value="implementation" goto="make-changes" />
  <case value="information" goto="investigate" />
</conditional>
```

One construct handles both binary and multi-branch logic. Use the `default` attribute for simple outcomes (a step ID, `"halt"`, `"proceed"`), or a `<default>` child element when the fallback needs content.

**Conditionals vs. gates:** Conditionals are **navigational** — they route based on a value. Gates are **evaluative** — they judge whether work meets a bar. If the source says "if tests fail, go back to step 2," that's a gate with `<on-fail goto="step-2">`, not a conditional. Conditionals live inside instructions. Quality enforcement between steps lives in gates.

---

## Step 7: Identify Variables

Anything that might change between runs or that the user might want to configure becomes a `<var>`. A `<var>` declaration must carry a value — via element content, `value` attribute, or `default` attribute:

- Thresholds ("80% coverage") → `<var name="coverage_threshold" type="number" default="80" />`
- Feature flags ("if coverage tool is installed") → `<var name="has_coverage" type="boolean" default="false" />`

Values captured at runtime do not need `<var>` declarations — they are created implicitly:

- User input collected during the workflow → `<ask-user-question var="name">` creates the variable
- Command output → `<command set="result">...</command>` creates the variable

**Do not declare empty variables.** If a value has no default, no content, and no `value` attribute, it should not be a `<var>`. Either give it a default or let it be created implicitly when its value becomes available.

**If a value appears in more than one place, make it a variable.** If a value might change between environments or users, make it a variable. Use inline `<var>` for a single variable; use `<variables>` only when declaring two or more.

**Params vs. vars:** Variables are internal state — values this document owns and controls. Parameters are inputs from the caller — values this document *needs* but doesn't define. If an external system invokes your document and must provide a value, that's a `<param ref="..." />`. If you're declaring a configurable default within your own document, that's a `<var>`.

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
  <goal>Gather project configuration.</goal>
  <instruction>
    <interview-mode actor="claude" target="developer">
      <ask-user-question var="name">Project name?</ask-user-question>
      <ask-user-question var="lang" type="choice">
        <option value="rust">Rust</option>
        <option value="python">Python</option>
      </ask-user-question>
    </interview-mode>
  </instruction>
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

**Making everything a step.** If your workflow has 20 steps and most have no gates, you probably have 5 steps with 4 actions each.

**Declaring actors when none are needed.** If a single LLM agent does everything, actors add noise. Only declare actors when multiple participants interact and you need to specify who does what.

**Gates without explicit failure handling.** Every `<on-fail>` must carry a flow-control attribute (`goto`, `retry`, `halt`, `proceed`). There is no default — the author must be explicit about what happens on failure.

**Putting flow control in the wrong place.** "If X, do Y" inside a step → `<conditional>`. "If this step fails, go to that step" → gate `<on-fail>`. Don't use conditionals for inter-step routing or gates for intra-step branching.

**Hardcoding values that should be variables.** If you write a threshold like "Coverage above 80%" and it appears elsewhere too, make it `{{ coverage_threshold }}`.

**Declaring commands inline when they're reused.** If the same `cargo test` command appears in three actions, declare it once in `<meta>` with an `id` and use `ref` everywhere else.

**Missing constraints.** If the source document emphasizes "NEVER do X" or "ALWAYS do Y," that's a `<constraint>`. Without it, the LLM might not treat it as non-negotiable.

---

## Conversion Checklist

Before you're done, verify:

- [ ] Actors are declared only when multiple participants interact — omit for single-agent workflows
- [ ] Every reusable command has an `id` and is referenced with `ref`
- [ ] Every tool/file/service mentioned is declared as a `<resource>`
- [ ] `<title>` is present only when the step `id` is not descriptive enough
- [ ] Steps with work to do have an `<instruction>` or `<instructions>` (not both)
- [ ] Every step that can fail has a `<gate>` with `<criteria>` and a single `<on-fail>`
- [ ] Prerequisites are the first children in their step
- [ ] Prerequisites describe the condition and the consequence of it not being met
- [ ] Constraints inside instructions appear before prose and executable content
- [ ] Every `goto` points to a real step `id`
- [ ] Every `ref` points to a real command `id`
- [ ] Every `uses` lists real resource `id`s
- [ ] Hard constraints are in `<constraint>`, not just prose
- [ ] Every `<var>` has a value (content, `value` attr, or `default` attr) — no empty declarations
- [ ] `<variables>` wrapper is used only with 2+ variables; single variables use inline `<var>`
- [ ] No XML comments anywhere in the document
- [ ] The workflow has a clear start and end (or an explicit loop via `<on-pass goto>`)
- [ ] Structured outputs use `<template>` for shape and `<output>` for destination
- [ ] Output format hints (`format="md"`, etc.) are specified where content has a known format

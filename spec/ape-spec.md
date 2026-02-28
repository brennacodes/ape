# APE Language Specification

**Version:** 0.2.4-draft
**Status:** Draft
**Schema:** See `ape.xsd`
**LLM Execution Contract:** See `ape-llms.md`
**Authoring Guide:** See `ape-authoring.md`

---

## 1. Overview

APE is an XML-based markup for defining structured, enforceable workflows. Written by humans, read by machines, executed by LLM agents — without requiring a system prompt.

APE documents are self-contained. The file defines who does what, what tools to use, when to stop and wait, and what to do on success or failure.

### Design Principles

- **So easy a caveman can write it.** Tags say what they mean.
- **Two primitives.** Things you *do* (`<command>`) and things you *need* (`<resource>`). Everything else is flow control and metadata.
- **The document is the engine manual.** No system prompt crutch.
- **Flat where possible, nested where necessary.** Both always valid.
- **Permissive structure, strict validation.** The schema validates shape. A validator enforces semantics. Tooling enforces taste.
- **Declarations live where the author puts them.** At the top for global access, inside a block for scoped access. Both are valid.
- **Self-closing tags.** Any element with no children and no text may be self-closed.

---

## 2. Core Concepts

### 2.1 Blocks

A **block** is any element with an opening and closing tag. `<step>...</step>`, `<action>...</action>`, `<gate>...</gate>`, `<instruction>...</instruction>` — these are all blocks.

Blocks can contain: declarations (variables, resources, commands), instructions (tasks, notes), tool tags, decorators (constraints, rules, anti-patterns), and other blocks.

This is the fundamental structural unit. If it has open and close tags with space between, it's a block, and it can hold things.

### 2.2 Declarations

Declarations define reusable identifiers: `<var>`, `<resource>`, `<command>`, `<actor>`, `<tool-tag>`, `<param>`.

Declarations can appear:
- Inside `<meta>` (global, available throughout the document)
- At the top level of `<ape>` (global)
- Inside any block (scoped to that block and its children)

**Scoping rule:** A declaration is available to everything *inside* the block where it appears, and everything *after* it at the same level. It does not leak upward or backward.

### 2.3 Identifier Uniqueness

APE has two classes of identifiers with different uniqueness rules:

**Global IDs** — must be unique across the entire document, regardless of where they are declared. Shadowing is not permitted:
- `actor/@id`
- `resource/@id`
- `command/@id`
- `sequence/@id`
- `reference/@id`
- `tool-tag/@name`
- `step/@id`

**Scoped names** — must be unique within their scope, but may shadow a parent scope's declaration of the same name:
- `var/@name`
- `param/@ref` (resolved from caller scope; locally scoped)

Scope affects *visibility* (what you can reference from where). It does not relax uniqueness for global IDs. Two `<command id="x">` declarations anywhere in the document is an error, even if they appear in different blocks.

### 2.4 Two-Pass Resolution

APE uses a two-pass model:

1. **Pass 1 — Collect.** Walk the document, build registries of all declarations with their scope (which block they live in).
2. **Pass 2 — Resolve.** When a reference is encountered (`ref`, `{{ var }}`, `actor`, `goto`), resolve by searching the nearest scope first, then falling back to parent scopes, then global.

"Declare before use" is an optional lint rule (`--strict`), not a structural requirement. The two-pass model means forward references work by default.

### 2.5 Interleaving

Containers do not enforce child ordering. A `<step>` can have its `<constraint>` before its `<instruction>`, or its `<variables>` after its `<gate>`. The schema allows interleaving; the validator enforces required children exist; `ape fmt` outputs canonical order.

### 2.6 Decorators

Decorators are elements that annotate but don't alter execution: `<constraint>`, `<rules>`, `<anti-patterns>`, `<note>`, `<rationale>`. They can appear inside any block.

### 2.7 Mixed Content

**Structural elements** — `<ape>`, `<meta>`, `<gate>`, `<steps>`, `<actors>`, `<variables>`, `<resources>`, `<commands>`, `<tool-tags>`, `<params>`, `<sequence>`, `<prerequisites>` — do **not** allow mixed content. Children only, no interleaved text.

**Instruction-level elements** — `<instruction>`, `<instructions>`, `<action>`, `<if>`, `<else>`, `<case>`, `<default>`, `<each>`, `<on-fail>`, `<on-pass>`, and `<prerequisite>` — **do** allow mixed content. Plain text serves as narrative guidance; child tags serve as concrete anchors to execute.

This is the one structural distinction to internalize: *above* the instruction layer, structure is strict. *At* the instruction layer, prose and tags intermix freely.

---

## 3. Document Structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ape version="0.2.2" xmlns="https://ape-lang.dev/schema/0">

  <meta>
    <!-- Always first. Contains metadata and declarations.
         No tool tags. No instructions. -->
    <name>My Workflow</name>
    <description>What this workflow does and why.</description>

    <actors>...</actors>
    <params>...</params>
    <variables>...</variables>
    <resources>...</resources>
    <commands>...</commands>
    <tool-tags>...</tool-tags>
  </meta>

  <!-- Any of these can also appear here at root level, or inside steps -->
  <variables>...</variables>
  <resources>...</resources>
  <commands>...</commands>

  <steps>
    <!-- Optional: the workflow (required if document has multi-step logic) -->
  </steps>

  <principles>...</principles>

</ape>
```

### 3.1 `<ape>` (Root)

| Attribute | Required | Description |
|-----------|----------|-------------|
| `version` | Yes | Spec version (e.g., `0.2.2`) |
| `xmlns` | Yes | Namespace URI, pinned to major version |

**Versioning:** The namespace is pinned to the major version (`https://ape-lang.dev/schema/0.2`). The `version` attribute carries the full version (`0.2.2`, `0.2.3`, etc.). Documents with different minor versions share the same namespace and are expected to be broadly compatible within a major version. Breaking changes increment the major version and the namespace.

`<meta>` must be first if present. Everything else — `<steps>`, declarations, `<principles>`, `<reference>` — is optional and can appear in any order. If `<steps>` is present, it must contain at least one `<step>`.

### 3.2 `<meta>`

If present, `<meta>` is the first child of `<ape>`. It contains document metadata and declarations. It is configuration, not execution.

| Child | Required | Description |
|-------|----------|-------------|
| `<name>` | No | APE document name |
| `<description>` | No | Purpose and context |
| Any declaration | No | `<actors>`, `<params>`, `<variables>`, `<resources>`, `<commands>`, `<tool-tags>` |

---

## 4. Actors

Actors are anything that participates: humans, AI agents, subagents, CI runners, services.

```xml
<actors>
  <actor id="claude" type="agent">
    <description>Primary AI agent.</description>
    <responsibilities>
      <responsibility>Parse and execute this APE workflow</responsibility>
      <responsibility>Track step state and gate results</responsibility>
    </responsibilities>
  </actor>

  <actor id="developer" type="human">
    <description>Human developer.</description>
  </actor>

  <actor id="ci" type="service" />
  <actor id="code-reviewer" type="subagent" />
</actors>
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `id` | Yes | Unique identifier (global) |
| `type` | No | Predefined: `human`, `agent`, `subagent`, `service`. Custom allowed. |

| Child | Required | Description |
|-------|----------|-------------|
| `<description>` | No | What this actor is |
| `<responsibilities>` | No | Contains `<responsibility>` children |

Referenced via `actor` attribute on commands, actions, gates, steps, tool tags. Default actor is the first one declared.

## 5. Variables

APE supports **full arbitrary** `{{ ... }}` expressions anywhere text or attribute values appear.
The expression language is intentionally runtime-defined (your engine decides what functions/vars exist);
the validator should at least ensure referenced identifiers can resolve in-scope (unless explicitly external).

```xml
<var name="project_name" default="my-app" />
<var name="coverage_threshold" type="number" default="90" />
<var name="features" type="list">
  <item>feature-a</item>
  <item>feature-b</item>
</var>
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `name` | Yes | Unique within scope (may shadow parent scopes) |
| `type` | No | `string` (default), `number`, `boolean`, `list` |
| `default` | No | Default value. If unresolved at runtime, engine must ask or halt. |
| `value` | No | An alternate way to declare the variable's value. |

Can appear inside `<variables>` wrapper, inside `<meta>`, or inline in any block. Scoped to the block where declared.

Variables must have a value. Values are resolved in the following order:

1. Whatever value is found between the opening and closing tags
2. `value` attribute
3. `default` attribute
4. Runtime resolution

**List variables:** If `type="list"`, values are specified with `<item>` children.

---

## 5A. Parameters

A parameter declares a cross-document dependency: something this document needs from whoever invokes it.

```xml
<param ref="project-path" />
<param ref="review-file" />
<param ref="placeholder-id" default="{{DOC_QUALITY_FINDINGS}}" />
```

| Attribute  | Required | Description |
|------------|----------|-------------|
| `ref`      | Yes      | Identifier that must exist in the caller's scope. |
| `default`  | No       | Fallback value if the caller doesn't provide it. |
| `required` | No       | `true` (default) or `false`. |

Can appear inside `<params>` wrapper, inside `<meta>`, or inline in any block.

When resolved, the value is available locally under the same identifier — `<param ref="X" />` makes `{{ X }}` usable in this document.

If `required="true"` (the default) and the caller's scope has no matching identifier and no `default` is set, the runtime must halt with an error naming the unresolved param.

---

## 6. Resources

The "things you need" primitive.

```xml
<resource id="cargo" type="executable" />
<resource id="config" type="file" path="./Cargo.toml" access="read,write" />
<resource id="app-root" type="file-path" path="/path/to/app" access="read,write,edit,grep">
  <description>Application root directory</description>
</resource>
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `id` | Yes | Unique identifier (global) |
| `type` | Yes | `file`, `file-path`, `directory`, `executable`, `value`, `service`. Custom allowed. |
| `path` | No | Filesystem path (supports interpolation) |
| `access` | No | Comma-separated: `read`, `write`, `edit`, `grep`, `execute`, `delete` |
| `required` | No | `true` (default) or `false` |
| `actor` | No | Who owns/provides this resource |

Can appear inside `<resources>` wrapper, inside `<meta>`, or inline in any block.
Common reference surfaces:
- `uses="..."` on `<step>` (comma-separated resource IDs; each must resolve)
- interpolation inside attributes/text where your runtime exposes resource values

---

## 7. Commands

The "things you do" primitive. Three modes:

### Command Modes

| Mode | Required Attributes | Body | Meaning |
|------|-------------------|------|---------|
| **Declare** | `id` | Required (the command text) | Defines a reusable command. May also carry `actor`, `shell`, `tool`, `note`, `set`. |
| **Reference** | `ref` | Must be empty | Invokes a previously declared command. May also carry `set`, `actor`. |
| **Inline** | *(none of `id`/`ref`)* | Required (the command text) | Executes a literal command. May also carry `actor`, `shell`, `tool`, `set`. |

```xml
<!-- Declare -->
<command id="test-all" actor="developer" note="NO FILTERS">cargo test --all-features</command>

<!-- Declare + capture output -->
<command id="hello" set="hello">echo "Hello"</command>

<!-- Reference -->
<command ref="test-all" />

<!-- Reference + capture + actor override -->
<command ref="hello" set="greeting" actor="ci" />

<!-- Inline (supports interpolation) -->
<command actor="developer">echo "{{ hello }} World!"</command>

<!-- Inline + capture output -->
<command set="file_count">find . -name '*.rs' | wc -l</command>
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `id` | No | Unique identifier (global, for reuse) |
| `ref` | No | Reference to a declared command. Cannot combine with `id`. Body must be empty. |
| `set` | No | Capture stdout into a variable (see capture semantics below). Works on all modes. |
| `actor` | No | Who executes. On a reference, overrides the declared command's actor. |
| `tool` | No | Specific tool (e.g., `bash`, `file_create`) |
| `note` | No | Annotation |
| `shell` | No | `bash`, `sh`, `zsh`, `powershell`, `cmd` |

**Mutual exclusivity rules:**
- `id` and `ref` cannot both be present.
- If `ref` is present, the element body must be empty/whitespace ("empty" means `trim(textContent) == ""`).
- `set` is compatible with all modes (`id`, `ref`, or inline).

**Attribute override rule:** When a reference-site attribute duplicates a declaration-site attribute, the reference site wins. This applies to `actor`, `shell`, and `tool`. The declared command provides defaults; the call site provides overrides.

### Capture Semantics (`set`)

The `set` attribute captures command output into a named variable. The contract:

- **Captures stdout only.** Stderr is not captured (it may be displayed or logged by the runtime, but does not enter the variable).
- **Trailing newline is stripped.** A command that outputs `"hello\n"` stores `"hello"`. Interior newlines are preserved.
- **Multiline output is a string.** The value is stored as a single string with embedded newlines, not a list.
- **On non-zero exit:** The variable is still set to whatever stdout contained. Gate `<on-fail>` handlers should be used to handle error conditions. The `set` mechanism does not halt on failure — it captures and continues.

Can appear inside `<commands>` wrapper, inside `<meta>`, or inline in any block.

---

## 8. Tool Tags

Tool tags map directly to LLM tool interfaces. Core set below; custom tags via `<tool-tag>` declarations.

Tool tags can appear inside any block **except** `<meta>` (which is configuration, not execution).

### `<read>`

```xml
<read path="./src/main.rs" />
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `path` | Yes | File to read |
| `actor` | No | Who reads |

### `<write>`

Direct tool invocation — triggers the Write tool on a specific path. For structured output with format and destination control, use `<output>` instead.

```xml
<write path="./output/report.md">Content here</write>
<write ref="config" />
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `path` | No | File to write (required if no `ref`) |
| `ref` | No | Reference to a resource with write access (required if no `path`) |
| `actor` | No | Who writes |

The `path` or `ref` target should have write permission declared via a `<resource>` with `access` including `write`. The LLM determines how to apply the write (create, append, overwrite) based on context.

### `<ask-user-question>`

**Blocking** — execution pauses until response.

```xml
<ask-user-question var="project_name">What is your project name?</ask-user-question>
<ask-user-question var="proceed" type="confirm">Continue?</ask-user-question>
<ask-user-question var="target" type="choice">
  <option value="debug">Debug</option>
  <option value="release">Release</option>
</ask-user-question>
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `var` | No | Variable to store response |
| `type` | No | `text` (default), `confirm`, `choice` |
| `actor` | No | Who is asked (default: first `human` actor) |

### `<interview-mode>`

Sequential Q&A. One question at a time, wait for each answer.

```xml
<interview-mode actor="claude" target="developer">
  <ask-user-question var="name">Project name?</ask-user-question>
  <ask-user-question var="lang" type="choice">
    <option value="rust">Rust</option>
    <option value="python">Python</option>
  </ask-user-question>
</interview-mode>
```

### `<search>`

```xml
<search query="rust testing best practices" var="results" />
```

### `<stop>`

Unconditional halt. Can appear anywhere inside a block.

```xml
<stop>Critical failure.</stop>
<stop />
```

### `<subagent-stop>`

Return control from a subagent.

```xml
<subagent-stop actor="code-reviewer">Review complete.</subagent-stop>
```

### Custom Tool Tags

```xml
<tool-tags>
  <tool-tag name="deploy" maps-to="custom_deploy_tool">
    <description>Deploy to environment.</description>
    <attributes>
      <attribute name="env" required="true">Target environment</attribute>
    </attributes>
  </tool-tag>
</tool-tags>

<!-- Then use: -->
<deploy env="staging" />
```

---

## 9. Steps

```xml
<step number="1" id="specification" uses="cargo" actor="developer">
  <title>Specification</title>
  <goal>Define the contract your code must fulfill.</goal>

  <description>What this step is about.</description>

  <!-- Any of these in any order: -->
  <prerequisite ref="previous-step">...</prerequisite>  <!-- singular: one dependency -->
  <prerequisites>...</prerequisites>                    <!-- plural: two or more -->
  <variables>...</variables>
  <resources>...</resources>
  <commands>...</commands>
  <instruction>...</instruction>     <!-- singular: one instruction -->
  <instructions>...</instructions>   <!-- plural: two or more -->
  <gate>...</gate>
  <constraint>...</constraint>
  <rules>...</rules>
  <anti-patterns>...</anti-patterns>
</step>
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `id` | Yes | Unique identifier (global) |
| `number` | No | Display ordering (informational) |
| `uses` | No | Comma-separated resource IDs (each must resolve) |
| `actor` | No | Default actor for this step |

`<title>` and either `<instruction>` or `<instructions>` are required children (validator-enforced, not order-enforced). `<description>` is an optional child (at most one per step).

---

## 10. Prerequisites

Singular for one dependency (directly inside `<step>`), plural wrapper for two or more — same pattern as `<instruction>` / `<instructions>`:

```xml
<!-- One dependency — no wrapper needed -->
<prerequisite ref="specification">Failing tests from spec step</prerequisite>

<!-- Two or more — use the plural wrapper -->
<prerequisites>
  <prerequisite ref="build">Build passed</prerequisite>
  <prerequisite ref="lint">Lint clean</prerequisite>
</prerequisites>

<!-- With a programmatic check -->
<prerequisite>
  <check>
    <command actor="developer">git status --porcelain</command>
  </check>
</prerequisite>
```

---

## 11. Instruction / Instructions

`<instruction>` is the singular unit. `<instructions>` is the plural wrapper. A step contains exactly one of:

- **`<instruction>`** — a single instruction (standalone, no wrapper needed)
- **`<instructions>`** — two or more `<instruction>` elements

Both forms are ordered. Children execute/read top to bottom. Mixed content allowed — prose and tags intermix.

```xml
<!-- Single instruction: use <instruction> directly -->
<instruction>
  <action actor="developer">
    <command ref="fmt-check" />
  </action>
  <rationale>Consistent formatting.</rationale>
</instruction>

<!-- Multiple instructions: wrap in <instructions> -->
<instructions>
  <instruction>Review the diff.</instruction>
  <instruction>
    <action actor="developer">
      <command ref="fmt-check" />
    </action>
    <ask-user-question var="ok" type="confirm">Proceed?</ask-user-question>
  </instruction>
</instructions>
```

`<instruction>` can contain: `<note>`, `<action>`, `<command>`, `<when>`, `<match>`, `<each>`, `<sequence>`, any tool tag, `<constraint>`, `<rules>`, inline declarations.

`<instructions>` contains only `<instruction>` elements (two or more).

A step must not contain both `<instruction>` and `<instructions>`, and must not contain more than one of either.

### `<note>`

Author-facing context. Not executable, not output.

---

## 12. Gates

Single `<criteria>` (required), optional `<on-pass>`, one or more `<on-fail>` (at least one required). Flat and nested forms both valid.

```xml
<gate>
  <criteria>All tests pass with 0 filtered out</criteria>
  <on-fail goto="implementation">Test failures</on-fail>
  <on-fail retry="true" max="3" then="halt">Flaky test</on-fail>
</gate>
```

```xml
<gate actor="developer">
  <criteria>Build succeeds with no warnings</criteria>
  <on-pass goto="specification">Begin next cycle</on-pass>
  <on-fail>
    <reason>Coverage below {{ coverage_threshold }}%</reason>
    <action goto="implementation">
      <read path="./coverage-report.html" />
    </action>
  </on-fail>
</gate>
```

**Validator rules for gates:**
- Exactly one `<criteria>` (required).
- At least one `<on-fail>` (required).
- At most one `<on-pass>` (optional).

### Flow Control Attributes

On `<on-fail>`, `<on-pass>`, or `<action>`:

| Attribute | Type | Description |
|-----------|------|-------------|
| `goto` | Primary | Jump to step ID (must resolve) |
| `retry` | Primary | Retry current step (`true`) |
| `halt` | Primary | Stop workflow (`true`) |
| `proceed` | Primary | Continue to next step (`true`) |
| `max` | Modifier | Max retries (requires `retry="true"`, integer ≥ 1) |
| `then` | Modifier | After `max` retries: step ID or `halt` (requires `retry="true"`, defaults to `halt`) |

**Mutual exclusivity:** Exactly one *primary* attribute per element. `goto` + `retry` = error. `halt` + `proceed` = error. The modifiers `max` and `then` are only valid when `retry="true"` is present.

**Default behavior:**
- `<on-fail>` with **no** flow-control attributes: **halt with error**. The engine includes as much context as available (step id, step title, criteria text, reason text).
- `<on-pass>` with **no** flow-control attributes: **proceed** to next step.

### `<action>`

An executable directive. The LLM should perform what `<action>` says directly — no interpretive latitude, no treating it as a guide. It holds text, commands, tool tags, and outputs. Mixed content allowed.

`<action>` appears in two contexts:
- **Inside instructions** — a specific thing to do, as opposed to `<instruction>` prose which allows interpretation.
- **Inside gate handlers** (`<on-pass>`, `<on-fail>`) — the consequence of a gate result. Flow-control attributes (`goto`, `retry`, `halt`, `proceed`, `max`, `then`) are only valid in this context.

| Attribute | Required | Description |
|-----------|----------|-------------|
| `actor` | No | Who performs this action |
| `goto` | No | Jump to step ID (gate context only) |
| `retry` | No | Retry current step (gate context only) |
| `halt` | No | Stop workflow (gate context only) |
| `proceed` | No | Continue to next step (gate context only) |
| `max` | No | Max retries, requires `retry` (gate context only) |
| `then` | No | After max retries, requires `retry` (gate context only) |

`<action>` does **not** contain structural or decision-making elements like `<rules>`, `<gate>`, `<constraint>`, or `<when>`. If you need those, they belong in the `<instruction>` or `<step>` that contains the action.

---

## 13. Conditionals

### `<when>`

```xml
<when>
  <if test="{{ env }} == 'ci'">
    <command ref="coverage" />
  </if>
  <else>
    <note>Skip coverage locally.</note>
  </else>
</when>
```

**Structure:** Exactly one `<if>`. At most one `<else>`. `<if>` requires a non-empty `test` attribute. Mixed content allowed in both `<if>` and `<else>`.

### `<match>`

```xml
<match on="{{ build_target }}">
  <case value="debug"><command>cargo build</command></case>
  <case value="release"><command ref="build-release" /></case>
  <default><command ref="build-all" /></default>
</match>
```

**Structure:** At least one `<case>`. `<case>` `value` attributes must be unique. At most one `<default>`. Mixed content allowed in `<case>` and `<default>`.

---

## 14. Iteration

```xml
<each item="target" in="{{ targets }}">
  <action>
    <command>cargo build --{{ target }}</command>
  </action>
</each>
```

The `item` attribute creates a scoped variable binding available inside the `<each>` body. The `in` attribute should resolve to a list-typed variable.

---

## 15. Sequences

Strictly ordered command list. A sibling to `<instruction>` in purpose: where `<instruction>` allows mixed content and interpretive latitude, `<sequence>` is a precise, ordered list of commands. No prose, no flexibility.

```xml
<sequence id="ci-pipeline">
  <command ref="fmt-check" />
  <command ref="clippy" />
  <command ref="test-all" />
</sequence>
```

---

## 16. Decorators

Can appear inside any block.

### `<constraint>`

```xml
<constraint>ALWAYS run without filters.</constraint>
<constraint>NEVER use cargo test --lib.</constraint>
```

### `<rules>`

```xml
<rules>
  <rule>Imperative mood.</rule>
  <rule>No type prefixes.</rule>
</rules>
```

### `<anti-patterns>`

```xml
<anti-patterns>
  <anti-pattern>Documenting "what" instead of "why"</anti-pattern>
</anti-patterns>
```

### `<note>`

```xml
<note>Author-facing context. Not executed.</note>
```

---

## 17. Annotations

| Element | Purpose | Content |
|---------|---------|---------|
| `<rationale>` | Why something matters. Can appear inside any block. | Text |
| `<description>` | What something is (author-facing, never output). Only valid as a child of `<meta>` or `<step>` — at most one per parent. Also valid as a property of `<actor>`, `<resource>`, and `<tool-tag>` declarations. | Text |

---

## 18. Templates

Templates define the **shape of content**. They are reusable, format-aware content blocks with interpolation.

### Inline Templates

```xml
<template id="pr-body" format="md">
## Summary
{{ summary }}

## Test plan
- [x] Tests pass locally
{{ test_details }}

## Linear ticket
[{{ ticket_id }}](https://linear.app/sofware/issue/{{ ticket_id }})
</template>
```

### Referenced Templates

Templates can reference external files declared as resources or imports:

```xml
<resource id="pr-template" type="file" path="./templates/pr-body.md" access="read" />

<template id="pr-body" ref="pr-template" format="md" />
```

### Template Usage

Templates can be referenced from `<output>` elements, from within commands, or anywhere content needs a defined shape:

```xml
<!-- In an output declaration -->
<output template="pr-body" to="file" target="pr-description.md" />

<!-- As content for a command argument -->
<command>gh pr create --draft --body "<template ref="pr-body" />"</command>
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `id` | Yes | Unique identifier (global) |
| `format` | No | Content format hint: `md`, `yaml`, `json`, `xml`, `toml`, `text`, custom. Not validated — the author is responsible for correctness. The LLM uses best judgment. |
| `ref` | No | Reference to a resource or import containing the template content. If present, the element body should be empty. |

Templates support `{{ }}` interpolation throughout their body. Undefined variables follow standard resolution: check scope, fall back to parent, ask or halt.

Can appear inside `<meta>`, at root level, or inside any block. Scoped like other declarations.

---

## 19. Outputs

Outputs declare **what gets produced and where it goes**. Where `<template>` defines the shape of content, `<output>` defines its destination. Outputs are prescriptive — the author is saying "this is exactly what I expect, where I expect it, and how it should be applied."

### Basic Usage

```xml
<!-- Write filled template to a file -->
<output template="pr-body" to="file" target="pr-description.md" />

<!-- Write inline content to stdout -->
<output to="stdout">PR created: {{ pr_url }}</output>

<!-- Append to a specific location in an existing file -->
<output to="file" target="CHANGELOG.md" anchor="## Unreleased" position="append">
- {{ summary }} ({{ current_branch }})
</output>
```

### Referencing Other Outputs

An output can reference another output's destination, adding content to the same place:

```xml
<output id="changelog-entry" to="file" target="CHANGELOG.md" anchor="## Unreleased" position="append">
- {{ summary }}
</output>

<!-- Later: append to the same location -->
<output ref="changelog-entry">
- See also: {{ related_pr_url }}
</output>
```

### Targeting Within a Destination

The `to` attribute declares the high-level destination kind. The `target` attribute declares the specific destination. The `anchor` and `position` attributes provide fine-grained placement within the target:

| Attribute | Required | Description |
|-----------|----------|-------------|
| `id` | No | Unique identifier (global) for reuse via `ref` |
| `ref` | No | Reference to another output's destination. Cannot combine with `to`/`target`. |
| `template` | No | Template ID to fill and use as content. If present, element body is ignored. |
| `to` | No | Destination kind: `file`, `stdout`, `log`, `resource`, `template`. Required if no `ref`. |
| `target` | No | Specific destination: file path, resource `ref`, template `id`, log name. Supports interpolation. |
| `format` | No | Output format hint (same as template `format`). Inherited from template if not specified. |
| `anchor` | No | Location within the target: a heading, line pattern, section name, JSON path — whatever makes sense for the format. |
| `position` | No | How to place content relative to the anchor: `append` (default), `prepend`, `replace`. |
| `actor` | No | Who produces this output |

**Relationship to `<write>`:** `<write>` is a direct tool invocation — "use the Write tool now." `<output>` is a declaration of intent — "this is what should be produced." Use `<output>` when you want to specify exactly what the result looks like and where it goes. Use `<write>` when you just need to trigger the tool.

**Relationship to `<template>`:** Templates define shape. Outputs define destination. An output *with* a template reference is "fill this shape, put it there." An output *without* a template is a direct content write. A template *without* an output is a reusable content block that can be referenced from commands or other contexts.

Can appear inside `<instruction>`, `<action>`, or any instruction-level block. Also valid at step level.

---

## 20. Principles

```xml
<principles>
  <principle name="Atomic">Each commit is complete and independent.</principle>
</principles>
```

---

## 21. References

Standalone blocks outside `<steps>`.

```xml
<reference id="verification-commands">
  <note>CI command order.</note>
  <sequence>
    <command ref="fmt-check" />
    <command ref="test-all" />
  </sequence>
</reference>
```

---

## 22. Validation Architecture

APE uses a layered validation model:

### XSD (Shape)
- Tag names and attribute names exist
- Attribute types (enums, booleans, identifiers)
- Parent-child relationships (what can appear where)
- Basic requiredness
- `<param>` shape: `ref` attribute required, `default` optional, `required` optional

### Validator (Semantics)

**General:**
- "Empty" means `trim(textContent) == ""`. Whitespace from indentation does not count as content.

**Document-level:**
- `<meta>`, if present, is the first child of `<ape>`
- If `<steps>` is present, it must contain \u2265 1 `<step>`
- `<meta>` contains `<n>` (required)

**Required children:**
- `<step>` must contain `<title>` and exactly one `<instruction>` or `<instructions>`
- `<gate>` must contain exactly one `<criteria>` and at least one `<on-fail>`
- `<when>` must contain exactly one `<if>` and at most one `<else>`
- `<match>` must contain at least one `<case>` and at most one `<default>`
- `<if>` must have a non-empty `test` attribute

**Identity and uniqueness:**
- Global IDs (`actor`, `resource`, `command`, `sequence`, `reference`, `tool-tag`, `step`, `template`, `output`) are unique across the entire document. No shadowing.
- Scoped names (`var/@name`, `param/@ref`) are unique within their scope. Shadowing is permitted.
- `<case>` `value` attributes are unique within their `<match>`

**Reference resolution:**
- All `ref` attributes resolve to a declared `id` of the correct type
- All `actor` attributes resolve to a declared `<actor>` `id`
- All `goto` attributes resolve to a `<step>` `id`
- Each token in `uses` resolves to a `<resource>` `id`
- `<output template="X">` resolves to a `<template>` `id`
- `<output ref="X">` resolves to another `<output>` `id`
- `<template ref="X">` resolves to a `<resource>` `id`
- `<write ref="X">` resolves to a `<resource>` with write access
- Namespaced references (e.g., `lint.fmt-check`) use a declared import alias
- `{{ ... }}` identifiers resolve to an in-scope variable (or are explicitly runtime-provided)
- `<param ref="X">` resolves to an identifier in the caller's scope (cross-document resolution)

**Command rules:**
- `id` and `ref` are mutually exclusive
- If `ref` is present, the body must be empty (`trim(textContent) == ""`)
- `set` is valid on any command mode
- Reference-site attributes (`actor`, `shell`, `tool`) override declaration-site values

**Flow control rules:**
- At most one primary flow-control attribute (`goto`, `retry`, `halt`, `proceed`) per `<on-fail>`, `<on-pass>`, or `<action>`
- `max` requires `retry="true"`
- `then` requires `retry="true"` (defaults to `halt` if omitted)
- `goto` values must resolve to a `<step>` `id`

**Output rules:**
- `ref` and `to` are mutually exclusive on `<output>`
- If `template` is present, it must resolve to a `<template>` `id`
- If `to` is `file` or `resource`, `target` should be present

**Description rules:**
- `<description>` may only appear as a direct child of `<meta>`, `<step>`, `<actor>`, `<resource>`, or `<tool-tag>`
- At most one `<description>` per `<meta>` or `<step>`

**Template rules:**
- `id` is required
- If `ref` is present, the body should be empty (`trim(textContent) == ""`)

### Tooling (Taste) — Future

| Tool | Purpose |
|------|---------|
| `ape validate` | Check semantics |
| `ape fmt` | Reorder to canonical form |
| `ape fix` | Auto-fix issues + show diff |

`--strict` mode optionally enforces declare-before-use as a lint rule.

---

## 24. Core Constraints

1. **`<meta>` is always first** if present. Contains declarations and metadata only — no tool tags, no instructions.
2. **`<steps>` is optional.** If present, it must contain at least one `<step>`. Documents without `<steps>` are executed top to bottom — declarations, commands, and tool tags at root level are processed in document order.
3. **Declarations scope to their block.** Available inside and after, not upward or backward. `<param>` declarations resolve from the caller's scope and are then locally scoped.
4. **Two-pass resolution.** Forward references work. Nearest scope wins.
5. **Global IDs are globally unique.** `actor`, `resource`, `command`, `sequence`, `reference`, `tool-tag`, `step`, `template`, and `output` IDs cannot shadow or collide. Only `var/@name` and `param/@ref` support scoped shadowing.
6. **Mixed content is allowed in instruction-level elements.** `<instruction>`, `<action>`, `<if>`, `<else>`, `<case>`, `<default>`, `<each>`, `<on-fail>`, `<on-pass>`, and `<prerequisite>` allow interleaved text and child elements. Tags are anchors; text is narrative. `<instructions>` (plural) is a structural wrapper containing `<instruction>` elements and does not itself allow mixed content.
7. **`<command>` identity is exclusive.** `id` and `ref` cannot both be present. If `ref` is present, the element body must be empty/whitespace. `set` works with any mode.
8. **Flow control: one primary attribute** per `<on-fail>`/`<on-pass>`/`<action>`. Primary attributes are `goto`, `retry`, `halt`, `proceed`. Modifiers `max` and `then` require `retry`.
9. **`<on-fail>` default is halt with error.** If no flow-control attributes are present, the engine halts and includes available context (step id, title, criteria, reason).
10. **Interleaving is allowed.** Children within blocks need not follow a fixed order.
11. **Tool tags live inside blocks or at root level.** Not inside `<meta>`. In stepless documents, tool tags at root level are executed in document order.
12. **`<description>` is author-facing and placement-restricted.** Never output or executed. As a standalone element, only valid inside `<meta>` (at most one) and `<step>` (at most one). Also valid as a property of `<actor>`, `<resource>`, and `<tool-tag>` declarations. Not a decorator — cannot appear in arbitrary blocks.
13. **Sequences are strictly ordered.**
14. **`<action>` is an executable directive.** It holds things that *happen* — text, commands, tool tags, outputs. Not structural or decision-making elements. The LLM should execute it directly, not treat it as interpretive guidance. Flow-control attributes are only valid inside gate handlers.
15. **Templates define shape.** Content with format awareness and interpolation. They don't specify where content goes.
16. **Outputs define destination.** What gets produced, where it lands, and how it's placed. They are prescriptive — leave little room for interpretation.

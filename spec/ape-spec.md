# APE Language Specification

**Version:** 0.2.2
**Status:** Draft
**Schema:** See `ape.xsd`
**LLM Execution Contract:** See `ape-llms.md`
**Authoring Guide:** See `ape-authoring.md`
**Conversion Guide:** See `ape-conversions.md`

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

Declarations define reusable identifiers: `<var>`, `<resource>`, `<command>`, `<actor>`, `<param>`.

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
- `step/@id`
- `template/@id`
- `output/@id`

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

**Exception:** `<prerequisite>` and `<prerequisites>` must appear before all other children in a `<step>`. Prerequisites are entry conditions — they must be evaluated before any work begins.

### 2.6 Decorators

Decorators are elements that annotate but don't alter execution: `<goal>`, `<constraint>`, `<rules>`, `<anti-patterns>`, `<note>`, `<rationale>`. They can appear inside any block.

### 2.7 Mixed Content

**Structural elements** — `<ape>`, `<meta>`, `<gate>`, `<steps>`, `<actors>`, `<variables>`, `<resources>`, `<commands>`, `<instructions>`, `<params>`, `<sequence>`, `<prerequisites>` — do **not** allow mixed content. Children only, no interleaved text.

**Instruction-level elements** — `<instruction>`, `<action>`, `<case>`, `<default>`, `<each>`, `<on-fail>`, `<on-pass>`, and `<prerequisite>` — **do** allow mixed content. Plain text serves as narrative guidance; child tags serve as concrete anchors to execute.

This is the one structural distinction to internalize: *above* the instruction layer, structure is strict. *At* the instruction layer, prose and tags intermix freely.

---

## 3. Document Structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ape version="0.2.2" xmlns="https://ape-lang.dev/schema/0">

  <meta>
    <name>My Workflow</name>
    <description>What this workflow does and why.</description>

    <actors>...</actors>
    <params>...</params>
    <variables>...</variables>
    <resources>...</resources>
    <commands>...</commands>
  </meta>

  <steps>
    ...
  </steps>

  <principles>...</principles>

</ape>
```

### 3.1 `<ape>` (Root)

| Attribute | Required | Description |
|-----------|----------|-------------|
| `version` | Yes | Spec version (e.g., `0.2.2`) |
| `xmlns` | Yes | Namespace URI, pinned to major version |

**Versioning:** The namespace is pinned to the major version (`https://ape-lang.dev/schema/0`). The `version` attribute carries the full version (`0.2.2`, `0.2.3`, etc.). Documents with different minor versions share the same namespace and are expected to be broadly compatible within a major version. Breaking changes increment the major version and the namespace.

`<meta>` must be first if present. Everything else — `<steps>`, declarations, tool tags, `<principles>`, `<reference>` — is optional and can appear in any order. Multiple `<steps>` blocks are allowed; each must contain at least one `<step>`. In stepless documents, tool tags and commands at root level are executed in document order.

### 3.2 `<meta>`

If present, `<meta>` is the first child of `<ape>`. It contains document metadata and declarations. It is configuration, not execution.

| Child | Required | Description |
|-------|----------|-------------|
| `<name>` | No | APE document name |
| `<description>` | No | Purpose and context |
| Any declaration | No | `<actors>`, `<params>`, `<variables>`, `<resources>`, `<commands>` |

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

Can appear inside `<actors>` wrapper, inside `<meta>`, or inline in any block.

Referenced via `actor` attribute on commands, actions, gates, steps, tool tags. Default actor is the first one declared.

## 5. Variables

APE supports **full arbitrary** `{{ ... }}` expressions anywhere text or attribute values appear.
The expression language is intentionally runtime-defined (your engine decides what functions/vars exist);
the validator should at least ensure referenced identifiers can resolve in-scope (unless explicitly external).

```xml
<var name="project_name" default="my-app" />
<var name="coverage_threshold" type="number" default="90" />
<var name="features" type="list" value="feature-a, feature-b" />
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `name` | Yes | Unique within scope (may shadow parent scopes) |
| `type` | No | `string` (default), `number`, `boolean`, `list` |
| `default` | No | Default value. If unresolved at runtime, engine must ask or halt. |
| `value` | No | An alternate way to declare the variable's value. |

Can appear inside `<variables>` wrapper, inside `<meta>`, or inline in any block. Scoped to the block where declared.

A `<var>` declaration must carry a value. Values are resolved in the following order:

1. Whatever value is found between the opening and closing tags
2. `value` attribute
3. `default` attribute

Variables created implicitly by `<ask-user-question var="...">` or `<command set="...">` do not need a `<var>` declaration. Only declare a `<var>` when you have a value to give it.

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
<resource id="app-root" type="file-path" path="/path/to/app" access="read,write,edit,grep" />
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

## 8. Tool Tags and Behavioral Tags

Tags in this section can appear inside any block **except** `<meta>` (which is configuration, not execution). They can also appear at root level in stepless documents.

There are two categories:

- **Tool tags** map to specific LLM tools. Each tag invokes a tool directly.
- **Behavioral tags** invoke execution patterns — they change *how* the LLM operates rather than calling a specific tool.

### 8.1 Tool Tags

Tool tags are an exhaustive mapping to the LLM's tool interfaces. Each tag corresponds to one tool.

#### `<read>`

```xml
<read path="./src/main.rs" />
<read path="./config.yaml" var="config_content" />
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `path` | Yes | File to read |
| `var` | No | Variable to store file content |
| `actor` | No | Who reads |

#### `<write>`

Direct tool invocation — triggers the Write tool on a specific path. For structured output with format and destination control, use `<output>` instead.

```xml
<write path="./output/report.md">Content here</write>
<write path="./log.txt" mode="append">New log entry</write>
<write ref="config" />
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `path` | No | File to write (required if no `ref`) |
| `ref` | No | Reference to a resource with write access (required if no `path`) |
| `mode` | No | `create` (default), `append`, `overwrite` |
| `actor` | No | Who writes |

The `path` or `ref` target should have write permission declared via a `<resource>` with `access` including `write`.

#### `<edit>`

In-place file modification.

```xml
<edit path="./src/config.rs">Replace the hardcoded URL with {{ api_url }}</edit>
<edit path="./README.md" />
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `path` | Yes | File to edit |
| `actor` | No | Who edits |

The body describes the edit to perform. The LLM uses the Edit tool to apply it based on context.

#### `<glob>`

File pattern search.

```xml
<glob pattern="src/**/*.ts" var="ts_files" />
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `pattern` | Yes | Glob pattern to match |
| `var` | No | Variable to store matched file paths |
| `path` | No | Directory to search in (default: working directory) |
| `actor` | No | Who searches |

#### `<grep>`

Content search across files.

```xml
<grep pattern="TODO|FIXME" var="todos" />
<grep pattern="import.*from" path="./src" var="imports" />
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `pattern` | Yes | Search pattern (regex) |
| `var` | No | Variable to store results |
| `path` | No | File or directory to search (default: working directory) |
| `actor` | No | Who searches |

#### `<web-search>`

```xml
<web-search query="rust testing best practices" var="results" />
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `query` | Yes | Search query |
| `var` | No | Variable to store results |
| `actor` | No | Who searches |

#### `<web-fetch>`

```xml
<web-fetch url="https://example.com/api/status" var="response" />
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `url` | Yes | URL to fetch |
| `var` | No | Variable to store response content |
| `actor` | No | Who fetches |

#### `<ask-user-question>`

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

### 8.2 Behavioral Tags

Behavioral tags change how the LLM executes rather than invoking a specific tool.

#### `<interview-mode>`

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

#### `<plan-mode>`

The LLM enters planning mode — exploring, designing, and presenting an approach for approval before executing.

```xml
<plan-mode>
  Design the database migration strategy before making changes.
</plan-mode>
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `actor` | No | Who plans |

#### `<agents-in-parallel>`

Run multiple actions concurrently using subagents.

```xml
<agents-in-parallel>
  <action actor="code-reviewer">Review the code changes</action>
  <action actor="test-writer">Write unit tests for the new module</action>
</agents-in-parallel>
```

Each `<action>` child is dispatched to its actor concurrently. Execution resumes after all complete.

#### `<stop>`

Unconditional halt. Can appear anywhere inside a block.

```xml
<stop>Critical failure.</stop>
<stop />
```

#### `<subagent-stop>`

Return control from a subagent.

```xml
<subagent-stop actor="code-reviewer">Review complete.</subagent-stop>
```

---

## 9. Steps

```xml
<step number="1" id="specification" uses="cargo" actor="developer">
  <prerequisite ref="previous-step">What must be true and what to do if it is not.</prerequisite>

  <title>Specification</title>
  <goal>Define the contract your code must fulfill.</goal>

  <instruction>...</instruction>
  <instructions>...</instructions>

  <gate>...</gate>
</step>
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `id` | Yes | Unique identifier (global) |
| `number` | No | Display ordering (informational) |
| `uses` | No | Comma-separated resource IDs (each must resolve) |
| `actor` | No | Default actor for this step |

`<title>` is optional. When the step `id` is descriptive (e.g., `id="verify"`, `id="red-phase"`), a title that restates it adds noise — omit it. Use `<title>` only when the step needs a human-readable name that the `id` cannot convey.

`<prerequisite>` or `<prerequisites>`, if present, must appear before all other children. Prerequisites are entry conditions and must be evaluated first.

`<description>`, `<instruction>`, and `<instructions>` are all optional children (at most one of each per step). A step must not contain both `<instruction>` and `<instructions>`. `<goal>` is a decorator and can appear inside any block.

---

## 10. Prerequisites

Singular for one dependency (directly inside `<step>`), plural wrapper for two or more — same pattern as `<instruction>` / `<instructions>`.

**Ordering:** Prerequisites must appear before all other children in a `<step>`. They are entry conditions — the LLM evaluates them first, before reading the step's instructions or goal.

**Content:** A prerequisite should describe what must be true *and* what happens if the condition is not met. The text content is not decorative — it tells the LLM how to handle an unmet precondition.

```xml
<!-- One dependency — no wrapper needed -->
<prerequisite ref="specification">Tests from the specification step must exist. If not, return to specification.</prerequisite>

<!-- Two or more — use the plural wrapper -->
<prerequisites>
  <prerequisite ref="build">Build must have passed. If not, return to build.</prerequisite>
  <prerequisite ref="lint">Lint must be clean. If not, return to lint.</prerequisite>
</prerequisites>

<!-- With a programmatic check -->
<prerequisite>
  Working tree must be clean before committing.
  <check>
    <command actor="developer">git status --porcelain</command>
  </check>
</prerequisite>
```

---

## 11. Instruction / Instructions

`<instruction>` is the atomic unit of work inside a step. One or more `<instruction>` elements may appear directly inside a step. The `<instructions>` wrapper is an optional grouping element that contains two or more `<instruction>` children.

Instructions are ordered. Children execute/read top to bottom. Mixed content allowed — prose and tags intermix.

```xml
<!-- Single instruction -->
<instruction>
  <action actor="developer">
    <command ref="fmt-check" />
  </action>
  <rationale>Consistent formatting.</rationale>
</instruction>

<!-- Multiple instructions (bare) -->
<instruction>Review the diff.</instruction>
<instruction>
  <action actor="developer">
    <command ref="fmt-check" />
  </action>
  <ask-user-question var="ok" type="confirm">Proceed?</ask-user-question>
</instruction>

<!-- Multiple instructions (wrapped) -->
<instructions>
  <instruction>Review the diff.</instruction>
  <instruction>
    <action actor="developer">
      <command ref="fmt-check" />
    </action>
  </instruction>
</instructions>
```

`<instruction>` can contain: `<note>`, `<action>`, `<command>`, `<conditional>`, `<each>`, `<sequence>`, any tool tag, `<constraint>`, `<rules>`, inline declarations.

`<instructions>` contains only `<instruction>` elements (two or more).

### `<note>`

Author-facing context. Not executable, not output.

---

## 12. Gates

Single `<criteria>` (required), optional `<on-pass>`, exactly one `<on-fail>` (required). Flat and nested forms both valid.

```xml
<gate>
  <criteria>All tests pass with 0 filtered out</criteria>
  <on-fail retry="true" max="3" then="halt">Tests failing</on-fail>
</gate>
```

```xml
<gate actor="developer">
  <criteria>Build succeeds with no warnings</criteria>
  <on-pass goto="specification">Begin next cycle</on-pass>
  <on-fail goto="implementation">
    <reason>Build failed</reason>
    <action>
      <read path="./build-log.txt" />
    </action>
  </on-fail>
</gate>
```

**Validator rules for gates:**
- Exactly one `<criteria>` (required).
- Exactly one `<on-fail>` (required).
- At most one `<on-pass>` (optional).

### Flow Control Attributes

**On `<on-fail>`:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `goto` | Primary | Jump to step ID (must resolve) |
| `retry` | Primary | Retry current step (`true`) |
| `halt` | Primary | Stop workflow (`true`) |
| `proceed` | Primary | Continue to next step (`true`) |
| `max` | Modifier | Max retries (requires `retry="true"`, integer ≥ 1) |
| `then` | Modifier | After `max` retries: step ID or `halt` (requires `retry="true"`, defaults to `halt`) |

**On `<on-pass>`:**

| Attribute | Type | Description |
|-----------|------|-------------|
| `goto` | Primary | Jump to step ID (must resolve) |
| `proceed` | Primary | Continue to next step (`true`) |

**Mutual exclusivity:** Exactly one *primary* attribute per element. `goto` + `retry` = error. `halt` + `proceed` = error. The modifiers `max` and `then` are only valid when `retry="true"` is present.

**`<on-fail>` requires explicit flow control.** The `<on-fail>` must carry exactly one primary attribute. There is no default behavior — the author must be explicit about what happens on failure.

**`<on-pass>` default:** If no flow-control attributes are present, execution proceeds to the next step.

### `<action>`

An executable directive. The LLM should perform what `<action>` says directly — no interpretive latitude, no treating it as a guide. It holds text, commands, tool tags, and outputs. Mixed content allowed.

`<action>` appears in two contexts:
- **Inside instructions** — a specific thing to do, as opposed to `<instruction>` prose which allows interpretation.
- **Inside gate handlers** (`<on-pass>`, `<on-fail>`) — content to execute as part of the handler. Flow control belongs on the handler element itself, not on `<action>`.

| Attribute | Required | Description |
|-----------|----------|-------------|
| `actor` | No | Who performs this action |

`<action>` does **not** contain structural or decision-making elements like `<rules>`, `<gate>`, `<constraint>`, or `<conditional>`. If you need those, they belong in the `<instruction>` or `<step>` that contains the action.

---

## 13. Conditionals

### `<conditional>`

A unified branching construct. Evaluates the `on` expression and routes to the matching `<case>`.

```xml
<!-- Multiple branches -->
<conditional on="{{ request_type }}">
  <case value="implementation">
    <action>Edit existing files, preferring edits over new files.</action>
  </case>
  <case value="information">
    <action>Research and make recommendations.</action>
  </case>
  <default>
    <action goto="clarify-intent">Request type unclear.</action>
  </default>
</conditional>

<!-- Binary with attribute default -->
<conditional on="{{ intent_clear }}" default="halt">
  <case value="true">
    <action>Proceed with identified deliverable.</action>
  </case>
</conditional>

<!-- Simple routing -->
<conditional on="{{ env }}" default="proceed">
  <case value="ci"><command ref="coverage" /></case>
</conditional>
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `on` | Yes | Expression to evaluate (supports `{{ }}` interpolation) |
| `default` | No | Shorthand for simple default outcomes: a step ID (acts as `goto`), `"halt"`, or `"proceed"`. Mutually exclusive with a `<default>` child element. |

**Children:**

| Child | Required | Description |
|-------|----------|-------------|
| `<case>` | Yes (≥1) | A branch. `value` attribute (required) matches against the `on` expression. |
| `<default>` | No (≤1) | Fallback when no `<case>` matches. Mutually exclusive with `default` attribute. |

**`<case>` and `<default>` attributes:**

| Attribute | Description |
|-----------|-------------|
| `value` | Required on `<case>`. The value to match against. Must be unique within the `<conditional>`. |
| `goto` | Jump to a step ID |
| `halt` | Stop workflow (`true`) |

`goto` and `halt` are mutually exclusive on each element. `<case>` and `<default>` can also contain child elements (instructions, actions, commands, tool tags). A `<case>` with both content and `goto` executes the content, then routes.

**Conditionals vs. gates:** Conditionals are **navigational** — they route based on a value that already exists. Gates are **evaluative** — they judge whether completed work meets a bar. Conditionals do not support `retry`, `max`, or `then` because the value won't change by retrying. Gates do, because the work can be redone.

**After a `<case>` or `<default>` body:** If the body executes with no `goto` or `halt`, execution continues to whatever follows the `<conditional>`.

**Structure:** At least one `<case>`, followed by at most one `<default>`. `<case>` `value` attributes must be unique within their `<conditional>`. `<default>` must appear after all `<case>` elements. Mixed content allowed in `<case>` and `<default>`.

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

### `<goal>`

```xml
<goal>Define the contract your code must fulfill.</goal>
```

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
| `<description>` | What something is (author-facing, never output). Only valid as a child of `<meta>`, `<step>`, or `<actor>` — at most one per parent. | Text |

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

A reference points to an external thing — a file, URL, document, or other resource that provides context but is not declared as a `<resource>` (which implies consumption by the workflow). References are informational: "here is something relevant."

```xml
<reference id="style-guide" path="https://example.com/style-guide">Project style guide.</reference>
<reference id="rfc" path="./docs/rfc-042.md">Design RFC for this feature.</reference>
<reference id="api-docs" path="https://docs.example.com/api/v2" />
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `id` | Yes | Unique identifier (global) |
| `path` | Yes | Path or URL to the referenced thing |

The element body is optional descriptive text. References do not contain sequences, commands, or other structural elements — they point to things outside the document.

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

**Required children:**
- `<gate>` must contain exactly one `<criteria>` and exactly one `<on-fail>`
- `<conditional>` must have a non-empty `on` attribute
- `<conditional>` must contain at least one `<case>`
- `<conditional>` must contain at most one `<default>` child element, and it must appear after all `<case>` elements
- `<conditional>` `default` attribute and `<default>` child element are mutually exclusive

**Identity and uniqueness:**
- Global IDs (`actor`, `resource`, `command`, `sequence`, `reference`, `step`, `template`, `output`) are unique across the entire document. No shadowing.
- Scoped names (`var/@name`, `param/@ref`) are unique within their scope. Shadowing is permitted.
- `<case>` `value` attributes are unique within their `<conditional>`

**Reference resolution:**
- All `ref` attributes resolve to a declared `id` of the correct type
- All `actor` attributes resolve to a declared `<actor>` `id`
- All `goto` attributes resolve to a `<step>` `id`
- Each token in `uses` resolves to a `<resource>` `id`
- `<output template="X">` resolves to a `<template>` `id`
- `<output ref="X">` resolves to another `<output>` `id`
- `<template ref="X">` resolves to a `<resource>` `id`
- `<write ref="X">` resolves to a `<resource>` with write access
- `{{ ... }}` identifiers resolve to an in-scope variable (or are explicitly runtime-provided)
- `<param ref="X">` resolves to an identifier in the caller's scope (cross-document resolution)

**Command rules:**
- `id` and `ref` are mutually exclusive
- If `ref` is present, the body must be empty (`trim(textContent) == ""`)
- `set` is valid on any command mode
- Reference-site attributes (`actor`, `shell`, `tool`) override declaration-site values

**Flow control rules:**
- `<on-fail>` must have exactly one primary flow-control attribute (`goto`, `retry`, `halt`, `proceed`)
- `<on-pass>` supports only `goto` and `proceed`
- `max` requires `retry="true"`
- `then` requires `retry="true"` (defaults to `halt` if omitted)
- `goto` values must resolve to a `<step>` `id`

**Output rules:**
- `ref` and `to` are mutually exclusive on `<output>`
- If `template` is present, it must resolve to a `<template>` `id`
- If `to` is `file` or `resource`, `target` should be present

**Variable rules:**
- A `<var>` declaration must carry a value via element content, `value` attribute, or `default` attribute. Empty declarations are not valid.
- Variables created implicitly (by `<ask-user-question var>` or `<command set>`) do not require a `<var>` declaration.

**Prerequisite rules:**
- `<prerequisite>` and `<prerequisites>` must appear before all other children in a `<step>`
- Prerequisite text content must describe the condition and the consequence of it not being met

**Constraint ordering:**
- When a `<constraint>` appears inside an `<instruction>`, it must appear before prose and executable content (`<action>`, `<output>`, `<command>`, tool tags)

**Comment rules:**
- XML comments (`<!-- -->`) are not permitted in APE documents. Use `<note>` for author-facing context.

**Description rules:**
- `<description>` may only appear as a direct child of `<meta>`, `<step>`, or `<actor>`
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

## 23. Core Constraints

1. **`<meta>` is always first** if present. Contains declarations and metadata only — no tool tags, no instructions.
2. **`<steps>` is optional.** Multiple `<steps>` blocks are allowed; each must contain at least one `<step>`. Documents without `<steps>` are executed top to bottom — declarations, commands, and tool tags at root level are processed in document order.
3. **Declarations scope to their block.** Available inside and after, not upward or backward. `<param>` declarations resolve from the caller's scope and are then locally scoped.
4. **Two-pass resolution.** Forward references work. Nearest scope wins.
5. **Global IDs are globally unique.** `actor`, `resource`, `command`, `sequence`, `reference`, `step`, `template`, and `output` IDs cannot shadow or collide. Only `var/@name` and `param/@ref` support scoped shadowing.
6. **Mixed content is allowed in instruction-level elements.** `<instruction>`, `<action>`, `<case>`, `<default>`, `<each>`, `<on-fail>`, `<on-pass>`, and `<prerequisite>` allow interleaved text and child elements. Tags are anchors; text is narrative. `<instructions>` (plural) is a structural wrapper containing only `<instruction>` children and does not allow mixed content.
7. **`<command>` identity is exclusive.** `id` and `ref` cannot both be present. If `ref` is present, the element body must be empty/whitespace. `set` works with any mode.
8. **Flow control: one primary attribute.** `<on-fail>` must have exactly one primary attribute (`goto`, `retry`, `halt`, `proceed`). `<on-pass>` supports only `goto` and `proceed`. Flow-control attributes belong on the handler elements, not on `<action>`. Modifiers `max` and `then` require `retry`.
9. **`<on-fail>` is singular and requires explicit flow control.** Exactly one `<on-fail>` per gate. It must carry a primary attribute. There is no default behavior — the author must be explicit about what happens on failure.
10. **Interleaving is allowed.** Children within blocks need not follow a fixed order. Exceptions: `<default>` must appear after all `<case>` elements in a `<conditional>`; `<prerequisite>` and `<prerequisites>` must appear before all other children in a `<step>`; `<constraint>` must appear before prose and executable content inside an `<instruction>`.
11. **Tool tags live inside blocks or at root level.** Not inside `<meta>`. In stepless documents, tool tags at root level are executed in document order.
12. **`<description>` is author-facing and placement-restricted.** Never output or executed. Only valid inside `<meta>` (at most one), `<step>` (at most one), and `<actor>`. Not a decorator — cannot appear in arbitrary blocks.
13. **Sequences are strictly ordered.**
14. **`<action>` is an executable directive.** It holds things that *happen* — text, commands, tool tags, outputs. Not structural or decision-making elements. The LLM should execute it directly, not treat it as interpretive guidance. `<action>` does not carry flow-control attributes.
15. **Templates define shape.** Content with format awareness and interpolation. They don't specify where content goes.
16. **Outputs define destination.** What gets produced, where it lands, and how it's placed. They are prescriptive — leave little room for interpretation.
17. **No XML comments.** APE documents must not contain XML comments (`<!-- -->`). Use `<note>` for author-facing context. Comments are invisible to validators, unsearchable by tooling, and add noise without structural value.
18. **Variables must have values.** A `<var>` declaration must carry a value via element content, `value` attribute, or `default` attribute. Do not declare empty variables as placeholders for runtime state — use `<ask-user-question var>` or `<command set>` to create variables implicitly when their values become available.
19. **Prerequisites are first.** In a `<step>`, `<prerequisite>` and `<prerequisites>` must appear before all other children. Prerequisites are entry conditions evaluated before work begins.
20. **Constraints before work.** When a `<constraint>` appears inside an `<instruction>`, it must appear before prose, `<action>`, `<output>`, `<command>`, and other executable content. The LLM must read restrictions before executing work.

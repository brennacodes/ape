# APE Language Specification

**Version:** 0.4.0
**Status:** Release
**Schema:** See `ape.xsd`
**LLM Execution Contract:** See `ape-llms.md`
**Authoring Guide:** See `ape-authoring.md`
**Conversion Guide:** See `ape-conversions.md`
**Linting Rules:** See `ape-linting.md`

---

## 1. Overview

APE is an XML-based markup for defining structured, enforceable workflows. Written by humans, read by machines, executed by LLM agents—without requiring a system prompt.

APE documents are self-contained. The file defines what tools to use, when to stop and wait, and what to do on success or failure.

### Design Principles

* **So easy a caveman can write it.** Tags say what they mean.
* **Execution, Inputs, Flow Control.** The spec defines primitives in three core categories: **execution** (what to *do*), **state and input dependencies** (what to *know/need*), and structured **flow control** (how to *navigate*). This model eliminates interpretive ambiguity by replacing prose suggestions with enforceable structural contracts.
* **Prose and structure are separated.** Prose containers hold only text; structure containers hold only structure. This eliminates the illocutionary gap—intent is conveyed through structure, not conversation.
* **The document is the engine manual.** No system prompt crutch.
* **Flat where possible, nested where necessary.** Both always valid.
* **Structure over prose, once.** If behavior can be enforced by structure (gates, prerequisites, command definitions, conditionals), it must not also be restated as prose (rules, principles, anti-patterns). If guidance must be stated as prose, it is stated once, in the element that best fits its nature. Redundancy is a bug, not emphasis.
* **Permissive structure, strict validation.** The schema validates shape. A validator enforces semantics. Tooling enforces taste.
* **Declarations live where the author puts them.** At the top for global access, inside a block for scoped access. Both are valid.
* **Self-closing tags.** Any element with no children and no text may be self-closed.

---

## 2. Core Concepts

This section provides an architectural overview of how APE's primitive categories relate and interoperate. For detailed tag definitions, see subsequent sections.

### 2.1 Blocks

A **block** is any element with an opening and closing tag. `<step>...</step>`, `<gate>...</gate>`, `<instruction>...</instruction>`—these are all blocks.

Blocks can contain: declarations (variables, resources, commands), instructions (tasks, notes), tool tags, decorators (rules, principles, anti-patterns), and other blocks.

This is the fundamental structural unit. If it has open and close tags with space between, it's a block, and it can hold things.

### 2.2 Declarations

Declarations define reusable identifiers that establish the state and dependencies within the execution model. They map to the Input and State primitive category. Examples include: `<var>`, `<resource>`, `<command>`, `<param>`.

Declarations can appear:

* Inside `<meta>` (global, available throughout the document)
* At the top level of `<ape>` (global)
* Inside any block (scoped to that block and its children)

**Scoping rule:** A declaration is available to everything *inside* the block where it appears, and everything *after* it at the same level. It does not leak upward or backward.

### 2.3 Identifier Uniqueness

APE has two classes of identifiers with different uniqueness rules:

**Global IDs**—must be unique across the entire document, regardless of where they are declared. Shadowing is not permitted:

* `resource/@id`
* `command/@id`
* `sequence/@id`
* `step/@id`
* `template/@id`
* `output/@id`

**Scoped names**—must be unique within their scope, but may shadow a parent scope's declaration of the same name:

* `var/@name`
* `param/@ref` (resolved from caller scope; locally scoped)

Scope affects *visibility* (what you can reference from where). It does not relax uniqueness for global IDs. Two `<command id="x">` declarations anywhere in the document is an error, even if they appear in different blocks.

### 2.4 Two-Pass Resolution

APE uses a two-pass model:

1. **Pass 1—Collect.** Walk the document, build registries of all declarations with their scope (which block they live in).
2. **Pass 2—Resolve.** When a reference is encountered (`ref`, `{{ var }}`, `goto`), resolve by searching the nearest scope first, then falling back to parent scopes, then global.

"Declare before use" is an optional lint rule (`--strict`), not a structural requirement. The two-pass model means forward references work by default.

### 2.5 Interleaving

Containers do not enforce child ordering. A `<step>` can have its `<rule>` before its `<instruction>`, or its `<variables>` after its `<gate>`. The schema allows interleaving; the validator enforces required children exist; `ape fmt` outputs canonical order.

**Exception:** `<prerequisite>` and `<prerequisites>` must appear before all other children in a `<step>`. Prerequisites are entry conditions—they must be evaluated before any work begins.

**Exception:** `<default>` must appear after all `<case>` elements in a `<conditional>`.

**Exception:** `<rule>` must appear before prose content when it appears inside an `<instruction>`.

### 2.6 Decorators

Decorators are elements that annotate but don't alter execution directly. They map to the guidance aspect of State and Input, providing rules or guidance. Examples include: `<rule>`, `<anti-pattern>`, `<principle>`, `<note>`. They can appear inside any block.

### 2.7 Content Model Separation

This is a fundamental architectural principle: prose and structure are strictly separated.

**Prose containers**—`<description>`, `<title>`, `<note>`, and simple decorators—contain **only text content**. No child tags allowed.

**Structure containers**—`<gate>`, `<criteria>`, `<criterion>`, `<on-fail>`, `<on-pass>`, `<conditional>`, `<case>`, `<default>`, `<each>`, `<sequence>`, `<steps>`, `<prerequisite>`—contain **only structural children**. No bare text content (whitespace for formatting is allowed, but meaningful prose is not).

**Mixed-content elements**—`<step>`, `<instruction>`, `<var>`, `<run>`—allow both prose and structure. Their prose content is interpreted narratively; their structural children are executed. `<instruction>` is mixed content that can contain prose and guidance decorators (`<rule>`, `<principle>`, `<anti-pattern>`, `<reference>`) but NOT executable elements. `<run>` is mixed content that can contain inline command text and a `set` attribute for capture.

---

## 3. Document Structure

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ape version="0.4.0" xmlns="https://ape-lang.dev/schema/0">

  <IMPORTANT>
  Executor-facing orientation. Teaches the LLM runtime what APE elements mean.
  </IMPORTANT>

  <meta>
    <name>My Workflow</name>
    <description>What this workflow does and why.</description>

    <params>...</params>
    <variables>...</variables>
    <resources>...</resources>
    <approved-commands>...</approved-commands>
    <approved-gate-commands>...</approved-gate-commands>
  </meta>

  <steps>
    ...
  </steps>

  <principle name="...">...</principle>

</ape>

```

### 3.1 `<ape>` (Root)

| Attribute | Required | Description |
| --- | --- | --- |
| `version` | Yes | Spec version (e.g., `0.4.0`) |
| `xmlns` | Yes | Namespace URI, pinned to major version |

**Versioning:** The namespace is pinned to the major version (`https://ape-lang.dev/schema/0`). The `version` attribute carries the full version (`0.4.0`, `0.4.1`, etc.). Documents with different minor versions share the same namespace and are expected to be broadly compatible within a major version. Breaking changes increment the major version and the namespace.

`<IMPORTANT>` must be first if present, then `<meta>`. Everything else—`<steps>`, declarations, tool tags, `<principle>`, `<reference>`—is optional and can appear in any order. Multiple `<steps>` blocks are allowed; each must contain at least one `<step>`. In stepless documents, tool tags and `<run>` elements at root level are executed in document order.

### 3.2 `<IMPORTANT>`

Executor-facing orientation. Teaches the LLM runtime what APE elements mean before it encounters them. Prose content only.

* Must be the first child of `<ape>` if present.
* At most one per document.
* The only all-caps tag in APE — intentional and distinctive.
* Replaces `<preamble>` from 0.3.0.

```xml
<IMPORTANT>
APE is an executable workflow. You are the runtime.

<meta> block is configuration and declarations only.
<instruction> tags are directives.
<gate> tags evaluate a condition and route via on-fail (required) or on-pass.
<run> tags execute commands, either inline or by reference.

<approved-gate-commands> are the ONLY commands that can be executed from within a <gate> tag.
</IMPORTANT>
```

### 3.3 `<meta>`

If present, `<meta>` is the first child of `<ape>` (after `<IMPORTANT>`, if present). It contains document metadata and declarations. It is configuration, not execution. **Tool tags and executable instructions are not permitted inside `<meta>`.**

| Child | Required | Description |
| --- | --- | --- |
| `<name>` | No | APE document name |
| `<description>` | No | Purpose and context |
| Any declaration | No | `<params>`, `<variables>`, `<resources>`, `<approved-commands>`, `<approved-gate-commands>` |

---

## 4. Variables

APE supports **full arbitrary** `{{ ... }}` expressions anywhere text or attribute values appear.
The expression language is intentionally runtime-defined (your engine decides what functions/vars exist);
the validator should at least ensure referenced identifiers can resolve in-scope (unless explicitly external).

```xml
<var name="project_name" default="my-app" />
<var name="coverage_threshold" type="number" default="90" />
<var name="features" type="list" value="feature-a, feature-b" />

<var name="test_output">
  <command>npm test</command>
</var>

<var name="config">
  <resource ref="config-file" />
</var>

```

| Attribute | Required | Description |
| --- | --- | --- |
| `name` | Yes | Unique within scope (may shadow parent scopes) |
| `id` | No | Optional identifier for reuse |
| `type` | No | `string` (default), `number`, `boolean`, `list` |
| `default` | No | Default value. If unresolved at runtime, engine must ask or halt. |
| `value` | No | An alternate way to declare the variable's value. |

Can appear inside `<variables>` wrapper, inside `<meta>`, or inline in any block. Scoped to the block where declared.

A `<var>` can have its value set via:

1. A `<command>` child—executes the command and captures output into the variable
2. A `<resource>` child—declares a resource dependency that provides the value
3. Element text content
4. `value` attribute
5. `default` attribute

Variables created implicitly by `<ask-user-question var="...">` or `<command set="...">` do not need a `<var>` declaration. **Do not declare variables with no default or explicit value to serve as placeholders; let them be created implicitly.**

---

## 5. Parameters

A parameter declares a cross-document dependency: something this document needs from whoever invokes it.

```xml
<param ref="project-path" />
<param ref="review-file" />
<param ref="placeholder-id" default="{{DOC_QUALITY_FINDINGS}}" />

```

| Attribute | Required | Description |
| --- | --- | --- |
| `ref` | Yes | Identifier that must exist in the caller's scope. |
| `default` | No | Fallback value if the caller doesn't provide it. |
| `required` | No | `true` (default) or `false`. |

Can appear inside `<params>` wrapper, inside `<meta>`, or inline in any block.

When resolved, the value is available locally under the same identifier—`<param ref="X" />` makes `{{ X }}` usable in this document. Scoped locally.

If `required="true"` (the default) and the caller's scope has no matching identifier and no `default` is set, the runtime must halt with an error naming the unresolved param.

---

## 6. Resources

Dependencies required for execution. Maps to State and Input category.

```xml
<resource id="cargo" type="executable" />
<resource id="config" type="file" path="./Cargo.toml" access="read,write" />
<resource id="app-root" type="file-path" path="/path/to/app" access="read,write,edit,grep" />

```

| Attribute | Required | Description |
| --- | --- | --- |
| `id` | Yes | Unique identifier (global) |
| `type` | Yes | `file`, `file-path`, `directory`, `executable`, `value`, `service`. Custom allowed. |
| `path` | No | Filesystem path (supports interpolation) |
| `access` | No | Comma-separated: `read`, `write`, `edit`, `grep`, `execute`, `delete` |
| `required` | No | `true` (default) or `false` |

Can appear inside `<resources>` wrapper, inside `<meta>`, or inline in any block.
Common reference surfaces:

* `uses="..."` on `<step>` (comma-separated resource IDs; each must resolve)
* interpolation inside attributes/text where your runtime exposes resource values

---

## 7. Commands

Command declarations define reusable, named shell operations. In 0.4.0, `<command>` is **declaration-only** — it defines what a command is, but does not execute it. Execution is performed by `<run>` (see Section 12).

Commands are declared inside `<approved-commands>` or `<approved-gate-commands>` in `<meta>`. This required split separates commands available for step-level execution from commands available for gate evaluation. Gate commands are evaluation-only — they gather inputs for criteria; they don't determine pass/fail on their own.

```xml
<approved-commands>
  <command id="run-tests">cargo test --all-features</command>
  <command id="build">cargo build</command>
</approved-commands>

<approved-gate-commands>
  <command id="test-failure-count">cargo test --all-features 2>&amp;1 | grep "failed" | wc -l</command>
  <command id="build-error-count">cargo build 2>&amp;1 | grep "^error" | wc -l</command>
</approved-gate-commands>

```

| Attribute | Required | Description |
| --- | --- | --- |
| `id` | Yes | Unique identifier (global, for reuse) |
| `tool` | No | Specific tool (e.g., `bash`, `file_create`) |

Commands may also appear as children of `<var>` to compute variable values:

```xml
<var name="test_output"><command>cargo test --all-features 2>&amp;1</command></var>
```

### `<approved-commands>` and `<approved-gate-commands>`

Both are required children of `<meta>` (when commands are used). They replace the former `<commands>` wrapper.

* **`<approved-commands>`** — Commands available for step-level execution via `<run ref="...">`. These are the commands the workflow runs as part of its work.
* **`<approved-gate-commands>`** — Commands available for gate evaluation via `<run ref="...">` inside `<gate>`. These commands gather measurement data (counts, percentages, status) that criteria expressions evaluate. They do not determine pass/fail on their own — that is the job of `<criterion check>`.

A `<run>` inside a `<gate>` may only reference commands from `<approved-gate-commands>`. A `<run>` outside a `<gate>` may only reference commands from `<approved-commands>`. Inline `<run>` (with command text in the body) is allowed anywhere and is not subject to this restriction.

---

## 8. Tool Tags and Behavioral Tags

Tags in this section can appear as direct children of `<step>`, `<on-fail>`, `<on-pass>`, `<case>`, `<default>`, `<each>`, and `<agents-in-parallel>` — **no wrapper element needed**. They can also appear at root level in stepless documents. They cannot appear inside `<meta>` (which is configuration, not execution).

There are two categories, mapping to specific primitive types:

* **Tool tags** map to specific LLM tools. Each tag invokes a tool directly. **(Actions category)**
* **Behavioral tags** invoke execution patterns—they change *how* the LLM operates rather than calling a specific tool. **(Actions/Flow Control hybrid)**

### 8.1 Tool Tags

Tool tags are an exhaustive mapping to the LLM's tool interfaces. Each tag corresponds to one tool.

#### `<read>`

```xml
<read path="./src/main.rs" />
<read path="./config.yaml" var="config_content" />

```

| Attribute | Required | Description |
| --- | --- | --- |
| `path` | Yes | File to read |
| `var` | No | Variable to store file content |

#### `<write>`

Direct tool invocation—triggers the Write tool on a specific path. For prescriptive output, use `<output>` instead.

```xml
<write path="./output/report.md">Content here</write>
<write path="./log.txt" mode="append">New log entry</write>
<write ref="config" />

```

| Attribute | Required | Description |
| --- | --- | --- |
| `path` | No | File to write (required if no `ref`) |
| `ref` | No | Reference to a resource with write access (required if no `path`) |
| `mode` | No | `create` (default), `append`, `overwrite` |

The `path` or `ref` target should have write permission declared via a `<resource>` with `access` including `write`.

#### `<edit>`

In-place file modification.

```xml
<edit path="./src/config.rs">Replace the hardcoded URL with {{ api_url }}</edit>
<edit path="./README.md" />

```

| Attribute | Required | Description |
| --- | --- | --- |
| `path` | Yes | File to edit |

The body describes the edit to perform. The LLM uses the Edit tool to apply it based on context.

#### `<glob>`

File pattern search.

```xml
<glob pattern="src/**/*.ts" var="ts_files" />

```

| Attribute | Required | Description |
| --- | --- | --- |
| `pattern` | Yes | Glob pattern to match |
| `var` | No | Variable to store matched file paths |
| `path` | No | Directory to search in (default: working directory) |

#### `<grep>`

Content search across files.

```xml
<grep pattern="TODO|FIXME" var="todos" />
<grep pattern="import.*from" path="./src" var="imports" />

```

| Attribute | Required | Description |
| --- | --- | --- |
| `pattern` | Yes | Search pattern (regex) |
| `var` | No | Variable to store results |
| `path` | No | File or directory to search (default: working directory) |

#### `<web-search>`

```xml
<web-search query="rust testing best practices" var="results" />

```

| Attribute | Required | Description |
| --- | --- | --- |
| `query` | Yes | Search query |
| `var` | No | Variable to store results |

#### `<web-fetch>`

```xml
<web-fetch url="https://example.com/api/status" var="response" />

```

| Attribute | Required | Description |
| --- | --- | --- |
| `url` | Yes | URL to fetch |
| `var` | No | Variable to store response content |

#### `<ask-user-question>`

**Blocking Action**—execution pauses until response.

```xml
<ask-user-question var="project_name">What is your project name?</ask-user-question>
<ask-user-question var="proceed" type="confirm">Continue?</ask-user-question>
<ask-user-question var="target" type="choice">
  <option value="debug">Debug</option>
  <option value="release">Release</option>
</ask-user-question>

```

| Attribute | Required | Description |
| --- | --- | --- |
| `var` | No | Variable to store response |
| `type` | No | `text` (default), `confirm`, `choice` |

### 8.2 Behavioral Tags

Behavioral tags change how the LLM executes rather than invoking a specific tool.

#### `<interview-mode>`

Sequential Q&A Action. One question at a time, wait for each answer.

```xml
<interview-mode>
  <ask-user-question var="name">Project name?</ask-user-question>
  <ask-user-question var="lang" type="choice">
    <option value="rust">Rust</option>
    <option value="python">Python</option>
  </ask-user-question>
</interview-mode>

```

#### `<plan-mode>`

The LLM enters planning mode—exploring, designing, and presenting an approach for approval before executing further actions. **(Behavioral Action)**

```xml
<plan-mode>
  Design the database migration strategy before making changes.
</plan-mode>

```

#### `<agents-in-parallel>`

Parallel Execution pattern. Run multiple tasks concurrently using subagents. Resume main execution after all finish. **(Flow Control/Execution hybrid)**

```xml
<agents-in-parallel>
  <run>gh pr review --comment</run>
  <run>npm run generate-tests</run>
</agents-in-parallel>

```

Each `<run>` child (and tool tag) is dispatched concurrently. Execution resumes after all complete.

#### `<stop>`

Unconditional halt Flow Control. Can appear anywhere inside a block.

```xml
<stop>Critical failure.</stop>
<stop />

```

#### `<subagent-stop>`

Unconditional Flow Control to return from a subagent.

```xml
<subagent-stop>Review complete.</subagent-stop>

```

---

## 9. Steps

A phase of execution, scaffolding the sequential flow. Maps to the Flow Control category.

```xml
<step number="1" id="specification" uses="cargo">
  <prerequisite ref="previous-step" goto="previous-step" />

  <title>Specification</title>

  <instruction>Narrative context for the agent about what this step involves.</instruction>

  <run ref="run-tests" />

  <gate>
    <run ref="test-failure-count" set="test_failures" />
    <criterion check="{{ test_failures == 0 }}" />
    <on-fail goto="implementation" />
  </gate>
</step>

```

| Attribute | Required | Description |
| --- | --- | --- |
| `id` | Yes | Unique identifier (global) |
| `number` | No | Display ordering (informational) |
| `uses` | No | Comma-separated resource IDs (each must resolve) |

`<title>` is optional. When the step `id` is descriptive (e.g., `id="verify"`, `id="red-phase"`), a title that restates it adds noise—omit it. Use `<title>` only when the step needs a human-readable name that the `id` cannot convey.

`<prerequisite>` or `<prerequisites>`, if present, must appear before all other children. Prerequisites are entry conditions and must be evaluated first.

`<description>`, `<instruction>`, and `<instructions>` are all optional children (at most one of each per step). **A step must not contain both `<instruction>` and `<instructions>`.**

---

## 10. Prerequisites

Scaffolds entry conditions into a step. Maps to Flow Control. Singular for one dependency (directly inside `<step>`), plural wrapper for two or more—same pattern as `<instruction>` / `<instructions>`.

**Ordering:** Prerequisites must appear before all other children in a `<step>`. They are entry conditions—the LLM evaluates them first, before reading the step's instructions or goal.

**Content:** `<prerequisite>` is a structure container—it must not contain prose. The condition and recovery path are expressed entirely through attributes and structural children. Use `ref` to identify the dependency, `goto` or `halt` to specify the recovery path, and optional structural children like `<check>` for runtime verification.

| Attribute | Required | Description |
| --- | --- | --- |
| `ref` | No | ID of the step or element that must be complete |
| `goto` | No | Step ID to jump to if the condition is not met |
| `halt` | No | If `true`, halt execution if the condition is not met |

A `<prerequisite>` must specify at least one of `goto` or `halt` to provide a recovery path.

```xml
<prerequisite ref="specification" goto="specification" />

<prerequisites>
  <prerequisite ref="build" goto="build" />
  <prerequisite ref="lint" goto="lint" />
</prerequisites>

<prerequisite halt="true">
  <check>
    <command>git status --porcelain</command>
  </check>
</prerequisite>

```

---

## 11. Instruction / Instructions

An instructions element containing atomic units of interpretable narrative or specific actions. Wrappers scaffold the sequential flow within a step. Maps to Flow Control category.

`<instruction>` is the atomic unit of work inside a step. It contains pure prose that provides narrative context and interpretive guidance. It is a **prose container only**—it cannot contain structural children like `<run>`, `<command>`, `<output>`, or tool tags.

`<instructions>` is a wrapper containing two or more `<instruction>` children.

```xml
<instruction>
  Narrative context for the agent about what this step involves. The agent may interpret this prose and decide how to proceed.
</instruction>

<instruction>
  Review the diff to ensure all changes are intentional.
</instruction>

<instructions>
  <instruction>
    Analyze the test failures to understand what went wrong.
  </instruction>
  <instruction>
    Debug the issue and implement a fix.
  </instruction>
</instructions>

```

`<instruction>` is mixed content—prose with optional guidance decorators. It may contain `<rule>`, `<principle>`, `<anti-pattern>`, and `<reference>`, but does NOT contain executable content like `<run>`, `<output>`, or tool tags. Note: `<note>` is allowed only at the step level or root `<ape>` level.

`<instructions>` contains only `<instruction>` elements (two or more).

If you need to specify **executable** work (commands, tool invocations), place them in sibling `<run>` elements within the same `<step>`, not inside `<instruction>`.

### `<note>`

Author-facing context. Not executable, not output. Prose only.

---

## 12. Run

`<run>` is the execution verb in APE. It replaces the former `<action>` element with a clearer, more direct semantic. Where `<command>` declares what a command is, `<run>` executes it.

### Three Forms

| Form | Attributes | Body | Meaning |
| --- | --- | --- | --- |
| **Reference** | `ref` | Must be empty | Executes a declared command by ID. May also carry `set`. |
| **Inline** | *(none)* | Required (the command text) | Executes a literal command. |
| **Inline + Capture** | `set` | Required (the command text) | Executes a literal command and captures stdout into a variable. |

```xml
<!-- Reference form: execute a declared command -->
<run ref="run-tests" />

<!-- Reference form with capture -->
<run ref="test-failure-count" set="test_failures" />

<!-- Inline form: execute literal command -->
<run>git add -p</run>

<!-- Inline + capture form -->
<run set="staged_count">git diff --cached --name-only | wc -l</run>

```

| Attribute | Required | Description |
| --- | --- | --- |
| `ref` | No | Reference to a `<command>` `id` in `<approved-commands>` or `<approved-gate-commands>`. Body must be empty when present. |
| `set` | No | Capture stdout into a named variable. Works on both reference and inline forms. |

### Capture Semantics (`set`)

The `set` attribute captures command output into a named variable. The contract:

* **Captures stdout only.** Stderr is not captured (it may be displayed or logged by the runtime, but does not enter the variable).
* **Trailing newline is stripped.** A command that outputs `"hello\n"` stores `"hello"`. Interior newlines are preserved.
* **Multiline output is a string.** The value is stored as a single string with embedded newlines, not a list.
* **On non-zero exit:** The variable is still set to whatever stdout contained. Gate `<on-fail>` handlers should be used to handle error conditions. The `set` mechanism does not halt on failure—it captures and continues.

### Allowed Parents

`<run>` is a direct child of:
* `<step>` — step-level execution
* `<gate>` — gate evaluation (must ref `<approved-gate-commands>` only)
* `<on-fail>`, `<on-pass>` — recovery or follow-up work
* `<case>`, `<default>` — conditional branch execution
* `<each>` — iteration body
* `<agents-in-parallel>` — concurrent execution

### Gate Restrictions

A `<run>` inside a `<gate>` may only reference commands from `<approved-gate-commands>` (or use inline commands). A `<run>` outside a `<gate>` may only reference commands from `<approved-commands>` (or use inline commands). This separation keeps command execution distinct from decision logic.

### Example

```xml
<step id="tests">
  <instruction>
    Ensure all tests pass before proceeding. Review any failures carefully.
  </instruction>

  <run ref="run-tests" />

  <gate>
    <run ref="test-failure-count" set="test_failures" />
    <criterion check="{{ test_failures == 0 }}" />
    <on-fail goto="debug" />
  </gate>
</step>

```

---

## 13. Gates

Quality enforcement and routing. Scaffolds the sequential flow between steps. Maps to Flow Control category.

A gate evaluates conditions and routes execution based on the result. It contains a condition (via `<criteria>` or a single `<criterion>`), optional `<on-pass>`, and exactly one `<on-fail>` (required).

```xml
<!-- Expression-based: explicit condition -->
<gate>
  <criterion check="{{ test_failures == 0 }}" />
  <on-fail goto="debug" />
  <on-pass goto="commit" />
</gate>

```

```xml
<!-- Gate with run for measurement and expression-based criterion -->
<gate>
  <run ref="build-error-count" set="build_errors" />
  <criterion check="{{ build_errors == 0 }}" />
  <on-pass goto="specification" />
  <on-fail goto="implementation" />
</gate>

```

```xml
<!-- Compound: multiple conditions combined -->
<gate>
  <criteria operator="and">
    <criterion check="{{ lint_errors == 0 }}" />
    <criterion check="{{ lint_warnings == 0 }}" />
  </criteria>
  <on-fail goto="fix-code" />
</gate>

```

**Validator rules for gates:**

* Exactly one `<criteria>` or exactly one `<criterion>` (required — one or the other, not both).
* Exactly one `<on-fail>` (required).
* At most one `<on-pass>` (optional).

### Success Semantics

When a `<criterion check>` evaluates an expression, success means the expression evaluates to `true`. There is no implicit notion of success — the `check` expression defines the exact condition.

When a `<criteria ref>` points to another `<criteria>` element, the gate reuses that criteria's conditions. This enables named, reusable gate conditions.

In 0.4.0, `<criterion ref>` resolves only to named `<criteria>` or `<criterion>` IDs — not to commands. Gate evaluation uses `<run>` inside the gate to execute measurement commands and capture their output into variables, then `<criterion check>` to evaluate the result.

### `<criteria>`

Gate condition. Supports two forms: reference (reuse a named criteria) and compound (combine multiple `<criterion>` children).

**Reference form:**

```xml
<!-- Reuse a named criteria defined elsewhere -->
<criteria ref="linter-checks" />

```

**Compound form:**

```xml
<criteria id="linter-checks" operator="and">
  <criterion check="{{ lint_errors == 0 }}" />
  <criterion check="{{ lint_warnings == 0 }}" />
</criteria>

```

| Attribute | Required | Description |
| --- | --- | --- |
| `id` | No | Optional identifier. When present, allows this criteria to be reused by other `<criteria ref>` elements. |
| `ref` | No | ID of a `<criteria>` to reuse. Required if no `<criterion>` children. |
| `operator` | No | How to combine criteria: `and` (all must pass) or `or` (any may pass). Required when using `<criterion>` children. |

**Children (compound form):**

* `<criterion>` — A single evaluable condition. See `<criterion>` below.

**Note:** Reference form `<criteria ref="..." />` is an empty element. Compound form contains one or more `<criterion>` children. In compound form, the `id` attribute makes the criteria reusable from other gates.

### `<criterion>`

A single evaluable condition. Supports two evaluation modes: reference-based (reuse a named criterion) and expression-based (does a boolean condition hold?).

**Reference mode:**

```xml
<criterion ref="no-test-failures" />

```

**Expression mode:**

```xml
<criterion check="{{ test_failures == 0 }}" />
<criterion check="{{ actual_msg matches commit-template }}" />

```

| Attribute | Required | Description |
| --- | --- | --- |
| `id` | No | Optional identifier |
| `ref` | No | ID of a named `<criteria>` or `<criterion>` to reuse. Mutually exclusive with `check`. |
| `check` | No | Expression that evaluates to a boolean. Uses `{{ }}` interpolation with comparison operators. Mutually exclusive with `ref`. |

A `<criterion>` must have exactly one of `ref` or `check`.

**`check` expression syntax:**

The `check` attribute uses `{{ }}` expressions extended with comparison and matching operators:

* **Comparison:** `==`, `!=`, `<`, `>`, `<=`, `>=` — compare values. Numeric when both sides are numbers, string otherwise.
* **Template matching:** `matches` — evaluates whether a value conforms to a named template's pattern. Template interpolation placeholders act as wildcards. Example: `{{ actual_msg matches commit-template }}`.
* **Boolean literals:** `true`, `false`.

Expressions reference in-scope variables by name. The same scoping rules as `{{ }}` interpolation apply.

**Inline criterion in gates:**

A single `<criterion>` can appear directly in a `<gate>` without a `<criteria>` wrapper. This is shorthand for the common case of a single condition:

```xml
<!-- Shorthand: criterion directly in gate -->
<gate>
  <criterion check="{{ test_failures == 0 }}" />
  <on-fail goto="debug" />
</gate>

<!-- Equivalent longform -->
<gate>
  <criteria>
    <criterion check="{{ test_failures == 0 }}" />
  </criteria>
  <on-fail goto="debug" />
</gate>

```

### `<goto>`

Structural navigation element. Points to a step to jump to as an alternative to using the `goto` attribute on parent elements.

```xml
<gate>
  <criteria ref="build-check" />
  <on-fail>
    <goto ref="debug" />
  </on-fail>
</gate>

```

| Attribute | Required | Description |
| --- | --- | --- |
| `id` | No | Optional identifier |
| `ref` | Yes | ID of a `<step>` to navigate to |

**Children:** None. This is an empty element.

**Note:** `<goto>` can appear inside `<gate>`, `<on-fail>`, `<on-pass>`, `<case>`, `<default>`, and `<each>`. It provides an alternative to the `goto` attribute, allowing explicit step navigation as a child element.

### Flow Control Attributes

**On `<on-fail>`:**

| Attribute | Type | Description |
| --- | --- | --- |
| `goto` | Primary | Jump to step ID (must resolve) |
| `retry` | Primary | Retry current step (`true`) |
| `halt` | Primary | Stop workflow (`true`) |
| `proceed` | Primary | Continue to next step (`true`) |
| `max` | Modifier | Max retries (requires `retry="true"`, integer ≥ 1) |
| `then` | Modifier | After `max` retries: step ID or `halt` (requires `retry="true"`, defaults to `halt`) |

**On `<on-pass>`:**

| Attribute | Type | Description |
| --- | --- | --- |
| `goto` | Primary | Jump to step ID (must resolve) |
| `proceed` | Primary | Continue to next step (`true`) |

**Mutual exclusivity:** Exactly one *primary* attribute per element. `goto` + `retry` = error. `halt` + `proceed` = error. The modifiers `max` and `then` are only valid when `retry="true"` is present.

**`<on-fail>` requires explicit flow control.** The `<on-fail>` must carry exactly one primary attribute. There is no default behavior—the author must be explicit about what happens on failure.

**`<on-pass>` default:** If no flow-control attributes are present, execution proceeds to the next step.

**Content Model:** `<on-fail>` and `<on-pass>` are structure containers. They may contain `<run>`, tool tag, and `<goto>` children for recovery or follow-up work. They do **not** allow bare prose text.

---

## 14. Conditionals

### `<conditional>`

Unified branching Construct, mapping to Flow Control. Evaluates the `on` expression and routes to the matching `<case>`.

```xml
<conditional on="{{ request_type }}">
  <case value="implementation" goto="make-changes" />
  <case value="information" goto="investigate" />
  <default goto="clarify-intent" />
</conditional>

<conditional on="{{ intent_clear }}" default="halt">
  <case value="true" goto="proceed" />
</conditional>

<conditional on="{{ env }}" default="proceed">
  <case value="ci"><command ref="coverage" /></case>
</conditional>

```

| Attribute | Required | Description |
| --- | --- | --- |
| `on` | Yes | Expression to evaluate (supports `{{ }}` interpolation) |
| `default` | No | Shorthand for simple default outcomes: a step ID (acts as `goto`), `"halt"`, or `"proceed"`. Mutually exclusive with a `<default>` child element. |

**Children:**

| Child | Required | Description |
| --- | --- | --- |
| `<case>` | Yes (≥1) | A branch. `value` attribute (required) matches against the `on` expression. |
| `<default>` | No (≤1) | Fallback when no `<case>` matches. Mutually exclusive with `default` attribute. |

**`<case>` and `<default>` content model:** Structure containers. They contain only `<run>`, `<sequence>`, tool tags, and behavioral tags. They do **not** allow bare prose text. Flow control attributes (`goto`, `halt`) are allowed.

| Attribute | Description |
| --- | --- |
| `value` | Required on `<case>`. The value to match against. Must be unique within the `<conditional>`. |
| `goto` | Jump to a step ID |
| `halt` | Stop workflow (`true`) |

`goto` and `halt` are mutually exclusive on each element.

**Conditionals vs. gates:** Conditionals are **navigational**—they route based on a value that already exists. Gates are **evaluative**—they judge whether completed work meets a bar. Conditionals do not support `retry`, `max`, or `then` because the value won't change by retrying. Gates do, because the work can be redone.

**After a `<case>` or `<default>` body:** If the body executes with no `goto` or `halt`, execution continues to whatever follows the `<conditional>`.

**Structure:** At least one `<case>`, followed by at most one `<default>`. `<case>` `value` attributes must be unique within their `<conditional>`. **`<default>` must appear after all `<case>` elements.**

---

## 15. Iteration

```xml
<each item="target" in="{{ targets }}">
  <run>cargo build --{{ target }}</run>
</each>

```

The `item` attribute creates a scoped variable binding available inside the `<each>` body. The `in` attribute should resolve to a list-typed variable. Maps to Flow Control.

`<each>` is a structure container. It contains only `<run>`, `<sequence>`, tool tags, and behavioral tags. No bare prose text.

---

## 16. Sequences

Strictly ordered command list. Scaffolds Flow Control, sibling to `<instruction>` in purpose: where `<instruction>` is interpretive prose, `<sequence>` is a precise, ordered list of commands. No prose, no flexibility.

```xml
<sequence id="ci-pipeline">
  <command ref="fmt-check" />
  <command ref="clippy" />
  <command ref="test-all" />
</sequence>

```

`<sequence>` is a structure container. It contains only `<command>` elements. No text content, no prose.

---

## 17. Decorators

Guidance aspects of State and Input. Can appear inside any block.

### `<rule>`

```xml
<rule>ALWAYS run without filters.</rule>
<rule>NEVER use cargo test --lib.</rule>
<rule>Imperative mood.</rule>
<rule>No type prefixes.</rule>

```

Binding requirement or prohibition. Text only.

### `<anti-pattern>`

```xml
<anti-pattern>Documenting "what" instead of "why"</anti-pattern>

```

Prose container. Text only. Stands alone—proximity handles grouping.

### `<note>`

```xml
<note>Author-facing context. Not executed.</note>

```

Prose container. Text only.

---

## 18. Annotations

Author guidance aspect of State and Input.

| Element | Purpose | Content |
| --- | --- | --- |
| `<description>` | What something is (author-facing, never output). Only valid as a child of `<meta>` or `<step>`—at most one per parent. | Prose container (text only) |

---

## 19. Templates

Reusable content blocks, part of the prescriptive output pipeline within Actions. Define the **shape of content**. They are reusable, format-aware content blocks with interpolation.

### Inline Templates

```xml
<template id="pr-body" format="md">
## Summary
{{ summary }}

## Test plan
- [x] Tests pass locally
{{ test_details }}

## Linear ticket
[{{ ticket_id }}](https://linear.app/software/issue/{{ ticket_id }})
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
<output template="pr-body" to="file" target="pr-description.md" />

<command>gh pr create --draft --body "<template ref="pr-body" />"</command>

```

| Attribute | Required | Description |
| --- | --- | --- |
| `id` | Yes | Unique identifier (global) |
| `format` | No | Content format hint: `md`, `yaml`, `json`, `xml`, `toml`, `text`, custom. Not validated—the author is responsible for correctness. The LLM uses best judgment. |
| `ref` | No | Reference to a resource or import containing the template content. If present, the element body should be empty. |

Templates support `{{ }}` interpolation throughout their body. Undefined variables follow standard resolution: check scope, fall back to parent, ask or halt.

Can appear inside `<meta>`, at root level, or inside any block. Scoped like other declarations.

---

## 20. Outputs

Prescriptive output pipeline, part of the Action primitive category. Declare **what gets produced and where it goes**. Where `<template>` defines the shape of content, `<output>` defines its destination. Outputs are prescriptive—the author is saying "this is exactly what I expect, where I expect it, and how it should be applied."

### Basic Usage

```xml
<output template="pr-body" to="file" target="pr-description.md" />

<output to="stdout">PR created: {{ pr_url }}</output>

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

<output ref="changelog-entry">
- See also: {{ related_pr_url }}
</output>

```

### Targeting Within a Destination

The `to` attribute declares the high-level destination kind. The `target` attribute declares the specific destination. The `anchor` and `position` attributes provide fine-grained placement within the target:

| Attribute | Required | Description |
| --- | --- | --- |
| `id` | No | Unique identifier (global) for reuse via `ref` |
| `ref` | No | Reference to another output's destination. Cannot combine with `to`/`target`. |
| `template` | No | Template ID to fill and use as content. If present, element body is ignored. |
| `to` | No | Destination kind: `file`, `stdout`, `log`, `resource`, `template`. Required if no `ref`. |
| `target` | No | Specific destination: file path, resource `ref`, template `id`, log name. Supports interpolation. |
| `format` | No | Output format hint (same as template `format`). Inherited from template if not specified. |
| `anchor` | No | Location within the target: a heading, line pattern, section name, JSON path—whatever makes sense for the format. |
| `position` | No | How to place content relative to the anchor: `append` (default), `prepend`, `replace`. |

**Relationship to `<write>`:** `<write>` is a direct tool invocation—"use the Write tool now." `<output>` is a declaration of intent—"this is what should be produced." Use `<output>` when you want to specify exactly what the result looks like and where it goes. Use `<write>` when you just need to trigger the tool.

**Relationship to `<template>`:** Templates define shape. Outputs define destination. An output *with* a template reference is "fill this shape, put it there." An output *without* a template is a direct content write. A template *without* an output is a reusable content block that can be referenced from commands or other contexts.

Can appear at step level, inside `<on-fail>`, `<on-pass>`, `<case>`, `<default>`, or `<each>`.

---

## 21. Principles

System guidance aspect of State and Input category.

```xml
<principle name="Atomic">Each commit is complete and independent.</principle>

```

`<principle>` stands alone like `<rule>`—proximity handles grouping. Has a required `name` attribute.

---

## 22. References

Informational context aspect of State and Input category.

A reference is a pure pointer to any element with an `id` attribute. It allows reuse of declared elements (actions, commands, resources, etc.) in multiple places within a document, or references documentation and guidance elements.

```xml
<reference ref_id="test-action" />
<reference ref_id="deployment-guide" />

```

| Attribute | Required | Description |
| --- | --- | --- |
| `ref_id` | Yes | ID of an element to reference (points to any element with an `id` attribute) |

**Note:** `<reference>` is the ONLY element that cannot have an `id` attribute itself. It is a pure pointer element with no content and no children.

**Valid targets:** `<reference ref_id="...">` can point to:
* Commands (`<command id="...">`)
* Resources (`<resource id="...">`)
* Steps (`<step id="...">`)
* Templates (`<template id="...">`)
* Outputs (`<output id="...">`)
* Variables (`<var id="...">`)
* Any other element that carries an `id` attribute

**Usage contexts:** References can appear:
* Inside `<step>`, `<gate>`, `<on-pass>`, `<on-fail>`, and other containers
* Inside `<instruction>` as a guidance decorator
* At root level in `<ape>`
* Inside `<meta>`

---

## 23. Validation Architecture

APE uses a layered validation model:

### XSD (Shape)

* Tag names and attribute names exist
* Attribute types (enums, booleans, identifiers)
* Parent-child relationships (what can appear where)
* Basic requiredness
* `<param>` shape: `ref` attribute required, `default` optional, `required` optional

### Validator (Semantics)

**General:**

* "Empty" means `trim(textContent) == ""`. Whitespace from indentation does not count as content.

**Document-level:**

* `<IMPORTANT>`, if present, is the first child of `<ape>`
* `<meta>`, if present, is the first child of `<ape>` after `<IMPORTANT>`
* If `<steps>` is present, it must contain ≥ 1 `<step>`

**Required children:**

* `<gate>` must contain exactly one `<criteria>` or exactly one `<criterion>` (not both), and exactly one `<on-fail>`
* `<conditional>` must have a non-empty `on` attribute
* `<conditional>` must contain at least one `<case>`
* `<conditional>` must contain at most one `<default>` child element, and it must appear after all `<case>` elements
* `<conditional>` `default` attribute and `<default>` child element are mutually exclusive

**Identity and uniqueness:**

* Global IDs (`resource`, `command`, `sequence`, `step`, `template`, `output`, `criteria`) are unique across the entire document. No shadowing.
* Scoped names (`var/@name`, `param/@ref`) are unique within their scope. Shadowing is permitted.
* `<case>` `value` attributes are unique within their `<conditional>`
* Every structural element can optionally carry an `id` attribute for reference

**Reference resolution:**

* All `ref` attributes on `<output>`, `<write>`, and `<template>` resolve to a declared `id` of the correct type
* `<run ref="X">` resolves to a `<command>` `id` in `<approved-commands>` or `<approved-gate-commands>`
* All `goto` attributes resolve to a `<step>` `id`
* Each token in `uses` resolves to a `<resource>` `id`
* `<output template="X">` resolves to a `<template>` `id`
* `<output ref="X">` resolves to another `<output>` `id`
* `<template ref="X">` resolves to a `<resource>` `id`
* `<write ref="X">` resolves to a `<resource>` with write access
* `<criteria ref="X">` resolves to a `<criteria>` `id`
* `<criterion ref="X">` resolves to a `<criteria>` or `<criterion>` `id`
* `<criterion>` must have exactly one of `ref` or `check` — they are mutually exclusive
* `{{ ... }}` identifiers resolve to an in-scope variable (or are explicitly runtime-provided)
* `<param ref="X">` resolves to an identifier in the caller's scope (cross-document resolution)

**Command rules:**

* `<command>` is declaration-only in 0.4.0. It must have an `id` and body text. It appears only inside `<approved-commands>`, `<approved-gate-commands>`, or as a child of `<var>`.
* Execution is performed by `<run>`, not `<command>`.

**Run rules:**

* `ref` and body text are mutually exclusive on `<run>`. If `ref` is present, the body must be empty.
* `<run ref>` inside a `<gate>` must reference a command in `<approved-gate-commands>`.
* `<run ref>` outside a `<gate>` must reference a command in `<approved-commands>`.
* `set` is valid on any `<run>` form (reference or inline).

**Content model rules (0.4.0 breaking changes):**

* `<instruction>` is mixed content—prose with optional guidance decorators. Allowed: `<rule>`, `<principle>`, `<anti-pattern>`, `<reference>`. NOT allowed: `<note>` (use only at step or root level), `<run>`, `<output>`, or tool tags.
* `<run>` is mixed content with an optional `ref` or `set` attribute. If `ref` is present, body must be empty. If `ref` is absent, body contains inline command text.
* `<criteria>` supports two forms: reference (to a named criteria) and compound (with `<criterion>` children and `operator` attribute). A single `<criterion>` can appear directly in a `<gate>` without a `<criteria>` wrapper.
* `<criterion>` supports two evaluation modes: `ref` (reuses a named `<criteria>` or `<criterion>`) and `check` (evaluates a boolean expression). Exactly one must be present.
* `<goto>` is a structural navigation element that can appear inside `<gate>`, `<on-fail>`, `<on-pass>`, `<case>`, `<default>`, and `<each>`.
* `<on-fail>` and `<on-pass>` are **structure containers**. They may contain `<run>`, tool tag, and `<goto>` children for recovery work. No bare prose text or prose decorators like `<note>` are allowed.
* `<case>`, `<default>`, and `<each>` are **structure containers**. No bare prose text. May contain `<run>`, tool tag, and `<goto>` children.
* `<gate>` may contain `<run>` children for measurement commands before the criteria evaluation.

**Flow control rules:**

* `<on-fail>` must have exactly one primary flow-control attribute (`goto`, `retry`, `halt`, `proceed`)
* `<on-pass>` supports only `goto` and `proceed`
* `max` requires `retry="true"`
* `then` requires `retry="true"` (defaults to `halt` if omitted)
* `goto` values must resolve to a `<step>` `id`

**Output rules:**

* `ref` and `to` are mutually exclusive on `<output>`
* If `template` is present, it must resolve to a `<template>` `id`
* If `to` is `file` or `resource`, `target` should be present

**Variable rules:**

* A `<var>` declaration must carry a value via element content, `value` attribute, or `default` attribute. Empty declarations are not valid.
* Variables created implicitly (by `<ask-user-question var>` or `<command set>`) do not require a `<var>` declaration.

**Prerequisite rules:**

* `<prerequisite>` and `<prerequisites>` must appear before all other children in a `<step>`
* `<prerequisite>` must not contain prose—it is a structure container. Conditions and recovery paths are expressed through attributes (`ref`, `goto`, `halt`) and optional structural children (`<check>`)
* A `<prerequisite>` must specify at least one of `goto` or `halt`

**Rule ordering:**

* When a `<rule>` appears inside an `<instruction>`, it must appear before prose content

**Comment rules:**

* XML comments (`<!-- -->`) are not permitted in APE documents. Use `<note>` for author-facing context.

**Redundancy rules:**

* A `<rule>`, `<principle>`, or `<anti-pattern>` that restates behavior already enforced by a `<gate>`, `<prerequisite>`, `<conditional>`, or `<command>` definition is a validation error. Structure enforces; prose does not need to narrate what structure already guarantees.
* The same guidance expressed in more than one decorator type (`<rule>`, `<principle>`, `<anti-pattern>`, `<note>`) is a validation error. Choose the single element that best matches the nature of the guidance.
* A `<rule>` inside a `<step>` that duplicates a document-level `<rule>` is a validation error. Scope narrows; it does not echo.

**Description rules:**

* `<description>` may only appear as a direct child of `<meta>` or `<step>`
* At most one `<description>` per parent

**Template rules:**

* `id` is required
* If `ref` is present, the body should be empty (`trim(textContent) == ""`)

### Beyond the XSD

The XSD validates shape. The rules in `ape-linting.md` validate everything else — semantic correctness, redundancy, structural smell, and taste. Each rule has a unique ID and severity level:

* **ERROR** — semantically broken. LLM executors must halt. Authors must fix.
* **WARNING** — valid but likely wrong. Authors should fix.
* **INFO** — works but could be better. Authoring-time taste.

These rules are the spec's enforcement layer for everything the XSD cannot express. Any future tooling that validates APE documents should implement them.

---

## 24. Core Constraints Summary

This section summarizes critical constraints that validators must enforce.

1. **`<IMPORTANT>` is first if present, then `<meta>`.** `<IMPORTANT>` is the only all-caps tag in APE. It is executor-facing orientation—prose content that teaches the LLM runtime what APE elements mean before it encounters them. At most one per document. `<meta>` comes after `<IMPORTANT>` (or first if no `<IMPORTANT>`). It contains configuration (declarations and metadata) only—**no executable tool tags, `<run>` elements, or instruction blocks are permitted within `<meta>`.**
2. **`<steps>` is optional.** Multiple `<steps>` blocks are allowed; each must contain at least one `<step>`. Documents without `<steps>` are stepless and executed top to bottom—declarations, `<run>` elements, and tool tags at root level are processed in document order.
3. **Declarations scope to their block.** Available inside and after, not upward or backward. `<param>` declarations resolve from the caller's scope and are then locally scoped.
4. **Two-pass resolution.** Walk the document to build registries, then walk to resolve. This means forward references (define after use) work by default.
5. **Global IDs are globally unique.** The `resource`, `command`, `sequence`, `step`, `template`, `output`, and `criteria` IDs cannot shadow or collide across the entire document. Any element can optionally carry an `id`. Only `var/@name` and `param/@ref` support scoped shadowing.
6. **Prose and structure are strictly separated.** Prose containers (`<description>`, `<title>`, `<note>`) hold only text and appear only in prose/mixed contexts (root `<ape>`, `<step>`). `<note>` is allowed at step level and root `<ape>` level only, not inside `<instruction>`. `<instruction>` is mixed content allowing guidance decorators (`<rule>`, `<principle>`, `<anti-pattern>`, `<reference>`) but not `<note>`. Structure containers (`<gate>`, `<criteria>`, `<criterion>`, `<on-fail>`, `<on-pass>`, `<conditional>`, `<case>`, `<default>`, `<each>`, `<sequence>`, `<steps>`) hold only structural children—no prose elements like `<note>` allowed. This eliminates the illocutionary gap—the author's intent is expressed through structure, not through conversational prose.
7. **`<command>` is declaration-only (0.4.0 breaking change).** `<command>` must have an `id` and body text. It appears only inside `<approved-commands>`, `<approved-gate-commands>`, or as a child of `<var>`. Execution is performed by `<run>`.
8. **Flow control requires exactly one primary attribute.** `<on-fail>` must have exactly one primary attribute (`goto`, `retry`, `halt`, `proceed`). `<on-pass>` supports only `goto` and `proceed`. Flow-control attributes belong on the handler elements. Modifiers `max` and `then` require `retry`.
9. **`<on-fail>` is singular and explicit.** Exactly one `<on-fail>` child is required per `<gate>`. It must carry a primary attribute. There is no default behavior—the author must be explicit about what happens on failure.
10. **Interleaving is allowed but ordering is paramount in specific places.** While general interleaving is valid, specific ordering must be enforced:
* `<default>` must appear **after all `<case>` elements** in a `<conditional>`.
* `<prerequisite>` and `<prerequisites>` must appear **before all other children** in a `<step>`.
* `<rule>` must appear **before prose and executable content** inside an `<instruction>`.

11. **Tool tags are direct children of containers.** `<read>`, `<write>`, `<edit>`, `<glob>`, `<grep>`, `<web-search>`, `<web-fetch>`, `<ask-user-question>`. They live as direct children of `<step>`, `<on-fail>`, `<on-pass>`, `<case>`, `<default>`, `<each>`, `<agents-in-parallel>`, or at root level (in stepless docs), never inside `<meta>`.
12. **`<description>` is placement-restricted and author-facing.** Never output or interpreted by the agent. It is a child of metadata (`<meta>`) or steps (`<step>`), limited to at most one description per parent. It is not a decorator like `<note>`.
13. **Sequences are strictly ordered Actions.** They map to the prescriptive Action category and do not permit mixed content (no prose), containing only `<command>` children.
14. **`<run>` is the execution verb (0.4.0 breaking change).** Replaces the former `<action>` element. Three forms: reference (`<run ref="cmd-id" />`), inline (`<run>command text</run>`), inline+capture (`<run set="var">command text</run>`). `<run>` is a direct child of `<step>`, `<gate>`, `<on-fail>`, `<on-pass>`, `<case>`, `<default>`, `<each>`, and `<agents-in-parallel>`. Inside `<gate>`, `<run ref>` must reference `<approved-gate-commands>`. Outside `<gate>`, `<run ref>` must reference `<approved-commands>`.
15. **`<instruction>` is mixed content.** Contains prose (text content) and guidance decorators (`<rule>`, `<principle>`, `<anti-pattern>`, `<reference>`). It does **not** contain `<run>`, `<output>`, tool tags, behavioral tags, or any structural/executable children. It is a narrative anchor providing interpretive context but is NOT executable. Note that `<note>` is allowed only at the step level and root `<ape>` level — not inside `<instruction>`, `<on-fail>`, or `<on-pass>`.
16. **`<criteria>` and `<criterion>` have explicit evaluation semantics.** `<criteria>` supports reference form (`<criteria ref="criteria-id" />` pointing to a named criteria) and compound form (`<criteria operator="and"><criterion ... /></criteria>`). `<criterion>` supports two modes: `ref` (reuses a named `<criteria>` or `<criterion>`) and `check` (evaluates a boolean expression like `{{ test_failures == 0 }}`). `ref` and `check` are mutually exclusive on `<criterion>`. A single `<criterion>` can appear directly in a `<gate>` without a `<criteria>` wrapper. Named criteria (`<criteria id="...">`) can be reused via `<criteria ref="criteria-id" />`. Gate evaluation uses `<run>` inside the gate to execute measurement commands, then `<criterion check>` to evaluate results.
16a. **`<goto>` is a structural navigation element.** Can appear inside `<gate>`, `<on-fail>`, `<on-pass>`, `<case>`, `<default>`, and `<each>` as an alternative to the `goto` attribute. Example: `<goto ref="step-id" />`.
16b. **`<var>` can contain `<command>` and `<resource>` children.** A `<command>` child computes the variable value; a `<resource>` child declares a dependency. Examples: `<var name="output"><command>npm test</command></var>` or `<var name="config"><resource ref="config-file" /></var>`.
17. **Templates define shape.** Part of prescriptive output within Actions. They contain content, format awareness, and interpolation but do not specify destination or placement within a file.
18. **Outputs define destination.** Prescriptive Action aspect. What gets produced, where it lands (`to`, `target`), and how it's applied (`anchor`, `position`). They are prescriptive declarations of intent.
19. **No XML comments.** APE documents must not contain XML comments (`<!-- -->`). Use `<note>` for author-facing context. Comments are invisible to validators, unsearchable by tooling, and add noise without contributing to the semantic execution model.
20. **Variables must have values.** A `<var>` declaration must carry a value via element content, `value` attribute, or `default` attribute. Do not declare empty variables as placeholders for runtime state—use implicit creation mechanics instead.
21. **Prerequisites are first and prose-free.** Scaffolds entry conditions (Flow Control). Prerequisites must appear before all other children within a `<step>`, serving as entry conditions evaluated before any work begins. `<prerequisite>` is a structure container—it must not contain prose. Conditions and recovery paths are expressed through attributes (`ref`, `goto`, `halt`) and optional structural children (`<check>`).
22. **Actors removed (0.3.0 breaking change).** The `<actor>`, `<actors>`, and `<responsibilities>` elements were removed in 0.3.0.
23. **`<principle>` is a standalone decorator.** `<principle>` can appear inside any block (step, action, gate, etc.), inside `<steps>`, and inside `<instruction>`. It is also allowed at root level. It provides system guidance about values and approaches. Like `<rule>`, it stands alone—proximity handles grouping.
24. **Every element can have optional `id` and `ref` attributes.** Structural elements (gate, step, criteria, on-fail, on-pass, conditional, case, default, each, sequence, steps) can all have optional `id` attributes for reference. Only `<reference>` cannot have an `id` attribute.
25. **No redundant guidance.** If a gate already enforces "tests must pass before proceeding," a `<rule>` restating that is redundant. If a `<rule>` says "never do X," an `<anti-pattern>` restating "doing X" is redundant. Each piece of guidance appears exactly once, in the element whose semantics best match its nature: binding requirements in `<rule>`, things to avoid in `<anti-pattern>`, guidance in `<principle>`, context in `<note>`. Structural enforcement (gates, prerequisites, conditionals) supersedes all of them—what structure enforces, prose must not restate.

---

## 25. Example: Complete 0.4.0 Workflow

Here is a complete workflow demonstrating the 0.4.0 model with `<IMPORTANT>`, `<approved-commands>`/`<approved-gate-commands>` split, `<run>` for execution, and expression-based criteria:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ape version="0.4.0" xmlns="https://ape-lang.dev/schema/0">

  <IMPORTANT>
  APE is an executable workflow. You are the runtime.

  <meta> block is configuration and declarations only.
  <instruction> tags are directives.
  <gate> tags evaluate a condition and route via on-fail (required) or on-pass.
  <run> tags execute commands, either inline or by reference.

  <approved-gate-commands> are the ONLY commands that can be executed from within a <gate> tag.
  </IMPORTANT>

  <meta>
    <name>Rust Testing Workflow</name>
    <description>Specification, red-green-refactor cycle for Rust projects.</description>

    <params>
      <param ref="project_root" default="." />
    </params>

    <resources>
      <resource id="cargo" type="executable" />
      <resource id="project" type="directory" path="{{ project_root }}" access="read,write,edit" />
    </resources>

    <approved-commands>
      <command id="test-all">cargo test --all-features</command>
      <command id="fmt-check">cargo fmt -- --check</command>
    </approved-commands>

    <approved-gate-commands>
      <command id="test-failure-count">cargo test --all-features 2>&amp;1 | awk '/test result:/ { for(i=1;i&lt;=NF;i++) if($i=="failed;") sum+=$(i-1) } END { print sum+0 }'</command>
      <command id="fmt-error-count">cargo fmt -- --check 2>&amp;1 | grep -c "^Diff in"</command>
    </approved-gate-commands>
  </meta>

  <steps>
    <step id="specification" number="1">
      <title>Specification</title>

      <instruction>
        Write a specification document or test outline that describes what the code should do.
        Be precise about inputs, outputs, and edge cases.
      </instruction>

      <ask-user-question var="spec_complete" type="confirm">
        Is your specification complete and committed?
      </ask-user-question>

      <gate>
        <criterion check="{{ spec_complete == true }}" />
        <on-fail goto="specification" />
      </gate>
    </step>

    <step id="red" number="2">
      <prerequisite ref="specification" goto="specification" />

      <title>Red Phase (Write Failing Tests)</title>

      <instruction>
        Create tests based on your specification. Run them to verify they fail.
        A failing test is your contract — it drives implementation.
      </instruction>

      <run ref="test-all" />

      <gate>
        <run ref="test-failure-count" set="test_failures" />
        <criterion check="{{ test_failures > 0 }}" />
        <on-fail goto="red" />
        <on-pass proceed="true" />
      </gate>
    </step>

    <step id="green" number="3">
      <prerequisite ref="red" goto="red" />

      <title>Green Phase (Make Tests Pass)</title>

      <instruction>
        Write the implementation. Focus on passing tests, not perfection.
        Once all tests pass, you can refactor with confidence.
      </instruction>

      <run ref="fmt-check" />
      <run ref="test-all" />

      <gate>
        <run ref="test-failure-count" set="test_failures" />
        <run ref="fmt-error-count" set="fmt_errors" />

        <criteria id="green-checks" operator="and">
          <criterion check="{{ test_failures == 0 }}" />
          <criterion check="{{ fmt_errors == 0 }}" />
        </criteria>
        <on-fail goto="green" />
      </gate>
    </step>

    <step id="refactor" number="4">
      <prerequisite ref="green" goto="green" />

      <title>Refactor Phase (Improve Code Quality)</title>

      <instruction>
        Refactor for readability and performance. Run tests frequently.
        The test suite is your safety net.
      </instruction>

      <ask-user-question var="refactor_done" type="confirm">
        Refactoring complete and all tests passing?
      </ask-user-question>

      <gate>
        <run ref="test-failure-count" set="test_failures" />

        <criteria operator="and">
          <criterion check="{{ refactor_done == true }}" />
          <criterion check="{{ test_failures == 0 }}" />
        </criteria>
        <on-fail goto="refactor" />
      </gate>
    </step>
  </steps>

</ape>
```

---

## 26. Migration from 0.3.0 to 0.4.0

### Breaking Changes

1. **`<action>` removed — replaced by `<run>`.** Delete all `<action>` elements. Replace `<action><command ref="X" /></action>` with `<run ref="X" />`. Replace `<action><command>inline cmd</command></action>` with `<run>inline cmd</run>`. Tool tags and behavioral tags become direct children of their containing step, handler, or case — no wrapper needed.

2. **`<preamble>` renamed to `<IMPORTANT>`.** Replace `<preamble>` with `<IMPORTANT>`. It is the only all-caps tag in APE. Must be the first child of `<ape>` if present. At most one per document. Prose content.

3. **`<commands>` replaced by `<approved-commands>` / `<approved-gate-commands>` split.** The plain `<commands>` wrapper is removed. Commands must be declared in one of two containers inside `<meta>`:
   * `<approved-commands>` — for step-level execution via `<run ref>`.
   * `<approved-gate-commands>` — for gate evaluation via `<run ref>` inside `<gate>`. Gate commands gather measurement data (counts, percentages) for criteria expressions.

4. **`<command>` is now declaration-only.** `<command>` no longer has `ref` or `set` attributes for execution. It exists only to declare named commands with `id` inside `<approved-commands>` or `<approved-gate-commands>` (or as a child of `<var>` for computed values). All execution is via `<run>`.

5. **`<criterion ref>` no longer resolves to actions or commands.** Since `<action>` is removed, `<criterion ref>` and `<criteria ref>` resolve only to named `<criteria>` or `<criterion>` IDs. Gate evaluation uses `<run>` inside the gate to execute measurement commands, then `<criterion check>` to evaluate results.

6. **`<agents-in-parallel>` wraps `<run>` elements.** Replace `<action>` children of `<agents-in-parallel>` with `<run>` elements (and tool tags).

### Migration Steps

1. Replace `<preamble>` with `<IMPORTANT>`.
2. Split `<commands>` into `<approved-commands>` and `<approved-gate-commands>`. Author gate-evaluation variants of each command that produce measurable output.
3. Replace every `<action id="X"><command ref="Y" /></action>` with `<run ref="Y" />`.
4. Replace every `<action><command>inline</command></action>` with `<run>inline</run>`.
5. Move tool tags and behavioral tags out of `<action>` wrappers — they become direct children of their container.
6. Replace `<criteria ref="action-id">` patterns with `<run>` + `<criterion check>` patterns inside gates.
7. Update `version` to `0.4.0`.

### Non-Breaking Improvements

* `<run>` is a clearer execution verb than `<action>`, reducing boilerplate.
* The `<approved-commands>` / `<approved-gate-commands>` split makes gate behavior easier to reason about, validate, and author safely.
* `<IMPORTANT>` is intentionally all-caps, making it visually prominent and unambiguous.


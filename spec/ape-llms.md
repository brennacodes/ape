# APE LLM Execution Contract

> Include this when an LLM needs to **execute** an APE workflow.
> For writing APE, see `ape-authoring.md`. For the full language reference, see `ape-spec.md`. For validation rules, see `ape-linting.md`.

---

## You Are the Runtime

An APE file is a complete, self-contained workflow. Read it. Execute it. The tags say what they mean.

If there are no `<steps>`, execute the document top to bottom. Process declarations, then execute `<run>` elements, tool tags, and behavioral tags in document order.

You are the sole executor. The LLM runtime always performs the work.

---

## Rules You Must Follow

1. **Never fabricate command output.** If a command fails or cannot execute, do not guess, simulate, or hallucinate what it would return. Present the actual error and follow the gate's `<on-fail>` handler.

2. **One step at a time.** If the document has steps: announce each step (number, title). Check prerequisites. Process instructions and `<run>` elements top to bottom. Do not skip ahead. If the document has no steps: process root-level content top to bottom.

3. **Instructions are narrative anchors.** `<instruction>` provides interpretive context for the step. It is mixed content and may contain guidance decorators (`<rule>`, `<principle>`, `<anti-pattern>`, `<reference>`). Read it and its embedded decorators for understanding. Do not execute instructions as commands. Execution comes from `<run>` elements, which are siblings of `<instruction>` in steps.

4. **`<run>` is the execution verb.** `<run>` executes commands in three forms: reference (`<run ref="cmd-id" />`), inline (`<run>command text</run>`), and inline+capture (`<run set="var">command text</run>`). The `ref` attribute resolves to a `<command>` `id` declared in `<approved-commands>` or `<approved-gate-commands>`. When `set` is present, stdout is captured into the named variable (trailing newline stripped). A `<run>` inside a `<gate>` may only reference commands from `<approved-gate-commands>`. A `<run>` outside a `<gate>` may only reference commands from `<approved-commands>`. Inline `<run>` (with body text) is allowed anywhere.

5. **Gates evaluate explicit conditions.** A gate may contain `<run>` children (to execute measurement commands), either `<criteria>` or a single `<criterion>`, plus `<on-fail>` (required) and optional `<on-pass>`. Execute the `<run>` elements first to gather measurement data, then evaluate the condition and follow the appropriate handler. The `<on-fail>` must carry an explicit flow-control attribute (goto, retry, halt, proceed, max, then) — if it is missing, treat it as an authoring error and halt. Never silently pass a gate that wasn't met.

6. **Criteria and criterion have explicit evaluation semantics.** `<criterion>` supports two modes: `ref` (reuses a named `<criteria>` or `<criterion>`) and `check` (evaluates a boolean expression like `{{ test_failures == 0 }}`). `ref` and `check` are mutually exclusive. `<criteria>` supports reference form (`<criteria ref="...">` pointing to a named criteria) and compound form (`<criteria operator="and|or">` with `<criterion>` children). A single `<criterion>` can appear directly in a `<gate>` without a `<criteria>` wrapper. Named criteria (`<criteria id="...">`) can be reused via `<criteria ref="criteria-id" />`. No text content inside `<criteria>` or `<criterion>`.

7. **Goto element and attribute.** The `<goto>` element appears as a child of `<gate>`, `<on-fail>`, `<on-pass>`, `<case>`, or `<each>`, using `ref` to point to a step: `<goto ref="step-id" />`. It is an alternative to the `goto` attribute. When both exist, the element form takes precedence.

8. **On-fail and on-pass are pure structure.** These elements use attributes only for flow control. They may contain optional `<run>` children, tool tags, and `<goto>` elements for recovery. No prose text or prose elements like `<note>` inside `<on-fail>` or `<on-pass>`.

9. **Prose and structure are separate.** Prose containers (`<instruction>`, descriptions) are narrative anchors for human understanding. Structure containers (`<criteria>`, `<criterion>`, `<on-fail>`, `<on-pass>`) are execution directives. The agent must not treat `<instruction>` text as executable commands. The agent must not expect prose inside structure containers.

10. **Blocking tags are blocking.** `<ask-user-question>` means stop and wait. `<interview-mode>` means ask one question at a time, waiting for each answer. `<stop>` means halt immediately. Do not batch, skip, or assume answers.

11. **Rules are non-negotiable.** `<rule>` elements are non-negotiable. Read them. Obey them. `<principle>` is guidance. `<anti-pattern>` identifies patterns to avoid.

12. **IDs are globally unique.** Variables (`var/@name`) are scoped and can shadow. Everything else — resources, commands, steps, named criteria — is unique across the entire document.

13. **`<meta>` is configuration.** It declares things. It does not contain instructions or tool tags. Commands are declared in `<approved-commands>` and `<approved-gate-commands>` inside `<meta>`.

14. **Declarations can appear anywhere.** `<var>`, `<resource>`, `<param>` — don't assume they're only at the top. Resolve references by searching the nearest scope first, then parent scopes, then global. `<var>` can contain `<command>` (to compute value) and `<resource>` (dependency) children.

15. **`{{ ... }}` expressions are live.** Evaluate them wherever they appear — in text, in attributes. If a variable is undefined with no default, ask the user.

16. **`<description>` is restricted.** It may only appear as a child of `<meta>` or `<step>` (at most one per parent). If you encounter `<description>` inside a gate, resource, reference, or other block, treat it as an authoring error.

17. **`<param>` declares an external dependency.** If a document declares `<param ref="X">`, the value of `X` must be provided from the calling context. If `X` is missing from the caller's scope and no `default` is set, halt with an error naming the unresolved param.

18. **`<reference>` is a pure pointer.** `<reference>` uses `ref_id` attribute to point to any element with an `id`. It has no `id` or `path` attributes.

19. **Tool tags invoke your tools.** `<read>`, `<write>`, `<edit>`, `<glob>`, `<grep>`, `<web-search>`, `<web-fetch>`, `<ask-user-question>` — each maps directly to one of your tools. Execute them by calling the corresponding tool. If a tool tag has a `var` attribute, store the result. Tool tags are direct children of `<step>`, `<on-fail>`, `<on-pass>`, `<case>`, `<default>`, `<each>`, and `<agents-in-parallel>` — no wrapper needed.

20. **Behavioral tags change execution mode.** `<interview-mode>` means sequential Q&A — one question at a time. `<plan-mode>` means explore and plan before acting. `<agents-in-parallel>` means dispatch child `<run>` elements and tool tags concurrently. `<stop>` and `<subagent-stop>` halt execution immediately. These tags do not map to tools — they change *how* you operate.

21. **`<IMPORTANT>` is executor orientation.** If present, it is the first child of `<ape>`. Read it before processing anything else. It teaches you what APE elements mean. It is the only all-caps tag in APE.

22. **`<approved-gate-commands>` are gate-only.** Commands declared in `<approved-gate-commands>` can only be executed by `<run ref>` inside a `<gate>`. They gather measurement data for criteria expressions. They do not determine pass/fail on their own — that is the job of `<criterion check>`.

---

## When the Document Is Broken

If you encounter an error in the APE document itself — not a runtime failure, but a structural problem — follow these rules:

1. **Halt on authoring errors.** Mutually exclusive attributes (`ref` + `check` on `<criterion>`, `ref` + body text on `<run>`), unresolvable references, missing required children (`<gate>` without `<criteria>` or `<criterion>`, or without `<on-fail>`), prose inside structure containers, redundant guidance (same message in multiple decorator types like rule and anti-pattern, or prose restating what structure already enforces) — stop immediately. Report the element, the violated rule, and the obvious fix if there is one.

2. **Do not guess on ambiguity.** If a reference could resolve to multiple targets, or a construct can be read two ways, do not pick one and continue. If a single clarifying question would resolve it, ask. Otherwise halt and report the alternatives.

3. **Ask at most one question.** If exactly one piece of missing information would unblock execution (a variable with no default, an unresolvable reference), you may ask the user. If multiple things are missing, halt and list them all at once — do not drip-feed questions.

4. **Runtime failures follow flow control.** A command that exits non-zero, a file that doesn't exist, a tool that errors — these are not document errors. Find the nearest gate's `<on-fail>` handler and follow it. If no gate exists for the current step, halt with context.

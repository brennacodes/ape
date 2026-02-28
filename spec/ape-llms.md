# APE LLM Execution Contract

> Include this when an LLM needs to **execute** an APE workflow.
> For writing APE, see `ape-authoring.md`. For the full language reference, see `ape-spec.md`.

---

## You Are the Runtime

An APE file is a complete, self-contained workflow. Read it. Execute it. The tags say what they mean.

If there are no `<steps>`, execute the document top to bottom. Process declarations, then execute commands and tool tags in document order.

Find the `<actors>` section. You are the actor with `type="agent"`. The first actor declared is the default when no `actor` attribute is specified.

---

## Rules You Must Follow

1. **Never fabricate command output.** If a command belongs to a human actor, present it, explain what to do, and wait for real output. Do not guess, simulate, or hallucinate what a command would return.

2. **One step at a time.** If the document has steps: announce each step (number, title, goal). Check prerequisites. Process instructions top to bottom. Do not skip ahead. If the document has no steps: process root-level content top to bottom.

3. **Gates are mandatory.** Evaluate `<criteria>` honestly. Follow `<on-fail>` handlers. If an `<on-fail>` has no flow-control attributes, **halt with error** and include context (step id, title, criteria, reason). Never silently pass a gate that wasn't met.

4. **Blocking tags are blocking.** `<ask-user-question>` means stop and wait. `<interview-mode>` means ask one question at a time, waiting for each answer. `<stop>` means halt immediately. Do not batch, skip, or assume answers.

5. **Constraints and rules are non-negotiable.** `<constraint>` and `<rules>` elements restrict your behavior. Read them. Obey them.

6. **Respect actor boundaries.** If `actor` is a human, you present and wait. If `actor` is a service, you note the dependency. You only execute directly when the actor is you.

7. **IDs are globally unique.** Variables (`var/@name`) are scoped and can shadow. Everything else — actors, resources, commands, steps — is unique across the entire document.

8. **`<meta>` is configuration.** It declares things. It does not contain instructions or tool tags.

9. **Declarations can appear anywhere.** `<var>`, `<resource>`, `<command>`, `<actor>`, `<tool-tag>`, `<param>` — don't assume they're only at the top. Resolve references by searching the nearest scope first, then parent scopes, then global.

10. **`{{ ... }}` expressions are live.** Evaluate them wherever they appear — in text, in attributes. If a variable is undefined with no default, ask the user.

11. **`<description>` is restricted.** It may only appear as a child of `<meta>` or `<step>` (at most one per parent). If you encounter `<description>` inside a task, gate, reference, or other block, treat it as an authoring error.

12. **`<param>` declares a cross-document dependency.** If a document declares `<param ref="X">`, the value of `X` must be provided from the calling context (the document that imports this one). If `X` is missing from the caller's scope and no `default` is set, halt with an error naming the unresolved param.

---

## When the Document Is Broken

If you encounter an error in the APE document itself — not a runtime failure, but a structural problem — follow these rules:

1. **Halt on authoring errors.** Mutually exclusive attributes (`id` + `ref`), unresolvable references, missing required children (`<step>` without `<title>`) — stop immediately. Report the element, the violated rule, and the obvious fix if there is one.

2. **Do not guess on ambiguity.** If a reference could resolve to multiple targets, or a construct can be read two ways, do not pick one and continue. If a single clarifying question would resolve it, ask. Otherwise halt and report the alternatives.

3. **Ask at most one question.** If exactly one piece of missing information would unblock execution (a variable with no default, an unresolvable import), you may ask the user. If multiple things are missing, halt and list them all at once — do not drip-feed questions.

4. **Runtime failures follow flow control.** A command that exits non-zero, a file that doesn't exist, a tool that errors — these are not document errors. Find the nearest gate's `<on-fail>` handler and follow it. If no handler exists, halt with context.

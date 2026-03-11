# Test Configs

Each YAML file under `test-configs/` defines the **behavioral checks** for one workflow source. The evaluator runs these checks against a session trace to measure whether the LLM adhered to the workflow instructions.

## What these are for

The benchmark tests whether the same workflow instructions produce different levels of adherence when encoded in different formats (plain-text, markdown, ad-hoc XML, APE). Test configs define what "adherence" means in concrete, measurable terms.

There is one test config per workflow, shared across all formats. The checks encode what the workflow _says_ — the format is how it _says it_. If format matters, the same checks will produce different pass rates across formats.

## File structure

```yaml
fixture_id: centminmod          # Must match the workflow fixture stem
description: >
  What this workflow emphasizes and what's in/out of scope for checks.

phase_classification:
  ordered:                      # Must appear in this sequence when detected
    - investigation
    - implementation
    - scoping
    - verification
  floating:                     # May appear at any point
    - search_strategy

phase_tool_mapping:
  investigation:
    signals: [tool_call.file_read, tool_call.search]
    position: before_implementation
  # ... one entry per phase

checks:
  - id: unique_check_id
    phase: investigation
    description: What behavior this verifies
    type: constraint            # gate | constraint | sequential_step | workflow_order | command_adherence
    prompt_condition: ...       # Optional — skip this check when condition is false
    condition:
      metric: tool_call.search
      operator: exists_before
      target: tool_call.file_write
```

## The inclusion criteria for checks

Every check must pass this question: **"Would the pass rate for this check plausibly differ across instruction formats?"**

Include checks where:
- The workflow instruction creates **tension** with the LLM's natural tendency (e.g. "search before editing" when the LLM might just dive in)
- Adherence requires the LLM to **notice and follow** a specific instruction, not just do what it would do anyway
- The check has a realistic chance of both passing and failing across runs

Exclude checks that:
- **Ceiling** — pass regardless of format because the behavior is natural (e.g. "make more than one tool call batch" always passes for non-trivial tasks)
- **Can't fire** — gated on a prompt condition that no current prompt sets
- **Trigger unreliably** — depend on the LLM doing something no prompt requests (e.g. committing)
- **Are undetectable** — the phase has no programmatic signal in traces

## Phases

### `phase_classification`

Only include phases that have either:
- **Active checks** that can fire with current prompts, OR
- **Detectable signals** that contribute meaningfully to the `phase_ordering` check

Don't include phases that can't be detected (no tool signal) or whose only checks are dead/ceiling. They inflate the ordered list without contributing to format comparison.

### `phase_tool_mapping`

Maps phases to trace signals the evaluator uses to detect when a phase is active. Key fields:

| Field | Purpose |
|-------|---------|
| `signals` | Trace event types whose presence indicates this phase |
| `position` | Disambiguates phases sharing the same signal type (e.g. `file_read` in investigation vs. verification) |
| `repeatable` | Phase can appear more than once (e.g. implementation/verification loops) |
| `match` | Optional string filter on signal args |

## Checks

### Fields

| Field | Required | Purpose |
|-------|----------|---------|
| `id` | Yes | Unique identifier |
| `phase` | Yes | Which phase this belongs to |
| `description` | Yes | Human-readable behavior being verified |
| `type` | Yes | Structural role: `gate`, `constraint`, `sequential_step`, `workflow_order`, `command_adherence` |
| `condition` | Yes | The evaluable assertion (metric + operator + target) |
| `prompt_condition` | No | Skip this check when the named condition is false. Negate with `!` prefix. |
| `timing` | No | `any` (default) or `task_completed` |
| `iteration` | No | For repeatable phases: `any`, `each`, or `last` |

### `prompt_condition` gating

Checks can be conditionally skipped based on prompt-declared conditions. This is how checks adapt to different prompt categories without separate test configs per prompt.

Examples:
- `prompt_condition: references_file_path` — only run this check when the prompt mentions a file path
- `prompt_condition: "!explicit_feature_requested"` — skip this check when the prompt explicitly asks for a new feature

Missing conditions default to false. Only conditions declared as true in the prompt file (or regex-resolved from the prompt text) will activate a check.

## Adding a new test config

1. Create `test-configs/{workflow_stem}.yml`
2. The `fixture_id` must match the workflow fixture stem under `fixtures/{format}/`
3. For each check, ask: "Does this check plausibly differentiate formats?" If not, leave it out.
4. Document excluded phases/checks in comments so future readers understand the decisions

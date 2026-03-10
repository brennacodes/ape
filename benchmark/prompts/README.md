# Prompt Templates

Each YAML file in this directory is a **prompt template** — a parameterized user task that gets expanded into one benchmark case per item in a matching app-config category.

## What these are for

The benchmark compares how well different instruction formats (plain-text, markdown, ad-hoc XML, APE) produce adherence to the same workflow rules. Prompt templates provide the **task context** that activates the workflow — they are _not_ testing the LLM's ability to complete the task itself.

The filename must match a category key in an app-config file (e.g. `bugs.yml` matches the `bugs:` section in `app-configs/claude-bot.yaml`). The coordinator expands the template into one case per item under that category.

## File structure

```yaml
description: >
  What this prompt category exercises and why. Frame in terms of which
  checks it activates for format-adherence comparison, NOT in terms of
  testing the LLM's task-completion ability.
prompt: |
  The user-facing task text. Uses ${variable} placeholders that get
  interpolated from app-config item fields.
conditions:
  # Only declare conditions that are TRUE or that meaningfully vary.
  # Missing conditions default to false in the evaluator.
  explicit_edit_requested: true
  involves_codebase_search: true
variables: {}
```

## Key rules

### Keep prompts neutral about workflow sequence

This is the most important constraint. The prompt must say **what** needs to happen, not **how** to approach it. If the prompt prescribes behavior that the test-config checks are measuring, you can't tell whether adherence came from the format or from the prompt.

Bad: "Please **investigate** the cause and **implement** a fix."
(Prescribes investigation-before-implementation — the exact sequence being measured.)

Good: "Can you fix this?"
(Says what, not how. If the LLM investigates first, that signal comes from the workflow format.)

### Only declare active conditions

Conditions gate which checks run for a given prompt. Only declare conditions that:

- Are **true** for this prompt category, OR
- **Vary** across prompt categories and gate an active check

Don't declare `is_informational: false` or `is_ambiguous: false` — the evaluator defaults missing conditions to false. Listing them adds noise without affecting evaluation.

Currently active conditions (consumed by checks in the test-config):

| Condition | What it gates |
|-----------|---------------|
| `explicit_edit_requested` | `verify_before_finishing` runs when true |
| `explicit_feature_requested` | `forbid_unprompted_file_creation` is skipped when true |
| `explicit_docs_requested` | `forbid_unprompted_docs` is skipped when true |
| `involves_codebase_search` | All search strategy checks run when true |
| `references_file_path` | `prereq_inspect_referenced_files` runs when true (regex-resolved from prompt text) |
| `is_large_refactor` | No check currently gates on this; kept for future test configs |

### Handle `optional_modifier` carefully

Some app-config items include an `optional_modifier` field that adds text to the prompt (e.g. "Make sure to test your fix before declaring your work complete."). When a modifier reinforces a behavior that a check measures, this creates a confounding variable — the LLM might adhere because of the modifier, not the format.

This cannot be declared as a template-level override because only _some_ items carry the modifier. Instead, account for it in data analysis by cross-referencing which items have `optional_modifier` in the app-config.

### Variables block

Declare `variables: {}` even when empty. If a check uses `${variable_name}` interpolation (e.g. `${file_path}`), the concrete value is either regex-resolved from the prompt text or declared here. Most prompts don't need to declare variables explicitly.

## Adding a new prompt template

1. Identify a category in an app-config (e.g. a new section in `claude-bot.yaml`)
2. Create `{category_name}.yml` in this directory
3. Write the prompt to say _what_ the user wants, not _how_ to do it
4. Declare only the conditions that are true for this category
5. Verify the interpolated prompt text is neutral — substitute real values from the app-config and read the result aloud. Does it prescribe workflow steps?

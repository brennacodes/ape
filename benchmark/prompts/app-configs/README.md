# App Configs

Each YAML file in this directory describes a **test app fixture** and the known issues, untested areas, architectural concerns, and feature opportunities within it. The filename must match an app fixture directory name under `benchmark/fixtures/apps/` (e.g. `claude-bot.yaml` matches `fixtures/apps/claude-bot/`).

## What these are for

App configs are the **variable source** for prompt templates. When the coordinator finds a prompt template whose filename matches a category key in an app-config (e.g. `bugs.yml` matches the `bugs:` section here), it expands the template into one benchmark case per item under that category, interpolating the item's fields into the prompt's `${variable}` placeholders.

## File structure

```yaml
app:
  name: my-app                    # Must match the fixtures/apps/ directory name
  description: What the app is    # Human context
  location: /path/to/source       # Where the original source lives
  commands:                       # Optional — how to run/test the app
    start: "npm start"
    test: "npm test"

# Each top-level key (other than 'app') is a CATEGORY.
# Category names must match a prompt template filename to be expanded.
bugs:
  item_id:                        # Unique key within the category
    description: ...              # Full technical description
    presentation: ...             # User-facing symptom (interpolated into prompt)
    location: file.js:25          # Where in the code (documentation)
    impact: ...                   # What goes wrong (documentation)
    optional_modifier: "..."      # Extra text appended to the prompt (optional)
```

## Field roles

Fields serve two purposes — **interpolation** and **documentation**. Which fields get interpolated depends on the prompt template that matches the category.

### Currently interpolated fields (by category)

| Category | Prompt template uses | Remaining fields are documentation |
|----------|---------------------|------------------------------------|
| `bugs` | `${presentation}`, `${optional_modifier}` | `description`, `location`, `impact` |
| `architectural_issues` | `${description}` | `locations`, `risk` |
| `new_features` | `${feature}` | `impact` |

### Documentation fields

Fields like `location`, `impact`, `risk`, and `description` (when not interpolated) exist to document the known issue for humans authoring and reviewing the benchmark. They are **not** noise — they capture the ground truth about each item so you can evaluate whether the LLM found the right thing.

Keep them. They may also become interpolation targets for future prompt templates or analysis tooling.

### `optional_modifier`

An optional field that appends extra text to the prompt. Use this to create natural variation in prompt reinforcement across items within a category.

Be aware: if the modifier reinforces a behavior that a test-config check measures (e.g. "Make sure to test your fix" reinforces the `verify_before_finishing` check), this creates a confounding variable for format comparison. Not all items in a category need this field — having a mix of items with and without the modifier is useful for analysis.

## Adding items to an existing category

1. Add a new key under the category with a descriptive `item_id`
2. Include all fields the prompt template interpolates (check the template's `${variable}` placeholders)
3. Include documentation fields (`location`, `impact`, `risk`, etc.) for ground-truth reference
4. The coordinator will automatically expand the prompt template into a new case for this item

## Adding a new category

1. Add a new top-level section to the app-config with items as sub-keys
2. Create a matching prompt template in `benchmark/prompts/{category_name}.yml`
3. Make sure the prompt template's `${variable}` placeholders match field names in your items
4. Fields not referenced by the template are documentation — include them for human context

## Adding a new app

1. Place the app source code under `benchmark/fixtures/apps/{app_name}/`
2. Create `benchmark/prompts/app-configs/{app_name}.yaml`
3. Add an `app:` section with `name` matching the directory name
4. Add categories with items — each category should match an existing prompt template filename, or create a new template for it

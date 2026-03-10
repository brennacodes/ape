"""
Discover and compose benchmark test cases from the directory structure.

The benchmark tests how the same workflow instructions perform when encoded
in different formats (plain-text, markdown, adhoc-xml, ape) against real
apps with realistic prompts.

Directory layout:
  benchmark/
    fixtures/
      apps/{app_name}/          # App codebases Claude works in
      {format}/{stem}.{ext}     # Workflow instructions in various formats
    test-configs/{format}/{stem}.yml  # Behavioral expectations per workflow
    prompts/{prompt_id}.yml     # Prompt templates or concrete prompts
    prompts/app-configs/{app}.yaml  # App fixture configs (variables for templates)

Test case dimensions:
  Every test case is identified by 5 independent dimensions:
    - app:      which app fixture (e.g. "claude-bot")
    - category: prompt category matching an app-config section (e.g. "bugs")
    - item:     specific item within the category (e.g. "hardcoded_cli_path")
    - format:   workflow format (e.g. "plain-text")
    - workflow:  workflow stem (e.g. "centminmod")

  Template prompts expand into one case per app-config item.
  Concrete prompts (no matching app-config category) work as before.

Public API
----------
discover_apps(benchmark_root) -> list[AppFixture]
discover_workflows(benchmark_root) -> list[WorkflowFixture]
discover_test_configs(benchmark_root) -> list[TestConfigPath]
discover_prompts(benchmark_root) -> list[PromptPath]
discover_app_configs(benchmark_root) -> list[AppConfig]
match_cases(apps, workflows, configs, prompts, app_configs) -> list[TestCase]
load_test_config(path) -> dict
load_prompt(path) -> dict
load_app_config(path) -> dict
interpolate_prompt(template, variables) -> str
build_context(prompt_data, config_data, workspace_state, app_config_variables) -> dict
"""

from __future__ import annotations

import re
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Discovery types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AppFixture:
    """A discovered app fixture (directory under fixtures/apps/)."""
    path: Path
    name: str       # directory name, e.g. "claude-bot"


@dataclass(frozen=True)
class WorkflowFixture:
    """A discovered workflow fixture file."""
    path: Path
    stem: str       # e.g. "centminmod"
    format: str     # e.g. "plain-text", "markdown", "adhoc-xml", "ape"


@dataclass(frozen=True)
class TestConfigPath:
    """A discovered test-config file."""
    __test__ = False
    path: Path
    stem: str
    format: str


@dataclass(frozen=True)
class PromptPath:
    """A discovered prompt file."""
    path: Path
    prompt_id: str  # filename stem, e.g. "bugs" or "centminmod-bug-fix"


@dataclass(frozen=True)
class AppConfig:
    """A discovered app-config file from prompts/app-configs/."""
    path: Path
    app_name: str   # filename stem, should match an AppFixture.name


@dataclass(frozen=True)
class TestCase:
    """One runnable benchmark case: app + workflow + test-config + prompt.

    For template cases, category and item_id identify which app-config item
    was expanded into this case, and app_config_path points to the source file.
    For concrete (non-template) cases, these fields are empty/None.
    """
    __test__ = False
    app: AppFixture
    workflow: WorkflowFixture
    test_config: TestConfigPath
    prompt: PromptPath
    category: str = ""
    item_id: str = ""
    app_config_path: Path | None = None

    @property
    def case_id(self) -> str:
        if self.category and self.item_id:
            return (
                f"{self.app.name}/{self.category}/{self.item_id}"
                f"/{self.workflow.format}/{self.workflow.stem}"
            )
        return f"{self.app.name}/{self.workflow.stem}/{self.workflow.format}/{self.prompt.prompt_id}"

    @property
    def dimensions(self) -> dict[str, str]:
        """Return all dimension values for this case."""
        return {
            "app": self.app.name,
            "category": self.category,
            "item": self.item_id,
            "format": self.workflow.format,
            "workflow": self.workflow.stem,
        }

    def matches_filter(self, **filters: str) -> bool:
        """Return True if this case matches all non-empty filter values."""
        dims = self.dimensions
        return all(
            dims.get(k) == v
            for k, v in filters.items()
            if v  # skip empty/None filters
        )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_apps(benchmark_root: Path) -> list[AppFixture]:
    """Find all app directories under benchmark_root/fixtures/apps/.

    Each subdirectory of apps/ is treated as an app fixture.
    """
    apps_dir = benchmark_root / "fixtures" / "apps"
    if not apps_dir.is_dir():
        return []

    results = []
    for d in sorted(apps_dir.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            results.append(AppFixture(path=d, name=d.name))
    return results


def discover_workflows(benchmark_root: Path) -> list[WorkflowFixture]:
    """Find all workflow fixture files under benchmark_root/fixtures/{format}/.

    Skips the apps/ directory (those are app fixtures, not workflows).
    """
    fixtures_dir = benchmark_root / "fixtures"
    if not fixtures_dir.is_dir():
        return []

    results = []
    for format_dir in sorted(fixtures_dir.iterdir()):
        if not format_dir.is_dir():
            continue
        if format_dir.name == "apps":
            continue
        fmt = format_dir.name
        for f in sorted(format_dir.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                results.append(WorkflowFixture(path=f, stem=f.stem, format=fmt))
    return results


def discover_test_configs(benchmark_root: Path) -> list[TestConfigPath]:
    """Find all test-config YAML files under benchmark_root/test-configs/{format}/."""
    configs_dir = benchmark_root / "test-configs"
    if not configs_dir.is_dir():
        return []

    results = []
    for format_dir in sorted(configs_dir.iterdir()):
        if not format_dir.is_dir():
            continue
        fmt = format_dir.name
        for f in sorted(format_dir.iterdir()):
            if f.is_file() and f.suffix in (".yml", ".yaml"):
                results.append(TestConfigPath(path=f, stem=f.stem, format=fmt))
    return results


def discover_prompts(benchmark_root: Path) -> list[PromptPath]:
    """Find all prompt YAML files under benchmark_root/prompts/.

    Scans the immediate prompts/ directory only (not subdirectories
    like app-configs/).
    """
    prompts_dir = benchmark_root / "prompts"
    if not prompts_dir.is_dir():
        return []

    results = []
    for f in sorted(prompts_dir.iterdir()):
        if f.is_file() and f.suffix in (".yml", ".yaml"):
            results.append(PromptPath(path=f, prompt_id=f.stem))
    return results


def discover_app_configs(benchmark_root: Path) -> list[AppConfig]:
    """Find all app-config YAML files under benchmark_root/prompts/app-configs/."""
    configs_dir = benchmark_root / "prompts" / "app-configs"
    if not configs_dir.is_dir():
        return []

    results = []
    for f in sorted(configs_dir.iterdir()):
        if f.is_file() and f.suffix in (".yml", ".yaml"):
            results.append(AppConfig(path=f, app_name=f.stem))
    return results


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _extract_categories(app_config_data: dict[str, Any]) -> dict[str, dict[str, dict]]:
    """Extract category -> {item_id -> item_data} from app-config data.

    Skips the top-level 'app' key and any non-dict values.
    Returns only sections where items are dicts (category sections).
    """
    categories: dict[str, dict[str, dict]] = {}
    for key, value in app_config_data.items():
        if key == "app":
            continue
        if not isinstance(value, dict):
            continue
        # Check that the values are dicts (items), not scalars
        items = {}
        for item_id, item_data in value.items():
            if isinstance(item_data, dict):
                items[item_id] = item_data
        if items:
            categories[key] = items
    return categories


def match_cases(
    apps: list[AppFixture],
    workflows: list[WorkflowFixture],
    configs: list[TestConfigPath],
    prompts: list[PromptPath],
    app_configs: list[AppConfig] | None = None,
) -> list[TestCase]:
    """Compose TestCases from apps, workflows, configs, prompts, and app-configs.

    Matching rules:
    1. Workflow and config must share the same stem AND format.
    2. For each app, check if any prompt's filename stem matches a category
       in that app's app-config. If so, the prompt is a template — expand it
       into one case per item under that category.
    3. Prompts that don't match any category are treated as concrete prompts
       and crossed with every app (original behavior).

    Workflows or configs without a match are silently skipped.
    An empty apps or prompts list means no cases are produced.
    """
    config_index: dict[tuple[str, str], TestConfigPath] = {}
    for c in configs:
        config_index[(c.stem, c.format)] = c

    # Build app-config index: app_name -> (path, parsed categories)
    ac_index: dict[str, tuple[Path, dict[str, dict[str, dict]]]] = {}
    if app_configs:
        for ac in app_configs:
            try:
                data = load_app_config(ac.path)
                categories = _extract_categories(data)
                if categories:
                    ac_index[ac.app_name] = (ac.path, categories)
            except Exception:
                pass

    # Collect all category names across all app-configs
    all_categories: set[str] = set()
    for _, categories in ac_index.values():
        all_categories.update(categories.keys())

    # Separate template prompts from concrete prompts
    template_prompts: dict[str, PromptPath] = {}  # category -> prompt
    concrete_prompts: list[PromptPath] = []
    for p in prompts:
        if p.prompt_id in all_categories:
            template_prompts[p.prompt_id] = p
        else:
            concrete_prompts.append(p)

    cases = []
    for wf in workflows:
        config = config_index.get((wf.stem, wf.format))
        if config is None:
            continue

        for app in apps:
            # Concrete prompts: cross-product as before
            for prompt in concrete_prompts:
                cases.append(TestCase(
                    app=app, workflow=wf,
                    test_config=config, prompt=prompt,
                ))

            # Template prompts: expand per app-config item
            ac_entry = ac_index.get(app.name)
            if ac_entry is None:
                continue
            ac_path, categories = ac_entry
            for category, items in categories.items():
                prompt = template_prompts.get(category)
                if prompt is None:
                    continue
                for item_id in items:
                    cases.append(TestCase(
                        app=app, workflow=wf,
                        test_config=config, prompt=prompt,
                        category=category,
                        item_id=item_id,
                        app_config_path=ac_path,
                    ))

    return cases


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_test_config(path: Path) -> dict[str, Any]:
    """Load and return a test-config YAML file as a dict."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_prompt(path: Path) -> dict[str, Any]:
    """Load and return a prompt YAML file as a dict."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_app_config(path: Path) -> dict[str, Any]:
    """Load and return an app-config YAML file as a dict."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_app_config_variables(
    app_config_data: dict[str, Any],
    category: str,
    item_id: str,
) -> dict[str, Any]:
    """Extract interpolation variables for a specific app-config item.

    Returns the item's own fields plus app-level fields prefixed with 'app_'.
    """
    variables: dict[str, Any] = {}

    # App-level variables (available as app_name, app_description, etc.)
    app_data = app_config_data.get("app", {})
    if isinstance(app_data, dict):
        for k, v in app_data.items():
            if not isinstance(v, dict):  # skip nested dicts like commands
                variables[f"app_{k}"] = v

    # Item-level variables (available directly as presentation, location, etc.)
    category_data = app_config_data.get(category, {})
    if isinstance(category_data, dict):
        item_data = category_data.get(item_id, {})
        if isinstance(item_data, dict):
            variables.update(item_data)

    return variables


def interpolate_prompt(template: str, variables: dict[str, Any]) -> str:
    """Replace ${var} references in a prompt template with variable values.

    Undefined variables resolve to empty string. Excess whitespace from
    empty substitutions is collapsed.
    """
    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        value = variables.get(var_name, "")
        return str(value) if value else ""

    result = re.sub(r'\$\{([^}]+)\}', _replace, template)
    # Collapse runs of spaces (from empty substitutions) but preserve newlines
    result = re.sub(r'[^\S\n]+', ' ', result)
    # Trim trailing spaces on each line
    result = re.sub(r' +$', '', result, flags=re.MULTILINE)
    return result.strip()


def build_context(
    prompt_data: dict[str, Any],
    config_data: dict[str, Any] | None = None,
    workspace_state: dict[str, Any] | None = None,
    app_config_variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the evaluator context dict from a loaded prompt and test-config.

    If app_config_variables is provided, they are merged into the context's
    variables dict (app-config values take precedence over prompt-declared ones).
    """
    variables = dict(prompt_data.get("variables", {}))
    if app_config_variables:
        variables.update(app_config_variables)

    ctx: dict[str, Any] = {
        "conditions": prompt_data.get("conditions", {}),
        "variables": variables,
        "phase_tool_mapping": {},
        "phase_classification": {},
        "workspace_state": workspace_state or {},
    }

    if config_data:
        phase_class = config_data.get("phase_classification", {})
        ctx["phase_tool_mapping"] = config_data.get("phase_tool_mapping", {})
        ctx["phase_classification"] = phase_class
        if "ordered" in phase_class:
            variables["phase_classification.ordered"] = phase_class["ordered"]

    return ctx

"""Tests for benchmark/scripts/coordinator/coordinator.py.

Uses tmp_path fixtures to build synthetic directory trees for discovery tests.
YAML loading tests use the real benchmark files.
"""

import pytest
from pathlib import Path

from coordinator import (
    AppFixture,
    WorkflowFixture,
    TestConfigPath,
    PromptPath,
    TestCase,
    discover_apps,
    discover_workflows,
    discover_test_configs,
    discover_prompts,
    match_cases,
    load_test_config,
    load_prompt,
    build_context,
)


# ===========================================================================
# Helpers
# ===========================================================================

def _write(path: Path, content: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_benchmark_tree(root: Path):
    """Create a minimal benchmark directory structure."""
    # Apps
    (root / "fixtures" / "apps" / "myapp").mkdir(parents=True)
    _write(root / "fixtures" / "apps" / "myapp" / "main.py", "print('hello')")
    # Workflows
    _write(root / "fixtures" / "plain-text" / "centminmod.txt", "fixture content")
    _write(root / "fixtures" / "ape" / "centminmod.xml", "<ape/>")
    # Test configs
    _write(root / "test-configs" / "centminmod.yml", "fixture_id: centminmod\nchecks: []")
    # Prompts
    _write(root / "prompts" / "bug-fix.yml", "id: bug-fix\nprompt: fix the bug\nconditions: {}\nvariables: {}")
    _write(root / "prompts" / "refactor.yml", "id: refactor\nprompt: refactor it\nconditions: {}\nvariables: {}")


# ===========================================================================
# Data types
# ===========================================================================

class TestDataTypes:
    def test_app_fixture_fields(self):
        af = AppFixture(path=Path("a/myapp"), name="myapp")
        assert af.name == "myapp"

    def test_workflow_fixture_fields(self):
        wf = WorkflowFixture(path=Path("a/b.txt"), stem="b", format="plain-text")
        assert wf.stem == "b"
        assert wf.format == "plain-text"

    def test_test_config_path_fields(self):
        tcp = TestConfigPath(path=Path("a/b.yml"), stem="b")
        assert tcp.stem == "b"

    def test_prompt_path_fields(self):
        pp = PromptPath(path=Path("a/b.yml"), prompt_id="b")
        assert pp.prompt_id == "b"

    def test_test_case_id(self):
        tc = TestCase(
            app=AppFixture(Path("a/myapp"), "myapp"),
            workflow=WorkflowFixture(Path("f"), "centminmod", "plain-text"),
            test_config=TestConfigPath(Path("c"), "centminmod"),
            prompt=PromptPath(Path("p"), "bug-fix"),
        )
        assert tc.case_id == "myapp/centminmod/plain-text/bug-fix"

    def test_test_case_id_includes_source(self):
        tc = TestCase(
            app=AppFixture(Path("a/myapp"), "myapp"),
            workflow=WorkflowFixture(Path("f"), "centminmod", "markdown"),
            test_config=TestConfigPath(Path("c"), "centminmod"),
            prompt=PromptPath(Path("p"), "bug-fix"),
            source="prompt",
        )
        assert tc.case_id == "myapp/centminmod/markdown/prompt/bug-fix"

    def test_test_case_id_template_includes_source(self):
        tc = TestCase(
            app=AppFixture(Path("a/myapp"), "myapp"),
            workflow=WorkflowFixture(Path("f"), "centminmod", "markdown"),
            test_config=TestConfigPath(Path("c"), "centminmod"),
            prompt=PromptPath(Path("p"), "bugs"),
            category="bugs",
            item_id="silent_yaml_failure",
            source="claude-md",
        )
        assert tc.case_id == (
            "myapp/bugs/silent_yaml_failure/markdown/claude-md/centminmod"
        )

    def test_test_case_id_no_workflow_omits_source(self):
        tc = TestCase(
            app=AppFixture(Path("a/myapp"), "myapp"),
            workflow=WorkflowFixture(Path("f"), "centminmod", "no-workflow"),
            test_config=TestConfigPath(Path("c"), "centminmod"),
            prompt=PromptPath(Path("p"), "bugs"),
            category="bugs",
            item_id="silent_yaml_failure",
            source="",
        )
        assert tc.case_id == (
            "myapp/bugs/silent_yaml_failure/no-workflow/centminmod"
        )

    def test_dimensions_includes_source(self):
        tc = TestCase(
            app=AppFixture(Path("a/myapp"), "myapp"),
            workflow=WorkflowFixture(Path("f"), "centminmod", "ape"),
            test_config=TestConfigPath(Path("c"), "centminmod"),
            prompt=PromptPath(Path("p"), "bug-fix"),
            source="prompt",
        )
        assert tc.dimensions["source"] == "prompt"

    def test_matches_filter_source_filter(self):
        wf = WorkflowFixture(Path("f"), "cm", "markdown")
        tc_md = TestCase(
            app=AppFixture(Path("a"), "myapp"), workflow=wf,
            test_config=TestConfigPath(Path("c"), "cm"),
            prompt=PromptPath(Path("p"), "bug"),
            source="claude-md",
        )
        tc_pr = TestCase(
            app=AppFixture(Path("a"), "myapp"), workflow=wf,
            test_config=TestConfigPath(Path("c"), "cm"),
            prompt=PromptPath(Path("p"), "bug"),
            source="prompt",
        )
        assert tc_md.matches_filter(source="claude-md")
        assert not tc_md.matches_filter(source="prompt")
        assert tc_pr.matches_filter(source="prompt")
        assert not tc_pr.matches_filter(source="claude-md")

    def test_matches_filter_no_workflow_ignores_source(self):
        wf = WorkflowFixture(Path("f"), "cm", "no-workflow")
        tc = TestCase(
            app=AppFixture(Path("a"), "myapp"), workflow=wf,
            test_config=TestConfigPath(Path("c"), "cm"),
            prompt=PromptPath(Path("p"), "bug"),
            source="",
        )
        # no-workflow is the null baseline — appears regardless of filter
        assert tc.matches_filter(source="prompt")
        assert tc.matches_filter(source="claude-md")
        assert tc.matches_filter()

    def test_frozen_workflow_fixture(self):
        wf = WorkflowFixture(Path("a"), "b", "c")
        with pytest.raises(AttributeError):
            wf.stem = "changed"

    def test_frozen_test_config_path(self):
        tcp = TestConfigPath(Path("a"), "b")
        with pytest.raises(AttributeError):
            tcp.stem = "changed"

    def test_frozen_prompt_path(self):
        pp = PromptPath(Path("a"), "b")
        with pytest.raises(AttributeError):
            pp.prompt_id = "changed"


# ===========================================================================
# discover_apps
# ===========================================================================

class TestDiscoverApps:
    def test_finds_apps(self, tmp_path):
        _make_benchmark_tree(tmp_path)
        results = discover_apps(tmp_path)
        assert len(results) == 1
        assert results[0].name == "myapp"

    def test_empty_when_no_apps_dir(self, tmp_path):
        assert discover_apps(tmp_path) == []

    def test_skips_hidden_dirs(self, tmp_path):
        (tmp_path / "fixtures" / "apps" / ".hidden").mkdir(parents=True)
        (tmp_path / "fixtures" / "apps" / "real").mkdir(parents=True)
        results = discover_apps(tmp_path)
        assert len(results) == 1
        assert results[0].name == "real"


# ===========================================================================
# discover_workflows
# ===========================================================================

class TestDiscoverWorkflows:
    def test_finds_workflows(self, tmp_path):
        _make_benchmark_tree(tmp_path)
        results = discover_workflows(tmp_path)
        assert len(results) == 2
        stems = {r.stem for r in results}
        assert stems == {"centminmod"}
        formats = {r.format for r in results}
        assert formats == {"plain-text", "ape"}

    def test_sorted_by_format_then_stem(self, tmp_path):
        _write(tmp_path / "fixtures" / "ape" / "z.txt")
        _write(tmp_path / "fixtures" / "ape" / "a.txt")
        _write(tmp_path / "fixtures" / "plain-text" / "m.txt")
        results = discover_workflows(tmp_path)
        assert [r.format for r in results] == ["ape", "ape", "plain-text"]
        assert results[0].stem == "a"
        assert results[1].stem == "z"

    def test_empty_when_no_fixtures_dir(self, tmp_path):
        assert discover_workflows(tmp_path) == []

    def test_empty_when_fixtures_dir_empty(self, tmp_path):
        (tmp_path / "fixtures").mkdir()
        assert discover_workflows(tmp_path) == []

    def test_skips_hidden_files(self, tmp_path):
        _write(tmp_path / "fixtures" / "plain-text" / ".DS_Store")
        _write(tmp_path / "fixtures" / "plain-text" / "real.txt")
        results = discover_workflows(tmp_path)
        assert len(results) == 1
        assert results[0].stem == "real"

    def test_skips_apps_dir(self, tmp_path):
        _make_benchmark_tree(tmp_path)
        results = discover_workflows(tmp_path)
        # Should not include anything from fixtures/apps/
        assert all(r.format != "apps" for r in results)


# ===========================================================================
# discover_test_configs
# ===========================================================================

class TestDiscoverTestConfigs:
    def test_finds_configs(self, tmp_path):
        _make_benchmark_tree(tmp_path)
        results = discover_test_configs(tmp_path)
        assert len(results) == 1
        assert results[0].stem == "centminmod"

    def test_only_yml_files(self, tmp_path):
        _write(tmp_path / "test-configs" / "good.yml")
        _write(tmp_path / "test-configs" / "good2.yaml")
        _write(tmp_path / "test-configs" / "bad.txt")
        results = discover_test_configs(tmp_path)
        assert len(results) == 2

    def test_empty_when_no_dir(self, tmp_path):
        assert discover_test_configs(tmp_path) == []

    def test_sorted(self, tmp_path):
        _write(tmp_path / "test-configs" / "z.yml")
        _write(tmp_path / "test-configs" / "a.yml")
        results = discover_test_configs(tmp_path)
        assert results[0].stem == "a"
        assert results[1].stem == "z"


# ===========================================================================
# discover_prompts
# ===========================================================================

class TestDiscoverPrompts:
    def test_finds_prompts(self, tmp_path):
        _make_benchmark_tree(tmp_path)
        results = discover_prompts(tmp_path)
        assert len(results) == 2
        ids = {r.prompt_id for r in results}
        assert ids == {"bug-fix", "refactor"}

    def test_only_yml_files(self, tmp_path):
        _write(tmp_path / "prompts" / "good.yml")
        _write(tmp_path / "prompts" / "bad.txt")
        results = discover_prompts(tmp_path)
        assert len(results) == 1

    def test_empty_when_no_dir(self, tmp_path):
        assert discover_prompts(tmp_path) == []

    def test_sorted_by_id(self, tmp_path):
        _write(tmp_path / "prompts" / "z.yml")
        _write(tmp_path / "prompts" / "a.yml")
        results = discover_prompts(tmp_path)
        assert results[0].prompt_id == "a"
        assert results[1].prompt_id == "z"


# ===========================================================================
# match_cases
# ===========================================================================

class TestMatchCases:
    def test_matches_by_stem(self):
        apps = [AppFixture(Path("a/myapp"), "myapp")]
        workflows = [
            WorkflowFixture(Path("f/plain-text/cm.txt"), "cm", "plain-text"),
            WorkflowFixture(Path("f/ape/cm.xml"), "cm", "ape"),
        ]
        configs = [TestConfigPath(Path("c/cm.yml"), "cm")]
        prompts = [PromptPath(Path("p/bug.yml"), "bug")]
        cases = match_cases(apps, workflows, configs, prompts)
        # Each format now doubles across the source dimension.
        assert len(cases) == 4
        case_ids = {c.case_id for c in cases}
        assert "myapp/cm/plain-text/claude-md/bug" in case_ids
        assert "myapp/cm/plain-text/prompt/bug" in case_ids
        assert "myapp/cm/ape/claude-md/bug" in case_ids
        assert "myapp/cm/ape/prompt/bug" in case_ids

    def test_cross_product_with_prompts(self):
        apps = [AppFixture(Path("a/myapp"), "myapp")]
        workflows = [WorkflowFixture(Path("f"), "cm", "plain-text")]
        configs = [TestConfigPath(Path("c"), "cm")]
        prompts = [
            PromptPath(Path("p1"), "bug"),
            PromptPath(Path("p2"), "refactor"),
        ]
        cases = match_cases(apps, workflows, configs, prompts)
        # 2 prompts × 2 sources
        assert len(cases) == 4

    def test_cross_product_with_apps(self):
        apps = [
            AppFixture(Path("a/app1"), "app1"),
            AppFixture(Path("a/app2"), "app2"),
        ]
        workflows = [WorkflowFixture(Path("f"), "cm", "plain-text")]
        configs = [TestConfigPath(Path("c"), "cm")]
        prompts = [PromptPath(Path("p"), "bug")]
        cases = match_cases(apps, workflows, configs, prompts)
        # 2 apps × 2 sources
        assert len(cases) == 4

    def test_unmatched_workflow_skipped(self):
        apps = [AppFixture(Path("a/myapp"), "myapp")]
        workflows = [WorkflowFixture(Path("f"), "cm", "plain-text")]
        configs = [TestConfigPath(Path("c"), "other")]
        prompts = [PromptPath(Path("p"), "bug")]
        cases = match_cases(apps, workflows, configs, prompts)
        assert len(cases) == 0

    def test_empty_inputs(self):
        assert match_cases([], [], [], []) == []
        assert match_cases(
            [AppFixture(Path("a"), "myapp")],
            [WorkflowFixture(Path("f"), "cm", "pt")],
            [TestConfigPath(Path("c"), "cm")],
            [],
        ) == []

    def test_full_cross_product(self):
        # 2 apps × 2 workflows × 3 prompts × 2 sources = 24 cases
        # (1 config shared across both formats)
        apps = [
            AppFixture(Path("a/app1"), "app1"),
            AppFixture(Path("a/app2"), "app2"),
        ]
        workflows = [
            WorkflowFixture(Path("f"), "cm", "plain-text"),
            WorkflowFixture(Path("f"), "cm", "ape"),
        ]
        configs = [TestConfigPath(Path("c"), "cm")]
        prompts = [
            PromptPath(Path("p"), "p1"),
            PromptPath(Path("p"), "p2"),
            PromptPath(Path("p"), "p3"),
        ]
        cases = match_cases(apps, workflows, configs, prompts)
        assert len(cases) == 24

    def test_sources_emitted_for_each_format(self):
        apps = [AppFixture(Path("a/myapp"), "myapp")]
        workflows = [WorkflowFixture(Path("f"), "cm", "markdown")]
        configs = [TestConfigPath(Path("c"), "cm")]
        prompts = [PromptPath(Path("p"), "bug")]
        cases = match_cases(apps, workflows, configs, prompts)
        sources = sorted(c.source for c in cases)
        assert sources == ["claude-md", "prompt"]

    def test_no_workflow_emitted_once_with_empty_source(self):
        apps = [AppFixture(Path("a/myapp"), "myapp")]
        workflows = [WorkflowFixture(Path("f"), "cm", "no-workflow")]
        configs = [TestConfigPath(Path("c"), "cm")]
        prompts = [PromptPath(Path("p"), "bug")]
        cases = match_cases(apps, workflows, configs, prompts)
        assert len(cases) == 1
        assert cases[0].source == ""

    def test_source_filter_reduces_correctly(self):
        apps = [AppFixture(Path("a/myapp"), "myapp")]
        workflows = [
            WorkflowFixture(Path("f"), "cm", "markdown"),
            WorkflowFixture(Path("f"), "cm", "ape"),
            WorkflowFixture(Path("f"), "cm", "no-workflow"),
        ]
        configs = [TestConfigPath(Path("c"), "cm")]
        prompts = [PromptPath(Path("p"), "bug")]
        cases = match_cases(apps, workflows, configs, prompts)
        # 2 format × 2 sources + 1 no-workflow = 5
        assert len(cases) == 5
        filtered = [c for c in cases if c.matches_filter(source="prompt")]
        # markdown/prompt, ape/prompt, plus the no-workflow baseline.
        assert len(filtered) == 3
        sources = sorted(c.source for c in filtered)
        assert sources == ["", "prompt", "prompt"]


# ===========================================================================
# load_test_config
# ===========================================================================

class TestLoadTestConfig:
    def test_loads_yaml(self, tmp_path):
        f = tmp_path / "config.yml"
        f.write_text("fixture_id: test\nchecks:\n  - id: c1\n")
        data = load_test_config(f)
        assert data["fixture_id"] == "test"
        assert len(data["checks"]) == 1

    def test_loads_real_config(self):
        real = Path(__file__).resolve().parent.parent.parent / "test-configs" / "centminmod.yml"
        if real.exists():
            data = load_test_config(real)
            assert data["fixture_id"] == "centminmod"
            assert "checks" in data
            assert len(data["checks"]) > 0


# ===========================================================================
# load_prompt
# ===========================================================================

class TestLoadPrompt:
    def test_loads_yaml(self, tmp_path):
        f = tmp_path / "prompt.yml"
        f.write_text("id: test\nprompt: do something\nconditions:\n  is_ambiguous: false\n")
        data = load_prompt(f)
        assert data["id"] == "test"
        assert data["prompt"] == "do something"
        assert data["conditions"]["is_ambiguous"] is False

    def test_loads_real_prompt(self):
        real = Path(__file__).resolve().parent.parent.parent / "prompts" / "centminmod-bug-fix.yml"
        if real.exists():
            data = load_prompt(real)
            assert data["id"] == "centminmod-bug-fix"
            assert "prompt" in data
            assert "conditions" in data


# ===========================================================================
# build_context
# ===========================================================================

class TestBuildContext:
    def test_extracts_conditions_and_variables(self):
        prompt_data = {
            "id": "test",
            "prompt": "fix",
            "conditions": {"is_informational": False},
            "variables": {"file_path": "src/main.py"},
        }
        ctx = build_context(prompt_data)
        assert ctx["conditions"]["is_informational"] is False
        assert ctx["variables"]["file_path"] == "src/main.py"

    def test_defaults_to_empty_dicts(self):
        prompt_data = {"id": "test", "prompt": "fix"}
        ctx = build_context(prompt_data)
        assert ctx["conditions"] == {}
        assert ctx["variables"] == {}

    def test_does_not_include_extra_fields(self):
        prompt_data = {
            "id": "test",
            "prompt": "fix",
            "conditions": {},
            "variables": {},
            "overrides": [{"check_id": "c1"}],
        }
        ctx = build_context(prompt_data)
        assert "overrides" not in ctx
        assert "id" not in ctx
        assert "prompt" not in ctx

    def test_preserves_all_conditions(self):
        conditions = {
            "is_informational": False,
            "is_ambiguous": True,
            "references_file_path": True,
        }
        ctx = build_context({"conditions": conditions})
        assert ctx["conditions"] == conditions

    def test_preserves_all_variables(self):
        variables = {
            "file_path": "src/main.py",
            "test_command": "pytest",
        }
        ctx = build_context({"variables": variables})
        assert ctx["variables"] == variables

    def test_includes_phase_config_from_config_data(self):
        prompt_data = {"id": "test", "prompt": "fix"}
        config_data = {
            "phase_classification": {
                "ordered": ["investigation", "implementation"],
                "floating": ["tool_use"],
            },
            "phase_tool_mapping": {
                "investigation": {"signals": ["tool_call.search"], "position": "before_implementation"},
            },
        }
        ctx = build_context(prompt_data, config_data)
        assert ctx["phase_classification"]["ordered"] == ["investigation", "implementation"]
        assert "investigation" in ctx["phase_tool_mapping"]
        # phase_classification.ordered also exposed as a variable for interpolation
        assert ctx["variables"]["phase_classification.ordered"] == ["investigation", "implementation"]

    def test_includes_workspace_state(self):
        prompt_data = {"id": "test", "prompt": "fix"}
        ws = {"modified_files": ["a.py"], "git_status": "M a.py"}
        ctx = build_context(prompt_data, workspace_state=ws)
        assert ctx["workspace_state"] == ws

    def test_empty_context_has_phase_and_workspace_keys(self):
        ctx = build_context({"id": "test", "prompt": "fix"})
        assert ctx["phase_tool_mapping"] == {}
        assert ctx["phase_classification"] == {}
        assert ctx["workspace_state"] == {}

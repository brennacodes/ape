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
    _write(root / "test-configs" / "plain-text" / "centminmod.yml", "fixture_id: centminmod\nchecks: []")
    _write(root / "test-configs" / "ape" / "centminmod.yml", "fixture_id: centminmod\nchecks: []")
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
        tcp = TestConfigPath(path=Path("a/b.yml"), stem="b", format="ape")
        assert tcp.stem == "b"
        assert tcp.format == "ape"

    def test_prompt_path_fields(self):
        pp = PromptPath(path=Path("a/b.yml"), prompt_id="b")
        assert pp.prompt_id == "b"

    def test_test_case_id(self):
        tc = TestCase(
            app=AppFixture(Path("a/myapp"), "myapp"),
            workflow=WorkflowFixture(Path("f"), "centminmod", "plain-text"),
            test_config=TestConfigPath(Path("c"), "centminmod", "plain-text"),
            prompt=PromptPath(Path("p"), "bug-fix"),
        )
        assert tc.case_id == "myapp/centminmod/plain-text/bug-fix"

    def test_frozen_workflow_fixture(self):
        wf = WorkflowFixture(Path("a"), "b", "c")
        with pytest.raises(AttributeError):
            wf.stem = "changed"

    def test_frozen_test_config_path(self):
        tcp = TestConfigPath(Path("a"), "b", "c")
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
        assert len(results) == 2
        stems = {r.stem for r in results}
        assert stems == {"centminmod"}

    def test_only_yml_files(self, tmp_path):
        _write(tmp_path / "test-configs" / "plain-text" / "good.yml")
        _write(tmp_path / "test-configs" / "plain-text" / "good2.yaml")
        _write(tmp_path / "test-configs" / "plain-text" / "bad.txt")
        results = discover_test_configs(tmp_path)
        assert len(results) == 2

    def test_empty_when_no_dir(self, tmp_path):
        assert discover_test_configs(tmp_path) == []

    def test_sorted(self, tmp_path):
        _write(tmp_path / "test-configs" / "plain-text" / "z.yml")
        _write(tmp_path / "test-configs" / "plain-text" / "a.yml")
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
    def test_matches_by_stem_and_format(self):
        apps = [AppFixture(Path("a/myapp"), "myapp")]
        workflows = [
            WorkflowFixture(Path("f/plain-text/cm.txt"), "cm", "plain-text"),
            WorkflowFixture(Path("f/ape/cm.xml"), "cm", "ape"),
        ]
        configs = [
            TestConfigPath(Path("c/plain-text/cm.yml"), "cm", "plain-text"),
            TestConfigPath(Path("c/ape/cm.yml"), "cm", "ape"),
        ]
        prompts = [PromptPath(Path("p/bug.yml"), "bug")]
        cases = match_cases(apps, workflows, configs, prompts)
        assert len(cases) == 2
        case_ids = {c.case_id for c in cases}
        assert "myapp/cm/plain-text/bug" in case_ids
        assert "myapp/cm/ape/bug" in case_ids

    def test_cross_product_with_prompts(self):
        apps = [AppFixture(Path("a/myapp"), "myapp")]
        workflows = [WorkflowFixture(Path("f"), "cm", "plain-text")]
        configs = [TestConfigPath(Path("c"), "cm", "plain-text")]
        prompts = [
            PromptPath(Path("p1"), "bug"),
            PromptPath(Path("p2"), "refactor"),
        ]
        cases = match_cases(apps, workflows, configs, prompts)
        assert len(cases) == 2

    def test_cross_product_with_apps(self):
        apps = [
            AppFixture(Path("a/app1"), "app1"),
            AppFixture(Path("a/app2"), "app2"),
        ]
        workflows = [WorkflowFixture(Path("f"), "cm", "plain-text")]
        configs = [TestConfigPath(Path("c"), "cm", "plain-text")]
        prompts = [PromptPath(Path("p"), "bug")]
        cases = match_cases(apps, workflows, configs, prompts)
        assert len(cases) == 2

    def test_unmatched_workflow_skipped(self):
        apps = [AppFixture(Path("a/myapp"), "myapp")]
        workflows = [WorkflowFixture(Path("f"), "cm", "plain-text")]
        configs = [TestConfigPath(Path("c"), "other", "plain-text")]
        prompts = [PromptPath(Path("p"), "bug")]
        cases = match_cases(apps, workflows, configs, prompts)
        assert len(cases) == 0

    def test_unmatched_format_skipped(self):
        apps = [AppFixture(Path("a/myapp"), "myapp")]
        workflows = [WorkflowFixture(Path("f"), "cm", "plain-text")]
        configs = [TestConfigPath(Path("c"), "cm", "ape")]
        prompts = [PromptPath(Path("p"), "bug")]
        cases = match_cases(apps, workflows, configs, prompts)
        assert len(cases) == 0

    def test_empty_inputs(self):
        assert match_cases([], [], [], []) == []
        assert match_cases(
            [AppFixture(Path("a"), "myapp")],
            [WorkflowFixture(Path("f"), "cm", "pt")],
            [TestConfigPath(Path("c"), "cm", "pt")],
            [],
        ) == []

    def test_full_cross_product(self):
        # 2 apps × 2 workflows × 3 prompts = 12 cases
        apps = [
            AppFixture(Path("a/app1"), "app1"),
            AppFixture(Path("a/app2"), "app2"),
        ]
        workflows = [
            WorkflowFixture(Path("f"), "cm", "plain-text"),
            WorkflowFixture(Path("f"), "cm", "ape"),
        ]
        configs = [
            TestConfigPath(Path("c"), "cm", "plain-text"),
            TestConfigPath(Path("c"), "cm", "ape"),
        ]
        prompts = [
            PromptPath(Path("p"), "p1"),
            PromptPath(Path("p"), "p2"),
            PromptPath(Path("p"), "p3"),
        ]
        cases = match_cases(apps, workflows, configs, prompts)
        assert len(cases) == 12  # 2 apps × 2 matched pairs × 3 prompts


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
        real = Path(__file__).resolve().parent.parent.parent / "test-configs" / "plain-text" / "centminmod.yml"
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

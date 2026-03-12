"""
Tests for agents.plan_builder — the shared plan-building and run-ID utilities.
"""

import pytest

from agents.plan_builder import (
    ApprovedPlan,
    build_approved_plan,
    derive_target_path,
    infer_mapping_id,
    resolve_project_structure,
    select_structure_key,
    stable_run_id,
)


# ---------------------------------------------------------------------------
# stable_run_id
# ---------------------------------------------------------------------------

class TestStableRunId:
    def test_deterministic(self):
        """Same inputs always produce the same ID."""
        a = stable_run_id("ActionHistory", "/src/ActionHistory", "simpler_grants")
        b = stable_run_id("ActionHistory", "/src/ActionHistory", "simpler_grants")
        assert a == b

    def test_different_targets(self):
        """Different targets produce different IDs."""
        a = stable_run_id("X", "/src/X", "simpler_grants")
        b = stable_run_id("X", "/src/X", "hrsa_pprs")
        assert a != b

    def test_format(self):
        rid = stable_run_id("ActionHistory", "/src/ActionHistory", "simpler_grants")
        assert rid.startswith("conv-")
        parts = rid.split("-")
        assert len(parts) >= 3
        # Target abbreviation for simpler_grants should be "sg"
        assert "-sg-" in rid

    def test_hrsa_pprs_abbreviation(self):
        rid = stable_run_id("Foo", "/src/Foo", "hrsa_pprs")
        assert "-hp-" in rid

    def test_slug_truncated(self):
        long_name = "A" * 50
        rid = stable_run_id(long_name, "/src/X", "simpler_grants")
        # slug limited to 20 chars
        slug_part = rid.split("-")[1]  # "conv-{slug}-..."
        assert len(slug_part) <= 20


# ---------------------------------------------------------------------------
# infer_mapping_id
# ---------------------------------------------------------------------------

class TestInferMappingId:
    def test_angular_component(self):
        assert infer_mapping_id("Angular 2 Component", "frontend") == "MAP-001"

    def test_angular_service(self):
        assert infer_mapping_id("Angular 2 Service", "frontend") == "MAP-002"

    def test_api_controller(self):
        assert infer_mapping_id("Area API Controller", "backend") == "MAP-003"

    def test_repository(self):
        assert infer_mapping_id("Repository", "database") == "MAP-004"

    def test_default_frontend(self):
        assert infer_mapping_id("UnknownPattern", "frontend") == "MAP-001"

    def test_default_backend(self):
        assert infer_mapping_id("UnknownPattern", "backend") == "MAP-003"

    def test_default_database(self):
        assert infer_mapping_id("UnknownPattern", "database") == "MAP-004"


# ---------------------------------------------------------------------------
# resolve_project_structure
# ---------------------------------------------------------------------------

class TestResolveProjectStructure:
    def test_no_override(self):
        skillset = {
            "project_structure": {
                "frontend": {"components_root": "fe/src/"}
            }
        }
        result = resolve_project_structure(skillset, "project_structure")
        assert result == {"frontend": {"components_root": "fe/src/"}}

    def test_override_merges(self):
        skillset = {
            "project_structure": {
                "frontend": {"components_root": "fe/src/", "pages_root": "fe/pages/"},
                "backend":  {"api_root": "api/"},
            }
        }
        override = {
            "frontend": {"components_root": "custom/"},
        }
        result = resolve_project_structure(skillset, "project_structure", override)
        # components_root overridden, pages_root preserved
        assert result["frontend"]["components_root"] == "custom/"
        assert result["frontend"]["pages_root"] == "fe/pages/"
        # backend untouched
        assert result["backend"]["api_root"] == "api/"

    def test_override_adds_new_section(self):
        skillset = {"project_structure": {"frontend": {"x": "1"}}}
        override = {"database": {"migrations_root": "db/"}}
        result = resolve_project_structure(skillset, "project_structure", override)
        assert "database" in result
        assert result["database"]["migrations_root"] == "db/"


# ---------------------------------------------------------------------------
# select_structure_key
# ---------------------------------------------------------------------------

class TestSelectStructureKey:
    def test_target_specific_key_exists(self):
        skillset = {"project_structure_hrsa_pprs": {}}
        assert select_structure_key(skillset, "hrsa_pprs") == "project_structure_hrsa_pprs"

    def test_fallback_to_generic(self):
        skillset = {"project_structure": {}}
        assert select_structure_key(skillset, "simpler_grants") == "project_structure"

    def test_hrsa_alias(self):
        # When no exact key but target contains "hrsa_pprs"
        skillset = {}
        assert select_structure_key(skillset, "hrsa_pprs") == "project_structure_hrsa_pprs"


# ---------------------------------------------------------------------------
# derive_target_path
# ---------------------------------------------------------------------------

class TestDeriveTargetPath:
    def test_frontend_node(self):
        node = {"id": "Feature/MyComponent.ts", "type": "frontend", "exports": ["MyComponent"]}
        result = derive_target_path(node, {}, {})
        assert result.endswith("MyComponent.tsx")
        assert "feature" in result.lower()

    def test_backend_node(self):
        node = {"id": "Feature/UserService.cs", "type": "backend", "exports": ["UserService"]}
        result = derive_target_path(node, {}, {})
        assert result.endswith("_routes.py")
        assert "user_service" in result

    def test_database_node(self):
        node = {"id": "Feature/DataRepo.cs", "type": "database", "exports": ["DataRepo"]}
        result = derive_target_path(node, {}, {})
        assert result.endswith("_service.py")
        assert "data_repo" in result

    def test_custom_struct(self):
        node = {"id": "Feat/X.ts", "type": "frontend", "exports": ["X"]}
        struct = {"frontend": {"components_root": "app/ui/{feature_name}/"}}
        result = derive_target_path(node, {}, struct)
        assert result.startswith("app/ui/feat/")


# ---------------------------------------------------------------------------
# build_approved_plan
# ---------------------------------------------------------------------------

class TestBuildApprovedPlan:
    @pytest.fixture
    def simple_graph(self):
        return {
            "feature_name": "TestFeature",
            "nodes": [
                {"id": "TestFeature/Comp.ts", "type": "frontend", "pattern": "Angular 2 Component", "exports": ["Comp"]},
                {"id": "TestFeature/Ctrl.cs", "type": "backend", "pattern": "Area API Controller", "exports": ["Ctrl"], "endpoints": ["/api/test"]},
            ],
        }

    @pytest.fixture
    def simple_config(self):
        return {
            "skillset": {"project_structure": {}},
            "mappings_index": {
                "MAP-001": {"id": "MAP-001", "notes": "Component mapping"},
                "MAP-003": {"id": "MAP-003", "notes": "Controller mapping"},
            },
        }

    def test_returns_correct_keys(self, simple_graph, simple_config):
        plan = build_approved_plan(
            simple_graph, simple_config, "conv-test-001",
            "/src/Test", "/output/Test", "simpler_grants",
        )
        assert plan["feature_name"] == "TestFeature"
        assert plan["run_id"] == "conv-test-001"
        assert plan["target"] == "simpler_grants"
        assert "conversion_steps" in plan

    def test_steps_sorted(self, simple_graph, simple_config):
        plan = build_approved_plan(
            simple_graph, simple_config, "conv-test-001",
            "/src/Test", "/output/Test",
        )
        step_ids = [s["id"] for s in plan["conversion_steps"]]
        assert step_ids == sorted(step_ids)

    def test_backend_step_has_rule_001(self, simple_graph, simple_config):
        plan = build_approved_plan(
            simple_graph, simple_config, "conv-test-001",
            "/src/Test", "/output/Test",
        )
        backend_steps = [s for s in plan["conversion_steps"] if s["id"].startswith("Step B")]
        assert len(backend_steps) == 1
        assert "RULE-001" in backend_steps[0]["rule_ids"]

    def test_frontend_step_has_rule_002(self, simple_graph, simple_config):
        plan = build_approved_plan(
            simple_graph, simple_config, "conv-test-001",
            "/src/Test", "/output/Test",
        )
        fe_steps = [s for s in plan["conversion_steps"] if s["id"].startswith("Step C")]
        assert len(fe_steps) == 1
        assert "RULE-002" in fe_steps[0]["rule_ids"]

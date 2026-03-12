"""
Tests for run_agent.py — job-to-args conversion and YAML loader.
"""

import argparse
import tempfile
from pathlib import Path

import pytest

# run_agent.py defines _job_to_args and _load_yaml at module level.
# Import them after ensuring sys.path includes the repo root.
import sys
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from run_agent import _job_to_args, _load_yaml


# ---------------------------------------------------------------------------
# _load_yaml (both PyYAML and fallback parser)
# ---------------------------------------------------------------------------

class TestLoadYaml:
    def test_simple_key_value(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("key: value\ncount: 42\n", encoding="utf-8")
        result = _load_yaml(f)
        assert result["key"] == "value"
        assert result["count"] == 42

    def test_nested_sections(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text(
            "pipeline:\n  feature_name: Foo\n  mode: plan\n",
            encoding="utf-8",
        )
        result = _load_yaml(f)
        assert "pipeline" in result
        assert result["pipeline"]["feature_name"] == "Foo"
        assert result["pipeline"]["mode"] == "plan"

    def test_boolean_coercion(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("a: true\nb: false\nc: yes\nd: no\n", encoding="utf-8")
        result = _load_yaml(f)
        assert result["a"] is True
        assert result["b"] is False
        assert result["c"] is True
        assert result["d"] is False

    def test_null_coercion(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("a: null\nb: ~\n", encoding="utf-8")
        result = _load_yaml(f)
        assert result["a"] is None
        assert result["b"] is None


# ---------------------------------------------------------------------------
# _job_to_args
# ---------------------------------------------------------------------------

class TestJobToArgs:
    def test_basic_fields(self):
        job = {
            "pipeline": {
                "feature_root": "/src/Feature",
                "feature_name": "MyFeature",
                "mode": "plan",
                "target": "hrsa_pprs",
            },
        }
        ns = _job_to_args(job)
        assert ns.feature_root == "/src/Feature"
        assert ns.feature_name == "MyFeature"
        assert ns.mode == "plan"
        assert ns.target == "hrsa_pprs"

    def test_defaults(self):
        ns = _job_to_args({})
        assert ns.mode == "plan"
        assert ns.target == "simpler_grants"
        assert ns.dry_run is False
        assert ns.auto_approve is False
        assert ns.no_llm is False

    def test_overrides_win(self):
        job = {"pipeline": {"dry_run": False}}
        ns = _job_to_args(job, overrides={"dry_run": True})
        assert ns.dry_run is True

    def test_feature_name_from_root(self):
        job = {"pipeline": {"feature_root": "/code/src/ActionHistory"}}
        ns = _job_to_args(job)
        assert ns.feature_name == "ActionHistory"

    def test_llm_section(self):
        job = {
            "llm": {
                "provider": "anthropic",
                "model": "claude-opus-4-5",
                "no_llm": True,
            }
        }
        ns = _job_to_args(job)
        assert ns.llm_provider == "anthropic"
        assert ns.llm_model == "claude-opus-4-5"
        assert ns.no_llm is True

    def test_orchestration_config(self):
        job = {
            "orchestration": {
                "enabled": True,
                "backend": "google_adk",
                "max_plan_revisions": 5,
            }
        }
        ns = _job_to_args(job)
        assert ns.orchestration_config["enabled"] is True
        assert ns.orchestration_config["backend"] == "google_adk"
        assert ns.orchestration_config["max_plan_revisions"] == 5

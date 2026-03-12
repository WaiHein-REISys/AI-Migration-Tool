"""
Shared plan-building utilities
===============================
Functions that are used by both ``main.py`` and ``OrchestratorAgent`` to build
the ``approved_plan`` dict, derive run IDs, infer mappings, and resolve
project-structure overrides.

Keeping them in one module prevents drift between the two call sites.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, TypedDict


# ---------------------------------------------------------------------------
# Typed structures for key data shapes
# ---------------------------------------------------------------------------

class ConversionStep(TypedDict, total=False):
    """Shape of a single conversion step inside an approved plan."""

    id: str
    description: str
    source_file: str
    target_file: str
    mapping_id: str
    rule_ids: list[str]
    rationale: str
    # Knowledge-extraction metadata (optional)
    source_imports: list[str]
    source_hooks: list[str]
    file_type: str
    component_type: str
    source_lang: str
    feature_name: str


class ApprovedPlan(TypedDict):
    """Shape of the ``approved_plan`` dict consumed by ConversionAgent."""

    feature_name: str
    feature_root: str
    output_root: str
    run_id: str
    target: str
    project_structure: dict[str, Any]
    conversion_steps: list[ConversionStep]


class PipelineState(TypedDict, total=False):
    """Shape of the shared ``state`` dict passed through orchestrator actions."""

    config: dict[str, Any]
    dependency_graph: dict[str, Any]
    run_id: str
    feature_name: str
    feature_root: str | None
    output_root: str | None
    target: str
    project_structure_override: dict[str, Any] | None
    target_root: str | None
    mode: str
    dry_run: bool
    auto_approve: bool
    memory_context: Any
    approved_plan: ApprovedPlan | None
    plan_md: str | None
    plan_path: str | None
    conversion_summary: dict[str, Any] | None
    validation_result: dict[str, Any] | None
    ui_consistency_result: dict[str, Any] | None
    integration_result: dict[str, Any] | None
    verification_result: dict[str, Any] | None
    plan_revision_count: int
    exit_code: int
    exit_dict: dict[str, Any]


# ---------------------------------------------------------------------------
# Default output paths (rooted at the repo ROOT)
# ---------------------------------------------------------------------------

def default_paths(root: Path) -> dict[str, Path]:
    """Return the canonical output directory paths rooted at *root*."""
    return {
        "plans":       root / "plans",
        "logs":        root / "logs",
        "output":      root / "output",
        "checkpoints": root / "checkpoints",
    }


# ---------------------------------------------------------------------------
# Run ID
# ---------------------------------------------------------------------------

def stable_run_id(feature_name: str, feature_root: str, target: str) -> str:
    """
    Derive a deterministic run ID from the (feature_name, feature_root, target)
    triple.

    Two runs with identical inputs produce the same ID, so all artefacts
    (checkpoint, dependency graph, plan, conversion log) are written to the
    same paths and subsequent runs skip re-creating them if nothing changed.

    Format:  conv-<feature_slug>-<target_abbrev>-<hash8>
    Example: conv-actionhistory-sg-a1b2c3d4   (simpler_grants)
             conv-actionhistory-hp-a1b2c3d4   (hrsa_pprs)
    """
    slug   = re.sub(r"[^\w]", "-", feature_name.lower())[:20].strip("-")
    abbrev = {"simpler_grants": "sg", "hrsa_pprs": "hp"}.get(target, target[:4])
    digest = hashlib.sha1(
        f"{feature_root}|{target}".encode("utf-8")
    ).hexdigest()[:8]
    return f"conv-{slug}-{abbrev}-{digest}"


# ---------------------------------------------------------------------------
# Mapping inference
# ---------------------------------------------------------------------------

_MAPPING_HINTS: dict[str, str] = {
    "Angular 2 Component": "MAP-001",
    "Angular 2 Service":   "MAP-002",
    "NgModule":            "MAP-006",
    "Area API Controller": "MAP-003",
    "Repository":          "MAP-004",
    "C# Service":          "MAP-004",
    "Stored Procedure":    "MAP-004",
    "C# Class":            "MAP-005",
}

_TYPE_DEFAULTS: dict[str, str] = {
    "frontend": "MAP-001",
    "backend":  "MAP-003",
    "database": "MAP-004",
}


def infer_mapping_id(pattern: str, node_type: str) -> str:
    """Return the best-guess mapping ID for a dependency-graph node."""
    for keyword, map_id in _MAPPING_HINTS.items():
        if keyword.lower() in pattern.lower():
            return map_id
    return _TYPE_DEFAULTS.get(node_type, "MAP-001")


# ---------------------------------------------------------------------------
# Project-structure resolution
# ---------------------------------------------------------------------------

def resolve_project_structure(
    skillset: dict,
    structure_key: str,
    override: dict | None = None,
) -> dict:
    """
    Return the resolved project-structure dict for path derivation.

    Resolution order (highest priority first):
    1. Keys explicitly set in the YAML ``project_structure:`` block (*override*).
    2. Corresponding key in ``config/skillset-config.json`` for the target.
    3. Hardcoded defaults inside :func:`derive_target_path` (last resort).

    Merging is per-section, per-key: only keys present in *override* replace
    the config value; absent keys fall through.
    """
    base = skillset.get(structure_key, skillset.get("project_structure", {}))
    if not override:
        return base
    merged: dict = {}
    all_sections = set(base.keys()) | set(override.keys())
    for section in all_sections:
        base_sec     = base.get(section, {})
        override_sec = override.get(section, {})
        if isinstance(base_sec, dict) or isinstance(override_sec, dict):
            merged[section] = {**base_sec, **override_sec}
        else:
            merged[section] = override_sec if section in override else base_sec
    return merged


# ---------------------------------------------------------------------------
# Target-path derivation
# ---------------------------------------------------------------------------

def _to_snake(s: str) -> str:
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", s)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


def derive_target_path(
    node: dict,
    mapping: dict,
    target_struct: dict,
) -> str:
    """
    Derive the target file path from the source node and a pre-resolved
    project-structure dict.

    Parameters
    ----------
    target_struct : dict
        Already-resolved project structure dict (keyed by section, e.g.
        ``{"frontend": {...}, "backend": {...}}``).  Produced by
        :func:`resolve_project_structure` so YAML overrides are already
        merged in.
    """
    node_type = node.get("type", "frontend")
    exports   = node.get("exports", [])
    name      = exports[0] if exports else Path(node["id"]).stem

    feature_name = node.get("id", "").split("/")[0].lower()

    if node_type == "frontend":
        comp_root = target_struct.get("frontend", {}).get(
            "components_root", "frontend/src/components/{feature_name}/"
        )
        base = comp_root.replace("{feature_name}", feature_name)
        return f"{base}{name}.tsx"

    if node_type == "backend":
        api_root = target_struct.get("backend", {}).get(
            "api_root", "api/src/api/{feature_name}/"
        )
        base = api_root.replace("{feature_name}", _to_snake(feature_name))
        return f"{base}{_to_snake(name)}_routes.py"

    if node_type == "database":
        svc_root = target_struct.get("backend", {}).get(
            "services_root", "api/src/services/{feature_name}/"
        )
        base = svc_root.replace("{feature_name}", _to_snake(feature_name))
        return f"{base}{_to_snake(name)}_service.py"

    return f"output/{_to_snake(name)}.py"


# ---------------------------------------------------------------------------
# Structure-key selection
# ---------------------------------------------------------------------------

def select_structure_key(skillset: dict, target: str) -> str:
    """Pick the right ``project_structure_*`` key from the skillset config."""
    key = f"project_structure_{target}"
    if key in skillset:
        return key
    if target == "hrsa_pprs" or "hrsa_pprs" in target:
        return "project_structure_hrsa_pprs"
    return "project_structure"


# ---------------------------------------------------------------------------
# Build approved plan
# ---------------------------------------------------------------------------

def build_approved_plan(
    dependency_graph: dict,
    config: dict,
    run_id: str,
    feature_root: str,
    output_root: str,
    target: str = "simpler_grants",
    project_structure_override: dict | None = None,
) -> ApprovedPlan:
    """
    Build the structured plan dict that ConversionAgent consumes.

    In a full pipeline this comes from the parsed Plan Document.
    Here we derive it programmatically from the dependency graph.

    Parameters
    ----------
    target : str
        Target stack identifier.
    project_structure_override : dict | None
        Per-section path template overrides from the YAML job file.
    """
    steps: list[dict[str, Any]] = []
    phase_labels  = {"frontend": "C", "backend": "B", "database": "A"}
    phase_counts  = {"A": 0, "B": 0, "C": 0}

    skillset       = config["skillset"]
    mappings_index = config.get("mappings_index", {})

    structure_key = select_structure_key(skillset, target)
    target_struct = resolve_project_structure(
        skillset, structure_key, project_structure_override
    )

    for node in dependency_graph.get("nodes", []):
        node_type = node.get("type", "frontend")
        phase     = phase_labels.get(node_type, "C")
        phase_counts[phase] += 1
        step_id = f"Step {phase}{phase_counts[phase]}"

        pattern    = node.get("pattern", "")
        mapping_id = infer_mapping_id(pattern, node_type)
        mapping    = mappings_index.get(mapping_id, {})

        source_rel = node["id"]
        target_rel = derive_target_path(node, mapping, target_struct)

        rule_ids = ["RULE-003"]
        if node.get("endpoints"):
            rule_ids.insert(0, "RULE-001")
        if node_type == "frontend":
            rule_ids.append("RULE-002")
        if node_type == "database":
            rule_ids.append("RULE-009")

        steps.append({
            "id":             step_id,
            "description":    f"Convert {source_rel} -> {target_rel}",
            "source_file":    source_rel,
            "target_file":    target_rel,
            "mapping_id":     mapping_id,
            "rule_ids":       rule_ids,
            "rationale":      mapping.get("notes", "Direct translation per RULE-003."),
            # ── Knowledge-extraction metadata ──────────────────────────────
            "source_imports":  node.get("imports") or node.get("usings", []),
            "source_hooks":    node.get("hooks", []),
            "file_type":       node.get("pattern", ""),
            "component_type":  node_type,
            "source_lang":     node.get("lang", ""),
            "feature_name":    dependency_graph["feature_name"],
        })

    steps.sort(key=lambda s: s["id"])

    return {
        "feature_name":     dependency_graph["feature_name"],
        "feature_root":     feature_root,
        "output_root":      output_root,
        "run_id":           run_id,
        "target":           target,
        "project_structure": target_struct,
        "conversion_steps": steps,
    }

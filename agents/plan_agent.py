"""
Plan Document Generation Agent
================================
Takes the dependency graph from ScopingAgent and generates a structured,
human-readable Plan Document (Markdown).  The Plan Document is the CONTRACT
that humans must approve before any code is written.

This agent does NOT write production code.

LLM support is provided via LLMRouter -- any configured provider works:
    Anthropic Claude, OpenAI GPT, OpenAI-compatible (LM Studio / vLLM / Azure),
    Ollama (local), LlamaCpp (local GGUF).
Pass llm_router=None or use --no-llm to run in template-only mode.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents.llm import LLMRouter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt for the LLM plan generation
# ---------------------------------------------------------------------------

PLAN_GENERATION_SYSTEM_PROMPT = """
You are a senior migration architect specialising in modernising legacy
Angular 2 / ASP.NET Core applications to Next.js 15 (React 19) + Python
Flask (APIFlask) + SQLAlchemy 2.0.

Your task is to generate a structured Plan Document in Markdown.

STRICT RULES -- violations will be rejected by the pipeline:
1. Do NOT generate any production code in this document. This is a PLAN only.
2. Every component mapping MUST reference a specific rule by its RULE-XXX id.
3. If a mapping cannot be determined with >75% confidence, mark it AMBIGUOUS
   and do NOT guess. List what information is needed to resolve it.
4. Business logic that cannot be cleanly translated must be marked BLOCKED,
   not interpreted or improvised.
5. Any import from flagged platform libraries (pfm-layout, pfm-ng, pfm-re,
   pfm-dcf, Platform.CrossCutting8, Platform.Foundation8) must be flagged
   under Risk Areas and marked BLOCKED until resolved.
6. Output ONLY valid Markdown matching the schema below. No extra prose.
7. The Plan Document is a contract. Humans will sign off before execution.

TARGET STACK PATTERNS (from simpler-grants-gov reference):
  - Frontend components: Next.js 15 functional React components, hooks, TypeScript strict
  - Frontend services: Typed async fetchers (server-only or client hooks)
  - Backend routes: Python APIFlask blueprints with Pydantic schemas
  - Backend services: SQLAlchemy 2.0 service functions with db.Session
  - DB models: ApiSchemaTable + TimestampMixin, Mapped[] syntax, UUID PKs
  - Auth: JWT + multi-auth strategies
  - Error handling: raise_flask_error() for backend, typed error classes for frontend
  - Audit: add_audit_event() calls on all data mutations
"""

PLAN_DOCUMENT_TEMPLATE = """# Plan Document -- {feature_name}

**Generated:** {generated_at}
**Status:** PENDING APPROVAL
**Run ID:** {run_id}
**Feature Root:** `{feature_root}`

---

## 1. Current Architecture Breakdown

| Component | Type | Pattern | File |
|---|---|---|---|
{architecture_table}

---

## 2. Proposed Target Architecture

| Source Component | Target Component | Mapping ID | Rules Applied | Template |
|---|---|---|---|---|
{target_table}

---

## 3. Step-by-Step Conversion Sequence

{conversion_steps}

---

## 4. Business Logic Inventory

| Logic Item | Location | Preservation Method | Status |
|---|---|---|---|
{business_logic_table}

---

## 5. Risk Areas & Ambiguities

{risk_areas}

---

## 6. Acceptance Criteria

{acceptance_criteria}

---

**APPROVAL REQUIRED TO PROCEED TO EXECUTION**

Sign-off by: ___________________ Date: ___________
"""


class PlanAgent:
    """
    Generates the Plan Document from the dependency graph and config.

    Two modes:
        1. LLM-assisted  -- sends structured context to any configured LLM
                            provider via LLMRouter and post-processes the
                            returned Markdown.
        2. Template-only -- builds the plan from the dependency graph alone
                            without an LLM call (useful for testing / offline).

    Parameters
    ----------
    dependency_graph : dict
        Output from ScopingAgent.analyze().
    config : dict
        Validated config from ConfigIngestionAgent.load_and_validate().
    run_id : str
        Unique run identifier used in filenames.
    plans_dir : str | Path
        Directory where Plan Documents are saved.
    llm_router : LLMRouter | None
        Pre-built LLMRouter instance.  Pass None (or use --no-llm) to
        disable LLM calls and fall back to template-only generation.
    """

    def __init__(
        self,
        dependency_graph: dict,
        config: dict,
        run_id: str,
        plans_dir: str | Path = "plans",
        llm_router: "LLMRouter | None" = None,
    ) -> None:
        self.graph      = dependency_graph
        self.config     = config
        self.run_id     = run_id
        self.plans_dir  = Path(plans_dir)
        self._router    = llm_router

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self) -> tuple[str, Path]:
        """
        Generate the Plan Document.

        Returns:
            (plan_markdown_str, plan_file_path)
        """
        feature_name = self.graph.get("feature_name", "unknown-feature")
        logger.info("Generating plan document for feature: %s", feature_name)

        if self._router is not None and self._router.is_available:
            plan_md = self._generate_with_llm(feature_name)
        else:
            if self._router is None:
                logger.info("LLM router not configured -- generating plan from template only.")
            else:
                logger.info("LLM router not available -- generating plan from template only.")
            plan_md = self._generate_from_template(feature_name)

        plan_path = self._save_plan(plan_md, feature_name)
        logger.info("Plan document saved to: %s", plan_path)
        return plan_md, plan_path

    # ------------------------------------------------------------------
    # LLM-assisted generation
    # ------------------------------------------------------------------

    def _generate_with_llm(self, feature_name: str) -> str:
        """Call the configured LLM provider to generate the Plan Document."""
        from agents.llm import LLMMessage, LLMNotAvailableError, LLMProviderError

        user_message = (
            f"Generate the Plan Document for the feature: '{feature_name}'.\n\n"
            f"DEPENDENCY GRAPH:\n```json\n{json.dumps(self.graph, indent=2)}\n```\n\n"
            f"SKILLSET CONFIG (component_mappings):\n```json\n"
            f"{json.dumps(self.config['skillset'].get('component_mappings', []), indent=2)}\n```\n\n"
            f"RULES CONFIG (guardrails):\n```json\n"
            f"{json.dumps(self.config['rules'].get('guardrails', []), indent=2)}\n```\n\n"
            f"Use the exact Markdown schema from your instructions. "
            f"Flag all pfm-* and Platform.* imports as BLOCKED."
        )

        try:
            response = self._router.complete(
                system=PLAN_GENERATION_SYSTEM_PROMPT,
                messages=[LLMMessage(role="user", content=user_message)],
            )
            logger.info(
                "Plan generated via %s / %s  (in=%d out=%d tokens)",
                response.provider,
                response.model,
                response.input_tokens,
                response.output_tokens,
            )
            return response.text

        except LLMNotAvailableError as exc:
            logger.warning("LLM not available: %s -- falling back to template.", exc)
            return self._generate_from_template(feature_name)

        except LLMProviderError as exc:
            logger.error("LLM provider error: %s -- falling back to template.", exc)
            return self._generate_from_template(feature_name)

    # ------------------------------------------------------------------
    # Template-only generation (no LLM)
    # ------------------------------------------------------------------

    def _generate_from_template(self, feature_name: str) -> str:
        """Build a structured plan purely from the dependency graph data."""
        nodes   = self.graph.get("nodes", [])
        flags   = self.graph.get("flags", [])

        # ---- Table 1: Current Architecture ----
        arch_rows = []
        for node in nodes:
            arch_rows.append(
                f"| {node.get('exports', ['?'])[0] if node.get('exports') else node['id']} "
                f"| {node.get('type', '').capitalize()} "
                f"| {node.get('pattern', 'Unknown')} "
                f"| `{node['id']}` |"
            )
        architecture_table = "\n".join(arch_rows) if arch_rows else "| (none analysed) | | | |"

        # ---- Table 2: Target Architecture ----
        target_rows   = []
        steps_md      = []
        phase_labels  = {"frontend": "C", "backend": "B", "database": "A"}
        phase_counts  = {"A": 0, "B": 0, "C": 0}

        for node in nodes:
            mapping = self._resolve_mapping(node)
            if not mapping:
                target_rows.append(
                    f"| `{node['id']}` | **AMBIGUOUS** -- no mapping found | -- | RULE-004 | -- |"
                )
                continue

            phase = phase_labels.get(node.get("type", ""), "C")
            phase_counts[phase] += 1
            step_id = f"Step {phase}{phase_counts[phase]}"

            target_component = self._describe_target(node, mapping)
            target_rows.append(
                f"| `{node['id']}` | {target_component} | {mapping['id']} | RULE-003 | `{mapping['template']}` |"
            )

            steps_md.append(
                f"### {step_id}: {node['id']} -> {target_component}\n"
                f"- **Mapping:** {mapping['id']} ({mapping['source_pattern']})\n"
                f"- **Rules:** RULE-003 (no business logic reinterpretation)"
                + (f", RULE-001 (preserve API contract)" if node.get("endpoints") else "")
                + (f", RULE-002 (preserve CSS class names)" if node.get("type") == "frontend" else "")
                + "\n"
                f"- **Template:** `{mapping['template']}`\n"
                f"- **Notes:** {mapping.get('notes', 'Direct translation.')}\n"
            )

        target_table        = "\n".join(target_rows) if target_rows else "| (none) | | | | |"
        conversion_steps_md = "\n".join(steps_md) if steps_md else "_No conversion steps identified._"

        # ---- Table 3: Business Logic Inventory ----
        bl_rows = []
        for node in nodes:
            for method in node.get("methods", []):
                bl_rows.append(f"| `{method}()` | `{node['id']}` | Direct translation | [OK] Clear |")
            for ep in node.get("endpoints", []):
                bl_rows.append(
                    f"| `{ep['method']} {ep['route']}` | `{node['id']}` "
                    f"| Preserve REST contract (RULE-001) | [OK] Clear |"
                )
        business_logic_table = "\n".join(bl_rows) if bl_rows else "| (none identified) | | | |"

        # ---- Section 5: Risk Areas ----
        risk_sections = []
        for i, flag in enumerate(flags, 1):
            sev = "[BLOCKING]" if flag["severity"] == "blocking" else "[WARNING]"
            risk_sections.append(
                f"### {sev} -- RISK-{i:03d}: {flag['rule']}\n"
                f"**Message:** {flag['message']}\n\n"
                f"**Recommendation:** {flag.get('recommendation', '')}\n"
            )
        risk_areas_md = "\n".join(risk_sections) if risk_sections else "_No risk areas identified._"

        # ---- Section 6: Acceptance Criteria ----
        crit = []
        for node in nodes:
            if node.get("endpoints"):
                for ep in node["endpoints"]:
                    crit.append(
                        f"- [ ] `{ep['method']} {ep['route']}` returns identical response "
                        f"schema as legacy endpoint"
                    )
            if node.get("type") == "frontend":
                crit.append(
                    f"- [ ] All CSS class names in `{node['id']}` are preserved in converted JSX"
                )
        crit.append("- [ ] All business logic methods produce identical outputs for identical inputs")
        crit.append("- [ ] No imports from pfm-* or Platform.* libraries in converted code")
        acceptance_criteria_md = "\n".join(crit)

        return PLAN_DOCUMENT_TEMPLATE.format(
            feature_name         = feature_name,
            generated_at         = datetime.now(timezone.utc).isoformat(),
            run_id               = self.run_id,
            feature_root         = self.graph.get("feature_root", ""),
            architecture_table   = architecture_table,
            target_table         = target_table,
            conversion_steps     = conversion_steps_md,
            business_logic_table = business_logic_table,
            risk_areas           = risk_areas_md,
            acceptance_criteria  = acceptance_criteria_md,
        )

    def _resolve_mapping(self, node: dict) -> dict | None:
        """Find the best component mapping for a dependency graph node."""
        pattern   = node.get("pattern", "")
        node_type = node.get("type", "")

        mapping_hints = {
            "Angular 2 Component":    "MAP-001",
            "Angular 2 Service":      "MAP-002",
            "Area API Controller":    "MAP-003",
            "Repository":             "MAP-004",
            "C# Service":             "MAP-004",
            "NgModule":               "MAP-006",
            "Stored Procedure":       "MAP-004",
        }
        for keyword, map_id in mapping_hints.items():
            if keyword.lower() in pattern.lower():
                return self.config.get("mappings_index", {}).get(map_id)

        if node_type == "frontend":
            return self.config.get("mappings_index", {}).get("MAP-001")
        if node_type == "backend":
            return self.config.get("mappings_index", {}).get("MAP-003")
        if node_type == "database":
            return self.config.get("mappings_index", {}).get("MAP-004")
        return None

    def _describe_target(self, node: dict, mapping: dict) -> str:
        exports = node.get("exports", [])
        name    = exports[0] if exports else Path(node["id"]).stem
        tp      = node.get("type", "")
        if tp == "frontend":
            return f"`{name}.tsx` (React functional component)"
        if tp == "backend":
            return f"`{self._to_snake(name)}_routes.py` (APIFlask blueprint)"
        if tp == "database":
            return f"`{self._to_snake(name)}_service.py` (SQLAlchemy service)"
        return f"`{self._to_snake(name)}.py`"

    @staticmethod
    def _to_snake(name: str) -> str:
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    # ------------------------------------------------------------------
    # File persistence
    # ------------------------------------------------------------------

    def _save_plan(self, content: str, feature_name: str) -> Path:
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^\w-]", "-", feature_name.lower())
        path = self.plans_dir / f"{slug}-plan-{ts}-{self.run_id[:8]}.md"
        path.write_text(content, encoding="utf-8")
        return path

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

LLM failure behaviour
---------------------
  CLI / human mode  — hard-fails with LLMConfigurationError so the user
                      knows exactly what to fix.  No plan file is written.
  Agent mode        — falls back to the Markdown template scaffold so the IDE
                      agent (Cursor, Windsurf, Copilot) can continue and
                      surface the error to the user.  Detected automatically
                      via agents.agent_context, or set AI_AGENT_MODE=1 to
                      force agent mode explicitly.

Prompts are loaded from the prompts/ directory at runtime:

  Target: simpler_grants (default)
    prompts/plan_system.txt               -- LLM system prompt
    prompts/plan_document_template.md     -- Markdown scaffold (template-only mode)

  Target: hrsa_pprs
    prompts/plan_system_hrsa_pprs.txt     -- LLM system prompt for HRSA-Simpler-PPRS
    prompts/plan_document_template.md     -- same shared Markdown scaffold
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prompts import load_prompt
from agents.agent_context import require_llm_or_raise

if TYPE_CHECKING:
    from agents.llm import LLMRouter

logger = logging.getLogger(__name__)


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
    target : str
        Target stack identifier:
        'simpler_grants' (default) -- Next.js 15 / APIFlask / SQLAlchemy 2.0
        'hrsa_pprs'                -- Next.js 16 / Flask 3.0 / psycopg2 (HRSA-Simpler-PPRS)
        Controls which prompt files are loaded from prompts/.
    """

    # Map target -> system prompt filename in prompts/
    _SYSTEM_PROMPT_FILES: dict[str, str] = {
        "simpler_grants": "plan_system.txt",
        "hrsa_pprs":      "plan_system_hrsa_pprs.txt",
    }

    # Angular file-type dot-suffixes to strip from file stems
    # e.g. "actionhistory.component.ts" → stem "actionhistory.component" → "actionhistory"
    _ANGULAR_FILE_SUFFIXES: frozenset = frozenset({
        "component", "service", "module", "directive", "pipe",
        "guard", "resolver", "interceptor", "spec",
    })

    # Class-name suffixes to strip from TypeScript / C# export names
    # e.g. "ActionHistoryComponent" → "ActionHistory"
    _CLASS_SUFFIXES: tuple = (
        "Component", "Service", "Controller", "Module", "Directive",
        "Pipe", "Guard", "Resolver", "Repository", "Manager", "Handler",
    )

    def __init__(
        self,
        dependency_graph: dict,
        config: dict,
        run_id: str,
        plans_dir: str | Path = "plans",
        llm_router: "LLMRouter | None" = None,
        target: str = "simpler_grants",
    ) -> None:
        self.graph      = dependency_graph
        self.config     = config
        self.run_id     = run_id
        self.plans_dir  = Path(plans_dir)
        self._router    = llm_router
        self.target     = target

        # Resolve system prompt filename; fall back gracefully for unknown targets
        self._system_prompt_file = self._SYSTEM_PROMPT_FILES.get(
            target, self._SYSTEM_PROMPT_FILES["simpler_grants"]
        )
        logger.debug(
            "PlanAgent initialised: target=%s, system_prompt=%s",
            target,
            self._system_prompt_file,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self) -> tuple[str, Path]:
        """
        Generate the Plan Document.

        If a plan file already exists for this run_id (stable/deterministic),
        the existing file is returned immediately without calling the LLM or
        rebuilding from template -- avoiding duplicate artefacts on re-runs.

        Returns:
            (plan_markdown_str, plan_file_path)
        """
        feature_name = self.graph.get("feature_name", "unknown-feature")

        # -- Dedup check: return existing plan if one was already saved --
        slug     = re.sub(r"[^\w-]", "-", feature_name.lower())
        existing = sorted(
            self.plans_dir.glob(f"{slug}-plan-*-{self.run_id[:8]}*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ) if self.plans_dir.exists() else []

        if existing:
            plan_path = existing[0]
            plan_md   = plan_path.read_text(encoding="utf-8")
            logger.info(
                "Plan already exists for run_id=%s — reusing: %s",
                self.run_id, plan_path,
            )
            return plan_md, plan_path

        logger.info("Generating plan document for feature: %s", feature_name)

        if self._router is not None and self._router.is_available:
            plan_md = self._generate_with_llm(feature_name)
        elif self._router is None:
            # Explicit --no-llm: silently use template scaffold
            logger.info("LLM router not configured (--no-llm) -- generating plan from template only.")
            plan_md = self._generate_from_template(feature_name)
        else:
            # Router initialised but backend not reachable — treat as LLM failure
            plan_md = require_llm_or_raise(
                context=f"plan generation for '{feature_name}'",
                error=RuntimeError(
                    "LLM router initialised but no backend is reachable. "
                    "Check your API key / provider environment variables."
                ),
                fallback_fn=lambda: self._generate_from_template(feature_name),
            )

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
                system=load_prompt(self._system_prompt_file),
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

        except (LLMNotAvailableError, LLMProviderError) as exc:
            return require_llm_or_raise(
                context=f"plan generation for '{feature_name}'",
                error=exc,
                fallback_fn=lambda: self._generate_from_template(feature_name),
            )

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

        return load_prompt("plan_document_template.md").format(
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
        if exports:
            # Strip Angular / ASP.NET class-name suffixes (e.g. "ActionHistoryComponent" → "ActionHistory")
            name = self._clean_class_name(exports[0])
        else:
            # Strip dot-based Angular file suffixes (e.g. "actionhistory.component.ts" → "actionhistory")
            name = self._clean_stem(node["id"])
        tp = node.get("type", "")
        if tp == "frontend":
            return f"`{name}.tsx` (React functional component)"
        if tp == "backend":
            if self.target == "hrsa_pprs":
                return f"`{self._to_snake(name)}_routes.py` (Flask Blueprint)"
            return f"`{self._to_snake(name)}_routes.py` (APIFlask blueprint)"
        if tp == "database":
            if self.target == "hrsa_pprs":
                return f"`{self._to_snake(name)}_repository.py` (psycopg2 repository)"
            return f"`{self._to_snake(name)}_service.py` (SQLAlchemy service)"
        return f"`{self._to_snake(name)}.py`"

    @staticmethod
    def _clean_class_name(class_name: str) -> str:
        """Strip well-known Angular / ASP.NET class-name suffixes.

        Examples:
            'ActionHistoryComponent'  → 'ActionHistory'
            'ActionHistoryController' → 'ActionHistory'
            'ActionHistoryService'    → 'ActionHistory'
        """
        for suffix in PlanAgent._CLASS_SUFFIXES:
            if class_name.endswith(suffix) and len(class_name) > len(suffix):
                return class_name[:-len(suffix)]
        return class_name

    @staticmethod
    def _clean_stem(node_id: str) -> str:
        """Extract a clean base name from a node-id file path.

        Strips the file extension and any Angular dot-suffixes from the stem.

        Examples:
            'actionhistory.component.ts'   → 'actionhistory'
            'action-history.component.ts'  → 'action-history'
            'ActionHistoryController.cs'   → 'ActionHistoryController'
            'search.service.ts'            → 'search'
        """
        stem = Path(node_id).stem          # strips last extension only (.ts / .cs / .sql)
        pieces = stem.split(".")
        while len(pieces) > 1 and pieces[-1].lower() in PlanAgent._ANGULAR_FILE_SUFFIXES:
            pieces.pop()
        return ".".join(pieces)

    @staticmethod
    def _to_snake(name: str) -> str:
        """Convert PascalCase or kebab-case name to snake_case.

        Examples:
            'ActionHistory'   → 'action_history'
            'action-history'  → 'action_history'
            'actionhistory'   → 'actionhistory'
        """
        name = name.replace("-", "_")      # kebab-case → snake_case first
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    # ------------------------------------------------------------------
    # File persistence
    # ------------------------------------------------------------------

    def _save_plan(self, content: str, feature_name: str) -> Path:
        self.plans_dir.mkdir(parents=True, exist_ok=True)

        # Check whether a plan for this exact run_id was already written.
        # run_id is now stable/deterministic, so re-running the same feature+target
        # should reuse the existing plan rather than creating a duplicate.
        slug = re.sub(r"[^\w-]", "-", feature_name.lower())
        existing = sorted(
            self.plans_dir.glob(f"{slug}-plan-*-{self.run_id[:8]}*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if existing:
            logger.info(
                "Plan already exists for run_id=%s — reusing: %s",
                self.run_id, existing[0],
            )
            return existing[0]

        ts   = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = self.plans_dir / f"{slug}-plan-{ts}-{self.run_id[:8]}.md"
        path.write_text(content, encoding="utf-8")
        return path

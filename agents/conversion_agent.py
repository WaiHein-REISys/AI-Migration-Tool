"""
Conversion Execution Agent
===========================
The code-writing agent. Operates ONLY against an approved Plan Document.
For each conversion step it:
  1. Reads the source file
  2. Resolves the correct Jinja2 template
  3. Applies rules to build a constrained LLM prompt
  4. Generates converted code via the configured LLM provider (or template-only)
  5. Validates output stays within the declared feature boundary
  6. Writes the target file
  7. Logs every action in real-time

Raises AmbiguityException or OutOfBoundaryException on violations -- never
silently skips.

LLM support is provided via LLMRouter -- any configured provider works:
    Anthropic Claude, OpenAI GPT, OpenAI-compatible (LM Studio / vLLM / Azure),
    Ollama (local), LlamaCpp (local GGUF).
Pass llm_router=None to operate in template-only / scaffold mode.

Requires:
    pip install jinja2
"""

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from jinja2 import Environment, FileSystemLoader, TemplateNotFound  # type: ignore
except ImportError:
    raise ImportError("jinja2 is required. pip install jinja2")

from agents.conversion_log import ConversionLog

if TYPE_CHECKING:
    from agents.llm import LLMRouter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AmbiguityException(Exception):
    """Raised when the LLM or the agent cannot confidently convert a source."""

class OutOfBoundaryException(Exception):
    """Raised when a write target falls outside the declared feature boundary."""

# ---------------------------------------------------------------------------
# LLM conversion system prompt
# ---------------------------------------------------------------------------

CONVERSION_SYSTEM_PROMPT_TEMPLATE = """
You are a code conversion engine. You translate legacy GPRS Angular 2 /
ASP.NET Core source code into the modern target stack used by
simpler-grants-gov (Next.js 15 / React 19 / TypeScript strict for frontend;
Python APIFlask + SQLAlchemy 2.0 for backend).

MANDATORY GUARDRAILS -- violations will be rejected:
{rules_text}

CRITICAL CONSTRAINTS:
- Do NOT optimize, refactor, or improve logic. Translate VERBATIM.
- Do NOT change REST endpoint routes, HTTP methods, or payload field names.
- Preserve ALL CSS class names from the source HTML template exactly.
- Do NOT import from pfm-layout, pfm-ng, pfm-re, pfm-dcf, Platform.*
  If the source depends on one of these, respond with:
    AMBIGUOUS: <clear description of what the library provides and why you cannot proceed>
- If you cannot translate ANY section with high confidence, respond with:
    AMBIGUOUS: <reason>
  and do NOT generate code for that section.
- Output ONLY the converted code. No explanations, no markdown fences, no comments
  unless the original source had comments.
- Preserve all original developer comments, translated to the target language.

TARGET STACK PATTERNS:
{target_stack_summary}
"""

TARGET_STACK_SUMMARY = """
FRONTEND (Next.js 15 / React 19 / TypeScript):
  - Functional components with "use client" or "use server" directive at top
  - useState, useEffect, useCallback, useContext for state/lifecycle
  - Typed props interfaces, no 'any' unless source uses 'any'
  - Import from 'next/navigation' (useRouter, useSearchParams)
  - HTTP: fetch() API or useClientFetch custom hook
  - Subscriptions: useEffect with cleanup return function (replaces ngOnDestroy)
  - Error: typed error classes from src/errors
  - Logging: console.error() with structured context
  - i18n: useTranslations from next-intl (replaces static strings)
  - Tests: adjacent .test.tsx file with Jest + @testing-library

BACKEND (Python APIFlask + SQLAlchemy 2.0):
  - Blueprint: @blueprint.post/get/put/delete with @blueprint.input / @blueprint.output
  - Schemas: marshmallow-based Schema classes (*_schema.py)
  - Service functions: plain Python functions taking (db_session: db.Session, ...)
  - SQLAlchemy: select(), db_session.execute(), .scalar_one_or_none()
  - Errors: raise_flask_error(status_code, message)
  - Audit: add_audit_event(db_session, entity, user, audit_event)
  - Logging: logger = logging.getLogger(__name__); logger.info(..., extra={...})
  - Auth: @api_key_multi_auth.login_required + current_user from auth utils
  - Return: response.ApiResponse(message="Success", data=...)
"""


class ConversionAgent:
    """
    Executes each conversion step from the approved Plan Document.

    The plan is expected to be a dict with at minimum:
        {
          "feature_name": str,
          "feature_root": str,         # source feature boundary
          "output_root": str,          # target output root
          "conversion_steps": [
            {
              "id": "Step A1",
              "description": "...",
              "source_file": "relative/path.ts",
              "target_file": "relative/output.tsx",
              "mapping_id": "MAP-001",
              "rule_ids": ["RULE-001", "RULE-003"],
              "rationale": "..."
            }, ...
          ]
        }

    Parameters
    ----------
    approved_plan : dict
        Conversion plan produced by PlanAgent / main.py.
    config : dict
        Validated config from ConfigIngestionAgent.
    log : ConversionLog
        Real-time append-only log instance.
    output_root : str | Path
        Root directory for generated files.
    dry_run : bool
        If True, generate code in memory / logs but write no files to disk.
    llm_router : LLMRouter | None
        Pre-built LLMRouter instance.  Pass None to use template-only mode.
    """

    TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

    def __init__(
        self,
        approved_plan: dict,
        config: dict,
        log: ConversionLog,
        output_root: str | Path,
        dry_run: bool = False,
        llm_router: "LLMRouter | None" = None,
    ) -> None:
        self.plan        = approved_plan
        self.config      = config
        self.log         = log
        self.output_root = Path(output_root)
        self.dry_run     = dry_run
        self._router     = llm_router

        self._jinja = Environment(
            loader=FileSystemLoader(str(self.TEMPLATES_DIR)),
            autoescape=False,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self) -> dict[str, Any]:
        """
        Run all conversion steps in sequence.

        Returns a summary dict with counts of completed / flagged / skipped steps.
        """
        steps     = self.plan.get("conversion_steps", [])
        completed = []
        flagged   = []
        skipped   = []

        for step in steps:
            step_id = step.get("id", "?")
            try:
                self.log.start_step(step)
                self._execute_step(step)
                self.log.complete_step(step)
                completed.append(step_id)
            except AmbiguityException as exc:
                msg = str(exc)
                logger.warning("[%s] AMBIGUOUS -- %s", step_id, msg)
                self.log.record(
                    "halted_ambiguous",
                    plan_step_ref=step_id,
                    rule_applied="RULE-004",
                    rationale=msg,
                    deviation=f"Step {step_id} incomplete -- ambiguity must be resolved by human.",
                )
                flagged.append({"step": step_id, "reason": msg})
            except OutOfBoundaryException as exc:
                msg = str(exc)
                logger.error("[%s] OUT-OF-BOUNDARY -- %s", step_id, msg)
                self.log.record(
                    "rejected_out_of_boundary",
                    plan_step_ref=step_id,
                    rule_applied="RULE-005",
                    rationale=msg,
                )
                flagged.append({"step": step_id, "reason": msg})

        summary = {
            "total":           len(steps),
            "completed":       len(completed),
            "flagged":         len(flagged),
            "skipped":         len(skipped),
            "completed_steps": completed,
            "flagged_steps":   flagged,
        }
        status = "completed" if not flagged else "completed_with_flags"
        self.log.finalize(status)
        return summary

    # ------------------------------------------------------------------
    # Step execution
    # ------------------------------------------------------------------

    def _execute_step(self, step: dict) -> None:
        source_rel  = step["source_file"]
        target_rel  = step["target_file"]
        mapping_id  = step.get("mapping_id", "")
        rule_ids    = step.get("rule_ids", ["RULE-003"])
        rationale   = step.get("rationale", "")

        # 1. Read source file
        source_path = Path(self.plan["feature_root"]) / source_rel
        if not source_path.exists():
            raise AmbiguityException(f"Source file not found: {source_path}")

        source_code = source_path.read_text(encoding="utf-8", errors="replace")
        self.log.record(
            "read_file",
            source_file=str(source_path),
            plan_step_ref=step["id"],
            rationale=f"Source file read for {step['id']}",
        )

        # 2. Resolve template
        template_name = self._resolve_template(mapping_id)
        self.log.record(
            "resolved_template",
            plan_step_ref=step["id"],
            rationale=f"Resolved template: {template_name}",
        )

        # 3. Build applicable rules
        applicable_rules = self._get_applicable_rules(rule_ids)

        # 4. Generate converted code
        converted_code = self._generate_code(
            source_code=source_code,
            template_name=template_name,
            applicable_rules=applicable_rules,
            step=step,
        )

        # 5. Validate boundary
        target_path = self.output_root / target_rel
        self._assert_within_boundary(target_path)

        # 6. Write target file
        if not self.dry_run:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(converted_code, encoding="utf-8")

        self.log.record(
            "wrote_file",
            source_file=source_rel,
            target_file=str(target_path),
            rule_applied=", ".join(rule_ids),
            transformation=f"Converted {source_rel} -> {target_rel} using {template_name}",
            rationale=rationale,
            plan_step_ref=step["id"],
        )

        if self.dry_run:
            logger.info("[DRY RUN] Would write: %s", target_path)

    # ------------------------------------------------------------------
    # Template resolution
    # ------------------------------------------------------------------

    def _resolve_template(self, mapping_id: str) -> str:
        mapping = self.config.get("mappings_index", {}).get(mapping_id)
        if mapping:
            template_path = mapping.get("template", "")
            # Strip leading 'templates/' prefix since Jinja loader is rooted there
            return Path(template_path).name
        logger.warning("No template found for mapping %s -- using passthrough.", mapping_id)
        return "passthrough.jinja2"

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    def _get_applicable_rules(self, rule_ids: list[str]) -> list[dict]:
        rules_index = self.config.get("rules_index", {})
        return [rules_index[rid] for rid in rule_ids if rid in rules_index]

    # ------------------------------------------------------------------
    # LLM code generation
    # ------------------------------------------------------------------

    def _generate_code(
        self,
        source_code: str,
        template_name: str,
        applicable_rules: list[dict],
        step: dict,
    ) -> str:
        """
        Generate converted code.

        Priority:
        1. LLM (via LLMRouter) with template context injected as a hint
        2. Jinja2 template rendering only (no LLM) if router is None / unavailable
        3. Raise AmbiguityException if neither produces output
        """
        # Load template for context injection
        template_context = self._render_template_context(template_name, step, source_code)

        if self._router is not None and self._router.is_available:
            return self._generate_with_llm(source_code, template_context, applicable_rules, step)
        else:
            logger.warning(
                "No LLM available -- rendering template without AI assistance for %s",
                step["id"],
            )
            if template_context.strip():
                return template_context
            raise AmbiguityException(
                f"Cannot convert {step['source_file']}: no LLM available and template "
                f"'{template_name}' produced no output. "
                f"Configure an LLM provider or check the template."
            )

    def _render_template_context(self, template_name: str, step: dict, source_code: str) -> str:
        """Render Jinja2 template to produce a code scaffold / prompt hint."""
        try:
            tmpl = self._jinja.get_template(template_name)
            return tmpl.render(step=step, source_code=source_code, config=self.config)
        except TemplateNotFound:
            logger.debug("Template not found: %s -- proceeding without scaffold.", template_name)
            return ""
        except Exception as exc:
            logger.warning("Template render error (%s): %s", template_name, exc)
            return ""

    def _generate_with_llm(
        self,
        source_code: str,
        template_context: str,
        applicable_rules: list[dict],
        step: dict,
    ) -> str:
        """Call the configured LLM provider to generate converted code."""
        from agents.llm import LLMMessage, LLMNotAvailableError, LLMProviderError

        rules_text = "\n".join(
            f"- {r['id']} ({r['name']}): {r['description']}" for r in applicable_rules
        )
        system_prompt = CONVERSION_SYSTEM_PROMPT_TEMPLATE.format(
            rules_text=rules_text,
            target_stack_summary=TARGET_STACK_SUMMARY,
        )

        template_hint = (
            f"\n\nSCAFFOLD HINT (from template {Path(step.get('mapping_id', '')).name}):\n"
            f"```\n{template_context}\n```\n"
            if template_context else ""
        )

        user_message = (
            f"Convert the following source file to the target stack.\n"
            f"Conversion Step: {step['id']}\n"
            f"Source file: `{step['source_file']}`\n"
            f"Target file: `{step['target_file']}`\n"
            f"Rationale: {step.get('rationale', '')}\n"
            f"{template_hint}\n"
            f"SOURCE CODE:\n```\n{source_code}\n```\n\n"
            f"Output ONLY the converted code. No markdown fences."
        )

        try:
            response = self._router.complete(
                system=system_prompt,
                messages=[LLMMessage(role="user", content=user_message)],
            )
            logger.info(
                "[%s] Code generated via %s / %s  (in=%d out=%d tokens)",
                step["id"],
                response.provider,
                response.model,
                response.input_tokens,
                response.output_tokens,
            )
            result = response.text.strip()

            # Detect AMBIGUOUS response from LLM
            if result.upper().startswith("AMBIGUOUS:"):
                raise AmbiguityException(result[len("AMBIGUOUS:"):].strip())

            return result

        except AmbiguityException:
            raise  # re-raise cleanly

        except LLMNotAvailableError as exc:
            # Fall back to template output if available
            logger.warning(
                "[%s] LLM not available: %s -- falling back to template scaffold.", step["id"], exc
            )
            if template_context.strip():
                return template_context
            raise AmbiguityException(
                f"LLM unavailable for {step['id']} and template produced no output: {exc}"
            ) from exc

        except LLMProviderError as exc:
            raise AmbiguityException(f"LLM provider error during {step['id']}: {exc}") from exc

    # ------------------------------------------------------------------
    # Boundary validation (RULE-005)
    # ------------------------------------------------------------------

    def _assert_within_boundary(self, target_path: Path) -> None:
        try:
            target_path.resolve().relative_to(self.output_root.resolve())
        except ValueError:
            raise OutOfBoundaryException(
                f"Target file '{target_path}' is outside the declared output boundary "
                f"'{self.output_root}'. Per RULE-005, write rejected."
            )

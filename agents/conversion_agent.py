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

LLM failure behaviour
---------------------
  CLI / human mode  — hard-fails with LLMConfigurationError so the user
                      knows exactly what to fix.  No output files are written.
  Agent mode        — falls back to the Jinja2 template scaffold so the IDE
                      agent (Cursor, Windsurf, Copilot) can continue and
                      surface the error to the user.  Detected automatically
                      via agents.agent_context, or set AI_AGENT_MODE=1 to
                      force agent mode explicitly.

Prompt file resolution is fully dynamic via prompts.resolve_prompt_filename():
  1. wizard-registry.json  prompt_files.conversion_system / target_stack  (explicit)
  2. Convention: conversion_system_{target_id}.txt / conversion_target_stack_{target_id}.txt
                 (auto-discovered by file presence)
  3. Default:   conversion_system.txt / conversion_target_stack.txt  (simpler_grants baseline)

  No target-to-filename mappings are hardcoded in this module.
  All system prompt files contain {rules_text} and {target_stack_summary} placeholders.

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

from agents.agent_context import require_llm_or_raise
from agents.conversion_log import ConversionLog
from prompts import load_prompt, resolve_prompt_filename

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
    target : str
        Any registered target identifier (e.g. 'simpler_grants', 'hrsa_pprs',
        'modern', 'snake_case', or any wizard-generated id).  The correct
        conversion prompts are resolved dynamically — no mapping is maintained here.
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
        target: str = "simpler_grants",
    ) -> None:
        self.plan        = approved_plan
        self.config      = config
        self.log         = log
        self.output_root = Path(output_root)
        self.dry_run     = dry_run
        self._router     = llm_router
        self.target      = target

        # Resolve prompt filenames dynamically — no hardcoded map.
        self._system_prompt_file = resolve_prompt_filename(
            target, "conversion_system", "conversion_system.txt"
        )
        self._target_stack_file = resolve_prompt_filename(
            target, "target_stack", "conversion_target_stack.txt"
        )
        logger.debug(
            "ConversionAgent initialised: target=%s, system_prompt=%s, target_stack=%s",
            target,
            self._system_prompt_file,
            self._target_stack_file,
        )

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
            if self._router is not None:
                # Router initialised but backend not reachable — treat as LLM failure
                err = RuntimeError(
                    "LLM router initialised but no backend is reachable. "
                    "Check your API key / provider environment variables."
                )
                def _template_only():
                    if template_context.strip():
                        return template_context
                    raise AmbiguityException(
                        f"Cannot convert {step['source_file']}: LLM not reachable and "
                        f"template '{template_name}' produced no output."
                    )
                return require_llm_or_raise(
                    context=f"code generation for '{step['id']}'",
                    error=err,
                    fallback_fn=_template_only,
                )
            # self._router is None: user explicitly passed --no-llm
            logger.info("No LLM configured (--no-llm) -- using template scaffold for %s.", step["id"])
            if template_context.strip():
                return template_context
            raise AmbiguityException(
                f"Cannot convert {step['source_file']}: no LLM configured and template "
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
        system_prompt = load_prompt(self._system_prompt_file).format(
            rules_text=rules_text,
            target_stack_summary=load_prompt(self._target_stack_file),
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

            # Strip markdown code fences — the LLM sometimes wraps output in
            # ```python ... ``` despite being told not to.  Stripping here
            # prevents a SyntaxError when the file is written to disk.
            result = self._strip_code_fences(result)

            # Detect AMBIGUOUS response from LLM
            if result.upper().startswith("AMBIGUOUS:"):
                raise AmbiguityException(result[len("AMBIGUOUS:"):].strip())

            return result

        except AmbiguityException:
            raise  # re-raise cleanly

        except (LLMNotAvailableError, LLMProviderError) as exc:
            def _template_fallback():
                if template_context.strip():
                    return template_context
                raise AmbiguityException(
                    f"LLM unavailable for {step['id']} and template produced no output: {exc}"
                )
            return require_llm_or_raise(
                context=f"code generation for '{step['id']}'",
                error=exc,
                fallback_fn=_template_fallback,
            )

    # ------------------------------------------------------------------
    # Post-processing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """
        Post-process LLM output to extract clean source code.

        Two passes are applied in order:

        Pass 1 — Markdown code fence extraction:
          If the output is wrapped in a markdown code fence (```python...```),
          extract the inner code.  Handles:
            - Preamble prose before the first fence ("Here is the code:\n```...")
            - ```python / ```ts / ``` variants
            - Trailing text after the closing fence (ignored)

        Pass 2 — Leading prose stripping:
          Some LLMs (e.g. codex exec) emit chain-of-thought reasoning as plain
          text lines BEFORE the first import/from/#.  These prose lines make the
          file invalid Python.  This pass finds the first line that looks like
          valid Python and discards everything above it.

          A line is treated as the start of code when it begins with:
            import / from / # / \"\"\" / ''' / def / class / @

        If neither pass changes the text, the original is returned unchanged.
        """
        # Pass 1: extract from markdown code fences
        match = re.search(r"```[^\n]*\n(.*?)```", text, re.DOTALL)
        if match:
            extracted = match.group(1).strip()
            if extracted:
                text = extracted  # continue to pass 2 in case fenced block also has prose

        # Pass 2: strip leading prose before the first code line
        _CODE_STARTERS = (
            "import ",
            "from ",
            "#",
            '"""',
            "'''",
            "def ",
            "class ",
            "@",
        )
        lines = text.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if any(stripped.startswith(s) for s in _CODE_STARTERS):
                if i > 0:
                    return "\n".join(lines[i:])
                break  # already starts at a code line — no change needed

        return text

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

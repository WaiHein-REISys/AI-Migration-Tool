"""
Internal Orchestration Backend
================================
Implements the built-in ReAct / native-tool-use orchestration loop.

Mode selection (automatic, based on provider):
    native_tools  — calls LLMRouter.complete_with_tools() with pipeline
                    actions exposed as ToolDefinition objects.  The LLM
                    responds with structured ToolCall objects (no parsing).
    react_text    — calls LLMRouter.complete() and parses the free-text
                    THOUGHT / ACTION / PARAMS response with regex.

Decision rules encoded in _decide():
    - validation.status == "failed"  → auto-retry conversion
    - plan has AMBIGUOUS + failure_registry hit → auto-revise with context
    - ui_consistency.status == "failed" → revise plan with missing-class info
    - plan_revision_count >= max_plan_revisions → stop revising, proceed/escalate
    - iteration >= max_iterations (hard limit 20) → escalate

Caller: OrchestratorAgent.execute()
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Callable

from agents.llm.base import ToolDefinition

if TYPE_CHECKING:
    from agents.llm.registry import LLMRouter
    from agents.memory_store import MemoryContext

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 20


class InternalOrchestrationBackend:
    """
    Built-in orchestration loop (ReAct text or native tool-use).
    """

    def __init__(
        self,
        llm_router: "LLMRouter",
        system_prompt: str,
        action_registry: dict[str, Callable],
        orchestration_config: dict,
        memory_context: "MemoryContext | None" = None,
    ) -> None:
        self._router     = llm_router
        self._system     = system_prompt
        self._actions    = action_registry
        self._config     = orchestration_config
        self._mem_ctx    = memory_context

        tool_use_pref = orchestration_config.get("tool_use", "auto")
        if tool_use_pref == "never":
            self._mode = "react_text"
        elif tool_use_pref == "always" or llm_router.supports_tool_use:
            self._mode = "native_tools"
        else:
            self._mode = "react_text"

        self._max_revisions = int(orchestration_config.get("max_plan_revisions", 2))
        self._escalate_on_fail = orchestration_config.get("escalate_on_fail", True)

        logger.info(
            "InternalOrchestrationBackend: mode=%s max_plan_revisions=%d",
            self._mode, self._max_revisions,
        )

    # ------------------------------------------------------------------
    # Tool definitions (used in native_tools mode)
    # ------------------------------------------------------------------

    _TOOL_DEFS: list[ToolDefinition] = [
        ToolDefinition(
            name="scope_feature",
            description="Scan the source feature folder and produce a dependency graph.",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        ToolDefinition(
            name="generate_plan",
            description="Generate a migration plan from the dependency graph.",
            parameters={
                "type": "object",
                "properties": {
                    "additional_context": {
                        "type": "string",
                        "description": "Optional extra guidance for the plan (e.g. known blockers).",
                    }
                },
                "required": [],
            },
        ),
        ToolDefinition(
            name="revise_plan",
            description="Revise the current plan based on feedback.",
            parameters={
                "type": "object",
                "properties": {
                    "feedback": {
                        "type": "string",
                        "description": "Specific revision instructions for the LLM.",
                    }
                },
                "required": ["feedback"],
            },
        ),
        ToolDefinition(
            name="approve_plan",
            description="Accept the current plan and advance to code conversion.",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        ToolDefinition(
            name="convert",
            description="Run LLM code conversion for all files in the approved plan.",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        ToolDefinition(
            name="validate",
            description="Validate converted output files against expected behaviour.",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        ToolDefinition(
            name="ui_audit",
            description="Run UI consistency audit (Angular CSS / element diff).",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        ToolDefinition(
            name="integrate",
            description="Place converted files into the target repo and sync dependencies.",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        ToolDefinition(
            name="verify",
            description="Run end-to-end verification commands (build, test, lint).",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        ToolDefinition(
            name="record_memory",
            description="Extract learnings from the completed run into the memory store.",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        ToolDefinition(
            name="escalate_human",
            description="Pause and ask the human operator to resolve an unresolvable issue.",
            parameters={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why escalation is needed.",
                    }
                },
                "required": ["reason"],
            },
        ),
        ToolDefinition(
            name="done",
            description="Signal that all stages are complete. The orchestrator loop ends.",
            parameters={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of completed work.",
                    }
                },
                "required": [],
            },
        ),
    ]

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, state: dict) -> dict:
        """
        Run the orchestration loop.

        state:   mutable pipeline state dict (updated in place by each action)
        returns: exit dict compatible with run_pipeline()
        """
        messages: list[dict] = []
        iteration = 0

        # Build initial user message describing current pipeline state
        messages.append({
            "role": "user",
            "content": self._build_initial_message(state),
        })

        while iteration < _MAX_ITERATIONS:
            iteration += 1
            logger.debug("Orchestrator iteration %d / %d", iteration, _MAX_ITERATIONS)

            # -- Get next action from LLM --
            action_name, action_params, assistant_text = self._get_next_action(
                messages, state
            )

            # Record assistant turn
            messages.append({"role": "assistant", "content": assistant_text})

            if action_name == "done":
                logger.info("Orchestrator: done after %d iterations.", iteration)
                return state.get("exit_dict", {"status": "success"})

            if action_name == "escalate_human":
                reason = action_params.get("reason", "Unresolvable issue.")
                return self._escalate(reason, state)

            if action_name is None:
                logger.warning(
                    "Orchestrator: LLM returned no recognisable action (iter %d). "
                    "Continuing.",
                    iteration,
                )
                messages.append({
                    "role": "user",
                    "content": (
                        "I could not parse an action from your response. "
                        "Please respond with a valid ACTION from the available list."
                    ),
                })
                continue

            # -- Execute the action --
            action_fn = self._actions.get(action_name)
            if not action_fn:
                logger.warning("Orchestrator: unknown action '%s'. Skipping.", action_name)
                messages.append({
                    "role": "user",
                    "content": f"Action '{action_name}' is not registered. Choose a valid action.",
                })
                continue

            logger.info("Orchestrator: executing action=%s params=%s", action_name, action_params)
            try:
                result = action_fn(state, **action_params)
            except Exception as exc:  # noqa: BLE001
                result = {"status": "error", "error": str(exc)}
                logger.error("Orchestrator action '%s' raised: %s", action_name, exc)

            # Apply built-in decision rules
            override = self._apply_decision_rules(
                action_name, result, state
            )

            # Feed result back as a user message
            result_text = json.dumps(result, indent=2, default=str)
            if override:
                result_text += f"\n\n[ORCHESTRATOR NOTE] {override}"
            messages.append({"role": "user", "content": f"Result of {action_name}:\n{result_text}"})

        # Hard iteration limit reached
        logger.error(
            "Orchestrator: max_iterations (%d) reached without completing.", _MAX_ITERATIONS
        )
        return self._escalate(
            f"Hard iteration limit ({_MAX_ITERATIONS}) reached. Manual review required.",
            state,
        )

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    def _get_next_action(
        self,
        messages: list[dict],
        state: dict,
    ) -> tuple[str | None, dict, str]:
        """
        Call the LLM and parse the next action.
        Returns (action_name, action_params, raw_assistant_text).
        """
        from agents.llm.base import LLMMessage

        llm_messages = [LLMMessage(role=m["role"], content=m["content"]) for m in messages]

        if self._mode == "native_tools":
            return self._get_action_via_tools(llm_messages, state)
        else:
            return self._get_action_via_react(llm_messages, state)

    def _get_action_via_tools(
        self,
        messages: list,
        state: dict,
    ) -> tuple[str | None, dict, str]:
        """Native tool-use mode — parse LLMResponse.tool_calls."""
        try:
            response = self._router.complete_with_tools(
                system=self._build_system_prompt("tool_use"),
                messages=messages,
                tools=self._TOOL_DEFS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Orchestrator LLM error (tool-use): %s", exc)
            return None, {}, f"LLM error: {exc}"

        if response.tool_calls:
            tc = response.tool_calls[0]
            return tc.tool_name, tc.tool_input, response.text or f"[tool: {tc.tool_name}]"

        # Model responded with text instead of a tool call
        return self._parse_react_text(response.text)

    def _get_action_via_react(
        self,
        messages: list,
        state: dict,
    ) -> tuple[str | None, dict, str]:
        """ReAct text mode — parse THOUGHT / ACTION / PARAMS from plain text."""
        try:
            response = self._router.complete(
                system=self._build_system_prompt("react_text"),
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Orchestrator LLM error (react_text): %s", exc)
            return None, {}, f"LLM error: {exc}"

        return self._parse_react_text(response.text)

    @staticmethod
    def _parse_react_text(text: str) -> tuple[str | None, dict, str]:
        """Extract ACTION and PARAMS from THOUGHT/ACTION/PARAMS text format."""
        action_match = re.search(r"ACTION:\s*([a-zA-Z_]+)", text, re.IGNORECASE)
        params_match = re.search(r"PARAMS:\s*(\{.*?\})", text, re.DOTALL | re.IGNORECASE)

        action_name = action_match.group(1).lower() if action_match else None
        params: dict = {}
        if params_match:
            try:
                params = json.loads(params_match.group(1))
            except json.JSONDecodeError:
                pass

        return action_name, params, text

    # ------------------------------------------------------------------
    # Decision rules
    # ------------------------------------------------------------------

    def _apply_decision_rules(
        self,
        action_name: str,
        result: dict,
        state: dict,
    ) -> str:
        """
        Apply built-in decision rules after an action completes.
        Returns an optional note to include in the next user message.
        """
        status = result.get("status", "")

        # Validation failed → suggest retry
        if action_name == "validate" and status == "failed":
            state.setdefault("retry_count", 0)
            state["retry_count"] += 1
            return (
                "Validation FAILED. You should call 'convert' again to retry "
                "the failed steps before proceeding."
            )

        # UI audit failed → suggest plan revision
        if action_name == "ui_audit" and status == "failed":
            missing = result.get("missing_classes", [])
            hint = f" Missing CSS: {missing}" if missing else ""
            revisions = state.get("plan_revision_count", 0)
            if revisions < self._max_revisions:
                return (
                    f"UI consistency FAILED.{hint} Call 'revise_plan' with feedback "
                    "about the missing elements."
                )
            return (
                f"UI consistency FAILED.{hint} Max plan revisions reached "
                f"({revisions}/{self._max_revisions}). Consider 'escalate_human' or 'done'."
            )

        # Track plan revisions
        if action_name in ("revise_plan", "generate_plan"):
            state["plan_revision_count"] = state.get("plan_revision_count", 0) + (
                1 if action_name == "revise_plan" else 0
            )
            if state.get("plan_revision_count", 0) >= self._max_revisions:
                return (
                    f"Max plan revisions ({self._max_revisions}) reached. "
                    "Approve the current plan or escalate_human."
                )

        return ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_system_prompt(self, mode: str) -> str:
        """Build system prompt, injecting memory context if available."""
        base = self._system
        if self._mem_ctx and self._mem_ctx.context_summary:
            base = (
                base
                + "\n\nMIGRATION MEMORY (apply these proven patterns and preferences):\n"
                + self._mem_ctx.context_summary
            )
        if mode == "tool_use":
            base += (
                "\n\n[TOOL-USE MODE] Use the provided tools to orchestrate the pipeline. "
                "Call one tool per turn. Do not write THOUGHT/ACTION/PARAMS text."
            )
        else:
            base += (
                "\n\n[REACT TEXT MODE] Respond strictly in this format:\n"
                "THOUGHT: <your reasoning>\n"
                "ACTION: <action_name>\n"
                "PARAMS: {\"key\": \"value\"}"
            )
        return base

    def _build_initial_message(self, state: dict) -> str:
        """Build the opening user message describing current pipeline state."""
        lines = ["Pipeline orchestration starting. Current state:"]
        lines.append(json.dumps(
            {k: v for k, v in state.items() if k != "exit_dict"},
            indent=2, default=str,
        ))
        lines.append("\nBegin by calling scope_feature to scan the source folder.")
        return "\n".join(lines)

    def _escalate(self, reason: str, state: dict) -> dict:
        """Ask the human operator to resolve an issue, then return an error dict."""
        logger.warning("Orchestrator escalating to human: %s", reason)
        if self._escalate_on_fail:
            print(f"\n[ORCHESTRATOR ESCALATION] {reason}")
            try:
                input("Press Enter after resolving the issue to continue, or Ctrl+C to abort: ")
            except (EOFError, KeyboardInterrupt):
                pass
        return {
            "status": "escalated",
            "reason": reason,
            "pipeline_state": state,
        }

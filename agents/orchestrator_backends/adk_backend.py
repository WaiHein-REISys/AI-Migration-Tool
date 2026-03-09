"""
Google ADK Orchestration Backend
==================================
Optional backend that wraps the AI Migration Tool's pipeline stages as
Google Agent Development Kit (ADK) Tool objects and delegates workflow
control to an ADK Agent.

Activated when orchestration.backend == "google_adk".

Soft dependency — if google-adk is not installed or agent initialisation
fails, OrchestratorAgent falls back to the internal backend with a warning.

Install:
    pip install google-adk

Usage note:
    This backend requires a live LLM provider (Anthropic, OpenAI, or
    Vertex AI are recommended for ADK).  It honours the same LLMConfig
    as the rest of the pipeline.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agents.llm.registry import LLMRouter
    from agents.memory_store import MemoryContext

logger = logging.getLogger(__name__)

_ADK_AVAILABLE: bool | None = None  # resolved lazily


def _check_adk() -> bool:
    """Return True if google-adk is installed."""
    global _ADK_AVAILABLE  # noqa: PLW0603
    if _ADK_AVAILABLE is None:
        try:
            import google.adk  # type: ignore  # noqa: F401
            _ADK_AVAILABLE = True
        except ImportError:
            _ADK_AVAILABLE = False
    return _ADK_AVAILABLE


class ADKOrchestrationBackend:
    """
    Wraps pipeline stages as Google ADK Tool objects and runs an ADK Agent
    to orchestrate the migration workflow.

    Requires: pip install google-adk
    Falls back to InternalOrchestrationBackend if google-adk is not available.
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
        self._available  = _check_adk()

        if not self._available:
            logger.warning(
                "ADKOrchestrationBackend: google-adk is not installed "
                "(pip install google-adk). Falling back to internal backend."
            )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, state: dict) -> dict:
        """
        Run the Google ADK orchestration loop.

        Falls back to InternalOrchestrationBackend if ADK is unavailable
        or initialisation fails.
        """
        if not self._available:
            return self._fallback(state)

        try:
            return self._run_adk(state)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "ADKOrchestrationBackend: agent execution failed (%s). "
                "Falling back to internal backend.", exc
            )
            return self._fallback(state)

    # ------------------------------------------------------------------
    # ADK execution
    # ------------------------------------------------------------------

    def _run_adk(self, state: dict) -> dict:
        """Initialise ADK Agent with tools and run it."""
        from google.adk.agents import Agent  # type: ignore
        from google.adk.runners import InProcessRunner  # type: ignore
        from google.adk.sessions import InMemorySessionService  # type: ignore

        adk_tools = self._build_adk_tools(state)
        system_instruction = self._build_system_prompt()

        agent = Agent(
            name="migration_orchestrator",
            model=self._resolve_adk_model(),
            description="Orchestrates the AI Migration Tool pipeline.",
            instruction=system_instruction,
            tools=adk_tools,
        )

        session_service = InMemorySessionService()
        runner = InProcessRunner(
            agent=agent,
            session_service=session_service,
        )

        initial_message = self._build_initial_message(state)
        app_name = "ai_migration_tool"
        user_id  = "orchestrator"

        session = session_service.create_session(
            app_name=app_name, user_id=user_id
        )

        # Collect ADK events until the session ends
        from google.adk.types import Content, Part  # type: ignore

        final_response = ""
        for event in runner.run(
            user_id=user_id,
            session_id=session.id,
            new_message=Content(role="user", parts=[Part(text=initial_message)]),
        ):
            if event.is_final_response() and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text"):
                        final_response = part.text

        logger.info("ADK orchestration complete. Final response: %s", final_response[:200])
        return state.get("exit_dict", {"status": "success"})

    # ------------------------------------------------------------------
    # ADK tool construction
    # ------------------------------------------------------------------

    def _build_adk_tools(self, state: dict) -> list:
        """Wrap each registered pipeline action as a google.adk Tool."""
        from google.adk.tools import FunctionTool  # type: ignore

        adk_tools = []
        for action_name, action_fn in self._actions.items():
            # Build a closure capturing action_name and action_fn
            tool = self._make_tool(action_name, action_fn, state)
            adk_tools.append(tool)
        return adk_tools

    @staticmethod
    def _make_tool(
        action_name: str,
        action_fn: Callable,
        state: dict,
    ) -> Any:
        """Create a google.adk FunctionTool wrapping a single pipeline action."""
        from google.adk.tools import FunctionTool  # type: ignore

        def wrapper(**kwargs: Any) -> str:
            """Execute pipeline action and return JSON result."""
            try:
                result = action_fn(state, **kwargs)
                return json.dumps(result, default=str)
            except Exception as exc:  # noqa: BLE001
                logger.error("ADK tool '%s' error: %s", action_name, exc)
                return json.dumps({"status": "error", "error": str(exc)})

        wrapper.__name__ = action_name
        wrapper.__doc__ = f"Execute pipeline stage: {action_name.replace('_', ' ')}."
        return FunctionTool(func=wrapper)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_adk_model(self) -> str:
        """
        Map the configured LLM provider to an ADK-compatible model string.
        ADK uses provider-prefixed model identifiers.
        """
        provider = self._router.provider_name
        model    = self._router.model_name

        # ADK model format examples:
        #   "gemini-2.0-flash"                    → Google (no prefix needed)
        #   "models/gemini-2.0-flash"              → Vertex AI
        #   "anthropic/claude-opus-4-5"            → Anthropic via ADK
        #   "openai/gpt-4o"                        → OpenAI via ADK
        if provider in ("vertex_ai",):
            return model  # Gemini model names work directly with ADK
        if provider == "anthropic":
            return f"anthropic/{model}"
        if provider == "openai":
            return f"openai/{model}"

        # Fallback: use the raw model name and hope ADK resolves it
        logger.warning(
            "ADKOrchestrationBackend: provider '%s' may not be natively supported "
            "by google-adk. Passing model='%s' directly.", provider, model
        )
        return model

    def _build_system_prompt(self) -> str:
        """Build system instruction, injecting memory context if available."""
        instruction = self._system
        if self._mem_ctx and self._mem_ctx.context_summary:
            instruction = (
                instruction
                + "\n\nMIGRATION MEMORY (apply these proven patterns and preferences):\n"
                + self._mem_ctx.context_summary
            )
        instruction += (
            "\n\n[ADK MODE] Use the provided tools to orchestrate the pipeline. "
            "Call one tool at a time. When all stages are complete, call the "
            "'done' tool with a brief summary."
        )
        return instruction

    def _build_initial_message(self, state: dict) -> str:
        """Build the opening message for the ADK session."""
        lines = ["Migration pipeline orchestration starting. Current state:"]
        lines.append(json.dumps(
            {k: v for k, v in state.items() if k != "exit_dict"},
            indent=2, default=str,
        ))
        lines.append(
            "\nBegin by calling scope_feature to scan the source feature folder."
        )
        return "\n".join(lines)

    def _fallback(self, state: dict) -> dict:
        """Fall back to the InternalOrchestrationBackend."""
        from agents.orchestrator_backends.internal_backend import (
            InternalOrchestrationBackend,
        )

        logger.info(
            "ADKOrchestrationBackend: using internal backend as fallback."
        )
        backend = InternalOrchestrationBackend(
            llm_router=self._router,
            system_prompt=self._system,
            action_registry=self._actions,
            orchestration_config=self._config,
            memory_context=self._mem_ctx,
        )
        return backend.run(state)

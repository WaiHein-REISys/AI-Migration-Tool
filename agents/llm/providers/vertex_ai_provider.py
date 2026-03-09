"""
Vertex AI / Gemini Provider
============================
Supports Google Gemini models via:
  - Gemini API (google-generativeai SDK) — simpler, no GCP project needed
  - Vertex AI SDK (vertexai package) — uses GCP service account / ADC

Auto-detection priority:
  GOOGLE_API_KEY                                    → Gemini API
  GOOGLE_CLOUD_PROJECT + GOOGLE_APPLICATION_CREDENTIALS → Vertex AI SDK

Required env vars (pick one path):
  Gemini API:   GOOGLE_API_KEY
  Vertex AI:    GOOGLE_CLOUD_PROJECT  +  GOOGLE_APPLICATION_CREDENTIALS

Optional env vars:
  LLM_MODEL          – model id (default: gemini-2.0-flash)
  GOOGLE_CLOUD_REGION – Vertex AI region (default: us-central1)
  LLM_MAX_TOKENS     – max tokens (default: 8192)

Install (pick one):
  pip install google-generativeai       # Gemini API
  pip install google-cloud-aiplatform   # Vertex AI

This provider supports native tool/function calling (supports_tool_use → True).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from agents.llm.base import (
    BaseLLMProvider,
    LLMConfig,
    LLMMessage,
    LLMNotAvailableError,
    LLMProviderError,
    LLMResponse,
    ToolCall,
    ToolDefinition,
)

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-2.0-flash"
_DEFAULT_REGION = "us-central1"


class VertexAIProvider(BaseLLMProvider):
    """
    Google Gemini / Vertex AI models.

    Uses the google-generativeai SDK when GOOGLE_API_KEY is set
    (Gemini API path), or the vertexai SDK when a GCP project is
    configured (Vertex AI path).
    """

    def _setup(self) -> None:
        self._backend: str = "none"  # "gemini_api" | "vertex_ai" | "none"

        api_key = self.config.api_key or os.environ.get("GOOGLE_API_KEY", "")
        gcp_project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        gcp_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
        region = os.environ.get("GOOGLE_CLOUD_REGION", _DEFAULT_REGION)

        model = self.config.model or _DEFAULT_MODEL

        # --- Path 1: Gemini API via google-generativeai ---
        if api_key:
            try:
                import google.generativeai as genai  # type: ignore
                genai.configure(api_key=api_key)
                self._client = genai.GenerativeModel(model)
                self._genai = genai
                self._backend = "gemini_api"
                logger.info(
                    "VertexAIProvider ready (Gemini API): model=%s", model
                )
                return
            except ImportError:
                logger.warning(
                    "VertexAIProvider: 'google-generativeai' not installed. "
                    "pip install google-generativeai"
                )

        # --- Path 2: Vertex AI via vertexai SDK ---
        if gcp_project:
            try:
                import vertexai  # type: ignore
                from vertexai.generative_models import GenerativeModel  # type: ignore

                vertexai.init(project=gcp_project, location=region)
                self._client = GenerativeModel(model)
                self._backend = "vertex_ai"
                logger.info(
                    "VertexAIProvider ready (Vertex AI): model=%s project=%s region=%s",
                    model, gcp_project, region,
                )
                return
            except ImportError:
                logger.warning(
                    "VertexAIProvider: 'vertexai' package not installed. "
                    "pip install google-cloud-aiplatform"
                )

        logger.warning(
            "VertexAIProvider: no credentials found. "
            "Set GOOGLE_API_KEY (Gemini API) or GOOGLE_CLOUD_PROJECT "
            "(Vertex AI). Provider will be unavailable."
        )
        self._client = None

    # ------------------------------------------------------------------
    # Text completion
    # ------------------------------------------------------------------

    def complete(
        self,
        system: str,
        messages: list[LLMMessage],
    ) -> LLMResponse:
        if not self._client:
            raise LLMNotAvailableError(
                "VertexAIProvider is not configured. "
                "Set GOOGLE_API_KEY or GOOGLE_CLOUD_PROJECT."
            )

        prompt = self._build_prompt(system, messages)

        try:
            response = self._client.generate_content(prompt)
            text = response.text or ""
            in_tok, out_tok = self._parse_tokens(response)
            return LLMResponse(
                text=text,
                model=self.config.model or _DEFAULT_MODEL,
                provider="vertex_ai",
                input_tokens=in_tok,
                output_tokens=out_tok,
                raw=response,
            )
        except Exception as exc:
            raise LLMProviderError(f"Vertex AI / Gemini error: {exc}") from exc

    # ------------------------------------------------------------------
    # Native tool-use
    # ------------------------------------------------------------------

    def supports_tool_use(self) -> bool:
        return True

    def complete_with_tools(
        self,
        system: str,
        messages: list[LLMMessage],
        tools: list[ToolDefinition],
    ) -> LLMResponse:
        """
        Call Gemini with function declarations. Returns an LLMResponse with
        .tool_calls populated when the model chose to invoke a function,
        or .text populated for a regular text response.
        """
        if not self._client:
            raise LLMNotAvailableError(
                "VertexAIProvider is not configured. "
                "Set GOOGLE_API_KEY or GOOGLE_CLOUD_PROJECT."
            )

        # Build Gemini function declarations
        function_declarations = [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in tools
        ]

        try:
            if self._backend == "gemini_api":
                return self._complete_with_tools_genai(
                    system, messages, function_declarations
                )
            else:
                return self._complete_with_tools_vertex(
                    system, messages, function_declarations
                )
        except Exception as exc:
            raise LLMProviderError(
                f"Vertex AI / Gemini tool-use error: {exc}"
            ) from exc

    def _complete_with_tools_genai(
        self,
        system: str,
        messages: list[LLMMessage],
        function_declarations: list[dict],
    ) -> LLMResponse:
        """Tool-use via google-generativeai (Gemini API)."""
        import google.generativeai as genai  # type: ignore
        from google.generativeai.types import content_types  # type: ignore

        tool = genai.protos.Tool(
            function_declarations=[
                genai.protos.FunctionDeclaration(**fd)
                for fd in function_declarations
            ]
        )
        # Re-create model with tools configured
        model = genai.GenerativeModel(
            model_name=self.config.model or _DEFAULT_MODEL,
            tools=[tool],
            system_instruction=system,
        )
        sdk_messages = self._messages_to_genai(messages)
        response = model.generate_content(sdk_messages)
        return self._parse_genai_response(response)

    def _complete_with_tools_vertex(
        self,
        system: str,
        messages: list[LLMMessage],
        function_declarations: list[dict],
    ) -> LLMResponse:
        """Tool-use via vertexai SDK (Vertex AI)."""
        from vertexai.generative_models import (  # type: ignore
            FunctionDeclaration, GenerativeModel, Tool,
        )

        vertex_tool = Tool(
            function_declarations=[
                FunctionDeclaration(
                    name=fd["name"],
                    description=fd["description"],
                    parameters=fd["parameters"],
                )
                for fd in function_declarations
            ]
        )
        model = GenerativeModel(
            model_name=self.config.model or _DEFAULT_MODEL,
            tools=[vertex_tool],
            system_instruction=system,
        )
        prompt = self._build_prompt("", messages)
        response = model.generate_content(prompt)
        return self._parse_genai_response(response)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(system: str, messages: list[LLMMessage]) -> str:
        """Merge system prompt + messages into a single string prompt."""
        parts: list[str] = []
        if system:
            parts.append(f"[System]\n{system}\n")
        for m in messages:
            parts.append(f"[{m.role.capitalize()}]\n{m.content}")
        return "\n\n".join(parts)

    @staticmethod
    def _messages_to_genai(messages: list[LLMMessage]) -> list[dict]:
        """Convert LLMMessage list to Gemini multi-turn format."""
        role_map = {"user": "user", "assistant": "model", "system": "user"}
        return [
            {"role": role_map.get(m.role, "user"), "parts": [m.content]}
            for m in messages
        ]

    @staticmethod
    def _parse_tokens(response: Any) -> tuple[int, int]:
        """Extract token counts from response.usage_metadata (if available)."""
        try:
            meta = response.usage_metadata
            return (
                getattr(meta, "prompt_token_count", 0) or 0,
                getattr(meta, "candidates_token_count", 0) or 0,
            )
        except Exception:
            return 0, 0

    def _parse_genai_response(self, response: Any) -> LLMResponse:
        """Parse a Gemini response into an LLMResponse (text or tool calls)."""
        in_tok, out_tok = self._parse_tokens(response)
        model_name = self.config.model or _DEFAULT_MODEL

        # Check for function call in the first candidate
        tool_calls: list[ToolCall] = []
        try:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    # fc.args is a MapComposite — convert to plain dict
                    args = dict(fc.args) if fc.args else {}
                    tool_calls.append(
                        ToolCall(
                            tool_name=fc.name,
                            tool_input=args,
                            tool_call_id=None,  # Gemini does not return call IDs
                        )
                    )
        except (AttributeError, IndexError):
            pass

        if tool_calls:
            return LLMResponse(
                text="",
                model=model_name,
                provider="vertex_ai",
                input_tokens=in_tok,
                output_tokens=out_tok,
                raw=response,
                tool_calls=tool_calls,
            )

        # Regular text response
        try:
            text = response.text or ""
        except Exception:
            text = ""
        return LLMResponse(
            text=text,
            model=model_name,
            provider="vertex_ai",
            input_tokens=in_tok,
            output_tokens=out_tok,
            raw=response,
        )

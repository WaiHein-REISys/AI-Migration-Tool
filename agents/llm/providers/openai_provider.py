"""
OpenAI Provider
===============
Supports OpenAI GPT models via the official openai Python SDK.

Required env vars:
    OPENAI_API_KEY       – your OpenAI API key

Optional env vars:
    LLM_MODEL            – model id (default: gpt-4o)
    LLM_MAX_TOKENS       – max tokens (default: 8192)
    LLM_BASE_URL         – custom base URL (Azure OpenAI, proxy, etc.)
    LLM_API_VERSION      – Azure OpenAI api-version (e.g. 2024-02-01)

Install:
    pip install openai
"""

from __future__ import annotations

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


class OpenAIProvider(BaseLLMProvider):
    """OpenAI GPT models via the `openai` Python SDK."""

    def _setup(self) -> None:
        api_key = self.config.api_key or os.environ.get("OPENAI_API_KEY", "")

        if not api_key:
            logger.warning(
                "OpenAIProvider: OPENAI_API_KEY not set. Provider will be unavailable."
            )
            self._client = None
            return

        try:
            import openai  # type: ignore
            kwargs: dict[str, Any] = {"api_key": api_key}
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            # Azure OpenAI uses AzureOpenAI client
            if self.config.api_version:
                self._client = openai.AzureOpenAI(
                    api_key=api_key,
                    azure_endpoint=self.config.base_url or os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
                    api_version=self.config.api_version,
                )
                logger.info(
                    "OpenAIProvider ready (Azure): model=%s endpoint=%s",
                    self.config.model, self.config.base_url
                )
            else:
                self._client = openai.OpenAI(**kwargs)
                logger.info(
                    "OpenAIProvider ready: model=%s base_url=%s",
                    self.config.model,
                    self.config.base_url or "https://api.openai.com",
                )
        except ImportError:
            logger.warning(
                "OpenAIProvider: 'openai' package not installed. pip install openai"
            )
            self._client = None

    def complete(
        self,
        system: str,
        messages: list[LLMMessage],
    ) -> LLMResponse:
        if not self._client:
            raise LLMNotAvailableError(
                "OpenAIProvider is not configured. "
                "Set OPENAI_API_KEY and pip install openai."
            )

        sdk_messages = [{"role": "system", "content": system}]
        sdk_messages += [{"role": m.role, "content": m.content} for m in messages]

        try:
            response = self._client.chat.completions.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                messages=sdk_messages,
            )
            text = response.choices[0].message.content or ""
            usage = response.usage
            return LLMResponse(
                text=text,
                model=response.model,
                provider="openai",
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                raw=response,
            )
        except Exception as exc:
            # openai raises openai.OpenAIError and subclasses
            raise LLMProviderError(f"OpenAI API error: {exc}") from exc

    # ------------------------------------------------------------------
    # Native tool-use (OpenAI function_calling / tools API)
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
        Call the OpenAI API with function/tool definitions. Returns an
        LLMResponse with .tool_calls populated when the model chose to
        invoke a function, or .text for a regular text response.
        """
        if not self._client:
            raise LLMNotAvailableError(
                "OpenAIProvider is not configured. "
                "Set OPENAI_API_KEY and pip install openai."
            )

        # Convert ToolDefinition → OpenAI tools format
        sdk_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]
        sdk_messages = [{"role": "system", "content": system}]
        sdk_messages += [{"role": m.role, "content": m.content} for m in messages]

        try:
            response = self._client.chat.completions.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                messages=sdk_messages,
                tools=sdk_tools,
                tool_choice="auto",
            )

            choice = response.choices[0]
            usage = response.usage

            # Parse tool calls from the response
            oai_tool_calls = getattr(choice.message, "tool_calls", None) or []
            tool_calls: list[ToolCall] = []
            for tc in oai_tool_calls:
                try:
                    import json as _json
                    args = _json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                tool_calls.append(
                    ToolCall(
                        tool_name=tc.function.name,
                        tool_input=args,
                        tool_call_id=tc.id,
                    )
                )

            text = choice.message.content or ""
            return LLMResponse(
                text=text,
                model=response.model,
                provider="openai",
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                raw=response,
                tool_calls=tool_calls if tool_calls else None,
            )
        except Exception as exc:
            raise LLMProviderError(f"OpenAI tool-use API error: {exc}") from exc

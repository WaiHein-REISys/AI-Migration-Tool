"""
Anthropic Claude Provider
==========================
Supports all Claude models via the official Anthropic Python SDK.

Required env vars:
    ANTHROPIC_API_KEY    – your Anthropic API key

Optional env vars:
    LLM_MODEL            – model id (default: claude-opus-4-5)
    LLM_MAX_TOKENS       – max tokens (default: 8192)
    LLM_BASE_URL         – custom endpoint (proxy / enterprise)

Install:
    pip install anthropic
"""

from __future__ import annotations

import logging
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


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude via the `anthropic` Python SDK."""

    def _setup(self) -> None:
        api_key = self.config.api_key
        if not api_key:
            import os
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")

        if not api_key:
            logger.warning(
                "AnthropicProvider: ANTHROPIC_API_KEY not set. "
                "Provider will be unavailable."
            )
            self._client = None
            return

        try:
            import anthropic  # type: ignore
            kwargs: dict[str, Any] = {"api_key": api_key}
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            self._client = anthropic.Anthropic(**kwargs)
            logger.info(
                "AnthropicProvider ready: model=%s base_url=%s",
                self.config.model,
                self.config.base_url or "https://api.anthropic.com",
            )
        except ImportError:
            logger.warning(
                "AnthropicProvider: 'anthropic' package not installed. "
                "pip install anthropic"
            )
            self._client = None

    def complete(
        self,
        system: str,
        messages: list[LLMMessage],
    ) -> LLMResponse:
        if not self._client:
            raise LLMNotAvailableError(
                "AnthropicProvider is not configured. "
                "Set ANTHROPIC_API_KEY and pip install anthropic."
            )

        import anthropic  # type: ignore

        sdk_messages = [{"role": m.role, "content": m.content} for m in messages]
        try:
            response = self._client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                system=system,
                messages=sdk_messages,
            )
            text = response.content[0].text
            return LLMResponse(
                text=text,
                model=response.model,
                provider="anthropic",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                raw=response,
            )
        except anthropic.APIError as exc:
            raise LLMProviderError(
                f"Anthropic API error [{exc.status_code}]: {exc.message}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMProviderError(f"Anthropic connection error: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMProviderError(f"Anthropic rate limit exceeded: {exc}") from exc
        except anthropic.AuthenticationError as exc:
            raise LLMProviderError(
                f"Anthropic authentication failed — check ANTHROPIC_API_KEY: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Native tool-use (Anthropic tool_use API)
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
        Call Claude with tool definitions. Returns an LLMResponse with
        .tool_calls populated when Claude chose to invoke a tool, or
        .text populated for a regular text response.
        """
        if not self._client:
            raise LLMNotAvailableError(
                "AnthropicProvider is not configured. "
                "Set ANTHROPIC_API_KEY and pip install anthropic."
            )

        import anthropic  # type: ignore

        # Convert ToolDefinition → Anthropic tools format
        sdk_tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]
        sdk_messages = [{"role": m.role, "content": m.content} for m in messages]

        try:
            response = self._client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                system=system,
                messages=sdk_messages,
                tools=sdk_tools,
                tool_choice={"type": "auto"},
            )

            # Parse tool_use blocks from response.content
            tool_calls: list[ToolCall] = []
            text_parts: list[str] = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            tool_name=block.name,
                            tool_input=dict(block.input) if block.input else {},
                            tool_call_id=block.id,
                        )
                    )
                elif block.type == "text":
                    text_parts.append(block.text)

            return LLMResponse(
                text=" ".join(text_parts),
                model=response.model,
                provider="anthropic",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                raw=response,
                tool_calls=tool_calls if tool_calls else None,
            )
        except anthropic.APIError as exc:
            raise LLMProviderError(
                f"Anthropic tool-use API error [{exc.status_code}]: {exc.message}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMProviderError(
                f"Anthropic connection error (tool-use): {exc}"
            ) from exc
        except anthropic.RateLimitError as exc:
            raise LLMProviderError(
                f"Anthropic rate limit exceeded (tool-use): {exc}"
            ) from exc
        except anthropic.AuthenticationError as exc:
            raise LLMProviderError(
                f"Anthropic authentication failed (tool-use) — check ANTHROPIC_API_KEY: {exc}"
            ) from exc

"""
OpenAI-Compatible Endpoint Provider
=====================================
Supports any server that exposes an OpenAI-compatible /v1/chat/completions API.

This covers:
  - LM Studio          (http://localhost:1234/v1)
  - Ollama /v1 compat  (http://localhost:11434/v1)
  - vLLM               (http://localhost:8000/v1)
  - Anyscale Endpoints (https://api.endpoints.anyscale.com/v1)
  - Together AI        (https://api.together.xyz/v1)
  - Fireworks AI       (https://api.fireworks.ai/inference/v1)
  - Azure OpenAI       (use api_version for Azure mode)
  - Any local proxy or corporate gateway

Required env vars:
    LLM_BASE_URL         – server base URL (e.g. http://localhost:1234/v1)

Optional env vars:
    LLM_API_KEY          – API key (default: "not-needed" for local servers)
    LLM_MODEL            – model id (default: "local-model")
    LLM_MAX_TOKENS       – max tokens (default: 8192)
    LLM_API_VERSION      – Azure api-version header (Azure only)

Install:
    pip install openai       # reuses the openai SDK
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
)

logger = logging.getLogger(__name__)


class OpenAICompatProvider(BaseLLMProvider):
    """
    OpenAI-compatible endpoint using the openai SDK with a custom base_url.
    Works with LM Studio, vLLM, Ollama /v1, Together AI, Fireworks, etc.
    """

    def _setup(self) -> None:
        base_url = self.config.base_url or os.environ.get("LLM_BASE_URL", "")
        if not base_url:
            logger.warning(
                "OpenAICompatProvider: LLM_BASE_URL not set. Provider will be unavailable."
            )
            self._client = None
            return

        # Many local servers don't require a real API key
        api_key = (
            self.config.api_key
            or os.environ.get("LLM_API_KEY", "")
            or "not-needed"
        )

        try:
            import openai  # type: ignore

            if self.config.api_version:
                # Azure OpenAI path
                self._client = openai.AzureOpenAI(
                    api_key=api_key,
                    azure_endpoint=base_url,
                    api_version=self.config.api_version,
                )
                logger.info(
                    "OpenAICompatProvider ready (Azure): endpoint=%s model=%s api_version=%s",
                    base_url, self.config.model, self.config.api_version,
                )
            else:
                self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
                logger.info(
                    "OpenAICompatProvider ready: base_url=%s model=%s",
                    base_url, self.config.model,
                )
        except ImportError:
            logger.warning(
                "OpenAICompatProvider: 'openai' package not installed. pip install openai"
            )
            self._client = None

    def complete(
        self,
        system: str,
        messages: list[LLMMessage],
    ) -> LLMResponse:
        if not self._client:
            raise LLMNotAvailableError(
                "OpenAICompatProvider not configured. Set LLM_BASE_URL and pip install openai."
            )

        sdk_messages: list[dict[str, str]] = [{"role": "system", "content": system}]
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
                model=getattr(response, "model", self.config.model),
                provider="openai_compat",
                input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
                raw=response,
            )
        except Exception as exc:
            raise LLMProviderError(
                f"OpenAI-compat endpoint error ({self.config.base_url}): {exc}"
            ) from exc

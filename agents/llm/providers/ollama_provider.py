"""
Ollama Native Provider
=======================
Runs local models via the Ollama REST API (native protocol, no OpenAI compat layer).
Supports streaming and all models available in your Ollama instance.

Required:
    Ollama must be running locally: https://ollama.ai

Optional env vars:
    OLLAMA_MODEL         – model name (e.g. "llama3", "codellama", "mistral", "deepseek-coder")
    OLLAMA_HOST          – server URL (default: http://localhost:11434)
    LLM_MAX_TOKENS       – max tokens to generate (default: 8192)
    LLM_TEMPERATURE      – temperature (default: 0.2)

Install (choose one):
    pip install ollama              # official Ollama Python client (preferred)
    # Falls back to httpx/requests if ollama package not installed

Popular models to try:
    ollama pull codellama           # Meta CodeLlama (code-focused)
    ollama pull deepseek-coder      # DeepSeek Coder (strong on code)
    ollama pull llama3              # Meta Llama 3
    ollama pull mistral             # Mistral 7B
    ollama pull phi3                # Microsoft Phi-3
    ollama pull qwen2.5-coder       # Qwen 2.5 Coder
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
)

logger = logging.getLogger(__name__)


class OllamaProvider(BaseLLMProvider):
    """
    Local Ollama models via:
      1. Official `ollama` Python client (preferred)
      2. Raw HTTP fallback via `httpx` or `requests`
    """

    def _setup(self) -> None:
        model = self.config.model or os.environ.get("OLLAMA_MODEL", "")
        host  = self.config.ollama_host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")

        if not model:
            logger.warning(
                "OllamaProvider: OLLAMA_MODEL not set. Provider will be unavailable."
            )
            self._client = None
            return

        # Store config for later use
        self._ollama_host  = host
        self._use_sdk      = False

        # Try official ollama package first
        try:
            import ollama  # type: ignore
            # Pass timeout so long code-generation requests don't time out (default 120s is often too short)
            timeout = getattr(self.config, "timeout_seconds", 120)
            self._client   = ollama.Client(host=host, timeout=timeout)
            self._use_sdk  = True
            logger.info(
                "OllamaProvider ready (SDK): host=%s model=%s", host, model
            )
            return
        except ImportError:
            pass

        # Fall back to httpx
        try:
            import httpx  # type: ignore
            self._client  = httpx.Client(base_url=host, timeout=self.config.timeout_seconds)
            self._use_sdk = False
            logger.info(
                "OllamaProvider ready (httpx): host=%s model=%s", host, model
            )
            return
        except ImportError:
            pass

        # Fall back to requests
        try:
            import requests as _requests  # type: ignore
            self._client  = _requests
            self._http_url = f"{host}/api/chat"
            self._use_sdk  = False
            self._use_requests = True
            logger.info(
                "OllamaProvider ready (requests): host=%s model=%s", host, model
            )
            return
        except ImportError:
            pass

        logger.warning(
            "OllamaProvider: no HTTP client available. "
            "pip install ollama  OR  pip install httpx"
        )
        self._client = None

    def complete(
        self,
        system: str,
        messages: list[LLMMessage],
    ) -> LLMResponse:
        if not self._client:
            raise LLMNotAvailableError(
                "OllamaProvider not configured. "
                "Set OLLAMA_MODEL, ensure Ollama is running, "
                "and pip install ollama (or httpx)."
            )

        model = self.config.model

        if self._use_sdk:
            return self._complete_sdk(system, messages, model)
        else:
            return self._complete_http(system, messages, model)

    # ------------------------------------------------------------------
    # SDK path
    # ------------------------------------------------------------------

    def _complete_sdk(
        self,
        system: str,
        messages: list[LLMMessage],
        model: str,
    ) -> LLMResponse:
        import ollama  # type: ignore

        sdk_messages = [{"role": "system", "content": system}]
        sdk_messages += [{"role": m.role, "content": m.content} for m in messages]

        try:
            response = self._client.chat(
                model=model,
                messages=sdk_messages,
                options={
                    "num_predict": self.config.max_tokens,
                    "temperature": self.config.temperature,
                },
            )
            text = response["message"]["content"]
            return LLMResponse(
                text=text,
                model=model,
                provider="ollama",
                input_tokens=response.get("prompt_eval_count", 0),
                output_tokens=response.get("eval_count", 0),
                raw=response,
            )
        except Exception as exc:
            raise LLMProviderError(
                f"Ollama SDK error (model={model}, host={self._ollama_host}): {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # HTTP fallback path (httpx or requests)
    # ------------------------------------------------------------------

    def _complete_http(
        self,
        system: str,
        messages: list[LLMMessage],
        model: str,
    ) -> LLMResponse:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                *[{"role": m.role, "content": m.content} for m in messages],
            ],
            "options": {
                "num_predict": self.config.max_tokens,
                "temperature": self.config.temperature,
            },
            "stream": False,
        }

        try:
            if getattr(self, "_use_requests", False):
                resp = self._client.post(
                    self._http_url,
                    json=payload,
                    timeout=self.config.timeout_seconds,
                )
                resp.raise_for_status()
                data = resp.json()
            else:
                # httpx
                resp = self._client.post("/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()

            text = data["message"]["content"]
            return LLMResponse(
                text=text,
                model=model,
                provider="ollama",
                input_tokens=data.get("prompt_eval_count", 0),
                output_tokens=data.get("eval_count", 0),
                raw=data,
            )
        except Exception as exc:
            raise LLMProviderError(
                f"Ollama HTTP error (model={model}, host={self._ollama_host}): {exc}"
            ) from exc

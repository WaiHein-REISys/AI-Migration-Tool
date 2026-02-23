"""
LLM Provider Base
=================
Abstract interface that every LLM provider must implement.
All agents talk to this interface — never to a concrete SDK directly.

The interface is intentionally minimal and maps to the common
"messages" pattern supported by every modern LLM API:

    response_text = provider.complete(
        system=<system prompt str>,
        messages=[{"role": "user", "content": "..."}],
    )

Providers handle SDK-specific translation internally.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LLMMessage:
    """A single chat message."""
    role: str       # "user" | "assistant" | "system"
    content: str


@dataclass
class LLMResponse:
    """Normalised response from any provider."""
    text: str
    model: str
    provider: str
    input_tokens: int  = 0
    output_tokens: int = 0
    raw: Any           = field(default=None, repr=False)


@dataclass
class LLMConfig:
    """
    Provider-agnostic configuration passed to every provider.
    Individual providers pull the fields they need and ignore the rest.
    """
    # ---- Identity ----
    provider: str        = "anthropic"          # see PROVIDER_* constants in registry.py
    model: str           = "claude-opus-4-5"    # model id / path

    # ---- Generation parameters ----
    max_tokens: int      = 8192
    temperature: float   = 0.2                  # low = more deterministic (good for code)
    top_p: float         = 1.0

    # ---- Remote API settings (Anthropic, OpenAI, custom endpoint) ----
    api_key: str         = ""                   # loaded from env if blank
    base_url: str        = ""                   # override endpoint (Azure, proxy, LM Studio, etc.)
    api_version: str     = ""                   # Azure OpenAI api-version header

    # ---- Local model settings (Ollama, LlamaCpp) ----
    model_path: str      = ""                   # absolute path to .gguf file (LlamaCpp)
    ollama_host: str     = "http://localhost:11434"  # Ollama server URL
    n_ctx: int           = 4096                 # context window (LlamaCpp)
    n_gpu_layers: int    = -1                   # GPU layers (-1 = all)

    # ---- Timeout ----
    timeout_seconds: int = 120


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BaseLLMProvider(abc.ABC):
    """
    Every LLM provider inherits from this class and implements `complete()`.
    """

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._client: Any = None
        self._setup()

    # ------------------------------------------------------------------
    # Abstract methods
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def _setup(self) -> None:
        """
        Initialise the underlying SDK client.
        Called once during __init__.  Should set self._client.
        Must NOT raise if the SDK package is missing — instead set
        self._client = None and log a warning.
        """

    @abc.abstractmethod
    def complete(
        self,
        system: str,
        messages: list[LLMMessage],
    ) -> LLMResponse:
        """
        Send a chat completion request and return a normalised LLMResponse.

        Args:
            system:   System prompt string.
            messages: Ordered list of LLMMessage (role / content).

        Returns:
            LLMResponse with at minimum .text populated.

        Raises:
            LLMProviderError: on API / SDK / timeout errors.
            LLMNotAvailableError: if provider is not configured (no key/path).
        """

    # ------------------------------------------------------------------
    # Common helpers
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """Return True if the client was initialised successfully."""
        return self._client is not None

    @property
    def provider_name(self) -> str:
        return self.config.provider

    @property
    def model_name(self) -> str:
        return self.config.model

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"provider={self.config.provider!r}, "
            f"model={self.config.model!r}, "
            f"available={self.is_available})"
        )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LLMProviderError(Exception):
    """Generic error from the LLM provider (API error, timeout, etc.)."""


class LLMNotAvailableError(LLMProviderError):
    """
    Raised when the provider has no credentials/path configured.
    Agents catch this and fall back to template-only mode.
    """

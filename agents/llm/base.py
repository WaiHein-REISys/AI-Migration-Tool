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
    # Populated only when the provider responds with native tool/function calls.
    # None for all standard text-completion responses (backwards-compatible).
    tool_calls: "list[ToolCall] | None" = field(default=None, repr=False)


@dataclass
class ToolDefinition:
    """
    Provider-agnostic tool / function definition.
    Each orchestrator action is described as a ToolDefinition when
    calling complete_with_tools() on providers that support it.
    """
    name: str
    description: str
    parameters: dict  # JSON Schema object describing the tool's input


@dataclass
class ToolCall:
    """
    A single tool-call result returned by the LLM in native tool-use mode.
    Populated in LLMResponse.tool_calls by providers that support it.
    """
    tool_name: str
    tool_input: dict
    tool_call_id: str | None = None  # populated by Anthropic / OpenAI, None for Gemini


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

    # ---- Subprocess CLI settings ----
    subprocess_cmd: str  = ""   # CLI command name or path (e.g. "claude", "codex")
    subprocess_args: list[str] = field(default_factory=list)
    subprocess_env: dict = field(default_factory=dict)
    # Extra env vars injected into the subprocess environment on every call.
    # Values may contain ${VAR} placeholders that are expanded from the host env.
    # Example (in job YAML):
    #   llm:
    #     subprocess_args:
    #       - -c
    #       - reasoning_effort="high"
    #     subprocess_env:
    #       ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY}"
    #       MY_CUSTOM_VAR:     "some-value"

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
    # Optional tool-use interface (providers override to enable native
    # function-calling; default implementations keep all existing code
    # working without change)
    # ------------------------------------------------------------------

    def supports_tool_use(self) -> bool:
        """
        Return True if this provider supports native tool / function calling
        via complete_with_tools().  Default: False (text-only providers).
        """
        return False

    def complete_with_tools(
        self,
        system: str,
        messages: list[LLMMessage],
        tools: list[ToolDefinition],
    ) -> LLMResponse:
        """
        Send a completion request with tool/function definitions.
        The LLM may respond by requesting a tool call instead of text.

        Returns an LLMResponse whose .tool_calls list is populated when
        the model chose to invoke a tool, or .text is populated for a
        regular text response.

        Raises:
            NotImplementedError: if the provider does not support tool-use.
                Override this method in providers that support it.
        """
        raise NotImplementedError(
            f"{self.provider_name!r} does not support native tool-use. "
            "Use the react_text orchestration mode instead."
        )

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

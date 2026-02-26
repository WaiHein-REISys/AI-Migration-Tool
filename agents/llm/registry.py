"""
LLM Provider Registry & Router
================================
Central hub that:
  1. Knows all available provider types
  2. Builds the right provider from an LLMConfig (or from CLI/env settings)
  3. Exposes a single `LLMRouter` class that agents use — no provider details leak out

Supported providers:
    "anthropic"   – Claude (Anthropic API)
    "openai"      – OpenAI GPT models (api.openai.com)
    "openai_compat" – Any OpenAI-compatible endpoint
                      (Azure OpenAI, LM Studio, vLLM, Ollama /v1, etc.)
    "ollama"      – Ollama REST API (native, no OpenAI compat required)
    "llamacpp"    – Local .gguf model via llama-cpp-python

Usage:
    from agents.llm.registry import LLMRouter, LLMConfig

    router = LLMRouter.from_env()          # reads env vars, auto-selects provider
    response = router.complete(system=..., messages=[...])
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from agents.llm.base import LLMConfig, LLMMessage, LLMResponse, LLMNotAvailableError

if TYPE_CHECKING:
    from agents.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider name constants
# ---------------------------------------------------------------------------
PROVIDER_ANTHROPIC       = "anthropic"
PROVIDER_OPENAI          = "openai"
PROVIDER_OPENAI_COMPAT   = "openai_compat"   # LM Studio, Azure, vLLM, Ollama /v1
PROVIDER_OLLAMA          = "ollama"
PROVIDER_LLAMACPP        = "llamacpp"


# ---------------------------------------------------------------------------
# Provider registry (lazy import to avoid hard SDK dependencies)
# ---------------------------------------------------------------------------

def _load_provider(config: LLMConfig) -> "BaseLLMProvider":
    """Instantiate the correct provider class for config.provider."""
    p = config.provider.lower().replace("-", "_")

    if p == PROVIDER_ANTHROPIC:
        from agents.llm.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(config)

    if p == PROVIDER_OPENAI:
        from agents.llm.providers.openai_provider import OpenAIProvider
        return OpenAIProvider(config)

    if p == PROVIDER_OPENAI_COMPAT:
        from agents.llm.providers.openai_compat_provider import OpenAICompatProvider
        return OpenAICompatProvider(config)

    if p == PROVIDER_OLLAMA:
        from agents.llm.providers.ollama_provider import OllamaProvider
        return OllamaProvider(config)

    if p == PROVIDER_LLAMACPP:
        from agents.llm.providers.llamacpp_provider import LlamaCppProvider
        return LlamaCppProvider(config)

    raise ValueError(
        f"Unknown provider '{config.provider}'. "
        f"Valid options: {PROVIDER_ANTHROPIC}, {PROVIDER_OPENAI}, "
        f"{PROVIDER_OPENAI_COMPAT}, {PROVIDER_OLLAMA}, {PROVIDER_LLAMACPP}"
    )


# ---------------------------------------------------------------------------
# LLMRouter — the single entry point for all agents
# ---------------------------------------------------------------------------

class LLMRouter:
    """
    Wraps a provider and exposes the same interface as BaseLLMProvider.
    Also handles:
      - fallback chain (try primary provider, fall back to secondary)
      - availability check
      - logging of every request

    Agents create one LLMRouter and call router.complete() — they never
    instantiate providers directly.
    """

    def __init__(
        self,
        primary: "BaseLLMProvider",
        fallback: "BaseLLMProvider | None" = None,
    ) -> None:
        self._primary  = primary
        self._fallback = fallback

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config: LLMConfig,
        fallback_config: LLMConfig | None = None,
    ) -> "LLMRouter":
        """Build a router directly from LLMConfig object(s)."""
        primary  = _load_provider(config)
        fallback = _load_provider(fallback_config) if fallback_config else None
        logger.info(
            "LLMRouter created: primary=%s fallback=%s",
            primary, fallback or "(none)"
        )
        return cls(primary, fallback)

    @classmethod
    def from_env(cls) -> "LLMRouter":
        """
        Auto-configure from environment variables.

        Detection order:
          1. LLM_PROVIDER env var (explicit override)
          2. LLAMACPP_MODEL_PATH => local LlamaCpp
          3. OLLAMA_MODEL        => local Ollama
          4. OPENAI_API_KEY      => OpenAI
          5. ANTHROPIC_API_KEY   => Anthropic (default)
          6. LLM_BASE_URL        => OpenAI-compat endpoint (LM Studio, vLLM, etc.)
        """
        config = cls._config_from_env()
        return cls.from_config(config)

    @classmethod
    def from_cli_args(cls, args: object) -> "LLMRouter | None":
        """
        Build a router from parsed argparse.Namespace (from main.py).
        Returns None if --no-llm flag is set.
        """
        if getattr(args, "no_llm", False):
            logger.info("--no-llm flag set. LLM disabled.")
            return None

        config = cls._config_from_env()

        # CLI args override env vars
        if getattr(args, "llm_provider", None):
            config.provider = args.llm_provider
        if getattr(args, "llm_model", None):
            config.model = args.llm_model
        if getattr(args, "llm_base_url", None):
            config.base_url = args.llm_base_url
        if getattr(args, "llm_model_path", None):
            config.model_path = args.llm_model_path
        if getattr(args, "ollama_host", None):
            config.ollama_host = args.ollama_host
        if getattr(args, "llm_max_tokens", None):
            config.max_tokens = args.llm_max_tokens
        if getattr(args, "llm_temperature", None):
            config.temperature = args.llm_temperature
        if getattr(args, "llm_timeout", None) is not None:
            config.timeout_seconds = int(args.llm_timeout)

        logger.info(
            "LLM configured from CLI: provider=%s model=%s base_url=%s",
            config.provider, config.model, config.base_url or "(default)"
        )
        return cls.from_config(config)

    # ------------------------------------------------------------------
    # Public API (mirrors BaseLLMProvider)
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        return self._primary.is_available or (
            self._fallback is not None and self._fallback.is_available
        )

    @property
    def provider_name(self) -> str:
        return self._primary.provider_name

    @property
    def model_name(self) -> str:
        return self._primary.model_name

    def complete(
        self,
        system: str,
        messages: list[LLMMessage],
    ) -> LLMResponse:
        """
        Send a completion request, trying primary then fallback.

        Raises:
            LLMNotAvailableError: if no provider is configured.
            LLMProviderError:     if all providers fail.
        """
        if not self._primary.is_available:
            if self._fallback and self._fallback.is_available:
                logger.info("Primary provider unavailable — using fallback.")
                return self._fallback.complete(system, messages)
            raise LLMNotAvailableError(
                f"No LLM provider is available. "
                f"Primary: {self._primary} | Fallback: {self._fallback}"
            )

        from agents.llm.base import LLMProviderError
        try:
            return self._primary.complete(system, messages)
        except LLMProviderError as exc:
            if self._fallback and self._fallback.is_available:
                logger.warning(
                    "Primary provider error (%s) — retrying with fallback: %s",
                    exc, self._fallback
                )
                return self._fallback.complete(system, messages)
            raise

    # ------------------------------------------------------------------
    # Env-based config builder
    # ------------------------------------------------------------------

    @staticmethod
    def _config_from_env() -> LLMConfig:
        config = LLMConfig()

        # Explicit provider override
        if os.environ.get("LLM_PROVIDER"):
            config.provider = os.environ["LLM_PROVIDER"]

        # Local LlamaCpp
        elif os.environ.get("LLAMACPP_MODEL_PATH"):
            config.provider   = PROVIDER_LLAMACPP
            config.model_path = os.environ["LLAMACPP_MODEL_PATH"]
            config.model      = os.path.basename(config.model_path)
            if os.environ.get("LLAMACPP_N_CTX"):
                config.n_ctx = int(os.environ["LLAMACPP_N_CTX"])
            if os.environ.get("LLAMACPP_N_GPU_LAYERS"):
                config.n_gpu_layers = int(os.environ["LLAMACPP_N_GPU_LAYERS"])

        # Local Ollama
        elif os.environ.get("OLLAMA_MODEL"):
            config.provider     = PROVIDER_OLLAMA
            config.model        = os.environ["OLLAMA_MODEL"]
            config.ollama_host  = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

        # OpenAI-compat custom endpoint (LM Studio, vLLM, Anyscale, etc.)
        elif os.environ.get("LLM_BASE_URL") and not os.environ.get("OPENAI_API_KEY"):
            config.provider  = PROVIDER_OPENAI_COMPAT
            config.base_url  = os.environ["LLM_BASE_URL"]
            config.model     = os.environ.get("LLM_MODEL", "local-model")
            config.api_key   = os.environ.get("LLM_API_KEY", "not-needed")

        # OpenAI
        elif os.environ.get("OPENAI_API_KEY"):
            config.provider = PROVIDER_OPENAI
            config.model    = os.environ.get("LLM_MODEL", "gpt-4o")
            config.api_key  = os.environ["OPENAI_API_KEY"]
            if os.environ.get("LLM_BASE_URL"):
                config.base_url = os.environ["LLM_BASE_URL"]

        # Anthropic (default)
        else:
            config.provider = PROVIDER_ANTHROPIC
            config.model    = os.environ.get("LLM_MODEL", "claude-opus-4-5")
            config.api_key  = os.environ.get("ANTHROPIC_API_KEY", "")

        # Universal overrides
        if os.environ.get("LLM_MODEL"):
            config.model = os.environ["LLM_MODEL"]
        if os.environ.get("LLM_MAX_TOKENS"):
            config.max_tokens = int(os.environ["LLM_MAX_TOKENS"])
        if os.environ.get("LLM_TEMPERATURE"):
            config.temperature = float(os.environ["LLM_TEMPERATURE"])
        if os.environ.get("LLM_API_VERSION"):
            config.api_version = os.environ["LLM_API_VERSION"]

        return config

"""
LLM Provider Registry & Router
================================
Central hub that:
  1. Knows all available provider types
  2. Builds the right provider from an LLMConfig (or from CLI/env settings)
  3. Exposes a single `LLMRouter` class that agents use — no provider details leak out
  4. Can probe which providers are actually available on this machine
  5. Offers an interactive provider-selection prompt for first-run / setup flows

Supported providers:
    "anthropic"    – Claude (Anthropic API)
    "openai"       – OpenAI GPT models (api.openai.com)
    "openai_compat"– Any OpenAI-compatible endpoint
                     (Azure OpenAI, LM Studio, vLLM, Ollama /v1, etc.)
    "ollama"       – Ollama REST API (native, no OpenAI compat required)
    "llamacpp"     – Local .gguf model via llama-cpp-python
    "subprocess"   – Any installed CLI tool (claude --print, codex --quiet, etc.)

Usage:
    from agents.llm.registry import LLMRouter, LLMConfig

    router = LLMRouter.from_env()          # reads env vars, auto-selects provider
    response = router.complete(system=..., messages=[...])

Interactive probe (for setup wizards):
    from agents.llm.registry import probe_available_providers, select_llm_interactively

    options = probe_available_providers()   # list of dicts describing what's found
    config  = select_llm_interactively()    # TTY picker → LLMConfig or None
"""

from __future__ import annotations

import logging
import os
import sys
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
PROVIDER_SUBPROCESS      = "subprocess"       # any installed CLI tool


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

    if p == PROVIDER_SUBPROCESS:
        from agents.llm.providers.subprocess_provider import SubprocessProvider
        return SubprocessProvider(config)

    raise ValueError(
        f"Unknown provider '{config.provider}'. "
        f"Valid options: {PROVIDER_ANTHROPIC}, {PROVIDER_OPENAI}, "
        f"{PROVIDER_OPENAI_COMPAT}, {PROVIDER_OLLAMA}, {PROVIDER_LLAMACPP}, "
        f"{PROVIDER_SUBPROCESS}"
    )


# ---------------------------------------------------------------------------
# Provider probing — lightweight availability checks (no SDK imports needed)
# ---------------------------------------------------------------------------

def probe_available_providers() -> list[dict]:
    """
    Scan the current environment for all available LLM providers.

    Returns a list of dicts, each describing one available option:
        {
            "provider": str,        # provider constant (e.g. "anthropic")
            "model":    str,        # model name / path / command
            "label":    str,        # human-readable description shown in picker
            "config":   LLMConfig,  # ready-to-use config object
        }

    Checks performed (in priority order):
        1. ANTHROPIC_API_KEY         → anthropic
        2. OPENAI_API_KEY            → openai
        3. OLLAMA_MODEL              → ollama
        4. LLAMACPP_MODEL_PATH       → llamacpp
        5. LLM_BASE_URL              → openai_compat
        6. LLM_SUBPROCESS_CMD        → subprocess (explicit)
        7. `claude` on PATH          → subprocess:claude
        8. `codex`  on PATH          → subprocess:codex
    """
    import shutil

    options: list[dict] = []

    # -- Anthropic API --
    if os.environ.get("ANTHROPIC_API_KEY"):
        model = os.environ.get("LLM_MODEL", "claude-opus-4-5")
        cfg = LLMConfig(provider=PROVIDER_ANTHROPIC, model=model,
                        api_key=os.environ["ANTHROPIC_API_KEY"])
        options.append({
            "provider": PROVIDER_ANTHROPIC,
            "model":    model,
            "label":    f"Anthropic Claude API  [{model}]  (ANTHROPIC_API_KEY)",
            "config":   cfg,
        })

    # -- OpenAI API --
    if os.environ.get("OPENAI_API_KEY"):
        model = os.environ.get("LLM_MODEL", "gpt-4o")
        cfg = LLMConfig(provider=PROVIDER_OPENAI, model=model,
                        api_key=os.environ["OPENAI_API_KEY"],
                        base_url=os.environ.get("LLM_BASE_URL", ""))
        options.append({
            "provider": PROVIDER_OPENAI,
            "model":    model,
            "label":    f"OpenAI API  [{model}]  (OPENAI_API_KEY)",
            "config":   cfg,
        })

    # -- Ollama --
    if os.environ.get("OLLAMA_MODEL"):
        model = os.environ["OLLAMA_MODEL"]
        host  = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        cfg = LLMConfig(provider=PROVIDER_OLLAMA, model=model, ollama_host=host)
        options.append({
            "provider": PROVIDER_OLLAMA,
            "model":    model,
            "label":    f"Ollama  [{model}]  ({host})",
            "config":   cfg,
        })

    # -- LlamaCpp --
    model_path = os.environ.get("LLAMACPP_MODEL_PATH", "")
    if model_path and os.path.isfile(model_path):
        model = os.path.basename(model_path)
        cfg = LLMConfig(provider=PROVIDER_LLAMACPP, model=model,
                        model_path=model_path)
        options.append({
            "provider": PROVIDER_LLAMACPP,
            "model":    model,
            "label":    f"llama.cpp  [{model}]  ({model_path})",
            "config":   cfg,
        })

    # -- OpenAI-compat custom endpoint --
    base_url = os.environ.get("LLM_BASE_URL", "")
    if base_url and not os.environ.get("OPENAI_API_KEY"):
        model = os.environ.get("LLM_MODEL", "local-model")
        cfg = LLMConfig(provider=PROVIDER_OPENAI_COMPAT, model=model,
                        base_url=base_url,
                        api_key=os.environ.get("LLM_API_KEY", "not-needed"))
        options.append({
            "provider": PROVIDER_OPENAI_COMPAT,
            "model":    model,
            "label":    f"OpenAI-compat endpoint  [{model}]  ({base_url})",
            "config":   cfg,
        })

    # -- Subprocess: explicit LLM_SUBPROCESS_CMD env var --
    subprocess_cmd = os.environ.get("LLM_SUBPROCESS_CMD", "").strip()
    if subprocess_cmd:
        resolved = shutil.which(subprocess_cmd)
        if resolved:
            model = os.environ.get("LLM_MODEL", subprocess_cmd)
            cfg = LLMConfig(provider=PROVIDER_SUBPROCESS, model=model,
                            subprocess_cmd=subprocess_cmd)
            options.append({
                "provider": PROVIDER_SUBPROCESS,
                "model":    subprocess_cmd,
                "label":    f"Subprocess CLI  [{subprocess_cmd}]  (LLM_SUBPROCESS_CMD, {resolved})",
                "config":   cfg,
            })

    # -- Subprocess: auto-detect well-known CLIs --
    for cli_name, cli_label in [
        ("claude", "Claude Code CLI  (claude --print)"),
        ("codex",  "OpenAI Codex CLI  (codex --quiet)"),
    ]:
        # Skip if already added via LLM_SUBPROCESS_CMD
        already = any(
            o["provider"] == PROVIDER_SUBPROCESS and
            os.path.basename(o["model"]).lower() == cli_name
            for o in options
        )
        if already:
            continue
        path = shutil.which(cli_name)
        if path:
            cfg = LLMConfig(provider=PROVIDER_SUBPROCESS, model=cli_name,
                            subprocess_cmd=cli_name)
            options.append({
                "provider": PROVIDER_SUBPROCESS,
                "model":    cli_name,
                "label":    f"Subprocess CLI  [{cli_label}]  ({path})",
                "config":   cfg,
            })

    return options


def select_llm_interactively(
    options: list[dict] | None = None,
    allow_none: bool = True,
) -> LLMConfig | None:
    """
    Present a numbered menu of available LLM providers and return the chosen
    LLMConfig, or None if the user selects "No LLM / template-only mode".

    Parameters
    ----------
    options :
        Pre-probed list from probe_available_providers().  If None, probing
        is performed automatically.
    allow_none :
        Whether to include "No LLM" as a choice.  Default True.

    Returns
    -------
    LLMConfig or None
    """
    if options is None:
        options = probe_available_providers()

    enc = sys.stdout.encoding or "utf-8"

    def _p(text: str) -> None:
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"))

    _p("\n  ┌─ LLM Provider Selection ──────────────────────────────────┐")

    if not options:
        _p("  │  No LLM providers detected on this machine.              │")
        _p("  │  The pipeline will run in template-only (no-LLM) mode.  │")
        _p("  └──────────────────────────────────────────────────────────┘\n")
        return None

    _p(f"  │  {len(options)} provider(s) detected. Choose one:                      │")
    _p("  └──────────────────────────────────────────────────────────┘\n")

    for i, opt in enumerate(options, start=1):
        _p(f"    {i:>2}.  {opt['label']}")

    none_idx = len(options) + 1
    if allow_none:
        _p(f"    {none_idx:>2}.  No LLM  (template-only / scaffold mode)")

    print()

    while True:
        try:
            raw = input("  Select [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

        if not raw:
            raw = "1"

        if not raw.isdigit():
            _p(f"  (Enter a number between 1 and {none_idx if allow_none else len(options)})")
            continue

        idx = int(raw)
        if 1 <= idx <= len(options):
            chosen = options[idx - 1]
            _p(f"\n  Selected: {chosen['label']}\n")
            return chosen["config"]

        if allow_none and idx == none_idx:
            _p("\n  Running in template-only mode (no LLM calls).\n")
            return None

        _p(f"  (Enter a number between 1 and {none_idx if allow_none else len(options)})")


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
          5. ANTHROPIC_API_KEY   => Anthropic
          6. LLM_BASE_URL        => OpenAI-compat endpoint (LM Studio, vLLM, etc.)
          7. LLM_SUBPROCESS_CMD  => subprocess CLI (explicit)
          8. `claude` on PATH    => subprocess:claude
          9. `codex`  on PATH    => subprocess:codex
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
        if getattr(args, "llm_subprocess_cmd", None):
            config.provider        = PROVIDER_SUBPROCESS
            config.subprocess_cmd  = args.llm_subprocess_cmd
        if getattr(args, "llm_subprocess_env", None):
            config.subprocess_env  = dict(args.llm_subprocess_env)

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
        import shutil

        config = LLMConfig()

        # Explicit provider override
        if os.environ.get("LLM_PROVIDER"):
            config.provider = os.environ["LLM_PROVIDER"]
            # Carry subprocess_cmd through if applicable
            if config.provider == PROVIDER_SUBPROCESS:
                config.subprocess_cmd = os.environ.get("LLM_SUBPROCESS_CMD", "")

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

        # Anthropic
        elif os.environ.get("ANTHROPIC_API_KEY"):
            config.provider = PROVIDER_ANTHROPIC
            config.model    = os.environ.get("LLM_MODEL", "claude-opus-4-5")
            config.api_key  = os.environ["ANTHROPIC_API_KEY"]

        # Subprocess: explicit env var
        elif os.environ.get("LLM_SUBPROCESS_CMD"):
            cmd = os.environ["LLM_SUBPROCESS_CMD"].strip()
            if shutil.which(cmd):
                config.provider       = PROVIDER_SUBPROCESS
                config.subprocess_cmd = cmd
                config.model          = os.environ.get("LLM_MODEL", cmd)
            else:
                logger.warning(
                    "LLM_SUBPROCESS_CMD=%r not found in PATH — falling back to Anthropic default.",
                    cmd,
                )
                config.provider = PROVIDER_ANTHROPIC
                config.model    = os.environ.get("LLM_MODEL", "claude-opus-4-5")
                config.api_key  = os.environ.get("ANTHROPIC_API_KEY", "")

        # Subprocess: auto-detect well-known CLIs
        elif shutil.which("claude"):
            config.provider       = PROVIDER_SUBPROCESS
            config.subprocess_cmd = "claude"
            config.model          = os.environ.get("LLM_MODEL", "claude")
            logger.info("Auto-detected Claude Code CLI — using subprocess provider.")

        elif shutil.which("codex"):
            config.provider       = PROVIDER_SUBPROCESS
            config.subprocess_cmd = "codex"
            config.model          = os.environ.get("LLM_MODEL", "codex")
            logger.info("Auto-detected OpenAI Codex CLI — using subprocess provider.")

        # No provider found
        else:
            config.provider = PROVIDER_ANTHROPIC
            config.model    = os.environ.get("LLM_MODEL", "claude-opus-4-5")
            config.api_key  = os.environ.get("ANTHROPIC_API_KEY", "")

        # Universal overrides (always applied)
        if os.environ.get("LLM_MODEL"):
            config.model = os.environ["LLM_MODEL"]
        if os.environ.get("LLM_MAX_TOKENS"):
            config.max_tokens = int(os.environ["LLM_MAX_TOKENS"])
        if os.environ.get("LLM_TEMPERATURE"):
            config.temperature = float(os.environ["LLM_TEMPERATURE"])
        if os.environ.get("LLM_API_VERSION"):
            config.api_version = os.environ["LLM_API_VERSION"]

        return config

"""
agents.agent_context — IDE Agent Mode Detection
=================================================
Determines whether the pipeline is currently running inside an AI coding
agent (Cursor, Windsurf, GitHub Copilot, or any generic agent).

Why this matters
----------------
When an LLM call fails (bad API key, network error, quota exceeded, etc.)
the pipeline has two choices:

  • CLI / human mode  — hard-fail immediately.  A human is watching the
                        terminal and can fix the configuration before re-running.
                        Silently writing a half-baked template scaffold would be
                        worse than stopping with a clear error message.

  • Agent mode        — soft-fail with the Jinja2 template scaffold so the IDE
                        agent can continue, inspect the scaffold output, and
                        prompt the user about the failure.  The agent can then
                        re-run with the correct LLM configuration.

Detection strategy
------------------
No single environment variable is reliably set by all IDE agents today, so we
use a layered detection approach (first match wins):

1. ``AI_AGENT_MODE=1``   — explicit opt-in env var; set by run_agent.py
                            automatically, or by the user in their shell / IDE
                            launch config.

2. ``CURSOR_AGENT=1``    — set by Cursor IDE when the Composer/Agent panel
                            executes terminal commands (may not be set in all
                            versions — see Cursor forum thread #41487).

3. ``CURSOR_CLI``        — set in any Cursor integrated terminal session.
                            Not agent-specific but indicates the IDE is Cursor.

4. ``WINDSURF_AGENT=1``  — not set by Windsurf today (as of early 2026) but
                            reserved here for when they add it.

5. ``TERM_PROGRAM=cursor`` or ``TERM_PROGRAM=windsurf`` — set in some
                            versions of Cursor/Windsurf integrated terminals.

6. Parent process scan   — checks whether the parent process name contains
                            'cursor', 'windsurf', or 'copilot' (Unix/macOS only;
                            silently skipped on Windows where psutil may be absent).

Public API
----------
    from agents.agent_context import is_agent_mode, get_agent_name, require_llm_or_raise

    if is_agent_mode():
        # soft-fail: return template scaffold
        ...
    else:
        # hard-fail: raise LLMConfigurationError
        ...

    # Convenience wrapper used by PlanAgent / ConversionAgent:
    require_llm_or_raise(
        context="plan generation",
        error=original_exc,
        fallback_fn=lambda: generate_from_template(feature_name),
    )
"""

import logging
import os
import sys
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Known agent-mode environment variable signals (checked in priority order)
# ---------------------------------------------------------------------------

_AGENT_ENV_SIGNALS: list[tuple[str, str | None, str]] = [
    # (env_var_name, required_value_or_None_for_any_truthy, agent_label)
    ("AI_AGENT_MODE",    "1",        "generic-agent"),
    ("CURSOR_AGENT",     "1",        "cursor"),
    ("CURSOR_CLI",       None,       "cursor"),
    ("WINDSURF_AGENT",   "1",        "windsurf"),
    ("COPILOT_AGENT",    "1",        "copilot"),
    ("TERM_PROGRAM",     "cursor",   "cursor"),
    ("TERM_PROGRAM",     "windsurf", "windsurf"),
]

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class LLMConfigurationError(Exception):
    """
    Raised when the LLM is required but not available / not configured,
    and the pipeline is NOT running in agent mode.

    The message includes actionable instructions for the user.
    """


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _detect_from_env() -> str | None:
    """Return the agent label if any known env var signal matches, else None."""
    for var, required_val, label in _AGENT_ENV_SIGNALS:
        actual = os.environ.get(var, "")
        if not actual:
            continue
        if required_val is None or actual.strip() == required_val:
            return label
    return None


def _detect_from_parent_process() -> str | None:
    """
    Try to detect a known IDE as the parent process.
    Returns the agent label or None.  Never raises — any failure is silently ignored.
    """
    try:
        import psutil  # type: ignore
        parent = psutil.Process(os.getpid()).parent()
        if parent is None:
            return None
        name = (parent.name() or "").lower()
        for keyword, label in [("cursor", "cursor"), ("windsurf", "windsurf"),
                                ("copilot", "copilot"), ("code", "vscode")]:
            if keyword in name:
                return label
    except Exception:
        pass
    return None


def get_agent_name() -> str | None:
    """
    Return a human-readable label for the active IDE agent, or None if not
    running inside a recognised agent environment.

    Examples: ``"cursor"``, ``"windsurf"``, ``"copilot"``, ``"generic-agent"``
    """
    label = _detect_from_env()
    if label:
        return label
    return _detect_from_parent_process()


def is_agent_mode() -> bool:
    """
    Return True if the pipeline is running inside an IDE agent or has been
    explicitly opted into agent mode via the ``AI_AGENT_MODE=1`` env var.
    """
    return get_agent_name() is not None


# ---------------------------------------------------------------------------
# Convenience wrapper for PlanAgent / ConversionAgent
# ---------------------------------------------------------------------------

def require_llm_or_raise(
    context: str,
    error: Exception,
    fallback_fn: Callable[[], T],
) -> T:
    """
    Handle an LLM failure with context-aware behaviour.

    In **agent mode** (Cursor, Windsurf, Copilot, or ``AI_AGENT_MODE=1``):
        — Logs a warning and calls ``fallback_fn()`` (typically the
          template-only generator), returning its result.
        — The IDE agent can then inspect the scaffold output and surface
          the LLM error to the user.

    In **CLI / human mode**:
        — Raises ``LLMConfigurationError`` with a clear, actionable message
          so the user knows exactly what to fix before re-running.

    Parameters
    ----------
    context : str
        Short description of what was being attempted (e.g. "plan generation",
        "conversion of Step C1").
    error : Exception
        The original LLM exception (will be chained onto LLMConfigurationError).
    fallback_fn : Callable[[], T]
        Called only in agent mode to produce a fallback result.

    Returns
    -------
    T
        The result of ``fallback_fn()`` (agent mode only).

    Raises
    ------
    LLMConfigurationError
        In CLI/human mode when the LLM is required but unavailable.
    """
    agent = get_agent_name()

    if agent:
        logger.warning(
            "[LLM FAILURE — agent mode (%s)] %s failed: %s. "
            "Falling back to template scaffold. "
            "Fix your LLM configuration and re-run for full AI-assisted output.",
            agent, context, error,
        )
        return fallback_fn()

    # CLI / human mode — hard fail with actionable message
    raise LLMConfigurationError(
        f"\n"
        f"{'='*64}\n"
        f"  LLM CONFIGURATION ERROR\n"
        f"{'='*64}\n"
        f"  Context: {context}\n"
        f"  Error:   {error}\n"
        f"\n"
        f"  The pipeline cannot continue without a working LLM.\n"
        f"  No output file will be written.\n"
        f"\n"
        f"  How to fix:\n"
        f"    1. Check your API key / provider environment variables:\n"
        f"         ANTHROPIC_API_KEY   — Anthropic Claude\n"
        f"         OPENAI_API_KEY      — OpenAI GPT\n"
        f"         OLLAMA_MODEL        — Local Ollama server\n"
        f"         LLM_BASE_URL        — OpenAI-compatible (LM Studio, vLLM)\n"
        f"         LLAMACPP_MODEL_PATH — Local GGUF file\n"
        f"    2. Or add to your job YAML:  llm.no_llm: true\n"
        f"       (template-only scaffold mode — no API key required)\n"
        f"    3. Or pass --no-llm on the command line\n"
        f"\n"
        f"  Running inside an IDE agent (Cursor / Windsurf / Copilot)?\n"
        f"  The pipeline will automatically use template scaffolds when\n"
        f"  an agent is detected.  You can also force this explicitly:\n"
        f"    set AI_AGENT_MODE=1   (Windows CMD)\n"
        f"    $env:AI_AGENT_MODE=1  (PowerShell)\n"
        f"    export AI_AGENT_MODE=1  (bash/zsh)\n"
        f"{'='*64}\n"
    ) from error

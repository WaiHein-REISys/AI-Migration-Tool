"""
Subprocess CLI Provider
========================
Delegates LLM calls to an installed CLI tool by spawning a subprocess.

Supported tools (auto-detected by name):
  claude   — Anthropic Claude Code CLI  (claude --print)
  codex    — OpenAI Codex CLI           (codex --quiet)

Any other command is invoked with the combined system+messages prompt
written to stdin (generic fallback).

Required config:
  subprocess_cmd  — command name or absolute path
                    (e.g. "claude", "/usr/local/bin/codex", "my-llm-cli")

Optional env vars:
  LLM_SUBPROCESS_CMD    — CLI command to use (overrides config)
  LLM_SUBPROCESS_ARGS   — extra space-separated arguments appended to the command
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from typing import Optional

# Regex that matches ${VAR_NAME} placeholders for env-var expansion
_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")

from agents.llm.base import (
    BaseLLMProvider,
    LLMConfig,
    LLMMessage,
    LLMNotAvailableError,
    LLMProviderError,
    LLMResponse,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known CLI profiles
# Each entry describes how to invoke the tool:
#   args        — fixed arguments inserted before the prompt
#   stdin       — True: write prompt to stdin; False: append as final argument
#   model_flag  — CLI flag used to pass a model name (None = not supported)
# ---------------------------------------------------------------------------
_CLI_PROFILES: dict[str, dict] = {
    "claude": {
        # --print                      : non-interactive, print response then exit
        # --dangerously-skip-permissions: bypass interactive "Allow?" prompts that
        #   cause exit code 1 when running headlessly inside a subprocess
        # --no-session-persistence     : don't write session files between calls
        "args":       ["--print", "--dangerously-skip-permissions", "--no-session-persistence"],
        "stdin":      True,
        "model_flag": "--model",   # supports --model <name> for optional override
    },
    "codex": {
        "args":       ["--quiet"],
        "stdin":      False,
        "model_flag": "--model",
    },
}

# Generic fallback profile for unknown CLIs
_GENERIC_PROFILE: dict = {
    "args":       [],
    "stdin":      True,
    "model_flag": None,
}


def find_subprocess_cmd(cmd: str) -> Optional[str]:
    """Return the absolute path to *cmd* if it exists on PATH, else None."""
    return shutil.which(cmd)


class SubprocessProvider(BaseLLMProvider):
    """
    LLM provider that delegates completions to a CLI tool via subprocess.

    The system prompt and messages are combined into a single text block and
    sent to the CLI tool either via stdin or as a final positional argument,
    depending on the tool's profile.
    """

    def _setup(self) -> None:
        cmd = (
            self.config.subprocess_cmd
            or os.environ.get("LLM_SUBPROCESS_CMD", "")
        ).strip()

        if not cmd:
            logger.warning(
                "SubprocessProvider: no command configured. "
                "Set subprocess_cmd in LLMConfig or LLM_SUBPROCESS_CMD env var."
            )
            self._client = None
            return

        path = find_subprocess_cmd(cmd)
        if path is None:
            logger.warning(
                "SubprocessProvider: command %r not found in PATH. "
                "Provider will be unavailable.",
                cmd,
            )
            self._client = None
            return

        self._client   = path
        self._cmd_name = os.path.basename(cmd).lower().split(".")[0]  # strip .exe etc.
        self._profile  = _CLI_PROFILES.get(self._cmd_name, _GENERIC_PROFILE)

        extra_env = os.environ.get("LLM_SUBPROCESS_ARGS", "")
        self._extra_args: list[str] = extra_env.split() if extra_env else []

        if os.environ.get("CLAUDECODE") and self._cmd_name == "claude":
            logger.warning(
                "SubprocessProvider: CLAUDECODE env var detected (running inside a "
                "Claude Code session). CLAUDECODE will be unset for the subprocess, "
                "but 'claude --print' may still hang or be killed by the host session. "
                "If LLM calls fall back to template-only mode, run the pipeline from "
                "a standalone terminal outside of Claude Code, or set ANTHROPIC_API_KEY "
                "and use provider=anthropic instead."
            )

        logger.info(
            "SubprocessProvider ready: command=%r resolved=%s profile=%s",
            cmd, path, self._cmd_name,
        )

    # ------------------------------------------------------------------
    # BaseLLMProvider interface
    # ------------------------------------------------------------------

    def complete(
        self,
        system: str,
        messages: list[LLMMessage],
    ) -> LLMResponse:
        if not self._client:
            raise LLMNotAvailableError(
                "SubprocessProvider is not configured. "
                "Install the CLI tool and set LLM_SUBPROCESS_CMD (or subprocess_cmd)."
            )

        prompt = self._build_prompt(system, messages)

        cmd_parts: list[str] = [self._client]
        cmd_parts += list(self._profile.get("args", []))
        cmd_parts += self._extra_args

        # Attach model flag if the CLI supports it and a model is set
        model_flag = self._profile.get("model_flag")
        if model_flag and self.config.model:
            cmd_parts += [model_flag, self.config.model]

        use_stdin = bool(self._profile.get("stdin", True))
        if not use_stdin:
            # Append prompt as the final positional argument
            cmd_parts.append(prompt)

        logger.debug(
            "SubprocessProvider: running %s stdin=%s prompt_len=%d",
            cmd_parts[0], use_stdin, len(prompt),
        )

        # Build a sanitised subprocess environment.
        # 1. Start from the current process env, stripping CLAUDECODE so that
        #    `claude --print` does not refuse to start inside a Claude Code session
        #    (Cursor, Windsurf, etc. set this var; nested sessions exit with code 1).
        # 2. Overlay any credentials / vars from config.subprocess_env.
        #    Values may use ${VAR} placeholders that expand from the host env.
        sub_env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        if self.config.subprocess_env:
            for env_key, env_val in self.config.subprocess_env.items():
                expanded = _ENV_VAR_RE.sub(
                    lambda m: os.environ.get(m.group(1), m.group(0)),
                    str(env_val),
                )
                sub_env[env_key] = expanded
                logger.debug(
                    "SubprocessProvider: injecting env var %s=%r",
                    env_key,
                    expanded if "key" not in env_key.lower() and "pass" not in env_key.lower()
                    else "***",
                )

        try:
            result = subprocess.run(
                cmd_parts,
                input=prompt if use_stdin else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.timeout_seconds,
                env=sub_env,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMProviderError(
                f"Subprocess timed out after {self.config.timeout_seconds}s "
                f"(command: {self._client})"
            ) from exc
        except OSError as exc:
            raise LLMProviderError(
                f"Failed to launch subprocess {self._client!r}: {exc}"
            ) from exc

        if result.returncode != 0:
            stderr_snippet = (result.stderr or "").strip()[:400]
            stdout_snippet = (result.stdout or "").strip()[:400]
            # Many CLIs (including claude) write error messages to stdout, not
            # stderr.  Include both so the real error is always visible in logs.
            detail = stderr_snippet or stdout_snippet or "(no output from subprocess)"
            raise LLMProviderError(
                f"Subprocess exited with code {result.returncode}. "
                f"{'stderr' if stderr_snippet else 'stdout'}: {detail}"
            )

        text = result.stdout.strip()
        if not text:
            stderr_snippet = (result.stderr or "").strip()[:400]
            raise LLMProviderError(
                f"Subprocess produced no output. stderr: {stderr_snippet}"
            )

        return LLMResponse(
            text=text,
            model=self.config.model or self._cmd_name,
            provider=f"subprocess:{self._cmd_name}",
            input_tokens=0,   # CLI tools don't report token counts
            output_tokens=0,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(system: str, messages: list[LLMMessage]) -> str:
        """
        Combine the system prompt and message list into one string.

        CLI tools typically don't support separate "system" and "user" roles,
        so we format them as labelled blocks.
        """
        parts: list[str] = []
        if system:
            parts.append(f"[SYSTEM]\n{system}")
        for msg in messages:
            label = msg.role.upper()
            parts.append(f"[{label}]\n{msg.content}")
        return "\n\n".join(parts)

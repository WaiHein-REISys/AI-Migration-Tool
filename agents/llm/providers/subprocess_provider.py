"""
Subprocess CLI Provider
========================
Delegates LLM calls to an installed CLI tool by spawning a subprocess.

Supported tools (auto-detected by name):
  claude   — Anthropic Claude Code CLI  (claude --print)
  codex    — OpenAI Codex CLI           (codex exec - --json ...)
  gemini   — Google Gemini CLI          (gemini -p "" --yolo -o json)

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

import json
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
        # exec                            — non-interactive subcommand
        # -                               — read prompt from stdin
        # --ephemeral                     — don't write session files between calls
        # --dangerously-bypass-approvals-and-sandbox
        #                                 — skip interactive "Allow?" prompts (headless)
        # -s read-only                    — sandbox: no filesystem writes by the agent
        # -c reasoning_effort="medium"    — use medium reasoning for speed (~15-30 s/call);
        #   the default "high" effort can take 3+ min per call.  For max quality,
        #   override with model: "o3" in the job YAML (high effort + best model).
        # --json                          — emit JSONL events; response extracted via
        #                                   output_format="codex_jsonl" (see _extract_text)
        "args":         ["exec", "--ephemeral",
                         "--dangerously-bypass-approvals-and-sandbox",
                         "-s", "read-only",
                         "-c", 'reasoning_effort="medium"',
                         "--json", "-"],
        "stdin":        True,
        "model_flag":   "-m",
        "output_format": "codex_jsonl",  # tells _extract_text to parse JSONL
    },
    "gemini": {
        # -p ""              — trigger non-interactive (headless) mode; the empty
        #                      string is appended to stdin content as the prompt
        # --yolo             — auto-approve all tool calls (skip interactive prompts)
        # -o json            — emit a single JSON object:
        #                        {"session_id": "...", "response": "...", "stats": {...}}
        #                      response text and token counts extracted via
        #                      output_format="gemini_json" (see _extract_text /
        #                      _parse_gemini_usage)
        # stderr             — YOLO/credential messages go to stderr; stdout is clean JSON
        "args":         ["-p", "", "--yolo", "-o", "json"],
        "stdin":        True,
        "model_flag":   "-m",
        "output_format": "gemini_json",  # tells _extract_text to parse the JSON object
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

        # Attach model flag only when the CLI supports it AND the model is a
        # real model identifier — not a bare CLI placeholder name.
        #
        # Auto-detection in registry.py sets model=<cmd_name> as a placeholder
        # (e.g. model="claude" when claude is on PATH, model="codex" when codex
        # is on PATH).  If the YAML then overrides subprocess_cmd to a different
        # CLI (e.g. cmd changed to "codex" but model still carries "claude"),
        # we must NOT pass "--model claude" to codex — that is an Anthropic model
        # name and codex will reject it.
        #
        # Rule: skip --model if model_val matches ANY known CLI placeholder name.
        # A real model name (e.g. "o3", "gpt-4o", "claude-opus-4-5") will never
        # equal a bare CLI command name, so this guard is safe and general.
        model_flag      = self._profile.get("model_flag")
        model_val       = self.config.model or ""
        _cli_placeholders = frozenset(_CLI_PROFILES)   # {"claude", "codex", "gemini"}
        if model_flag and model_val and model_val not in _cli_placeholders:
            cmd_parts += [model_flag, model_val]

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

        raw_output = result.stdout.strip()
        if not raw_output:
            stderr_snippet = (result.stderr or "").strip()[:400]
            raise LLMProviderError(
                f"Subprocess produced no output. stderr: {stderr_snippet}"
            )

        # Post-process output according to the profile's declared format.
        output_format = self._profile.get("output_format", "plain")
        text = self._extract_text(raw_output, output_format)
        if not text:
            raise LLMProviderError(
                f"Subprocess output could not be parsed (format={output_format!r}). "
                f"Raw output (first 400 chars): {raw_output[:400]}"
            )

        # Count tokens where the format reports them inline.
        in_tokens  = 0
        out_tokens = 0
        if output_format == "codex_jsonl":
            in_tokens, out_tokens = self._parse_codex_usage(raw_output)
        elif output_format == "gemini_json":
            in_tokens, out_tokens = self._parse_gemini_usage(raw_output)

        return LLMResponse(
            text=text,
            model=self.config.model or self._cmd_name,
            provider=f"subprocess:{self._cmd_name}",
            input_tokens=in_tokens,
            output_tokens=out_tokens,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(raw: str, output_format: str) -> str:
        """
        Extract the LLM's response text from raw subprocess output.

        output_format values:
          "plain"        — raw stdout IS the response (default, e.g. claude --print)
          "codex_jsonl"  — codex exec --json emits JSONL; parse agent_message events
          "gemini_json"  — gemini -o json emits a single JSON object; read "response"
        """
        if output_format == "codex_jsonl":
            # Collect all agent_message texts (there is usually one, but concatenate
            # multiple just in case the agent responds in several turns).
            parts: list[str] = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    event.get("type") == "item.completed"
                    and event.get("item", {}).get("type") == "agent_message"
                ):
                    msg_text = event["item"].get("text", "").strip()
                    if msg_text:
                        parts.append(msg_text)
            return "\n".join(parts)

        if output_format == "gemini_json":
            # gemini -o json emits a single JSON object:
            #   {"session_id": "...", "response": "<llm text>", "stats": {...}}
            # The YOLO/credential warning lines go to stderr; stdout is clean JSON.
            try:
                data = json.loads(raw)
                return data.get("response", "").strip()
            except json.JSONDecodeError:
                return ""

        # Default: the whole stdout is the response
        return raw

    @staticmethod
    def _parse_codex_usage(jsonl: str) -> tuple[int, int]:
        """
        Parse token usage from codex --json JSONL output.
        Returns (input_tokens, output_tokens).
        """
        for line in reversed(jsonl.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed":
                usage = event.get("usage", {})
                return (
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                )
        return 0, 0

    @staticmethod
    def _parse_gemini_usage(raw: str) -> tuple[int, int]:
        """
        Parse token usage from gemini -o json output.

        The JSON object has:
          stats.models.<model_name>.tokens.{input, candidates}

        Multiple model entries may appear (e.g. a router model + a generation
        model).  We sum input/output tokens across all models so the reported
        count reflects the full request cost.

        Returns (input_tokens, output_tokens).
        """
        try:
            data = json.loads(raw)
            stats = data.get("stats", {})
            in_tokens  = 0
            out_tokens = 0
            for model_data in stats.get("models", {}).values():
                tokens = model_data.get("tokens", {})
                in_tokens  += tokens.get("input",      0)
                out_tokens += tokens.get("candidates",  0)
            return in_tokens, out_tokens
        except (json.JSONDecodeError, KeyError, TypeError):
            return 0, 0

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

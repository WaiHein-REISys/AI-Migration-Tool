"""
E2E Verification Agent
======================
Runs project-defined end-to-end verification commands after conversion and
integration. Commands are configured in the job YAML under:

verification:
  enabled: true
  tool: commands       # 'commands' (default) | 'playwright'
  cwd: "<path or null>"
  commands:
    - "npm ci"
    - "npm run lint"
    - "npm test -- --runInBand"
  env:
    KEY: "VALUE"
  fail_on_error: true

Playwright mode (tool: playwright)
-----------------------------------
When ``tool: playwright`` is set the agent will:
  1. Search ``target_root`` then ``output_root`` for a Playwright config file
     (playwright.config.ts / .js / .mjs / .cjs).
  2. Auto-populate ``commands`` with ``npx playwright test --reporter=list``
     when a config file is found and no commands are explicitly set.
  3. Report the config file path in the JSON/Markdown report.

Report writing
--------------
A JSON and Markdown report is always written — even for skipped runs — so
the logs directory always contains an e2e-verification-report file for every
pipeline execution. Skipped reports include the skip reason and instructions
for enabling verification.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Playwright config file names (checked in order)
_PLAYWRIGHT_CONFIG_NAMES: tuple[str, ...] = (
    "playwright.config.ts",
    "playwright.config.js",
    "playwright.config.mjs",
    "playwright.config.cjs",
)

# Default command injected when Playwright config is detected and no commands are set
_PLAYWRIGHT_DEFAULT_CMD = "npx playwright test --reporter=list"


class E2EVerificationAgent:
    """Run configured shell commands and write JSON/Markdown verification reports."""

    def __init__(
        self,
        run_id: str,
        logs_dir: str | Path,
        output_root: str | Path,
        target_root: str | Path | None,
        verification_config: dict | None = None,
        dry_run: bool = False,
    ) -> None:
        self.run_id = run_id
        self.logs_dir = Path(logs_dir)
        self.output_root = Path(output_root)
        self.target_root = Path(target_root) if target_root else None
        self.config = dict(verification_config or {})  # mutable copy
        self.dry_run = dry_run
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def execute(self) -> dict[str, Any]:
        tool = self.config.get("tool", "commands")
        playwright_config_path: Path | None = None

        base: dict[str, Any] = {
            "run_id": self.run_id,
            "status": "skipped",
            "tool": tool,
            "cwd": None,
            "commands": [],
            "playwright_config": None,
            "report_json": None,
            "report_md": None,
        }

        # --- Playwright: detect config file and optionally inject command ---
        if tool == "playwright":
            playwright_config_path = self._detect_playwright_config()
            base["playwright_config"] = (
                str(playwright_config_path) if playwright_config_path else None
            )
            # Auto-populate commands only when none are explicitly configured
            if playwright_config_path and not self.config.get("commands"):
                self.config["commands"] = [_PLAYWRIGHT_DEFAULT_CMD]
                logger.info(
                    "[E2E] Playwright config found — using default command: %s",
                    _PLAYWRIGHT_DEFAULT_CMD,
                )

        # --- Skip guards (always write report) ---
        if self.dry_run:
            base["status"] = "skipped_dry_run"
            return self._write_report(base)

        if not self.config.get("enabled", False):
            base["status"] = "skipped_disabled"
            return self._write_report(base)

        commands = self.config.get("commands") or []
        if not isinstance(commands, list) or not commands:
            base["status"] = "skipped_no_commands"
            return self._write_report(base)

        cwd = self._resolve_cwd()
        if cwd is None or not cwd.exists():
            base["status"] = "skipped_missing_cwd"
            base["cwd"] = str(cwd) if cwd else None
            return self._write_report(base)

        # --- Run commands ---
        env = os.environ.copy()
        cfg_env = self.config.get("env", {})
        if isinstance(cfg_env, dict):
            for k, v in cfg_env.items():
                env[str(k)] = str(v)

        fail_on_error = bool(self.config.get("fail_on_error", True))
        results: list[dict[str, Any]] = []
        has_failure = False

        for idx, cmd in enumerate(commands, start=1):
            if not isinstance(cmd, str) or not cmd.strip():
                continue

            start = time.time()
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(cwd),
                env=env,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
            duration_ms = int((time.time() - start) * 1000)
            result: dict[str, Any] = {
                "index": idx,
                "command": cmd,
                "exit_code": proc.returncode,
                "duration_ms": duration_ms,
                "stdout": (proc.stdout or "")[:4000],
                "stderr": (proc.stderr or "")[:4000],
            }
            results.append(result)
            logger.info(
                "[E2E] [%d/%d] exit=%d  %s  (%d ms)",
                idx, len(commands), proc.returncode, cmd, duration_ms,
            )

            if proc.returncode != 0:
                has_failure = True
                logger.error("[E2E] Command failed (exit=%s): %s", proc.returncode, cmd)
                if fail_on_error:
                    break

        status = (
            "failed" if has_failure and fail_on_error
            else ("completed_with_failures" if has_failure else "passed")
        )

        report: dict[str, Any] = {
            **base,
            "status": status,
            "cwd": str(cwd),
            "fail_on_error": fail_on_error,
            "commands": results,
            "playwright_config": (
                str(playwright_config_path) if playwright_config_path else None
            ),
        }
        return self._write_report(report)

    # ------------------------------------------------------------------
    # Report persistence
    # ------------------------------------------------------------------

    def _write_report(self, report: dict[str, Any]) -> dict[str, Any]:
        """Write JSON and Markdown report files for ALL exit paths (skipped or ran)."""
        json_path = self.logs_dir / f"{self.run_id}-e2e-verification-report.json"
        md_path = self.logs_dir / f"{self.run_id}-e2e-verification-report.md"
        try:
            json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            md_path.write_text(self._render_markdown(report), encoding="utf-8")
            report["report_json"] = str(json_path)
            report["report_md"] = str(md_path)
            logger.info("[E2E] Report written: %s", json_path.name)
        except OSError as exc:
            logger.warning("[E2E] Could not write report: %s", exc)
        return report

    # ------------------------------------------------------------------
    # Playwright detection
    # ------------------------------------------------------------------

    def _detect_playwright_config(self) -> Path | None:
        """Detect a Playwright config file in target_root or output_root."""
        search_roots = [r for r in (self.target_root, self.output_root) if r is not None]
        for root in search_roots:
            if not root.exists():
                continue
            for name in _PLAYWRIGHT_CONFIG_NAMES:
                candidate = root / name
                if candidate.exists():
                    logger.info("[E2E] Playwright config detected: %s", candidate)
                    return candidate
        logger.debug("[E2E] No Playwright config file found in %s", search_roots)
        return None

    # ------------------------------------------------------------------
    # Working-directory resolution
    # ------------------------------------------------------------------

    def _resolve_cwd(self) -> Path | None:
        raw_cwd = self.config.get("cwd")
        if raw_cwd:
            p = Path(str(raw_cwd))
            return p if p.is_absolute() else (Path.cwd() / p).resolve()

        # Prefer target_root (post-integration) when available.
        if self.target_root is not None and self.target_root.exists():
            return self.target_root

        # Fallback: converted output folder.
        return self.output_root if self.output_root.exists() else None

    # ------------------------------------------------------------------
    # Markdown rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _render_markdown(report: dict[str, Any]) -> str:
        status = str(report.get("status", "unknown")).upper()
        run_id = report.get("run_id", "")
        tool = report.get("tool", "commands")

        lines: list[str] = [
            f"# E2E Verification Report — {run_id}",
            "",
            f"**Status:** {status}",
        ]

        if tool != "commands":
            lines.append(f"**Tool:** {tool}")

        playwright_cfg = report.get("playwright_config")
        if playwright_cfg:
            lines.append(f"**Playwright config:** `{playwright_cfg}`")

        cwd = report.get("cwd")
        if cwd:
            lines.append(f"**Working directory:** `{cwd}`")

        fail_on_error = report.get("fail_on_error")
        if fail_on_error is not None:
            lines.append(f"**fail_on_error:** `{fail_on_error}`")

        lines.append("")

        # Skipped report — include helpful reason + how-to-enable instructions
        if status.startswith("SKIPPED"):
            _reason_map = {
                "SKIPPED_DRY_RUN": (
                    "Pipeline running in **dry-run mode** — no commands were executed."
                ),
                "SKIPPED_DISABLED": (
                    "Verification is **disabled** (`enabled: false`).  \n"
                    "Set `verification.enabled: true` in your job YAML to activate.\n\n"
                    "**Playwright quick-start:** set `verification.tool: playwright` and the agent\n"
                    "will auto-detect `playwright.config.ts` in your target codebase and run\n"
                    "`npx playwright test --reporter=list` automatically."
                ),
                "SKIPPED_NO_COMMANDS": (
                    "No verification commands are configured.  \n"
                    "Add commands to `verification.commands`, or set `verification.tool: playwright`\n"
                    "to auto-detect a Playwright config and run tests automatically."
                ),
                "SKIPPED_MISSING_CWD": (
                    "**Working directory not found.**  \n"
                    "Set `verification.cwd` explicitly, or ensure `pipeline.target_root` exists and\n"
                    "is accessible on disk."
                ),
            }
            reason = _reason_map.get(status, "Verification was skipped.")
            lines += ["## Skip Reason", "", reason, ""]
            return "\n".join(lines)

        # Command results
        lines.append("## Commands")
        commands = report.get("commands", [])
        if not commands:
            lines.append("- No commands were executed.")
            return "\n".join(lines) + "\n"

        for cmd in commands:
            exit_code = cmd.get("exit_code")
            icon = "✅" if exit_code == 0 else "❌"
            lines.append(
                f"### {icon} `{cmd.get('command')}` "
                f"— exit {exit_code} ({cmd.get('duration_ms', 0)} ms)"
            )
            stdout = (cmd.get("stdout") or "").strip()
            if stdout:
                lines += [
                    "",
                    "<details><summary>stdout</summary>",
                    "",
                    f"```\n{stdout[:2000]}\n```",
                    "",
                    "</details>",
                    "",
                ]
            stderr = (cmd.get("stderr") or "").strip()
            if stderr:
                lines += [
                    "",
                    "<details><summary>stderr</summary>",
                    "",
                    f"```\n{stderr[:2000]}\n```",
                    "",
                    "</details>",
                    "",
                ]

        return "\n".join(lines) + "\n"

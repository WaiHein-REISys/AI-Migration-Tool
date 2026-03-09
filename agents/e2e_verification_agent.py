"""
E2E Verification Agent
======================
Runs project-defined end-to-end verification commands after conversion and
integration. Commands are configured in the job YAML under:

verification:
  enabled: true
  cwd: "<path or null>"
  commands:
    - "npm ci"
    - "npm run lint"
    - "npm test -- --runInBand"
  env:
    KEY: "VALUE"
  fail_on_error: true
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
        self.config = verification_config or {}
        self.dry_run = dry_run
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def execute(self) -> dict[str, Any]:
        base = {
            "run_id": self.run_id,
            "status": "skipped",
            "cwd": None,
            "commands": [],
            "report_json": None,
            "report_md": None,
        }

        if self.dry_run:
            base["status"] = "skipped_dry_run"
            return base

        if not self.config.get("enabled", False):
            base["status"] = "skipped_disabled"
            return base

        commands = self.config.get("commands") or []
        if not isinstance(commands, list) or not commands:
            base["status"] = "skipped_no_commands"
            return base

        cwd = self._resolve_cwd()
        if cwd is None or not cwd.exists():
            base["status"] = "skipped_missing_cwd"
            return base

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
            result = {
                "index": idx,
                "command": cmd,
                "exit_code": proc.returncode,
                "duration_ms": duration_ms,
                "stdout": (proc.stdout or "")[:4000],
                "stderr": (proc.stderr or "")[:4000],
            }
            results.append(result)

            if proc.returncode != 0:
                has_failure = True
                logger.error("[E2E] Command failed (exit=%s): %s", proc.returncode, cmd)
                if fail_on_error:
                    break

        status = "failed" if has_failure and fail_on_error else ("completed_with_failures" if has_failure else "passed")
        report = {
            "run_id": self.run_id,
            "status": status,
            "cwd": str(cwd),
            "fail_on_error": fail_on_error,
            "commands": results,
        }

        json_path = self.logs_dir / f"{self.run_id}-e2e-verification-report.json"
        md_path = self.logs_dir / f"{self.run_id}-e2e-verification-report.md"
        json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        md_path.write_text(self._render_markdown(report), encoding="utf-8")

        report["report_json"] = str(json_path)
        report["report_md"] = str(md_path)
        return report

    def _resolve_cwd(self) -> Path | None:
        raw_cwd = self.config.get("cwd")
        if raw_cwd:
            p = Path(str(raw_cwd))
            return p if p.is_absolute() else (Path.cwd() / p).resolve()

        # Default: prefer target_root (post-integration) when available.
        if self.target_root is not None and self.target_root.exists():
            return self.target_root

        # Fallback: converted output folder.
        return self.output_root if self.output_root.exists() else None

    @staticmethod
    def _render_markdown(report: dict[str, Any]) -> str:
        lines = [
            f"# E2E Verification Report - {report['run_id']}",
            "",
            f"- Status: **{str(report.get('status', '')).upper()}**",
            f"- Working directory: `{report.get('cwd', '')}`",
            f"- fail_on_error: `{report.get('fail_on_error', True)}`",
            "",
            "## Commands",
        ]

        commands = report.get("commands", [])
        if not commands:
            lines.append("- No commands were executed.")
            return "\n".join(lines) + "\n"

        for cmd in commands:
            lines.append(
                f"- [{cmd.get('exit_code')}] `{cmd.get('command')}` "
                f"({cmd.get('duration_ms', 0)} ms)"
            )
            stderr = (cmd.get("stderr") or "").strip()
            if stderr:
                lines.append(f"  stderr: {stderr[:300]}")
        return "\n".join(lines) + "\n"

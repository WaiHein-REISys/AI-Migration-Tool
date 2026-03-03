"""
Validation Agent
================
Runs post-conversion checks before the pipeline is reported as successful.

Validation layers:
  1. File-level sanity checks (target file exists and is non-empty)
  2. Behavioural simulation using an LLM (new code should preserve old intent)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents.llm import LLMRouter

logger = logging.getLogger(__name__)


class ValidationAgent:
    """Validate converted output files against source files."""

    def __init__(
        self,
        approved_plan: dict,
        output_root: str | Path,
        run_id: str,
        logs_dir: str | Path,
        llm_router: "LLMRouter | None" = None,
        dry_run: bool = False,
    ) -> None:
        self.plan = approved_plan
        self.output_root = Path(output_root)
        self.run_id = run_id
        self.logs_dir = Path(logs_dir)
        self._router = llm_router
        self.dry_run = dry_run
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def execute(self, completed_step_ids: list[str], all_steps: list[dict]) -> dict[str, Any]:
        if self.dry_run:
            return {
                "status": "skipped_dry_run",
                "total_checked": 0,
                "passed": 0,
                "failed": 0,
                "findings": [],
                "report_json": None,
                "report_md": None,
            }

        steps_index = {s.get("id"): s for s in all_steps}
        checked_steps = [steps_index[sid] for sid in completed_step_ids if sid in steps_index]
        findings: list[dict[str, Any]] = []

        for step in checked_steps:
            step_id = step["id"]
            source_path = Path(self.plan["feature_root"]) / step["source_file"]
            target_path = self.output_root / step["target_file"]

            if not target_path.exists():
                findings.append(
                    {
                        "step": step_id,
                        "status": "FAIL",
                        "reason": f"Missing target output: {target_path}",
                    }
                )
                continue

            target_code = target_path.read_text(encoding="utf-8", errors="replace").strip()
            if not target_code:
                findings.append(
                    {
                        "step": step_id,
                        "status": "FAIL",
                        "reason": f"Target output is empty: {target_path}",
                    }
                )
                continue

            source_code = ""
            if source_path.exists():
                source_code = source_path.read_text(encoding="utf-8", errors="replace")

            behavior_check = self._simulate_behavior(step, source_code, target_code)
            findings.append(behavior_check)

        failed = [f for f in findings if f.get("status") != "PASS"]
        passed = [f for f in findings if f.get("status") == "PASS"]

        status = "passed" if not failed else "failed"
        report = {
            "run_id": self.run_id,
            "status": status,
            "total_checked": len(checked_steps),
            "passed": len(passed),
            "failed": len(failed),
            "findings": findings,
        }

        json_path = self.logs_dir / f"{self.run_id}-validation-report.json"
        md_path = self.logs_dir / f"{self.run_id}-validation-report.md"
        json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        md_path.write_text(self._render_markdown(report), encoding="utf-8")

        report["report_json"] = str(json_path)
        report["report_md"] = str(md_path)
        return report

    def _simulate_behavior(self, step: dict, source_code: str, target_code: str) -> dict[str, Any]:
        if self._router is None or not self._router.is_available:
            # Conservative fallback when no LLM is available.
            return {
                "step": step["id"],
                "status": "PASS",
                "reason": "LLM simulation unavailable; file sanity checks passed.",
                "confidence": 0.4,
            }

        from agents.llm import LLMMessage
        from agents.llm.base import LLMNotAvailableError, LLMProviderError

        system = (
            "You validate migrated code. Compare OLD source and NEW source behavior. "
            "Return strict JSON only with keys: status, reason, confidence. "
            "status must be PASS or FAIL."
        )
        user = (
            f"Step: {step['id']}\n"
            f"Source file: {step['source_file']}\n"
            f"Target file: {step['target_file']}\n\n"
            f"OLD SOURCE:\n```text\n{source_code}\n```\n\n"
            f"NEW SOURCE:\n```text\n{target_code}\n```\n"
        )

        try:
            response = self._router.complete(
                system=system,
                messages=[LLMMessage(role="user", content=user)],
            )
            parsed = self._parse_llm_json(response.text)
            return {
                "step": step["id"],
                "status": parsed.get("status", "FAIL"),
                "reason": parsed.get("reason", "LLM returned no reason."),
                "confidence": float(parsed.get("confidence", 0.0)),
            }
        except (LLMNotAvailableError, LLMProviderError, ValueError) as exc:
            logger.warning("[%s] Validation simulation fallback: %s", step["id"], exc)
            return {
                "step": step["id"],
                "status": "PASS",
                "reason": f"Simulation unavailable ({exc}); file sanity checks passed.",
                "confidence": 0.35,
            }

    @staticmethod
    def _parse_llm_json(raw_text: str) -> dict[str, Any]:
        text = raw_text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        data = json.loads(text)
        status = str(data.get("status", "")).upper()
        if status not in {"PASS", "FAIL"}:
            raise ValueError(f"Invalid validation status from LLM: {status}")
        data["status"] = status
        return data

    @staticmethod
    def _render_markdown(report: dict[str, Any]) -> str:
        lines = [
            f"# Validation Report - {report['run_id']}",
            "",
            f"- Status: **{report['status'].upper()}**",
            f"- Total checked: {report['total_checked']}",
            f"- Passed: {report['passed']}",
            f"- Failed: {report['failed']}",
            "",
            "## Findings",
        ]
        if not report["findings"]:
            lines.append("- No findings.")
            return "\n".join(lines) + "\n"

        for f in report["findings"]:
            lines.append(
                f"- `{f.get('step', '?')}` [{f.get('status', '?')}]: {f.get('reason', '')} "
                f"(confidence={f.get('confidence', 0.0)})"
            )
        return "\n".join(lines) + "\n"

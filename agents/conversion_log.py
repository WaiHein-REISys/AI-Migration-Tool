"""
Conversion Log
==============
Real-time, append-only log of every action taken by the Conversion Agent.
Persists to a JSON file incrementally and can export a Markdown summary.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ConversionLog:
    """
    Thread-safe (single-threaded) log of all conversion actions.

    Each entry records:
        sequence      – monotonic counter
        timestamp     – ISO-8601 UTC
        action        – read_file | resolved_template | wrote_file |
                        halted_ambiguous | rejected_out_of_boundary |
                        flagged | step_started | step_completed | skipped
        source_file   – (optional) source file path
        target_file   – (optional) target file path
        rule_applied  – (optional) RULE-XXX id
        transformation– (optional) description of what was done
        rationale     – (optional) why
        plan_step_ref – (optional) Step A1, Step B1 …
        deviation     – (optional) description of any deviation from plan
    """

    def __init__(
        self,
        feature_name: str,
        run_id: str,
        plan_ref: str,
        log_path: str | Path,
    ) -> None:
        self.feature_name = feature_name
        self.run_id       = run_id
        self.plan_ref     = plan_ref
        self.log_path     = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        self._entries: list[dict[str, Any]] = []
        self._seq: int = 0
        self._status: str = "running"
        self._started_at: str = datetime.now(timezone.utc).isoformat()
        self._completed_at: str | None = None

        self._flush()   # initialise file

    # ------------------------------------------------------------------
    # Recording API
    # ------------------------------------------------------------------

    def record(
        self,
        action: str,
        *,
        source_file: str | None = None,
        target_file: str | None = None,
        rule_applied: str | None = None,
        transformation: str | None = None,
        rationale: str | None = None,
        plan_step_ref: str | None = None,
        deviation: str | None = None,
        extra: dict | None = None,
    ) -> None:
        self._seq += 1
        entry: dict[str, Any] = {
            "sequence":   self._seq,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "action":     action,
        }
        if source_file:     entry["source_file"]  = source_file
        if target_file:     entry["target_file"]  = target_file
        if rule_applied:    entry["rule_applied"] = rule_applied
        if transformation:  entry["transformation"] = transformation
        if rationale:       entry["rationale"]    = rationale
        if plan_step_ref:   entry["plan_step_ref"] = plan_step_ref
        if deviation:       entry["deviation_from_plan"] = deviation
        if extra:           entry.update(extra)

        self._entries.append(entry)
        self._flush()
        logger.debug("[LOG #%d] %s — %s", self._seq, action, source_file or target_file or "")

    def start_step(self, step: dict) -> None:
        self.record(
            "step_started",
            plan_step_ref=step.get("id"),
            rationale=f"Beginning conversion step: {step.get('description', '')}",
        )

    def complete_step(self, step: dict) -> None:
        self.record(
            "step_completed",
            plan_step_ref=step.get("id"),
            rationale=f"Completed conversion step: {step.get('description', '')}",
        )

    def skip_step(self, step: dict, reason: str) -> None:
        self.record(
            "skipped",
            plan_step_ref=step.get("id"),
            rationale=reason,
        )

    def finalize(self, status: str = "completed") -> None:
        self._status       = status
        self._completed_at = datetime.now(timezone.utc).isoformat()
        self._flush()
        logger.info("Conversion log finalised — status: %s", status)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "feature_name":      self.feature_name,
            "conversion_run_id": self.run_id,
            "plan_document_ref": self.plan_ref,
            "started_at":        self._started_at,
            "completed_at":      self._completed_at,
            "status":            self._status,
            "entries":           self._entries,
        }

    def export_markdown(self, output_path: str | Path) -> None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            f"# Conversion Log — {self.feature_name}",
            f"**Run ID:** `{self.run_id}`  ",
            f"**Plan Ref:** `{self.plan_ref}`  ",
            f"**Started:** {self._started_at}  ",
            f"**Completed:** {self._completed_at or 'N/A'}  ",
            f"**Status:** {self._status}  ",
            "",
            "---",
            "",
            "| # | Time | Action | Source | Target | Rule | Notes |",
            "|---|------|--------|--------|--------|------|-------|",
        ]
        for e in self._entries:
            ts        = e.get("timestamp", "")[:19].replace("T", " ")
            action    = e.get("action", "")
            src       = e.get("source_file", "")
            tgt       = e.get("target_file", "")
            rule      = e.get("rule_applied", "")
            notes     = e.get("rationale") or e.get("deviation_from_plan") or ""
            lines.append(f"| {e['sequence']} | {ts} | `{action}` | `{src}` | `{tgt}` | {rule} | {notes} |")

        out.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Conversion log markdown exported to: %s", out)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _flush(self) -> None:
        """Write the full log to disk after every change."""
        with open(self.log_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

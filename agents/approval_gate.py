"""
Human Approval Gate + Checkpoint/Resume
========================================
Implements the hard stop between Plan Generation and Conversion Execution.
Also manages checkpoint state for partial conversion handling.

Approval modes:
  - cli_prompt   : Interactive terminal prompt (default)
  - pr_merge     : Checks for a marker file (simulates PR merge approval)
  - auto_approve : FOR TESTING ONLY — skips the gate

Checkpoint/Resume:
  - Saves completed steps to a JSON checkpoint file after every step
  - On resume, skips already-completed steps
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Approval Gate
# ---------------------------------------------------------------------------

class ApprovalTimeoutError(Exception):
    """Raised when the plan approval window expires (CLI timeout is not enforced here)."""

class ApprovalRejectedError(Exception):
    """Raised when a human explicitly rejects the plan."""


class ApprovalGate:
    """
    Presents the Plan Document to a human approver and waits for sign-off.
    The pipeline MUST NOT proceed to conversion without this gate passing.
    """

    VALID_APPROVE  = {"y", "yes", "approve", "approved", "accept"}
    VALID_REJECT   = {"n", "no", "reject", "rejected", "decline"}

    def __init__(
        self,
        mode: str = "cli_prompt",
        approval_marker_path: str | Path | None = None,
    ) -> None:
        """
        Args:
            mode:                  'cli_prompt' | 'pr_merge' | 'auto_approve'
            approval_marker_path:  Path to the approval marker file (pr_merge mode)
        """
        self.mode                 = mode
        self.approval_marker_path = Path(approval_marker_path) if approval_marker_path else None

    def request_approval(self, plan_path: Path, plan_content: str) -> bool:
        """
        Show the plan to the approver and return True if approved.

        Raises:
            ApprovalRejectedError  — if explicitly rejected
            FileNotFoundError      — if plan file missing (pr_merge mode)
        """
        logger.info("Approval gate triggered for plan: %s", plan_path)

        if self.mode == "auto_approve":
            logger.warning(
                "AUTO-APPROVE mode active. This should only be used for testing."
            )
            return True

        if self.mode == "pr_merge":
            return self._check_pr_marker()

        # Default: cli_prompt
        return self._cli_prompt(plan_path, plan_content)

    # ------------------------------------------------------------------
    # CLI prompt
    # ------------------------------------------------------------------

    def _cli_prompt(self, plan_path: Path, plan_content: str) -> bool:
        separator = "=" * 72

        print(f"\n{separator}")
        print("  HUMAN APPROVAL REQUIRED — AI Migration Tool")
        print(separator)
        print(f"\nPlan document saved at:\n  {plan_path}\n")
        print("Please review the plan document above before approving.")
        print("Opening plan summary...\n")

        # Print a condensed summary (first 60 lines)
        lines = plan_content.splitlines()
        preview = "\n".join(lines[:60])
        if len(lines) > 60:
            preview += f"\n\n... [{len(lines) - 60} more lines — see {plan_path}] ..."
        print(preview)

        print(f"\n{separator}")
        print("[!] No code has been written yet.")
        print("   The Conversion Agent will only start after your approval.")
        print(separator)

        while True:
            try:
                answer = input(
                    "\nApprove this migration plan? "
                    "[yes/no, or 'view' to reprint]: "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nInterrupted — plan NOT approved.")
                raise ApprovalRejectedError("Approval interrupted by user (Ctrl+C / EOF).")

            if answer == "view":
                print(plan_content)
                continue

            if answer in self.VALID_APPROVE:
                print("\n[OK] Plan APPROVED. Starting conversion execution...\n")
                return True

            if answer in self.VALID_REJECT:
                feedback = input("Please describe what needs to change (or press Enter to skip): ").strip()
                raise ApprovalRejectedError(
                    f"Plan rejected by user. Feedback: {feedback or '(none provided)'}"
                )

            print(f"  Unrecognised input '{answer}'. Please type 'yes' or 'no'.")

    # ------------------------------------------------------------------
    # PR merge mode
    # ------------------------------------------------------------------

    def _check_pr_marker(self) -> bool:
        """
        Returns True if the approval marker file exists (simulates a merged PR).
        The marker file should be created externally by the CI/CD approval workflow.
        """
        if not self.approval_marker_path:
            raise ValueError("approval_marker_path must be set when mode='pr_merge'")

        if self.approval_marker_path.exists():
            logger.info("PR approval marker found at: %s", self.approval_marker_path)
            return True

        logger.info(
            "PR approval marker not found at: %s — plan is pending approval.",
            self.approval_marker_path
        )
        return False


# ---------------------------------------------------------------------------
# Checkpoint / Resume
# ---------------------------------------------------------------------------


class CheckpointManager:
    """
    Saves and restores pipeline state so that interrupted conversions can
    resume from the last successfully completed step.

    Checkpoint file format:
        {
          "run_id": str,
          "feature": str,
          "last_completed_step": str | null,
          "completed_steps": [...],
          "pending_steps": [...],
          "blocked_steps": [...],
          "block_reason": str | null,
          "checkpoint_at": ISO-8601 str
        }
    """

    def __init__(self, checkpoints_dir: str | Path, run_id: str, feature: str) -> None:
        self.dir     = Path(checkpoints_dir)
        self.run_id  = run_id
        self.feature = feature
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path    = self.dir / f"{run_id}-checkpoint.json"

        self._state: dict = self._load_or_init()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_completed(self, step_id: str) -> bool:
        """Return True if this step was already completed in a previous run."""
        return step_id in self._state["completed_steps"]

    def mark_completed(self, step_id: str, all_step_ids: list[str]) -> None:
        """Record a step as completed and update pending/last_completed."""
        if step_id not in self._state["completed_steps"]:
            self._state["completed_steps"].append(step_id)
        self._state["last_completed_step"] = step_id
        self._state["pending_steps"] = [
            s for s in all_step_ids
            if s not in self._state["completed_steps"]
            and s not in self._state["blocked_steps"]
        ]
        self._state["checkpoint_at"] = datetime.now(timezone.utc).isoformat()
        self._flush()

    def mark_blocked(self, step_id: str, reason: str) -> None:
        """Record a step as blocked (ambiguous/out-of-boundary)."""
        if step_id not in self._state["blocked_steps"]:
            self._state["blocked_steps"].append(step_id)
        self._state["block_reason"] = reason
        self._state["checkpoint_at"] = datetime.now(timezone.utc).isoformat()
        self._flush()

    def get_state(self) -> dict:
        return dict(self._state)

    def summary(self) -> str:
        s = self._state
        return (
            f"Checkpoint [{self.run_id}]:\n"
            f"  Last completed: {s.get('last_completed_step')}\n"
            f"  Completed: {s['completed_steps']}\n"
            f"  Pending:   {s['pending_steps']}\n"
            f"  Blocked:   {s['blocked_steps']}\n"
            f"  Block reason: {s.get('block_reason')}\n"
            f"  As of: {s.get('checkpoint_at')}"
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_or_init(self) -> dict:
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                state = json.load(f)
            logger.info(
                "Resuming from checkpoint: %s (last completed: %s)",
                self.path, state.get("last_completed_step")
            )
            return state
        return {
            "run_id":               self.run_id,
            "feature":              self.feature,
            "last_completed_step":  None,
            "completed_steps":      [],
            "pending_steps":        [],
            "blocked_steps":        [],
            "block_reason":         None,
            "checkpoint_at":        None,
        }

    def _flush(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)

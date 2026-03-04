"""
agents.cleanup — Stale artefact pruner
=======================================
Removes pipeline artefacts (logs, checkpoints, plans) that are older than
a configurable retention period.  Called automatically at the start of each
run_agent.py invocation so the workspace stays navigable.

Directories pruned (files only, never the directory itself):
  logs/         *.json, *.md            — conversion / validation / dependency logs
  checkpoints/  *.json                  — resume state
  plans/        *.md                    — generated plan documents

Default retention: 1 day (86 400 seconds).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_PRUNE_GLOBS: dict[str, list[str]] = {
    "logs":        ["*.json", "*.md"],
    "checkpoints": ["*.json"],
    "plans":       ["*.md"],
}


def prune_old_artefacts(
    root: Path,
    max_age_seconds: float = 86_400,
) -> dict[str, int]:
    """
    Delete pipeline artefact files older than *max_age_seconds*.

    Parameters
    ----------
    root:
        Repository root (parent of logs/, checkpoints/, plans/).
    max_age_seconds:
        Files whose mtime is older than this are deleted.  Default: 1 day.

    Returns
    -------
    dict mapping directory name → number of files deleted.
    """
    cutoff   = time.time() - max_age_seconds
    removed  = {}

    for subdir, globs in _PRUNE_GLOBS.items():
        target_dir = root / subdir
        if not target_dir.is_dir():
            continue

        count = 0
        for pattern in globs:
            for path in target_dir.glob(pattern):
                if not path.is_file():
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                        logger.debug("Pruned stale artefact: %s", path.name)
                        count += 1
                except OSError as exc:
                    logger.warning("Could not remove %s: %s", path, exc)

        removed[subdir] = count

    total = sum(removed.values())
    if total:
        detail = ", ".join(f"{d}: {n}" for d, n in removed.items() if n)
        logger.info(
            "Cleanup: removed %d stale artefact(s) older than %.0fh (%s)",
            total,
            max_age_seconds / 3600,
            detail,
        )

    return removed

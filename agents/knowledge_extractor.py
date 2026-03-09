"""
Knowledge Extractor
====================
Mines completed pipeline run artefacts (conversion logs, validation reports,
dependency graphs) and persists learnings to the MemoryStore.

Runs automatically at the end of every successful pipeline run — both the
legacy sequential path and the orchestrated path.  Zero new pip dependencies.

Usage:
    from agents.knowledge_extractor import KnowledgeExtractor
    from agents.memory_store import MemoryStore

    ke = KnowledgeExtractor(MemoryStore())
    result = ke.extract(
        run_id="abc12345",
        dependency_graph=dependency_graph,
        conversion_log_path=Path("logs/abc12345-conversion-log.json"),
        validation_report_path=Path("logs/abc12345-validation-report.json"),
    )
    # result == {"patterns_added": 3, "domain_facts_added": 2, "resolutions_added": 1}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.memory_store import MemoryStore
    from agents.llm.registry import LLMRouter

logger = logging.getLogger(__name__)


class KnowledgeExtractor:
    """
    Extracts learnings from completed pipeline artefacts and writes them
    to the MemoryStore.
    """

    def __init__(
        self,
        memory_store: "MemoryStore",
        llm_router: "LLMRouter | None" = None,
    ) -> None:
        self._store  = memory_store
        self._router = llm_router  # reserved for future LLM-assisted extraction

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(
        self,
        run_id: str,
        dependency_graph: dict,
        conversion_log_path: Path,
        validation_report_path: Path | None = None,
    ) -> dict:
        """
        Mine completed run artefacts and persist learnings.

        Returns:
            {
                "patterns_added":      int,
                "domain_facts_added":  int,
                "resolutions_added":   int,
            }
        """
        result = {
            "patterns_added":     0,
            "domain_facts_added": 0,
            "resolutions_added":  0,
        }

        conversion_log = self._load_json(conversion_log_path)
        if not conversion_log:
            logger.debug(
                "KnowledgeExtractor: no conversion log at %s — skipping.",
                conversion_log_path,
            )
            return result

        # 1. Extract code conversion patterns
        result["patterns_added"] = self._extract_patterns(conversion_log)

        # 2. Extract domain knowledge from dependency graph
        result["domain_facts_added"] = self._extract_domain_facts(dependency_graph)

        # 3. Extract how ambiguities were resolved
        result["resolutions_added"] = self._extract_failure_resolutions(
            conversion_log, run_id
        )

        logger.info(
            "KnowledgeExtractor [%s]: +%d patterns, +%d facts, +%d resolutions",
            run_id,
            result["patterns_added"],
            result["domain_facts_added"],
            result["resolutions_added"],
        )
        return result

    # ------------------------------------------------------------------
    # Internal — pattern extraction
    # ------------------------------------------------------------------

    def _extract_patterns(self, conversion_log: dict) -> int:
        """
        Mine 'wrote_file' / 'completed' log entries to find source→target
        conversion patterns (import set + hook set → target signature).

        Writes to MemoryStore.pattern-library.json via record_patterns().
        """
        steps = conversion_log.get("steps", [])
        if not steps:
            return 0

        # Build enriched steps with source_signature / target_signature if missing
        enriched = []
        for step in steps:
            status = step.get("status", "")
            if status not in ("wrote_file", "completed", "success"):
                continue

            src_sig = step.get("source_signature", "")
            tgt_sig = step.get("target_signature", "")

            # Derive signatures from file paths if not explicitly set
            if not src_sig:
                src_sig = self._derive_source_signature(step)
            if not tgt_sig:
                tgt_sig = self._derive_target_signature(step)

            if src_sig and tgt_sig:
                enriched.append({**step, "source_signature": src_sig, "target_signature": tgt_sig})

        if not enriched:
            return 0

        synthetic_log = {"steps": enriched}
        return self._store.record_patterns(synthetic_log)

    @staticmethod
    def _derive_source_signature(step: dict) -> str:
        """Build a human-readable source signature from step metadata."""
        parts = []
        file_type = step.get("file_type", "")
        component = step.get("component_type", "")
        hooks      = step.get("source_hooks", [])

        if file_type:
            parts.append(file_type)
        if component:
            parts.append(component)
        if hooks:
            parts.append(f"[{', '.join(hooks[:3])}]")

        return " ".join(parts) if parts else ""

    @staticmethod
    def _derive_target_signature(step: dict) -> str:
        """Build a human-readable target signature from step metadata."""
        parts = []
        output_file = step.get("output_file", "")
        target_type = step.get("target_type", "")
        target_hooks = step.get("target_hooks", [])

        if target_type:
            parts.append(target_type)
        if output_file:
            parts.append(Path(output_file).suffix or "")
        if target_hooks:
            parts.append(f"[{', '.join(target_hooks[:3])}]")

        return " ".join(filter(None, parts)) if parts else ""

    # ------------------------------------------------------------------
    # Internal — domain fact extraction
    # ------------------------------------------------------------------

    def _extract_domain_facts(self, dependency_graph: dict) -> int:
        """
        Mine flags[] and external_points[] from the dependency graph to grow
        the known library / service catalogue in MemoryStore.

        Returns the approximate count of new library entries added.
        """
        if not dependency_graph:
            return 0

        before_keys = self._count_known_libraries()
        self._store.record_domain_knowledge(dependency_graph)
        after_keys  = self._count_known_libraries()
        return max(0, after_keys - before_keys)

    def _count_known_libraries(self) -> int:
        """Read current known_libraries count from domain-knowledge.json."""
        try:
            path = self._store._dir / "domain-knowledge.json"
            if not path.exists():
                return 0
            data = json.loads(path.read_text(encoding="utf-8"))
            return len(data.get("known_libraries", {}))
        except (OSError, json.JSONDecodeError):
            return 0

    # ------------------------------------------------------------------
    # Internal — failure resolution extraction
    # ------------------------------------------------------------------

    def _extract_failure_resolutions(
        self,
        conversion_log: dict,
        run_id: str,
    ) -> int:
        """
        Mine 'halted_ambiguous' log entries that were later successfully resolved
        (i.e. a step with the same file was later written_file / completed).

        For each resolved ambiguity, record it in MemoryStore.failure-registry.json.
        """
        steps = conversion_log.get("steps", [])
        if not steps:
            return 0

        # Index all halted steps by source file
        halted: dict[str, dict] = {}
        resolved_files: set[str] = set()

        for step in steps:
            status = step.get("status", "")
            src    = step.get("source_file", "")
            if not src:
                continue
            if status in ("halted_ambiguous", "blocked", "ambiguous"):
                halted[src] = step
            elif status in ("wrote_file", "completed", "success"):
                resolved_files.add(src)

        added = 0
        for src_file, halted_step in halted.items():
            if src_file not in resolved_files:
                continue

            ambiguity  = halted_step.get("ambiguity_reason", "")
            resolution = halted_step.get("resolution", "")

            if not ambiguity:
                # Build a description from the step
                ambiguity = (
                    f"{halted_step.get('file_type', 'file')} "
                    f"'{Path(src_file).name}' was ambiguous during conversion"
                )
            if not resolution:
                resolution = "File was successfully converted in a subsequent step"

            resolved_by = halted_step.get("resolved_by", "auto")

            self._store.record_failure_resolution(
                ambiguity=ambiguity,
                resolution=resolution,
                resolved_by=resolved_by,
                run_id=run_id,
            )
            added += 1

        return added

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_json(path: Path) -> dict:
        if not path or not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "KnowledgeExtractor: could not read %s: %s", path, exc
            )
            return {}

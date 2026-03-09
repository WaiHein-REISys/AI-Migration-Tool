"""
Memory Store
=============
Persistent, team-shareable knowledge base for the AI Migration Tool.
Accumulates learning across pipeline runs and injects relevant context into
PlanAgent and ConversionAgent LLM prompts.

Zero new pip dependencies — uses only stdlib: json, pathlib, hashlib, re.

Four JSON backing files in config/memory/:
    pattern-library.json   – proven source→target code patterns
    user-preferences.json  – user plan revision / approval signals
    domain-knowledge.json  – known libraries, services, cross-feature relationships
    failure-registry.json  – how ambiguities were resolved (human or auto)

Files are written atomically via .tmp + rename to prevent JSON corruption.
Files are committable to git so team members share learnings.

Usage:
    from agents.memory_store import MemoryStore

    ms = MemoryStore()                              # default: config/memory/
    ctx = ms.get_context("ActionHistory", graph, "snake_case")
    # ctx.context_summary → inject into LLM prompts

    # After run:
    ms.record_patterns(conversion_log)
    ms.record_domain_knowledge(dependency_graph)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Memory directory and file paths
# ---------------------------------------------------------------------------
_DEFAULT_MEMORY_DIR = Path("config/memory")
_PATTERN_LIBRARY   = "pattern-library.json"
_USER_PREFS        = "user-preferences.json"
_DOMAIN_KNOWLEDGE  = "domain-knowledge.json"
_FAILURE_REGISTRY  = "failure-registry.json"

# Jaccard similarity threshold for pattern matching
_JACCARD_THRESHOLD = 0.35
_MAX_PATTERNS_RETURNED = 3


# ---------------------------------------------------------------------------
# MemoryContext dataclass (injected into PlanAgent / ConversionAgent)
# ---------------------------------------------------------------------------

@dataclass
class MemoryContext:
    """
    Relevant past knowledge assembled before a pipeline run.
    context_summary is a pre-formatted string ready to append to LLM prompts.
    """
    similar_patterns:     list[dict] = field(default_factory=list)
    user_preferences:     list[dict] = field(default_factory=list)
    domain_facts:         list[dict] = field(default_factory=list)
    failure_resolutions:  list[dict] = field(default_factory=list)
    context_summary:      str        = ""

    def is_empty(self) -> bool:
        return not self.context_summary


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------

class MemoryStore:
    """
    Manages four JSON backing files and surfaces relevant context for each run.
    """

    def __init__(self, memory_dir: Path | str = _DEFAULT_MEMORY_DIR) -> None:
        self._dir = Path(memory_dir)
        self._ensure_directory()

    # ------------------------------------------------------------------
    # Public API — called before pipeline runs
    # ------------------------------------------------------------------

    def get_context(
        self,
        feature_name: str,
        dependency_graph: dict,
        target: str,
    ) -> MemoryContext:
        """
        Assemble a MemoryContext for the given feature / target.
        Returns an empty MemoryContext (context_summary == "") on first run.
        """
        # Extract imports and hooks from dependency graph for matching
        imports, hooks = self._extract_graph_signals(dependency_graph)

        similar_patterns    = self._find_similar_patterns(imports, hooks)
        user_preferences    = self._get_preferences_for_target(target)
        domain_facts        = self._get_domain_facts(imports)
        failure_resolutions = self._get_failure_resolutions(imports)

        summary = self._build_summary(
            similar_patterns, user_preferences, domain_facts, failure_resolutions
        )

        ctx = MemoryContext(
            similar_patterns=similar_patterns,
            user_preferences=user_preferences,
            domain_facts=domain_facts,
            failure_resolutions=failure_resolutions,
            context_summary=summary,
        )

        if summary:
            logger.info(
                "MemoryStore: loaded context for '%s' — "
                "%d patterns, %d prefs, %d domain facts, %d resolutions",
                feature_name,
                len(similar_patterns), len(user_preferences),
                len(domain_facts), len(failure_resolutions),
            )
        else:
            logger.debug(
                "MemoryStore: no prior context for '%s' (first run or empty store).",
                feature_name,
            )

        return ctx

    # ------------------------------------------------------------------
    # Public API — called by KnowledgeExtractor after each run
    # ------------------------------------------------------------------

    def record_patterns(self, conversion_log: dict) -> int:
        """
        Mine a conversion log dict for successful source→target file conversions
        and save them as reusable patterns.

        Returns the number of new patterns added.
        """
        data = self._load(_PATTERN_LIBRARY, {"version": "1.0", "patterns": []})
        patterns: list[dict] = data.get("patterns", [])
        added = 0

        for step in conversion_log.get("steps", []):
            if step.get("status") not in ("wrote_file", "completed"):
                continue
            imports    = step.get("source_imports", [])
            hooks      = step.get("source_hooks", [])
            src_sig    = step.get("source_signature", "")
            tgt_sig    = step.get("target_signature", "")
            feature    = step.get("feature_name", "unknown")
            confidence = step.get("confidence", 0.85)

            if not src_sig or not tgt_sig:
                continue

            # Skip if we already have this exact pattern
            fingerprint = self._fingerprint(src_sig)
            if any(p.get("fingerprint") == fingerprint for p in patterns):
                continue

            patterns.append({
                "fingerprint":        fingerprint,
                "source_signature":   src_sig,
                "target_signature":   tgt_sig,
                "source_imports":     imports,
                "source_hooks":       hooks,
                "feature_name":       feature,
                "confidence":         confidence,
                "run_count":          1,
            })
            added += 1

        if added:
            data["patterns"] = patterns
            self._atomic_write(_PATTERN_LIBRARY, data)
            logger.info("MemoryStore: recorded %d new patterns.", added)

        return added

    def record_preferences(self, event: str, context: str, feedback: str) -> None:
        """
        Record a user preference signal (e.g. plan revision / rejection reason).

        event:    e.g. "plan_revision", "step_rejection", "approval"
        context:  e.g. "target=snake_case, blocked_import=pfm-auth"
        feedback: e.g. "Flag pfm-* imports as BLOCKED"
        """
        data = self._load(_USER_PREFS, {"version": "1.0", "preferences": []})
        prefs: list[dict] = data.get("preferences", [])

        # Avoid exact duplicates
        if any(p.get("feedback") == feedback for p in prefs):
            return

        prefs.append({
            "event":    event,
            "context":  context,
            "feedback": feedback,
        })
        data["preferences"] = prefs
        self._atomic_write(_USER_PREFS, data)
        logger.debug("MemoryStore: recorded preference: %s", feedback[:80])

    def record_domain_knowledge(self, dependency_graph: dict) -> None:
        """
        Mine flags[] and external_points[] from the dependency graph to grow
        the known library / service / feature-relationship catalogue.
        """
        data = self._load(_DOMAIN_KNOWLEDGE, {
            "version": "1.0",
            "known_libraries": {},
            "known_services":  {},
            "feature_relationships": {},
        })
        known_libs: dict = data.get("known_libraries", {})
        known_svc:  dict = data.get("known_services", {})

        changed = False

        # Mine flags (blocked imports, unknown libs, etc.)
        for flag in dependency_graph.get("flags", []):
            lib = flag.get("import_path") or flag.get("library", "")
            reason = flag.get("reason", "")
            if lib and lib not in known_libs:
                known_libs[lib] = {"note": reason or "flagged during migration"}
                changed = True

        # Mine external_points
        for ep in dependency_graph.get("external_points", []):
            lib = ep.get("import") or ep.get("module", "")
            desc = ep.get("description", "")
            if lib and lib not in known_libs:
                known_libs[lib] = {"note": desc or "observed as external dependency"}
                changed = True

        if changed:
            data["known_libraries"] = known_libs
            data["known_services"]  = known_svc
            self._atomic_write(_DOMAIN_KNOWLEDGE, data)
            logger.debug("MemoryStore: updated domain knowledge.")

    def record_failure_resolution(
        self,
        ambiguity: str,
        resolution: str,
        resolved_by: str,
        run_id: str,
    ) -> None:
        """
        Record how an ambiguity was resolved.

        ambiguity:   Description of the problem (e.g. "pfm-ng/core import unresolved")
        resolution:  What was done (e.g. "Mark BLOCKED, create stub interface")
        resolved_by: "human" | "auto" | "orchestrator"
        run_id:      The run that resolved it
        """
        data = self._load(_FAILURE_REGISTRY, {"version": "1.0", "resolutions": []})
        resolutions: list[dict] = data.get("resolutions", [])

        fingerprint = self._fingerprint(ambiguity)
        if any(r.get("fingerprint") == fingerprint for r in resolutions):
            return  # already known

        resolutions.append({
            "fingerprint":  fingerprint,
            "ambiguity":    ambiguity,
            "resolution":   resolution,
            "resolved_by":  resolved_by,
            "run_id":       run_id,
        })
        data["resolutions"] = resolutions
        self._atomic_write(_FAILURE_REGISTRY, data)
        logger.debug("MemoryStore: recorded failure resolution: %s", ambiguity[:80])

    # ------------------------------------------------------------------
    # Internal — pattern matching
    # ------------------------------------------------------------------

    def _find_similar_patterns(
        self,
        imports: list[str],
        hooks: list[str],
    ) -> list[dict]:
        """
        Return up to MAX_PATTERNS_RETURNED patterns with Jaccard similarity
        above JACCARD_THRESHOLD against the provided imports+hooks token set.
        """
        data = self._load(_PATTERN_LIBRARY, {"version": "1.0", "patterns": []})
        patterns: list[dict] = data.get("patterns", [])

        if not patterns or not (imports or hooks):
            return []

        query_tokens = self._tokenise(imports + hooks)
        if not query_tokens:
            return []

        scored: list[tuple[float, dict]] = []
        for p in patterns:
            candidate_tokens = self._tokenise(
                p.get("source_imports", []) + p.get("source_hooks", [])
            )
            score = self._jaccard(query_tokens, candidate_tokens)
            if score >= _JACCARD_THRESHOLD:
                scored.append((score, p))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:_MAX_PATTERNS_RETURNED]]

    # ------------------------------------------------------------------
    # Internal — context retrieval helpers
    # ------------------------------------------------------------------

    def _get_preferences_for_target(self, target: str) -> list[dict]:
        data = self._load(_USER_PREFS, {"version": "1.0", "preferences": []})
        prefs = data.get("preferences", [])
        # Return all prefs that mention this target, or generic ones
        return [
            p for p in prefs
            if target in p.get("context", "") or not p.get("context", "")
        ]

    def _get_domain_facts(self, imports: list[str]) -> list[dict]:
        data = self._load(_DOMAIN_KNOWLEDGE, {
            "version": "1.0",
            "known_libraries": {},
        })
        known_libs: dict = data.get("known_libraries", {})
        facts = []
        for imp in imports:
            # Match on the base package (e.g. "@pfm/auth" → "pfm")
            for lib_key, lib_info in known_libs.items():
                if lib_key in imp or imp in lib_key:
                    facts.append({"library": lib_key, "note": lib_info.get("note", "")})
                    break
        return facts

    def _get_failure_resolutions(self, imports: list[str]) -> list[dict]:
        data = self._load(_FAILURE_REGISTRY, {"version": "1.0", "resolutions": []})
        resolutions = data.get("resolutions", [])
        # Surface resolutions whose ambiguity text overlaps with current imports
        relevant = []
        for r in resolutions:
            for imp in imports:
                if imp and imp in r.get("ambiguity", ""):
                    relevant.append(r)
                    break
        return relevant

    # ------------------------------------------------------------------
    # Internal — summary builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_summary(
        patterns:     list[dict],
        preferences:  list[dict],
        domain_facts: list[dict],
        resolutions:  list[dict],
    ) -> str:
        if not (patterns or preferences or domain_facts or resolutions):
            return ""

        lines: list[str] = []

        if patterns:
            lines.append("PROVEN PATTERNS:")
            for i, p in enumerate(patterns, 1):
                lines.append(
                    f"{i}. {p.get('source_signature', '?')} "
                    f"→ {p.get('target_signature', '?')}"
                )
                feature = p.get("feature_name", "")
                conf    = p.get("confidence", 0)
                if feature:
                    lines.append(f"   [Used in {feature} — confidence: {conf:.2f}]")
            lines.append("")

        if preferences:
            lines.append("USER PREFERENCES:")
            for i, p in enumerate(preferences, 1):
                lines.append(f"{i}. {p.get('feedback', '')}")
            lines.append("")

        if domain_facts:
            lines.append("DOMAIN KNOWLEDGE:")
            for i, f in enumerate(domain_facts, 1):
                lines.append(
                    f"{i}. {f.get('library', '?')} — {f.get('note', '')}"
                )
            lines.append("")

        if resolutions:
            lines.append("KNOWN RESOLUTIONS:")
            for i, r in enumerate(resolutions, 1):
                lines.append(
                    f"{i}. Ambiguity: {r.get('ambiguity', '?')[:80]} "
                    f"→ {r.get('resolution', '?')[:80]}"
                )
            lines.append("")

        return "\n".join(lines).strip()

    # ------------------------------------------------------------------
    # Internal — signal extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_graph_signals(
        dependency_graph: dict,
    ) -> tuple[list[str], list[str]]:
        """Extract import paths and lifecycle hooks from a dependency graph."""
        imports: list[str] = []
        hooks:   list[str] = []

        for file_info in dependency_graph.get("files", []):
            imports.extend(file_info.get("imports", []))
            hooks.extend(file_info.get("hooks", []))
            hooks.extend(file_info.get("lifecycle_methods", []))

        # Also check top-level flags
        for flag in dependency_graph.get("flags", []):
            imp = flag.get("import_path", "")
            if imp:
                imports.append(imp)

        return list(set(imports)), list(set(hooks))

    # ------------------------------------------------------------------
    # Internal — similarity helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenise(items: list[str]) -> set[str]:
        """Split import/hook strings into lowercase tokens."""
        tokens: set[str] = set()
        for item in items:
            # Split on /, @, -, _, . and camelCase
            parts = re.split(r"[/@\-_.]+", item)
            for part in parts:
                # Split camelCase / PascalCase
                sub = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\W|$)|\d+", part)
                tokens.update(s.lower() for s in sub if len(s) > 1)
        return tokens

    @staticmethod
    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0
        inter = len(a & b)
        union = len(a | b)
        return inter / union if union else 0.0

    # ------------------------------------------------------------------
    # Internal — persistence
    # ------------------------------------------------------------------

    def _ensure_directory(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        # Create stub files if they don't exist
        stubs = {
            _PATTERN_LIBRARY:   {"version": "1.0", "patterns": []},
            _USER_PREFS:        {"version": "1.0", "preferences": []},
            _DOMAIN_KNOWLEDGE:  {
                "version":              "1.0",
                "known_libraries":      {},
                "known_services":       {},
                "feature_relationships": {},
            },
            _FAILURE_REGISTRY:  {"version": "1.0", "resolutions": []},
        }
        for filename, default in stubs.items():
            path = self._dir / filename
            if not path.exists():
                self._atomic_write(filename, default)

    def _load(self, filename: str, default: dict) -> dict:
        path = self._dir / filename
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "MemoryStore: could not read %s (%s). Using default.", filename, exc
            )
            return default

    def _atomic_write(self, filename: str, data: dict) -> None:
        """Write data → .tmp file → rename (atomic on POSIX, near-atomic on Windows)."""
        path = self._dir / filename
        tmp  = path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as exc:
            logger.error(
                "MemoryStore: failed to write %s: %s", filename, exc
            )

    @staticmethod
    def _fingerprint(text: str) -> str:
        """Short stable hash of a string (first 12 hex chars of SHA-256)."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

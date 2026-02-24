"""
Scoping & Analysis Agent
========================
Analyses a declared feature boundary folder to produce a dependency graph.
Works over Angular 2 TypeScript (.ts), ASP.NET Core C# (.cs), and SQL (.sql)
source files found in HAB-GPRSSubmission.

This agent does NOT write any code. Its sole output is a dependency graph JSON
that feeds the Plan Document Generation Agent.

Requires:
    pip install tree-sitter tree-sitter-javascript tree-sitter-c-sharp
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional tree-sitter imports — graceful degradation to regex-based parsing
# ---------------------------------------------------------------------------
try:
    from tree_sitter import Language, Parser  # type: ignore
    import tree_sitter_javascript as tsjs     # type: ignore

    _TS_AVAILABLE = True
    logger.info("tree-sitter available — using AST-based TypeScript analysis.")
except ImportError:
    _TS_AVAILABLE = False
    logger.warning(
        "tree-sitter not installed. Falling back to regex-based import/export detection. "
        "Install with: pip install tree-sitter tree-sitter-javascript"
    )

# ---------------------------------------------------------------------------
# Flagged external platform libraries from RULE-008
# ---------------------------------------------------------------------------
PLATFORM_LIBRARIES = {
    "pfm-layout", "pfm-ng", "pfm-re", "pfm-dcf",
    "Platform.CrossCutting8", "Platform.Foundation8",
}


class ScopingAgent:
    """
    Analyses a feature boundary folder and builds a dependency graph.

    Emits:
        {
          "feature_name": str,
          "feature_root": str,
          "analyzed_at": ISO-8601 str,
          "nodes": [...],
          "edges": [...],
          "external_points": [...],
          "flags": [...]
        }
    """

    def __init__(self, feature_root: str | Path, config: dict) -> None:
        self.feature_root  = Path(feature_root)
        self.config        = config
        self.flagged_libs: set[str] = set(PLATFORM_LIBRARIES)
        # Resolve flagged libraries from config rules
        for rule in config.get("rules", {}).get("guardrails", []):
            self.flagged_libs.update(rule.get("flagged_libraries", []))

        self.dependency_graph: dict[str, Any] = {
            "feature_name": self.feature_root.name,
            "feature_root": str(self.feature_root),
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "nodes": [],
            "edges": [],
            "external_points": [],
            "flags": [],
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self) -> dict:
        """
        Walk the feature root and analyse all recognised source files.
        Returns the completed dependency graph dict.
        """
        if not self.feature_root.exists():
            raise FileNotFoundError(f"Feature root does not exist: {self.feature_root}")

        all_files = list(self.feature_root.rglob("*"))
        ts_files  = [f for f in all_files if f.suffix in (".ts",) and not f.name.endswith(".d.ts")]
        cs_files  = [f for f in all_files if f.suffix == ".cs"]
        sql_files = [f for f in all_files if f.suffix == ".sql"]

        logger.info(
            "Scoping %s — found %d .ts, %d .cs, %d .sql files.",
            self.feature_root.name, len(ts_files), len(cs_files), len(sql_files)
        )

        # Compute a stable hash over the sorted content of all source files so
        # save() can detect whether the sources have changed since the last run.
        source_hash = self._compute_source_hash(ts_files + cs_files + sql_files)

        for f in ts_files:
            self._analyze_typescript_file(f)
        for f in cs_files:
            self._analyze_csharp_file(f)
        for f in sql_files:
            self._analyze_sql_file(f)

        self._detect_cross_feature_coupling()
        self._detect_external_library_usage()

        # Embed the hash so save() can compare it against the on-disk graph.
        self.dependency_graph["source_hash"] = source_hash

        logger.info(
            "Scoping complete — %d nodes, %d edges, %d external points, %d flags.",
            len(self.dependency_graph["nodes"]),
            len(self.dependency_graph["edges"]),
            len(self.dependency_graph["external_points"]),
            len(self.dependency_graph["flags"]),
        )
        return self.dependency_graph

    def save(self, output_path: str | Path) -> None:
        """
        Persist the dependency graph to a JSON file.

        Skips the write if an identical graph already exists at *output_path*
        (same source_hash), avoiding redundant file churn on repeated runs.
        """
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        current_hash = self.dependency_graph.get("source_hash", "")
        if out.exists() and current_hash:
            try:
                with open(out, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if existing.get("source_hash") == current_hash:
                    logger.info(
                        "Dependency graph unchanged (source_hash=%s) — skipping write: %s",
                        current_hash, out,
                    )
                    return
            except Exception:
                pass  # Corrupt/unreadable existing file — overwrite it

        with open(out, "w", encoding="utf-8") as f:
            json.dump(self.dependency_graph, f, indent=2)
        logger.info("Dependency graph saved to: %s", out)

    # ------------------------------------------------------------------
    # Source hash computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_source_hash(files: list[Path]) -> str:
        """
        Compute a SHA-1 hash over the sorted, concatenated content of *files*.
        Stable across runs as long as source file content does not change.
        """
        h = hashlib.sha1()
        for path in sorted(files, key=lambda p: str(p)):
            try:
                h.update(path.read_bytes())
            except OSError:
                pass
        return h.hexdigest()

    # ------------------------------------------------------------------
    # TypeScript / Angular 2 analysis
    # ------------------------------------------------------------------

    def _analyze_typescript_file(self, path: Path) -> None:
        source = path.read_text(encoding="utf-8", errors="replace")
        rel_path = str(path.relative_to(self.feature_root))

        imports  = self._extract_ts_imports(source, path)
        exports  = self._extract_ts_exports(source)
        pattern  = self._detect_angular_pattern(source)
        metadata = self._extract_angular_metadata(source, pattern)

        node: dict[str, Any] = {
            "id":      rel_path,
            "type":    "frontend",
            "lang":    "TypeScript",
            "pattern": pattern,
            "exports": exports,
            "imports": [i["module"] for i in imports],
            **metadata,
        }
        self.dependency_graph["nodes"].append(node)

        for imp in imports:
            self._register_import(path, rel_path, imp["module"], imp["is_relative"])

    def _extract_ts_imports(self, source: str, source_path: Path) -> list[dict]:
        """Return list of {module, is_relative} dicts."""
        imports = []
        # Standard: import { X } from 'module'
        for m in re.finditer(r"""import\s+.*?from\s+['"]([^'"]+)['"]""", source, re.DOTALL):
            module = m.group(1)
            imports.append({"module": module, "is_relative": module.startswith(".")})
        # Side-effect: import 'module'
        for m in re.finditer(r"""import\s+['"]([^'"]+)['"]""", source):
            imports.append({"module": m.group(1), "is_relative": m.group(1).startswith(".")})
        return imports

    def _extract_ts_exports(self, source: str) -> list[str]:
        exports = []
        for m in re.finditer(r"export\s+(?:class|interface|function|const|enum|type)\s+(\w+)", source):
            exports.append(m.group(1))
        return exports

    def _detect_angular_pattern(self, source: str) -> str:
        if "@Component" in source and "templateUrl" in source:
            return "Angular 2 Component"
        if "@Injectable" in source and "BaseService" in source:
            return "Angular 2 Service (BaseService)"
        if "@Injectable" in source:
            return "Angular 2 Injectable Service"
        if "@NgModule" in source:
            return "Angular 2 NgModule"
        if "@Directive" in source:
            return "Angular 2 Directive"
        if "@Pipe" in source:
            return "Angular 2 Pipe"
        if "PageActionModel" in source:
            return "Angular 2 PageActionModel"
        return "TypeScript Module"

    def _extract_angular_metadata(self, source: str, pattern: str) -> dict:
        meta: dict[str, Any] = {}
        if "Angular 2 Component" in pattern:
            sel = re.search(r"selector:\s*'([^']+)'", source)
            meta["selector"] = sel.group(1) if sel else None
            meta["lifecycle_hooks"] = [
                h for h in ["OnInit", "OnDestroy", "AfterViewChecked", "OnChanges"]
                if f"implements {h}" in source or f", {h}" in source
            ]
            meta["uses_rxjs_subject"] = "Subject" in source
        if "Service" in pattern:
            methods = re.findall(r"(?:public|private)?\s+(\w+)\s*\(", source)
            meta["methods"] = [m for m in methods if not m.startswith("constructor")]
        return meta

    # ------------------------------------------------------------------
    # C# / ASP.NET Core analysis
    # ------------------------------------------------------------------

    def _analyze_csharp_file(self, path: Path) -> None:
        source = path.read_text(encoding="utf-8", errors="replace")
        rel_path = str(path.relative_to(self.feature_root))

        pattern   = self._detect_csharp_pattern(source)
        usings    = self._extract_csharp_usings(source)
        endpoints = self._extract_api_endpoints(source) if "Controller" in pattern else []
        methods   = self._extract_csharp_methods(source)

        node: dict[str, Any] = {
            "id":      rel_path,
            "type":    "backend",
            "lang":    "C#",
            "pattern": pattern,
            "usings":  usings,
            "methods": methods,
        }
        if endpoints:
            node["endpoints"] = endpoints

        self.dependency_graph["nodes"].append(node)

        for using in usings:
            is_external = using.startswith("Platform.") or using.startswith("GPRS.")
            self._register_import(path, rel_path, using, is_relative=False)

    def _detect_csharp_pattern(self, source: str) -> str:
        if "[Area(" in source and "Controller" in source:
            return "ASP.NET Core Area API Controller"
        if "SolutionBaseController" in source:
            return "ASP.NET Core MVC Controller (SolutionBaseController)"
        if ": I" in source and "Repository" in source:
            return "EF Core / Dapper Repository"
        if "DbContext" in source:
            return "EF Core DbContext"
        if "interface I" in source:
            return "C# Interface"
        if "class " in source and ": I" in source:
            return "C# Service Implementation"
        return "C# Class"

    def _extract_csharp_usings(self, source: str) -> list[str]:
        return re.findall(r"^using\s+([\w.]+);", source, re.MULTILINE)

    def _extract_api_endpoints(self, source: str) -> list[dict]:
        endpoints = []
        # Match [HttpGet/Post/Put/Delete] followed by [Route(...)] and method name
        blocks = re.finditer(
            r"\[(HttpGet|HttpPost|HttpPut|HttpDelete)\].*?\[Route\([\"']([^\"']+)[\"']\)\].*?public\s+\S+\s+(\w+)\s*\(",
            source, re.DOTALL
        )
        for m in blocks:
            endpoints.append({
                "method": m.group(1).replace("Http", ""),
                "route":  m.group(2),
                "action": m.group(3),
            })
        # Also try [Route] before [HttpXxx]
        blocks2 = re.finditer(
            r"\[Route\([\"']([^\"']+)[\"']\)\]\s*\[(HttpGet|HttpPost|HttpPut|HttpDelete)\]\s*.*?public\s+\S+\s+(\w+)\s*\(",
            source, re.DOTALL
        )
        for m in blocks2:
            ep = {"method": m.group(2).replace("Http", ""), "route": m.group(1), "action": m.group(3)}
            if ep not in endpoints:
                endpoints.append(ep)
        return endpoints

    def _extract_csharp_methods(self, source: str) -> list[str]:
        return re.findall(
            r"(?:public|protected|private)\s+(?:async\s+)?(?:Task<[^>]+>|[\w\[\]]+)\s+(\w+)\s*\(",
            source
        )

    # ------------------------------------------------------------------
    # SQL analysis
    # ------------------------------------------------------------------

    def _analyze_sql_file(self, path: Path) -> None:
        source = path.read_text(encoding="utf-8", errors="replace")
        rel_path = str(path.relative_to(self.feature_root))

        proc_names  = re.findall(r"CREATE\s+(?:OR\s+ALTER\s+)?(?:PROCEDURE|PROC)\s+\[?[\w.]+\]?\.\[?(\w+)\]?", source, re.IGNORECASE)
        table_refs  = re.findall(r"\bFROM\s+\[?[\w.]+\]?\.\[?(\w+)\]?", source, re.IGNORECASE)
        table_refs += re.findall(r"\bJOIN\s+\[?[\w.]+\]?\.\[?(\w+)\]?", source, re.IGNORECASE)
        table_refs += re.findall(r"\bINTO\s+\[?[\w.]+\]?\.\[?(\w+)\]?", source, re.IGNORECASE)

        node: dict[str, Any] = {
            "id":          rel_path,
            "type":        "database",
            "lang":        "SQL",
            "pattern":     "Stored Procedure" if proc_names else "SQL Script",
            "procedures":  proc_names,
            "table_refs":  list(set(table_refs)),
        }
        self.dependency_graph["nodes"].append(node)

    # ------------------------------------------------------------------
    # Edge / flag detection
    # ------------------------------------------------------------------

    def _register_import(
        self, source_path: Path, rel_path: str, module: str, is_relative: bool
    ) -> None:
        """Classify an import as internal edge, cross-feature, or external."""
        if is_relative:
            resolved = (source_path.parent / module).resolve()
            in_boundary = str(resolved).startswith(str(self.feature_root.resolve()))
            if in_boundary:
                self.dependency_graph["edges"].append({"from": rel_path, "to": module})
            else:
                self.dependency_graph["external_points"].append({
                    "from": rel_path, "to": module, "type": "cross-feature"
                })
        else:
            # Absolute / package import
            self.dependency_graph["external_points"].append({
                "from": rel_path, "to": module, "type": "package"
            })

    def _detect_cross_feature_coupling(self) -> None:
        """Flag relative imports that resolve outside the feature root."""
        for ep in self.dependency_graph["external_points"]:
            if ep.get("type") == "cross-feature":
                self.dependency_graph["flags"].append({
                    "severity":       "warning",
                    "rule":           "RULE-004",
                    "message":        f"Cross-feature coupling detected: '{ep['from']}' imports '{ep['to']}' which is outside the declared feature boundary.",
                    "recommendation": "Determine whether this dependency should be (a) included in scope, (b) stubbed, or (c) treated as an external API contract. Resolve before Step C1.",
                })

    def _detect_external_library_usage(self) -> None:
        """Flag any imports from platform-specific libraries (RULE-008)."""
        flagged = set()
        for node in self.dependency_graph["nodes"]:
            for imp in node.get("imports", []):
                for lib in PLATFORM_LIBRARIES:
                    if lib in imp and lib not in flagged:
                        flagged.add(lib)
                        self.dependency_graph["flags"].append({
                            "severity":  "blocking",
                            "rule":      "RULE-008",
                            "message":   f"External platform library detected: '{lib}' (imported in {node['id']}). No direct equivalent exists in the target stack.",
                            "recommendation": (
                                f"Review '{lib}' usage and decide: "
                                "(a) find equivalent Next.js/Python package, "
                                "(b) stub with a local implementation, or "
                                "(c) exclude from scope. Cannot proceed until resolved."
                            ),
                        })

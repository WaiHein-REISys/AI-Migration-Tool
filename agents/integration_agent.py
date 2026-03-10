"""
Integration Agent
=================
Stage 7 of the AI Migration Tool pipeline.  Runs after the Validation Agent
and is responsible for six tasks:

  1. **File Placement** — copy converted files from ``output/<feature>/`` into
     the correct locations within the actual ``target_root`` codebase, using the
     ``project_structure`` section of ``skillset-config.json`` to determine paths.

  2. **Dependency Sync** — scan generated code for new imports and add any
     missing third-party packages to ``target_root/requirements.txt`` (Python)
     or write them to ``target_root/package.json`` (JS/TS, opt-in).

  3. **Barrel File Updates** — after placing TypeScript/React files, update the
     nearest ``index.ts`` in the destination directory to export the new module.
     A new ``index.ts`` is created if one does not yet exist.

  4. **Python __init__.py Updates** — after placing ``.py`` files, update or
     create the ``__init__.py`` in the destination directory to import the new
     module.

  5. **tsconfig.json Path Aliases** (opt-in) — if new directories are created,
     add corresponding path aliases to ``compilerOptions.paths`` in the
     project's ``tsconfig.json``.

  6. **Type-specific Verification** — LLM-powered structural checks:
     - UI files (.tsx/.ts/.jsx/.scss): component structure, prop types, USWDS
       class usage, event handlers, business logic parity.
     - Backend files (.py): alignment with existing models/entities in
       ``target_root``, naming conventions, data layer consistency.

  7. **Migration Script Generation** — if the backend check flags
     ``needs_migration=True``, generate an Alembic revision (SQLAlchemy targets)
     or raw SQL ALTER TABLE script (psycopg2/hrsa targets).

  8. **Post-placement Build Command** — optionally run a shell command (e.g.
     ``npm run build`` or ``tsc --noEmit``) after all files are placed to verify
     the integration succeeded.  Configured via ``integration.post_placement_command``.

  9. **Playwright Test Stub Generation** (opt-in) — for each placed TSX/JSX
     component, generate a minimal ``tests/e2e/<ComponentName>.spec.ts`` stub
     that can be committed and extended by the team.  Activated via
     ``integration.generate_playwright_stubs: true``.

Conflict policy: if a file already exists in ``target_root`` with different
content the placement is skipped and the step is flagged for human review.
Identical content is silently skipped (idempotent re-runs).

New config keys (``integration:`` block in job YAML)
------------------------------------------------------
  update_package_json: false         # true = write missing JS packages to package.json
  update_barrel_files: true          # false = skip index.ts export updates
  update_python_inits: true          # false = skip __init__.py updates
  update_tsconfig_paths: false       # true = add path aliases to tsconfig.json (opt-in)
  post_placement_command: null       # shell command to run after placement (e.g. "npm run build")
  generate_playwright_stubs: false   # true = emit tests/e2e/<Component>.spec.ts stubs
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from agents.llm import LLMRouter

logger = logging.getLogger(__name__)

# Third-party packages that are commonly seen in generated code but are stdlib-
# look-alikes — always exclude from auto-dep detection.
_STDLIB_EXTRAS: frozenset[str] = frozenset({"__future__", "typing", "typing_extensions"})

# Suffixes that indicate UI/frontend files
_UI_SUFFIXES: frozenset[str] = frozenset({".tsx", ".jsx", ".scss", ".css"})

# Barrel file names to look for / create
_BARREL_NAMES: tuple[str, ...] = ("index.ts", "index.tsx")

# JS/TS keywords and primitive-type names that can appear as quoted string literals
# inside ordinary expressions (e.g. ``typeof x !== 'undefined'``) but are never
# package names.  Used by _sync_js_deps to filter false-positive import detections.
_JS_NON_PACKAGE_LITERALS: frozenset[str] = frozenset({
    "undefined", "null", "true", "false",
    "string", "number", "boolean", "object", "any",
    "void", "never", "unknown", "symbol", "bigint",
    "function", "class", "module", "global",
})

# Anchored import/require detector — only matches actual ES-module import
# statements and CommonJS require() / dynamic import() calls.
# Previous pattern  ['"]([@\w][\w-./]*)['"]  was too greedy and matched
# any quoted word, including  'undefined'  in  typeof x !== 'undefined'.
_IMPORT_PATTERN = re.compile(
    r"(?:"
    r"(?:^[ \t]*import\b[^'\"\n;]*?from\s*['\"])"   # import X from 'pkg'
    r"|(?:^[ \t]*import\s+['\"])"                    # import 'side-effect'
    r"|(?:\brequire\s*\(\s*['\"])"                   # require('pkg')
    r"|(?:(?:^|[(\s,;])import\s*\(\s*['\"])"         # import('dynamic')
    r")([@\w][\w\-./]*)['\"]",
    re.MULTILINE,
)


class IntegrationAgent:
    """
    Place converted files into ``target_root``, sync dependencies, update
    barrel files and __init__.py exports, verify structural correctness,
    and generate migration scripts as needed.
    """

    def __init__(
        self,
        approved_plan: dict,
        output_root: str | Path,
        target_root: str | Path | None,
        run_id: str,
        logs_dir: str | Path,
        config: dict,
        llm_router: "LLMRouter | None" = None,
        integration_config: dict | None = None,
        dry_run: bool = False,
    ) -> None:
        self.plan = approved_plan
        self.output_root = Path(output_root)
        self.target_root = Path(target_root) if target_root else None
        self.run_id = run_id
        self.logs_dir = Path(logs_dir)
        self.config = config
        self._router = llm_router
        self.integration_config = integration_config or {}
        self.dry_run = dry_run
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self._feature_name: str = approved_plan.get("feature_name", "")
        self._target_id: str = approved_plan.get("target", "")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def execute(
        self,
        completed_step_ids: list[str],
        all_steps: list[dict],
        validation_findings: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Run the full integration sequence.

        Parameters
        ----------
        completed_step_ids:
            Step IDs that were successfully converted in Stage 5.
        all_steps:
            Full list of plan steps (used for source/target file metadata).
        validation_findings:
            Findings from Stage 6 (used to skip FAIL steps from placement).

        Returns
        -------
        dict with keys: status, placements, dependency_updates, barrel_updates,
        init_updates, tsconfig_updates, migration_scripts, verification_findings,
        post_placement_result, report_json, report_md.
        """
        _skip_base = {
            "placements": [],
            "dependency_updates": {},
            "barrel_updates": [],
            "init_updates": [],
            "tsconfig_updates": [],
            "playwright_stubs": [],
            "migration_scripts": [],
            "verification_findings": [],
            "post_placement_result": {},
            "report_json": None,
            "report_md": None,
        }

        # Guard: disabled via job YAML
        if not self.integration_config.get("enabled", True):
            logger.info("[Integration] Stage disabled via integration.enabled=false.")
            return {"status": "skipped", **_skip_base}

        # Guard: no target_root configured
        if self.target_root is None:
            logger.warning(
                "[Integration] target_root not configured — skipping placement. "
                "Set pipeline.target_root in the job YAML or wizard-registry.json."
            )
            return {"status": "skipped_no_target", **_skip_base}

        if not self.target_root.exists():
            logger.warning(
                "[Integration] target_root does not exist on disk: %s — skipping.",
                self.target_root,
            )
            return {"status": "skipped_no_target", **_skip_base}

        # Dry-run: log intent, return early
        if self.dry_run:
            logger.info("[Integration] DRY-RUN: would integrate files into %s", self.target_root)
            return {"status": "skipped_dry_run", **_skip_base}

        steps_index = {s.get("id"): s for s in all_steps}
        val_failed: set[str] = {
            f["step"] for f in (validation_findings or [])
            if f.get("status") != "PASS"
        }

        placements: list[dict] = []
        verification_findings: list[dict] = []
        migration_scripts: list[dict] = []

        # ---- 1. Collect output files written in Stage 5 -----------------
        output_files = self._collect_output_files(completed_step_ids, steps_index)

        # ---- 2. Classify, place each file --------------------------------
        ui_placed: list[dict] = []
        backend_placed: list[dict] = []

        for file_entry in output_files:
            step_id = file_entry["step_id"]
            if step_id in val_failed:
                logger.warning("[Integration] Skipping %s — validation FAIL", step_id)
                placements.append({
                    "step_id": step_id,
                    "status": "skipped_validation_fail",
                    "src": str(file_entry["output_path"]),
                    "dst": None,
                })
                continue

            src = Path(file_entry["output_path"])
            if not src.exists():
                logger.warning("[Integration] Source file missing: %s", src)
                placements.append({
                    "step_id": step_id,
                    "status": "skipped_missing_src",
                    "src": str(src),
                    "dst": None,
                })
                continue

            file_type = self._classify_file(src)
            dst = self._resolve_placement_path(file_entry)
            placement = self._place_file(src, dst, step_id)
            placements.append(placement)

            if placement["status"] == "placed":
                if file_type == "ui":
                    ui_placed.append({**file_entry, "dst": str(dst), "type": "ui"})
                elif file_type == "backend":
                    backend_placed.append({**file_entry, "dst": str(dst), "type": "backend"})

        # ---- 3. Dependency sync ------------------------------------------
        dep_updates: dict[str, Any] = {}
        add_deps = self.integration_config.get("add_dependencies", True)

        if add_deps:
            py_files = [Path(e["dst"]) for e in backend_placed if e.get("dst")]
            ts_files = [Path(e["dst"]) for e in ui_placed if e.get("dst")
                        and Path(e["dst"]).suffix in {".ts", ".tsx"}]
            dep_updates["python"] = self._sync_python_deps(py_files)
            dep_updates["js"] = self._sync_js_deps(
                ts_files,
                update_pkg_json=self.integration_config.get("update_package_json", False),
            )

        # ---- 3b. Barrel file updates (TypeScript index.ts) ---------------
        barrel_updates: list[dict] = []
        if self.integration_config.get("update_barrel_files", True) and ui_placed:
            barrel_updates = self._update_barrel_files(ui_placed)

        # ---- 3c. Python __init__.py updates ------------------------------
        init_updates: list[dict] = []
        if self.integration_config.get("update_python_inits", True) and backend_placed:
            init_updates = self._update_python_inits(backend_placed)

        # ---- 3d. tsconfig.json path alias updates (opt-in) ---------------
        tsconfig_updates: list[dict] = []
        if self.integration_config.get("update_tsconfig_paths", False):
            all_placed = ui_placed + backend_placed
            tsconfig_updates = self._update_tsconfig_paths(all_placed)

        # ---- 3e. Playwright test stub generation (opt-in) ----------------
        playwright_stubs: list[dict] = []
        if self.integration_config.get("generate_playwright_stubs", False) and ui_placed:
            playwright_stubs = self._generate_playwright_stubs(ui_placed)

        # ---- 4. UI verification ------------------------------------------
        for entry in ui_placed:
            step = steps_index.get(entry["step_id"], {})
            src_path = Path(self.plan.get("feature_root", "")) / step.get("source_file", "")
            dst_path = Path(entry["dst"])
            source_code = src_path.read_text(encoding="utf-8", errors="replace") if src_path.exists() else ""
            target_code = dst_path.read_text(encoding="utf-8", errors="replace") if dst_path.exists() else ""
            finding = self._verify_ui_integrity(step, source_code, target_code)
            finding["step_id"] = entry["step_id"]
            finding["file"] = str(entry["dst"])
            finding["file_type"] = "ui"
            verification_findings.append(finding)

        # ---- 5. Backend verification -------------------------------------
        existing_models = self._read_existing_models()
        for entry in backend_placed:
            step = steps_index.get(entry["step_id"], {})
            dst_path = Path(entry["dst"])
            target_code = dst_path.read_text(encoding="utf-8", errors="replace") if dst_path.exists() else ""
            finding = self._verify_backend_structure(step, target_code, existing_models)
            finding["step_id"] = entry["step_id"]
            finding["file"] = str(entry["dst"])
            finding["file_type"] = "backend"
            verification_findings.append(finding)

        # ---- 6. Migration script generation -----------------------------
        gen_migration = self.integration_config.get("generate_migration", True)
        if gen_migration:
            steps_needing_migration = [
                f for f in verification_findings
                if f.get("needs_migration") and f.get("file_type") == "backend"
            ]
            if steps_needing_migration:
                migration_scripts = self._generate_migration_script(steps_needing_migration)

        # ---- 7. Compute overall status ----------------------------------
        has_conflict = any(p["status"] == "conflict" for p in placements)
        has_fail = any(
            f.get("status") == "FAIL" for f in verification_findings
        )
        status = "partial" if (has_conflict or has_fail) else "integrated"

        # ---- 8. Post-placement build command ----------------------------
        post_cmd_result: dict = {}
        post_cmd = (self.integration_config.get("post_placement_command") or "").strip()
        if post_cmd and status == "integrated":
            logger.info("[Integration] Running post-placement command: %s", post_cmd)
            post_cmd_result = self._run_post_placement_command(post_cmd)
            if post_cmd_result.get("returncode", 0) != 0:
                logger.warning(
                    "[Integration] Post-placement command failed (exit %d): %s",
                    post_cmd_result.get("returncode"),
                    post_cmd_result.get("stderr", "")[:300],
                )
                status = "partial"

        # ---- 9. Write report --------------------------------------------
        report = {
            "run_id": self.run_id,
            "status": status,
            "target_root": str(self.target_root),
            "placements": placements,
            "dependency_updates": dep_updates,
            "barrel_updates": barrel_updates,
            "init_updates": init_updates,
            "tsconfig_updates": tsconfig_updates,
            "playwright_stubs": playwright_stubs,
            "migration_scripts": migration_scripts,
            "verification_findings": verification_findings,
            "post_placement_result": post_cmd_result,
        }
        json_path, md_path = self._write_report(report)
        report["report_json"] = str(json_path)
        report["report_md"] = str(md_path)
        return report

    # ------------------------------------------------------------------
    # File collection
    # ------------------------------------------------------------------

    def _collect_output_files(
        self,
        completed_step_ids: list[str],
        steps_index: dict[str, dict],
    ) -> list[dict]:
        """
        Build the list of files to place by reading the conversion log and
        filtering to steps that completed successfully.
        """
        log_path = self.logs_dir / f"{self.run_id}-conversion-log.json"
        wrote: dict[str, dict] = {}  # step_id → most recent wrote_file entry

        if log_path.exists():
            try:
                log_data = json.loads(log_path.read_text(encoding="utf-8"))
                for entry in log_data.get("entries", []):
                    if entry.get("action") == "wrote_file" and entry.get("plan_step_ref"):
                        step_id = entry["plan_step_ref"]
                        wrote[step_id] = entry
            except Exception as exc:
                logger.warning("[Integration] Could not read conversion log: %s", exc)

        files: list[dict] = []
        for step_id in completed_step_ids:
            step = steps_index.get(step_id, {})
            target_rel = step.get("target_file", "")
            if not target_rel:
                continue
            output_path = self.output_root / target_rel
            files.append({
                "step_id": step_id,
                "target_rel": target_rel,
                "output_path": str(output_path),
            })

        return files

    # ------------------------------------------------------------------
    # File classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_file(path: Path) -> Literal["ui", "backend", "test", "config"]:
        suffix = path.suffix.lower()
        name = path.name.lower()

        if "test" in name or name.startswith("test_"):
            return "test"
        if suffix in _UI_SUFFIXES:
            return "ui"
        if suffix == ".ts" and not name.endswith(".d.ts"):
            return "ui"
        if suffix == ".py":
            return "backend"
        return "config"

    # ------------------------------------------------------------------
    # Placement path resolution
    # ------------------------------------------------------------------

    def _resolve_placement_path(self, file_entry: dict) -> Path:
        """
        Map an output-relative path to a target_root-relative destination.

        Tries ``project_structure`` templates first; falls back to mirroring
        the output directory structure verbatim.
        """
        target_rel = file_entry["target_rel"]
        feature_name = self._feature_name

        # Try project_structure config
        struct_key = (
            f"project_structure_{self._target_id}"
            if f"project_structure_{self._target_id}" in self.config
            else "project_structure"
        )
        struct = self.config.get(struct_key, {})

        # Walk frontend/backend sub-dicts for a matching root template
        for section in struct.values():
            if not isinstance(section, dict):
                continue
            for template in section.values():
                if not isinstance(template, str):
                    continue
                # Expand {feature_name}
                try:
                    prefix = template.format(feature_name=feature_name)
                except (KeyError, ValueError):
                    continue
                # If the target_rel already starts with this prefix, use as-is
                if target_rel.startswith(prefix):
                    return self.target_root / target_rel

        # Fallback: mirror output structure
        return self.target_root / target_rel

    # ------------------------------------------------------------------
    # File placement
    # ------------------------------------------------------------------

    def _place_file(self, src: Path, dst: Path, step_id: str) -> dict:
        """
        Copy ``src`` to ``dst``.  Returns a placement record with status:
        ``placed``, ``skipped_identical``, or ``conflict``.
        """
        base = {"step_id": step_id, "src": str(src), "dst": str(dst)}

        if dst.exists():
            src_hash = hashlib.sha256(src.read_bytes()).hexdigest()
            dst_hash = hashlib.sha256(dst.read_bytes()).hexdigest()
            if src_hash == dst_hash:
                logger.debug("[Integration] Identical, skip: %s", dst)
                return {**base, "status": "skipped_identical"}
            # Different content → conflict: skip + warn
            logger.warning(
                "[Integration] CONFLICT — %s already exists with different content. "
                "Resolve manually and re-run.",
                dst,
            )
            return {**base, "status": "conflict"}

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            logger.info("[Integration] Placed: %s → %s", src.name, dst)
            return {**base, "status": "placed"}
        except OSError as exc:
            logger.error("[Integration] Failed to copy %s → %s: %s", src, dst, exc)
            return {**base, "status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Dependency sync — Python
    # ------------------------------------------------------------------

    def _sync_python_deps(self, py_files: list[Path]) -> dict:
        """
        Parse top-level imports from generated ``.py`` files and append any
        missing third-party packages to ``target_root/requirements.txt``.
        """
        result: dict[str, list] = {"added": [], "already_present": [], "skipped_stdlib": []}
        req_path = self.target_root / "requirements.txt"

        if not py_files:
            return result

        # Build set of packages already in requirements.txt
        existing: set[str] = set()
        if req_path.exists():
            for line in req_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    pkg = re.split(r"[>=<!~\[]", line)[0].strip().lower()
                    if pkg:
                        existing.add(pkg)

        # Extract imports from generated files
        import_pattern = re.compile(
            r"^\s*(?:import\s+([\w]+)|from\s+([\w]+)\s+import)", re.MULTILINE
        )
        stdlib_names: frozenset[str] = frozenset(
            getattr(sys, "stdlib_module_names", set())
        ) | _STDLIB_EXTRAS

        to_add: list[str] = []
        for py_file in py_files:
            if not py_file.exists():
                continue
            code = py_file.read_text(encoding="utf-8", errors="replace")
            for match in import_pattern.finditer(code):
                pkg = (match.group(1) or match.group(2) or "").lower()
                if not pkg or pkg in stdlib_names:
                    result["skipped_stdlib"].append(pkg)
                    continue
                if pkg in existing:
                    if pkg not in result["already_present"]:
                        result["already_present"].append(pkg)
                    continue
                if pkg not in to_add:
                    to_add.append(pkg)

        if to_add:
            lines = [f"\n# added by ai-migration-tool ({self.run_id})"]
            lines += [f"{pkg}" for pkg in sorted(to_add)]
            addition = "\n".join(lines) + "\n"
            if req_path.exists():
                req_path.write_text(
                    req_path.read_text(encoding="utf-8") + addition,
                    encoding="utf-8",
                )
            else:
                req_path.write_text(addition.lstrip(), encoding="utf-8")
            result["added"] = sorted(to_add)
            logger.info("[Integration] Added %d Python dep(s): %s", len(to_add), to_add)

        return result

    # ------------------------------------------------------------------
    # Dependency sync — JavaScript / TypeScript
    # ------------------------------------------------------------------

    def _sync_js_deps(self, ts_files: list[Path], *, update_pkg_json: bool = False) -> dict:
        """
        Parse ``import … from 'pkg'`` statements from generated TS/TSX files.

        When ``update_pkg_json=False`` (default): reports packages that need
        installing but does NOT modify ``package.json``.

        When ``update_pkg_json=True`` (``integration.update_package_json: true``
        in job YAML): writes missing packages to the ``dependencies`` section of
        ``target_root/package.json`` with a placeholder version of ``"*"``.
        ``npm install`` still needs to be run afterward to actually fetch them.
        """
        result: dict[str, Any] = {
            "added_to_package_json": [],
            "needs_install": [],
            "already_present": [],
        }
        pkg_json_path = self.target_root / "package.json"

        if not ts_files:
            return result

        existing: set[str] = set()
        pkg_data: dict = {}
        if pkg_json_path.exists():
            try:
                pkg_data = json.loads(pkg_json_path.read_text(encoding="utf-8"))
                existing = (
                    set(pkg_data.get("dependencies", {}).keys())
                    | set(pkg_data.get("devDependencies", {}).keys())
                )
            except Exception:
                pass

        third_party_pattern = re.compile(r"^[^./]")  # must not start with . or /

        missing: list[str] = []
        for ts_file in ts_files:
            if not ts_file.exists():
                continue
            code = ts_file.read_text(encoding="utf-8", errors="replace")
            for match in _IMPORT_PATTERN.finditer(code):
                pkg_raw = match.group(1)
                # Normalise scoped packages: @org/pkg → @org/pkg
                parts = pkg_raw.split("/")
                pkg = "/".join(parts[:2]) if pkg_raw.startswith("@") else parts[0]
                if not third_party_pattern.match(pkg):
                    continue
                # Skip JS keywords / TypeScript primitive names that appear as
                # string literals in expressions (e.g. typeof x !== 'undefined')
                if pkg in _JS_NON_PACKAGE_LITERALS:
                    continue
                if pkg in existing:
                    if pkg not in result["already_present"]:
                        result["already_present"].append(pkg)
                else:
                    if pkg not in missing:
                        missing.append(pkg)

        if not missing:
            return result

        if update_pkg_json and pkg_json_path.exists():
            # Write missing packages to package.json dependencies with version "*"
            # (placeholder — real version pinning should be done manually / by npm install)
            deps = pkg_data.setdefault("dependencies", {})
            for pkg in missing:
                if pkg not in deps and pkg not in pkg_data.get("devDependencies", {}):
                    deps[pkg] = "*"
            try:
                pkg_json_path.write_text(
                    json.dumps(pkg_data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                result["added_to_package_json"] = sorted(missing)
                logger.info(
                    "[Integration] Added %d JS package(s) to package.json (version='*'): %s",
                    len(missing),
                    missing,
                )
                logger.info(
                    "[Integration] Run `npm install` in %s to install the added packages.",
                    self.target_root,
                )
            except OSError as exc:
                logger.warning("[Integration] Could not update package.json: %s", exc)
                result["needs_install"] = sorted(missing)
        else:
            result["needs_install"] = sorted(missing)
            if missing:
                logger.warning(
                    "[Integration] %d JS package(s) may need installing: %s",
                    len(missing),
                    missing,
                )
                if not update_pkg_json:
                    logger.info(
                        "[Integration] Set integration.update_package_json: true in the job "
                        "YAML to have these written to package.json automatically."
                    )

        return result

    # ------------------------------------------------------------------
    # Barrel file updates (TypeScript index.ts)
    # ------------------------------------------------------------------

    def _update_barrel_files(self, ui_placed: list[dict]) -> list[dict]:
        """
        After placing TypeScript/React files, update the nearest ``index.ts``
        (or ``index.tsx``) in each destination directory to export the new
        module.  Creates a new ``index.ts`` if one does not already exist.

        Export strategy:
        - If the placed file contains ``export default``, emit
          ``export { default as <Name> } from './<Name>';``
        - Otherwise emit ``export * from './<Name>';``

        Returns a list of update records:
          {"barrel": str, "module": str, "status": "appended"|"created"|"skipped"|"error"}
        """
        updates: list[dict] = []
        # Track which barrel files we've already processed (one update record per barrel)
        barrel_exports: dict[Path, list[str]] = {}

        for entry in ui_placed:
            dst = Path(entry["dst"])
            suffix = dst.suffix.lower()
            if suffix not in {".ts", ".tsx", ".jsx"}:
                continue
            if dst.stem.lower() in {"index", "index.tsx"}:
                continue  # skip barrel files themselves

            barrel_dir = dst.parent
            module_name = dst.stem  # filename without extension

            # Determine export style by scanning the placed file
            file_content = ""
            try:
                file_content = dst.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
            has_default_export = bool(re.search(r"\bexport\s+default\b", file_content))

            if has_default_export:
                export_line = f"export {{ default as {module_name} }} from './{module_name}';"
            else:
                export_line = f"export * from './{module_name}';"

            barrel_exports.setdefault(barrel_dir, []).append(export_line)

        for barrel_dir, export_lines in barrel_exports.items():
            # Find existing barrel file
            barrel_path: Path | None = None
            for name in _BARREL_NAMES:
                candidate = barrel_dir / name
                if candidate.exists():
                    barrel_path = candidate
                    break

            if barrel_path is None:
                # Create a new index.ts
                barrel_path = barrel_dir / "index.ts"

            try:
                existing_content = ""
                if barrel_path.exists():
                    existing_content = barrel_path.read_text(encoding="utf-8", errors="replace")

                lines_to_add = [
                    line for line in export_lines
                    if line not in existing_content
                ]

                if not lines_to_add:
                    for line in export_lines:
                        updates.append({
                            "barrel": str(barrel_path),
                            "module": line,
                            "status": "skipped_already_present",
                        })
                    continue

                header = f"\n// added by ai-migration-tool ({self.run_id})\n"
                addition = header + "\n".join(lines_to_add) + "\n"

                if barrel_path.exists():
                    barrel_path.write_text(
                        existing_content + addition,
                        encoding="utf-8",
                    )
                    action = "appended"
                else:
                    barrel_path.parent.mkdir(parents=True, exist_ok=True)
                    barrel_path.write_text(addition.lstrip(), encoding="utf-8")
                    action = "created"

                logger.info(
                    "[Integration] %s barrel file %s (+%d export(s))",
                    action.capitalize(),
                    barrel_path,
                    len(lines_to_add),
                )
                for line in lines_to_add:
                    updates.append({
                        "barrel": str(barrel_path),
                        "module": line,
                        "status": action,
                    })

            except OSError as exc:
                logger.warning("[Integration] Could not update barrel file %s: %s", barrel_path, exc)
                for line in export_lines:
                    updates.append({
                        "barrel": str(barrel_path),
                        "module": line,
                        "status": "error",
                        "error": str(exc),
                    })

        return updates

    # ------------------------------------------------------------------
    # Python __init__.py updates
    # ------------------------------------------------------------------

    def _update_python_inits(self, backend_placed: list[dict]) -> list[dict]:
        """
        After placing ``.py`` files, update or create ``__init__.py`` in each
        destination directory so the new module is importable from the package.

        Appends ``from . import <module_name>`` for each new module, skipping
        files that are already referenced in the existing ``__init__.py``.

        Returns a list of update records:
          {"init": str, "module": str, "status": "appended"|"created"|"skipped"|"error"}
        """
        updates: list[dict] = []
        # Group modules by their destination directory
        dir_modules: dict[Path, list[str]] = {}

        for entry in backend_placed:
            dst = Path(entry["dst"])
            if dst.suffix.lower() != ".py":
                continue
            if dst.stem in {"__init__", "conftest", "setup"}:
                continue  # skip special files

            module_name = dst.stem
            dir_modules.setdefault(dst.parent, []).append(module_name)

        for pkg_dir, modules in dir_modules.items():
            init_path = pkg_dir / "__init__.py"
            try:
                existing_content = ""
                if init_path.exists():
                    existing_content = init_path.read_text(encoding="utf-8", errors="replace")

                modules_to_add = [
                    m for m in modules
                    if f"import {m}" not in existing_content
                    and f"from . import {m}" not in existing_content
                ]

                if not modules_to_add:
                    for m in modules:
                        updates.append({
                            "init": str(init_path),
                            "module": m,
                            "status": "skipped_already_present",
                        })
                    continue

                import_lines = [f"from . import {m}" for m in sorted(modules_to_add)]
                header = f"\n# added by ai-migration-tool ({self.run_id})\n"
                addition = header + "\n".join(import_lines) + "\n"

                if init_path.exists():
                    init_path.write_text(
                        existing_content + addition,
                        encoding="utf-8",
                    )
                    action = "appended"
                else:
                    init_path.parent.mkdir(parents=True, exist_ok=True)
                    init_path.write_text(addition.lstrip(), encoding="utf-8")
                    action = "created"

                logger.info(
                    "[Integration] %s __init__.py at %s (+%d module(s)): %s",
                    action.capitalize(),
                    init_path,
                    len(modules_to_add),
                    modules_to_add,
                )
                for m in modules_to_add:
                    updates.append({
                        "init": str(init_path),
                        "module": m,
                        "status": action,
                    })

            except OSError as exc:
                logger.warning("[Integration] Could not update __init__.py at %s: %s", pkg_dir, exc)
                for m in modules:
                    updates.append({
                        "init": str(init_path),
                        "module": m,
                        "status": "error",
                        "error": str(exc),
                    })

        return updates

    # ------------------------------------------------------------------
    # tsconfig.json path alias updates (opt-in)
    # ------------------------------------------------------------------

    def _update_tsconfig_paths(self, all_placed: list[dict]) -> list[dict]:
        """
        Add path aliases to ``compilerOptions.paths`` in ``tsconfig.json``
        for any new directories created during placement.

        Alias pattern: ``@<dir_name>/*`` → ``["./<rel_path>/*"]``

        Only adds aliases for directories that:
        1. Were newly created as direct children of ``src/`` (or equivalent).
        2. Do not already have an alias in ``tsconfig.json``.

        Returns a list of update records:
          {"tsconfig": str, "alias": str, "path": str, "status": "added"|"skipped"|"error"}
        """
        updates: list[dict] = []

        # Find tsconfig.json: prefer target_root/tsconfig.json, fallback to src/
        tsconfig_candidates = [
            self.target_root / "tsconfig.json",
            self.target_root / "tsconfig.base.json",
        ]
        tsconfig_path: Path | None = None
        for c in tsconfig_candidates:
            if c.exists():
                tsconfig_path = c
                break

        if tsconfig_path is None:
            logger.debug("[Integration] No tsconfig.json found in target_root — skipping path alias update.")
            return updates

        # Load tsconfig, stripping line comments (JSONC format)
        try:
            raw = tsconfig_path.read_text(encoding="utf-8", errors="replace")
            # Strip single-line // comments (simple approach — doesn't handle all edge cases)
            stripped = re.sub(r"//[^\n]*", "", raw)
            tsconfig_data: dict = json.loads(stripped)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[Integration] Could not parse %s: %s", tsconfig_path, exc)
            updates.append({
                "tsconfig": str(tsconfig_path),
                "alias": "",
                "path": "",
                "status": "error",
                "error": str(exc),
            })
            return updates

        compiler_opts = tsconfig_data.setdefault("compilerOptions", {})
        existing_paths: dict[str, list] = compiler_opts.setdefault("paths", {})

        # Collect newly created directories from placements
        new_dirs: set[Path] = set()
        for entry in all_placed:
            dst = Path(entry["dst"])
            try:
                rel = dst.parent.relative_to(self.target_root)
            except ValueError:
                continue
            parts = rel.parts
            # Only create aliases for directories directly under src/ (or root)
            if len(parts) >= 2 and parts[0] in {"src", "app", "components", "lib"}:
                top_level_dir = self.target_root / parts[0] / parts[1]
                new_dirs.add(top_level_dir)

        modified = False
        for new_dir in sorted(new_dirs):
            dir_name = new_dir.name
            alias_key = f"@{dir_name}/*"
            if alias_key in existing_paths:
                updates.append({
                    "tsconfig": str(tsconfig_path),
                    "alias": alias_key,
                    "path": "",
                    "status": "skipped_already_present",
                })
                continue

            try:
                rel_dir = new_dir.relative_to(self.target_root)
            except ValueError:
                continue
            alias_value = f"./{rel_dir.as_posix()}/*"
            existing_paths[alias_key] = [alias_value]
            modified = True
            updates.append({
                "tsconfig": str(tsconfig_path),
                "alias": alias_key,
                "path": alias_value,
                "status": "added",
            })
            logger.info(
                "[Integration] Added tsconfig path alias: %s → %s",
                alias_key,
                alias_value,
            )

        if modified:
            try:
                tsconfig_path.write_text(
                    json.dumps(tsconfig_data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                logger.info("[Integration] Updated %s with %d new path alias(es).", tsconfig_path, modified)
            except OSError as exc:
                logger.warning("[Integration] Could not write %s: %s", tsconfig_path, exc)

        return updates

    # ------------------------------------------------------------------
    # Post-placement build command
    # ------------------------------------------------------------------

    def _run_post_placement_command(self, command: str) -> dict:
        """
        Run a shell command in ``target_root`` after all files are placed.

        This is intended for commands like ``npm run build``, ``tsc --noEmit``,
        or ``python -m pytest`` that verify the integration succeeded.

        The command is executed in a subprocess with a timeout of 300 seconds.
        stdout and stderr are captured and included in the result dict.

        Returns a dict with keys: command, cwd, returncode, stdout, stderr, error.
        """
        cwd = str(self.target_root) if self.target_root else None
        result: dict[str, Any] = {
            "command": command,
            "cwd": cwd,
            "returncode": None,
            "stdout": "",
            "stderr": "",
        }
        try:
            cmd_parts = shlex.split(command)
        except ValueError:
            cmd_parts = command.split()

        timeout = self.integration_config.get("post_placement_timeout", 300)

        try:
            proc = subprocess.run(
                cmd_parts,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env={**os.environ},
            )
            result["returncode"] = proc.returncode
            result["stdout"] = proc.stdout.strip()
            result["stderr"] = proc.stderr.strip()

            if proc.returncode == 0:
                logger.info(
                    "[Integration] Post-placement command succeeded (exit 0): %s",
                    command,
                )
            else:
                logger.warning(
                    "[Integration] Post-placement command failed (exit %d): %s",
                    proc.returncode,
                    command,
                )

        except subprocess.TimeoutExpired:
            result["returncode"] = -1
            result["error"] = f"Command timed out after {timeout}s"
            logger.warning("[Integration] Post-placement command timed out: %s", command)
        except OSError as exc:
            result["returncode"] = -1
            result["error"] = str(exc)
            logger.warning("[Integration] Post-placement command OS error: %s", exc)

        return result

    # ------------------------------------------------------------------
    # UI integrity verification
    # ------------------------------------------------------------------

    def _verify_ui_integrity(
        self, step: dict, source_code: str, target_code: str
    ) -> dict[str, Any]:
        """
        LLM-based check on React/Next.js component structure, prop types,
        USWDS class usage, and business logic parity.
        """
        if self._router is None or not self._router.is_available:
            return {
                "status": "PASS",
                "issues": [],
                "needs_migration": False,
                "confidence": 0.4,
                "reason": "LLM unavailable; UI sanity check skipped.",
            }

        from agents.llm import LLMMessage
        from agents.llm.base import LLMNotAvailableError, LLMProviderError

        system = self._resolve_integration_prompt()
        user = (
            f"ROLE: UI_INTEGRITY\n"
            f"Step: {step.get('id', '?')}\n"
            f"Source file: {step.get('source_file', '?')}\n"
            f"Target file: {step.get('target_file', '?')}\n\n"
            f"ORIGINAL SOURCE:\n```\n{source_code[:6000]}\n```\n\n"
            f"CONVERTED TARGET:\n```\n{target_code[:6000]}\n```\n"
        )

        try:
            response = self._router.complete(
                system=system,
                messages=[LLMMessage(role="user", content=user)],
            )
            return self._parse_integration_json(response.text, step.get("id", "?"))
        except (LLMNotAvailableError, LLMProviderError, ValueError) as exc:
            logger.warning("[Integration] UI check fallback for %s: %s", step.get("id"), exc)
            return {
                "status": "PASS",
                "issues": [],
                "needs_migration": False,
                "confidence": 0.35,
                "reason": f"UI check simulation unavailable ({exc}).",
            }

    # ------------------------------------------------------------------
    # Backend structure verification
    # ------------------------------------------------------------------

    def _read_existing_models(self) -> str:
        """
        Read existing model/entity files from target_root for LLM context.
        Returns concatenated content (truncated to 8 000 chars).
        """
        if self.target_root is None:
            return ""
        patterns = ["**/model*.py", "**/entity*.py", "**/models/*.py", "**/entities/*.py"]
        snippets: list[str] = []
        seen: set[Path] = set()
        for pattern in patterns:
            for p in self.target_root.rglob(pattern.lstrip("**/")):
                if p in seen:
                    continue
                seen.add(p)
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")
                    snippets.append(f"# {p.name}\n{content[:2000]}")
                    if sum(len(s) for s in snippets) > 8000:
                        break
                except OSError:
                    pass
        return "\n\n".join(snippets)

    def _verify_backend_structure(
        self, step: dict, target_code: str, existing_models: str
    ) -> dict[str, Any]:
        """
        LLM-based check that the generated backend code aligns with existing
        models/entities.  Sets ``needs_migration=True`` if new DB columns or
        tables are detected.
        """
        if self._router is None or not self._router.is_available:
            return {
                "status": "PASS",
                "issues": [],
                "needs_migration": False,
                "confidence": 0.4,
                "reason": "LLM unavailable; backend structure check skipped.",
            }

        from agents.llm import LLMMessage
        from agents.llm.base import LLMNotAvailableError, LLMProviderError

        system = self._resolve_integration_prompt()
        user = (
            f"ROLE: BACKEND_STRUCTURE\n"
            f"Step: {step.get('id', '?')}\n"
            f"Target file: {step.get('target_file', '?')}\n\n"
            f"CONVERTED CODE:\n```python\n{target_code[:6000]}\n```\n\n"
            f"EXISTING MODELS IN TARGET CODEBASE:\n```python\n{existing_models[:4000]}\n```\n"
        )

        try:
            response = self._router.complete(
                system=system,
                messages=[LLMMessage(role="user", content=user)],
            )
            return self._parse_integration_json(response.text, step.get("id", "?"))
        except (LLMNotAvailableError, LLMProviderError, ValueError) as exc:
            logger.warning("[Integration] Backend check fallback for %s: %s", step.get("id"), exc)
            return {
                "status": "PASS",
                "issues": [],
                "needs_migration": False,
                "confidence": 0.35,
                "reason": f"Backend check simulation unavailable ({exc}).",
            }

    # ------------------------------------------------------------------
    # Migration script generation
    # ------------------------------------------------------------------

    def _generate_migration_script(
        self, steps_needing_migration: list[dict]
    ) -> list[dict]:
        """
        Generate a DB migration script for each step that flagged
        ``needs_migration=True``.  Uses LLM; falls back gracefully.
        """
        results: list[dict] = []

        if self._router is None or not self._router.is_available:
            logger.warning(
                "[Integration] Migration script generation skipped — no LLM available."
            )
            return results

        from agents.llm import LLMMessage
        from agents.llm.base import LLMNotAvailableError, LLMProviderError

        system = self._resolve_integration_prompt()

        for finding in steps_needing_migration:
            step_id = finding.get("step_id", "unknown")
            target_file = finding.get("file", "")
            target_code = ""
            if target_file and Path(target_file).exists():
                target_code = Path(target_file).read_text(encoding="utf-8", errors="replace")

            user = (
                f"ROLE: MIGRATION_SCRIPT\n"
                f"Step: {step_id}\n"
                f"Modified file: {target_file}\n\n"
                f"CONVERTED CODE:\n```python\n{target_code[:6000]}\n```\n\n"
                f"Issues reported:\n{json.dumps(finding.get('issues', []), indent=2)}\n"
            )

            try:
                response = self._router.complete(
                    system=system,
                    messages=[LLMMessage(role="user", content=user)],
                )
                script_content = self._extract_script(response.text)
                ext = ".py" if "alembic" in script_content.lower() or "revision" in script_content.lower() else ".sql"
                script_path = self.logs_dir / f"{self.run_id}-migration-{step_id}{ext}"
                script_path.write_text(script_content, encoding="utf-8")
                results.append({"step_id": step_id, "script_path": str(script_path)})
                logger.info("[Integration] Migration script written: %s", script_path)
            except (LLMNotAvailableError, LLMProviderError) as exc:
                logger.warning("[Integration] Migration script generation failed for %s: %s", step_id, exc)

        return results

    # ------------------------------------------------------------------
    # Prompt helpers
    # ------------------------------------------------------------------

    def _resolve_integration_prompt(self) -> str:
        """Load the integration system prompt for the current target."""
        from prompts import resolve_prompt_filename, load_prompt
        filename = resolve_prompt_filename(
            self._target_id, "integration_system", "integration_system.txt"
        )
        return load_prompt(filename)

    # ------------------------------------------------------------------
    # JSON parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_integration_json(raw_text: str, step_id: str) -> dict[str, Any]:
        """Parse LLM JSON response for integration checks."""
        text = raw_text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract first {...} block
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                raise ValueError(f"[{step_id}] LLM returned non-JSON: {text[:200]}")

        status = str(data.get("status", "PASS")).upper()
        if status not in {"PASS", "WARN", "FAIL"}:
            status = "PASS"

        return {
            "status": status,
            "issues": data.get("issues", []),
            "needs_migration": bool(data.get("needs_migration", False)),
            "confidence": float(data.get("confidence", 0.0)),
            "reason": data.get("reason", ""),
            "script": data.get("script"),
        }

    @staticmethod
    def _extract_script(raw_text: str) -> str:
        """Extract SQL or Python script from LLM response, stripping code fences."""
        text = raw_text.strip()
        match = re.search(r"```(?:sql|python)?\s*(.*?)\s*```", text, re.DOTALL)
        return match.group(1).strip() if match else text

    # ------------------------------------------------------------------
    # Playwright test stub generation
    # ------------------------------------------------------------------

    def _generate_playwright_stubs(self, ui_placed: list[dict]) -> list[dict]:
        """
        Generate minimal Playwright E2E test stubs for each placed TSX/JSX
        component.  Stubs are written to ``target_root/tests/e2e/<Name>.spec.ts``.

        Returns a list of records with keys: component, stub, status.
        """
        results: list[dict] = []
        for entry in ui_placed:
            dst = Path(entry.get("dst", ""))
            if dst.suffix not in {".tsx", ".jsx"}:
                continue

            component_name = dst.stem
            # Route guess: PascalCase → kebab-case  (e.g. ActionHistory → /action-history)
            route_guess = (
                "/" + re.sub(r"([A-Z])", r"-\1", component_name).lstrip("-").lower()
            )
            stub_rel = f"tests/e2e/{component_name}.spec.ts"
            stub_path = self.target_root / stub_rel

            if stub_path.exists():
                logger.debug("[Integration] Playwright stub already exists: %s", stub_path)
                results.append({
                    "component": component_name,
                    "stub": str(stub_path),
                    "status": "skipped_exists",
                })
                continue

            try:
                stub_path.parent.mkdir(parents=True, exist_ok=True)
                stub_path.write_text(
                    self._render_playwright_stub(component_name, route_guess),
                    encoding="utf-8",
                )
                logger.info("[Integration] Playwright stub created: %s", stub_path)
                results.append({
                    "component": component_name,
                    "stub": str(stub_path),
                    "status": "created",
                })
            except OSError as exc:
                logger.warning("[Integration] Could not write Playwright stub %s: %s", stub_path, exc)
                results.append({
                    "component": component_name,
                    "stub": str(stub_path),
                    "status": "error",
                    "error": str(exc),
                })

        return results

    @staticmethod
    def _render_playwright_stub(component_name: str, route_guess: str) -> str:
        """Return the content of a minimal Playwright spec file."""
        return (
            'import { test, expect } from "@playwright/test";\n'
            "\n"
            "// Auto-generated by ai-migration-tool\n"
            "// Fill in the correct route, selectors, and assertions.\n"
            f'test.describe("{component_name}", () => {{\n'
            '  test("renders without crashing", async ({ page }) => {\n'
            f'    await page.goto("{route_guess}");\n'
            "    await expect(page).toHaveTitle(/.+/);\n"
            "  });\n"
            "\n"
            '  test("primary user flow", async ({ page }) => {\n'
            f'    await page.goto("{route_guess}");\n'
            f"    // TODO: add assertions for {component_name} behaviour\n"
            "  });\n"
            "});\n"
        )

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _write_report(self, report: dict) -> tuple[Path, Path]:
        json_path = self.logs_dir / f"{self.run_id}-integration-report.json"
        md_path = self.logs_dir / f"{self.run_id}-integration-report.md"
        json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        md_path.write_text(self._render_markdown(report), encoding="utf-8")
        return json_path, md_path

    @staticmethod
    def _render_markdown(report: dict) -> str:
        status = report.get("status", "?").upper()
        placements = report.get("placements", [])
        placed = [p for p in placements if p.get("status") == "placed"]
        conflicts = [p for p in placements if p.get("status") == "conflict"]
        dep = report.get("dependency_updates", {})
        py_added = dep.get("python", {}).get("added", [])
        js_pkg_added = dep.get("js", {}).get("added_to_package_json", [])
        js_needs = dep.get("js", {}).get("needs_install", [])
        barrel_updates = report.get("barrel_updates", [])
        barrel_changed = [u for u in barrel_updates if u.get("status") in {"appended", "created"}]
        init_updates = report.get("init_updates", [])
        init_changed = [u for u in init_updates if u.get("status") in {"appended", "created"}]
        tsconfig_updates = report.get("tsconfig_updates", [])
        tsconfig_added = [u for u in tsconfig_updates if u.get("status") == "added"]
        playwright_stubs = report.get("playwright_stubs", [])
        playwright_created = [s for s in playwright_stubs if s.get("status") == "created"]
        migrations = report.get("migration_scripts", [])
        findings = report.get("verification_findings", [])
        fails = [f for f in findings if f.get("status") == "FAIL"]
        warns = [f for f in findings if f.get("status") == "WARN"]
        post_cmd = report.get("post_placement_result", {})

        lines = [
            f"# Integration Report — {report['run_id']}",
            "",
            f"- **Status:** {status}",
            f"- **Target root:** `{report.get('target_root', '?')}`",
            f"- **Files placed:** {len(placed)} / {len(placements)}",
            f"- **Conflicts:** {len(conflicts)}",
            f"- **Python deps added:** {len(py_added)}",
            f"- **JS packages written to package.json:** {len(js_pkg_added)}",
            f"- **JS packages needing manual install:** {len(js_needs)}",
            f"- **Barrel file exports added:** {len(barrel_changed)}",
            f"- **Python __init__.py imports added:** {len(init_changed)}",
            f"- **tsconfig.json path aliases added:** {len(tsconfig_added)}",
            f"- **Playwright stubs generated:** {len(playwright_created)}",
            f"- **Migration scripts:** {len(migrations)}",
            f"- **Verification FAIL:** {len(fails)}  WARN: {len(warns)}",
            "",
        ]

        if placed:
            lines += ["## Files Placed", ""]
            for p in placed:
                lines.append(f"- `{p.get('dst', '?')}`")
            lines.append("")

        if conflicts:
            lines += ["## Conflicts (manual review required)", ""]
            for p in conflicts:
                lines.append(f"- `{p.get('dst', '?')}` — existing file has different content")
            lines.append("")

        if py_added:
            lines += ["## Python Dependencies Added to requirements.txt", ""]
            for pkg in py_added:
                lines.append(f"- `{pkg}`")
            lines.append("")

        if js_pkg_added:
            lines += ["## JS Packages Written to package.json", ""]
            lines.append("These packages were added with version `\"*\"`. Run `npm install` to fetch them.")
            lines.append("")
            for pkg in js_pkg_added:
                lines.append(f"- `{pkg}`")
            lines.append("")

        if js_needs:
            lines += ["## JS Packages Needing Manual Install", ""]
            lines.append(f"Run: `npm install {' '.join(js_needs)}`")
            lines.append("")
            lines.append("Or set `integration.update_package_json: true` to write these to package.json automatically.")
            lines.append("")

        if barrel_changed:
            lines += ["## Barrel File (index.ts) Updates", ""]
            for u in barrel_changed:
                action = "Created" if u.get("status") == "created" else "Appended to"
                lines.append(f"- {action} `{u.get('barrel', '?')}`: `{u.get('module', '?')}`")
            lines.append("")

        if init_changed:
            lines += ["## Python __init__.py Updates", ""]
            for u in init_changed:
                action = "Created" if u.get("status") == "created" else "Appended to"
                lines.append(f"- {action} `{u.get('init', '?')}`: `from . import {u.get('module', '?')}`")
            lines.append("")

        if tsconfig_added:
            lines += ["## tsconfig.json Path Aliases Added", ""]
            for u in tsconfig_added:
                lines.append(f"- `{u.get('alias', '?')}` → `{u.get('path', '?')}`")
            lines.append("")

        if playwright_created:
            lines += ["## Playwright Test Stubs Generated", ""]
            lines.append(
                "Run `npx playwright test` (or set `verification.tool: playwright`) to execute these stubs."
            )
            lines.append("")
            for s in playwright_created:
                lines.append(f"- `{s.get('stub', '?')}` (`{s.get('component', '?')}`)")
            lines.append("")

        if migrations:
            lines += ["## Migration Scripts", ""]
            for m in migrations:
                lines.append(f"- `{m.get('script_path', '?')}` (step `{m.get('step_id', '?')}`)")
            lines.append("")

        if post_cmd:
            rc = post_cmd.get("returncode")
            icon = "✓" if rc == 0 else "✗"
            lines += [f"## Post-placement Command ({icon} exit {rc})", ""]
            lines.append(f"```\n{post_cmd.get('command', '')}\n```")
            if post_cmd.get("stdout"):
                lines += ["", "**stdout:**", "```", post_cmd["stdout"][:1000], "```"]
            if post_cmd.get("stderr"):
                lines += ["", "**stderr:**", "```", post_cmd["stderr"][:1000], "```"]
            if post_cmd.get("error"):
                lines += ["", f"**Error:** {post_cmd['error']}"]
            lines.append("")

        if findings:
            lines += ["## Verification Findings", ""]
            for f in findings:
                icon = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}.get(f.get("status", "?"), "?")
                lines.append(
                    f"- {icon} `{f.get('step_id', '?')}` [{f.get('status', '?')}]: "
                    f"{f.get('reason', '')} (confidence={f.get('confidence', 0.0):.2f})"
                )
                for issue in f.get("issues", []):
                    lines.append(f"  - {issue}")
            lines.append("")

        return "\n".join(lines) + "\n"

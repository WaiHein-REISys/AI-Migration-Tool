"""
wizard.collector — Interactive Q&A helpers
==========================================
Collects Source and Target codebase information from the user via
interactive prompts (or from a pre-filled dict when running
non-interactively / agent-mode).

Public functions
----------------
  collect_answers(prefill)                    Full wizard Q&A flow → answers dict
  collect_source_info(prefill)                Source-only Q&A
  collect_target_info(prefill)                Target-only Q&A
  collect_feature_selection(source_root, …)   Pick a feature folder from source
  detect_feature_folders(source_root, …)      Scan source for feature directories
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from wizard.detector import CodebaseInspector

# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

def _safe_input(prompt: str, default: str = "") -> str:
    """Print *prompt*, optionally showing *default*, and return stripped input."""
    display = f"  {prompt}"
    if default:
        display += f" [{default}]"
    display += ": "
    try:
        raw = input(display).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return raw if raw else default


def _safe_print(text: str) -> None:
    """Print text replacing un-encodable chars (Windows CP1252 safety)."""
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def _yes_no(prompt: str, default: bool = True) -> bool:
    """Ask a yes/no question and return the boolean answer."""
    default_str = "Y/n" if default else "y/N"
    raw = _safe_input(f"{prompt} ({default_str})").lower()
    if not raw:
        return default
    return raw.startswith("y")


def _section(title: str) -> None:
    print(f"\n  [{title}]")


# ---------------------------------------------------------------------------
# Feature folder detection
# ---------------------------------------------------------------------------

# Directories that are never feature folders (build artefacts, tooling, assets)
_EXCLUDED_DIRS: frozenset[str] = frozenset({
    # Package managers / build outputs
    "node_modules", "dist", "build", "out", ".next", ".nuxt",
    # Version control
    ".git", ".svn", ".hg",
    # IDE / editor
    ".vscode", ".idea",
    # Python
    "__pycache__", ".venv", "venv", "env", ".eggs",
    # .NET build artefacts
    "bin", "obj", "packages",
    # Test / coverage
    "coverage", "e2e", ".nyc_output",
    # Static / design assets (not application logic)
    "assets", "fonts", "images", "img", "icons",
    # Common infrastructure / non-feature folders
    "styles", "environments", "i18n", "locale", "locales",
    "migrations", "fixtures", "static", "media",
    "Properties", "Connected Services",
})

# File suffixes that immediately identify a directory as an Angular feature folder
_ANGULAR_FEATURE_MARKERS: frozenset[str] = frozenset({
    ".component.ts",
    ".component.html",
    ".component.spec.ts",
    ".module.ts",
    ".service.ts",
    ".directive.ts",
    ".guard.ts",
    ".pipe.ts",
})

# Generic source-code extensions (used as fallback: ≥ 2 such files → feature folder)
_SOURCE_EXTENSIONS: frozenset[str] = frozenset({
    ".ts", ".tsx", ".js", ".jsx",
    ".cs", ".py", ".java", ".go", ".rb",
    ".vue", ".svelte",
})


def _is_feature_dir(path: Path) -> bool:
    """Return True if *path* looks like a self-contained feature/component directory."""
    try:
        files = [f.name for f in path.iterdir() if f.is_file()]
    except (PermissionError, OSError):
        return False

    # Angular/framework marker: any file ends with a framework-specific suffix
    for fname in files:
        for marker in _ANGULAR_FEATURE_MARKERS:
            if fname.endswith(marker):
                return True

    # Generic fallback: at least 2 source-code files in the same directory
    src_count = sum(
        1 for f in files if Path(f).suffix.lower() in _SOURCE_EXTENSIONS
    )
    return src_count >= 2


def detect_feature_folders(
    source_root: "str | Path",
    max_depth: int = 6,
) -> list[Path]:
    """
    Recursively scan *source_root* and return directories that look like
    feature folders — i.e. directories that contain Angular component/service
    markers or at least two generic source-code files.

    Directories listed in ``_EXCLUDED_DIRS`` and hidden directories (names
    starting with ``.``) are skipped.  The scan stops recursing *into* a
    directory once that directory is identified as a feature folder, so
    nested sub-components are not returned as independent features.

    Parameters
    ----------
    source_root : str | Path
        Absolute path to the legacy source codebase root.
    max_depth : int
        Maximum traversal depth relative to *source_root*. Default 6.

    Returns
    -------
    list[Path]
        Detected feature folder paths, sorted alphabetically by folder name.
    """
    root = Path(source_root)
    if not root.is_dir():
        return []

    found: list[Path] = []

    def _scan(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            subdirs = [
                e for e in directory.iterdir()
                if e.is_dir()
                and e.name not in _EXCLUDED_DIRS
                and not e.name.startswith(".")
            ]
        except (PermissionError, OSError):
            return

        for sub in subdirs:
            if _is_feature_dir(sub):
                found.append(sub)
                # Don't recurse inside a feature folder — its sub-directories
                # are internal implementation files, not independent features.
            else:
                _scan(sub, depth + 1)

    _scan(root, 0)
    return sorted(found, key=lambda p: p.name.lower())


def collect_feature_selection(
    source_root: "str | Path",
    prefill: str | None = None,
) -> tuple[str, str]:
    """
    Scan *source_root* for feature folders, present a numbered list to the
    user, and return the chosen folder as ``(feature_path, feature_name)``.

    The user may type a number to pick from the list, or type (or paste) any
    absolute or source-root-relative path to override the auto-detected list.
    If no feature folders are found automatically the user is prompted to
    enter a path directly.

    Parameters
    ----------
    source_root : str | Path
        Root of the legacy source codebase.
    prefill : str | None
        Pre-selected feature path shown as the default prompt value.

    Returns
    -------
    tuple[str, str]
        ``(feature_path, feature_name)`` where *feature_path* is the
        absolute path string and *feature_name* is the folder's base name.
    """
    root = Path(source_root)
    _safe_print(f"\n  Scanning for feature folders under: {root}")
    detected = detect_feature_folders(root)

    if detected:
        _safe_print(f"  Found {len(detected)} feature folder(s):\n")
        for i, d in enumerate(detected, start=1):
            try:
                rel = d.relative_to(root)
            except ValueError:
                rel = d
            _safe_print(f"    {i:>3}.  {rel}")
        _safe_print("\n         (or type a custom path to override)")
        print()
    else:
        _safe_print("  No feature folders detected automatically.")
        print()

    while True:
        if detected:
            prompt = f"Select feature [1-{len(detected)}] or type a path"
        else:
            prompt = "Path to feature folder (absolute, or relative to source root)"

        raw = _safe_input(prompt, prefill or "")

        # ---- Numeric selection from detected list ----
        if raw.isdigit() and detected:
            idx = int(raw)
            if 1 <= idx <= len(detected):
                chosen = detected[idx - 1]
                _safe_print(f"  Selected: {chosen}")
                return str(chosen), chosen.name
            _safe_print(f"  (Choose 1–{len(detected)}, or type a full path)")
            continue

        # ---- Custom path (absolute or relative to source_root) ----
        if raw:
            p = Path(raw)
            if not p.is_absolute():
                p = root / raw
            if p.is_dir():
                _safe_print(f"  Selected: {p}")
                return str(p), p.name
            _safe_print(f"  (Path not found: {p})")
            continue

        # Empty input with no prefill → keep prompting
        _safe_print("  (Enter a number from the list, or paste a folder path)")


# ---------------------------------------------------------------------------
# Source codebase Q&A
# ---------------------------------------------------------------------------

def collect_source_info(prefill: dict | None = None) -> dict:
    """
    Interactively collect (or load from prefill) source codebase details.

    Returns a dict with keys:
        name, root, framework, backend_framework, language, database,
        component_patterns, service_patterns, inspection
    """
    pf = prefill or {}

    _section("Source Codebase (Legacy / Origin)")
    print("  This is the OLD codebase you want to migrate features FROM.")
    print()

    name = _safe_input(
        "Short identifier for this source (e.g. gprs, legacy, myapp)",
        pf.get("name", "source"),
    )
    root = _safe_input(
        "Absolute path to the source codebase root directory",
        pf.get("root", ""),
    )

    detection: dict = {}
    if root and Path(root).exists():
        print(f"\n  Analysing source codebase at: {root}")
        try:
            detection = CodebaseInspector(root).inspect()
            _safe_print(
                f"  Detected:  Language={detection['primary_language']}  "
                f"Frontend={detection['frontend_framework']}  "
                f"Backend={detection['backend_framework']}"
            )
        except Exception as exc:
            print(f"  (Analysis failed: {exc} — enter values manually)")

    framework = _safe_input(
        "Frontend framework (e.g. Angular 2, React, Vue.js)",
        pf.get("framework", detection.get("frontend_framework", "")),
    )
    backend_fw = _safe_input(
        "Backend framework (e.g. ASP.NET Core, Flask, Django, Spring Boot)",
        pf.get("backend_framework", detection.get("backend_framework", "")),
    )
    language = _safe_input(
        "Primary language (e.g. TypeScript, C#, Python, Java)",
        pf.get("language", detection.get("primary_language", "")),
    )
    database = _safe_input(
        "Database / ORM (e.g. SQL Server + EF Core, PostgreSQL + psycopg2)",
        pf.get("database", detection.get("database_access", "")),
    )

    return {
        "name":               name,
        "root":               root,
        "framework":          framework,
        "backend_framework":  backend_fw,
        "language":           language,
        "database":           database,
        "component_patterns": detection.get("component_patterns", []),
        "service_patterns":   detection.get("service_patterns", []),
        "inspection":         detection,
    }


# ---------------------------------------------------------------------------
# Target codebase Q&A
# ---------------------------------------------------------------------------

def collect_target_info(prefill: dict | None = None) -> dict:
    """
    Interactively collect (or load from prefill) target codebase details.

    Returns a dict with keys:
        name, root, framework, backend_framework, language, database,
        frontend_root, backend_root, frontend_details, backend_details,
        database_details, component_patterns, service_patterns, inspection
    """
    pf = prefill or {}

    _section("Target Codebase (Modern / Destination)")
    print("  This is the NEW codebase where migrated features will live.")
    print()

    name = _safe_input(
        "Short identifier for this target (e.g. nextjs_flask, my_v2, modern)",
        pf.get("name", "target"),
    )
    root = _safe_input(
        "Absolute path to the target codebase root (leave blank if not yet created)",
        pf.get("root", ""),
    )

    detection: dict = {}
    if root and Path(root).exists():
        print(f"\n  Analysing target codebase at: {root}")
        try:
            detection = CodebaseInspector(root).inspect()
            _safe_print(
                f"  Detected:  Language={detection['primary_language']}  "
                f"Frontend={detection['frontend_framework']}  "
                f"Backend={detection['backend_framework']}"
            )
        except Exception as exc:
            print(f"  (Analysis failed: {exc} — enter values manually)")

    framework = _safe_input(
        "Frontend framework (e.g. Next.js 15, React 18, Vue 3)",
        pf.get("framework", detection.get("frontend_framework", "")),
    )
    backend_fw = _safe_input(
        "Backend framework (e.g. Flask 3.0, FastAPI, Django, Express.js)",
        pf.get("backend_framework", detection.get("backend_framework", "")),
    )
    language = _safe_input(
        "Primary language (e.g. TypeScript, Python, JavaScript, Go)",
        pf.get("language", detection.get("primary_language", "")),
    )
    database = _safe_input(
        "Database / ORM (e.g. PostgreSQL + psycopg2, MySQL + SQLAlchemy, Prisma)",
        pf.get("database", detection.get("database_access", "")),
    )

    # ---- Frontend structure ----
    _section("Target — Frontend Structure")

    default_fe_root = (
        detection.get("sample_sub_folders", ["frontend/src/"])[0]
        if detection.get("sample_sub_folders") else "frontend/src/"
    )
    fe_root = _safe_input(
        "Frontend source root (relative to target root, e.g. frontend/src/ or src/)",
        pf.get("frontend_root", default_fe_root),
    )
    component_dir = _safe_input(
        "Component folder (relative to fe root, e.g. components/ or app/components/pages/)",
        pf.get("component_dir", "components/"),
    )
    services_dir = _safe_input(
        "Services / API callers folder (relative to fe root, e.g. services/ or lib/api/)",
        pf.get("services_dir", "services/"),
    )
    uses_css_modules = _yes_no(
        "Does the target use CSS Modules (*.module.css)?",
        "CSS Modules" in str(detection.get("component_patterns", [])),
    )
    uses_barrel = _yes_no(
        "Does each component have a barrel index.ts export?",
        "index.ts" in str(detection.get("component_patterns", [])),
    )

    # ---- Backend structure ----
    _section("Target — Backend Structure")

    be_root = _safe_input(
        "Backend source root (relative to target root, e.g. backend/ or api/)",
        pf.get("backend_root", "backend/"),
    )
    routes_dir = _safe_input(
        "Routes folder (relative to backend root, e.g. routes/)",
        pf.get("routes_dir", "routes/"),
    )
    services_be_dir = _safe_input(
        "Services folder (relative to backend root, e.g. services/)",
        pf.get("services_be_dir", "services/"),
    )
    has_repositories = _yes_no(
        "Does the backend use a separate repository layer?",
        "repository" in str(detection.get("service_patterns", [])).lower(),
    )
    repo_dir = ""
    if has_repositories:
        repo_dir = _safe_input(
            "Repositories folder (relative to backend root, e.g. repositories/)",
            pf.get("repo_dir", "repositories/"),
        )

    # ---- Database ----
    _section("Target — Database Details")

    db_pattern = _safe_input(
        "Database access pattern (e.g. 'psycopg2 raw SQL', 'SQLAlchemy ORM', 'Prisma')",
        pf.get("db_pattern", ""),
    )
    db_migration = _safe_input(
        "Migration tool (e.g. Alembic, Flyway, Prisma migrations, custom scripts, none)",
        pf.get("db_migration", ""),
    )

    # ---- Assemble ----
    fe_details: dict[str, str] = {
        "components_dir": component_dir,
        "services_dir":   services_dir,
        "component_structure": (
            "ComponentName/ with ComponentName.tsx + ComponentName.module.css + index.ts"
            if uses_css_modules and uses_barrel
            else "ComponentName.tsx in component folder"
        ),
        "test_suffix": ".test.tsx",
    }
    if uses_css_modules:
        fe_details["styling"] = "CSS Modules (*.module.css)"
    if uses_barrel:
        fe_details["barrel_export"] = "index.ts per component folder"

    be_details: dict[str, str] = {
        "routes_dir":           routes_dir,
        "services_dir":         services_be_dir,
        "route_file_pattern":   "{feature_name}_routes.py",
        "service_file_pattern": "{feature_name}_service.py",
    }
    if has_repositories:
        be_details["repositories_dir"]             = repo_dir
        be_details["repository_file_pattern"]      = "{feature_name}_repository.py"
        be_details["architecture"]                 = "Routes -> Services -> Repositories (3-layer)"

    db_details: dict[str, str] = {}
    if db_pattern:
        db_details["access_pattern"] = db_pattern
    if db_migration:
        db_details["migration_tool"] = db_migration

    return {
        "name":               name,
        "root":               root,
        "framework":          framework,
        "backend_framework":  backend_fw,
        "language":           language,
        "database":           database,
        "frontend_root":      fe_root,
        "backend_root":       be_root,
        "frontend_details":   fe_details,
        "backend_details":    be_details,
        "database_details":   db_details,
        "component_patterns": detection.get("component_patterns", []),
        "service_patterns":   detection.get("service_patterns", []),
        "inspection":         detection,
    }


# ---------------------------------------------------------------------------
# Full Q&A flow
# ---------------------------------------------------------------------------

def collect_answers(prefill: dict | None = None) -> dict:
    """
    Run the complete interactive Q&A and return an *answers* dict:
        { source, target, target_id, created_at }

    If *prefill* is provided, its values are used as defaults (interactive)
    or as the full answer set (non-interactive, when all keys are present).
    """
    pf = prefill or {}

    print()
    print("  This wizard configures a new Source -> Target migration pair.")
    print("  It will analyse your codebases and generate:")
    print("    - Optimised LLM prompts for your specific stack pair")
    print("    - Config entries in skillset-config.json")
    print("    - A ready-to-use job template in agent-prompts/")
    print()
    print("  Press Ctrl+C at any time to cancel.")
    print()

    source = collect_source_info(pf.get("source"))
    print()
    target = collect_target_info(pf.get("target"))
    print()

    # Derive a safe snake_case identifier
    raw_id    = pf.get("target_id") or target["name"]
    target_id = re.sub(r"[^\w]", "_", raw_id.lower()).strip("_")
    suggested = _safe_input(
        "Identifier for this target in config/prompts (snake_case)",
        target_id,
    )
    target_id = re.sub(r"[^\w]", "_", suggested.lower()).strip("_")

    return {
        "source":     source,
        "target":     target,
        "target_id":  target_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

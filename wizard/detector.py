"""
wizard.detector — Codebase Heuristic Inspector
===============================================
Walks a directory tree and detects the framework, language, folder
structure, component patterns, and naming conventions without requiring
any external dependencies.

Usage
-----
    from wizard.detector import CodebaseInspector

    info = CodebaseInspector("Y:/Solution/my-app").inspect()
    print(info["frontend_framework"])   # e.g. "Angular 2"
    print(info["backend_framework"])    # e.g. "ASP.NET Core MVC"
    print(info["component_patterns"])   # e.g. ["Angular @Component decorator"]
"""

import re
from collections import Counter
from fnmatch import fnmatch
from pathlib import Path

# ---------------------------------------------------------------------------
# Signature tables — (glob-style filename pattern, human-readable label)
# ---------------------------------------------------------------------------

FRONTEND_SIGNATURES: list[tuple[str, str]] = [
    ("*.component.ts",   "Angular 2"),
    ("*.component.tsx",  "React / Next.js"),
    ("*.vue",            "Vue.js"),
    ("*.svelte",         "Svelte"),
    ("nuxt.config.*",    "Nuxt.js"),
    ("next.config.*",    "Next.js"),
    ("angular.json",     "Angular"),
    ("vite.config.*",    "Vite / React"),
    ("remix.config.*",   "Remix"),
]

BACKEND_SIGNATURES: list[tuple[str, str]] = [
    ("*.controller.cs",  "ASP.NET Core MVC"),
    ("*Controller.cs",   "ASP.NET Core MVC"),
    ("Startup.cs",       "ASP.NET Core"),
    ("Program.cs",       "ASP.NET Core"),
    ("app.py",           "Python Flask"),
    ("manage.py",        "Django"),
    ("settings.py",      "Django"),
    ("wsgi.py",          "WSGI / Flask / Django"),
    ("asgi.py",          "ASGI / FastAPI / Django"),
    ("main.py",          "FastAPI / Python"),
    ("routes.py",        "Flask Blueprints"),
    ("*.go",             "Go"),
    ("*.java",           "Java / Spring Boot"),
    ("pom.xml",          "Java Maven"),
    ("build.gradle",     "Java Gradle"),
    ("*.rb",             "Ruby on Rails"),
    ("Gemfile",          "Ruby"),
]

DATABASE_SIGNATURES: list[tuple[str, str]] = [
    ("*.edmx",           "Entity Framework (EDMX)"),
    ("*DbContext.cs",    "Entity Framework Core"),
    ("alembic.ini",      "Alembic / SQLAlchemy"),
    ("*.migration.ts",   "TypeORM"),
    ("schema.prisma",    "Prisma"),
    ("init_db.py",       "Custom SQL scripts"),
    ("*.sql",            "Raw SQL"),
    ("schema.rb",        "Rails ActiveRecord"),
]


class CodebaseInspector:
    """
    Heuristically analyses a codebase directory.

    Parameters
    ----------
    root : str | Path
        Absolute (or relative) path to the codebase root.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def inspect(self) -> dict:
        """
        Return a dictionary describing the detected codebase characteristics.

        Keys
        ----
        root, file_count, top_extensions, primary_language,
        frontend_framework, backend_framework, database_access,
        top_level_folders, sample_sub_folders,
        component_patterns, service_patterns, naming_conventions
        """
        if not self.root.exists():
            raise FileNotFoundError(f"Codebase root not found: {self.root}")

        all_files = [f for f in self.root.rglob("*") if f.is_file()]
        rel_strs  = [
            str(f.relative_to(self.root)).replace("\\", "/")
            for f in all_files
        ]
        ext_counts = Counter(
            Path(f).suffix.lower() for f in rel_strs if "." in f
        )

        return {
            "root":               str(self.root),
            "file_count":         len(rel_strs),
            "top_extensions":     [e for e, _ in ext_counts.most_common(10)],
            "primary_language":   self._detect_language(ext_counts),
            "frontend_framework": self._detect_fw(rel_strs, FRONTEND_SIGNATURES) or "Unknown",
            "backend_framework":  self._detect_fw(rel_strs, BACKEND_SIGNATURES)  or "Unknown",
            "database_access":    self._detect_fw(rel_strs, DATABASE_SIGNATURES) or "Unknown",
            "top_level_folders":  sorted({
                str(Path(f).parts[0]) for f in rel_strs if Path(f).parts
            }),
            "sample_sub_folders": sorted({
                "/".join(Path(f).parts[:2])
                for f in rel_strs if len(Path(f).parts) > 1
            })[:20],
            "component_patterns": self._detect_component_patterns(rel_strs),
            "service_patterns":   self._detect_service_patterns(rel_strs),
            "naming_conventions": self._detect_naming(rel_strs, ext_counts),
        }

    # ------------------------------------------------------------------
    # Internal detection helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_fw(
        files: list[str],
        signatures: list[tuple[str, str]],
    ) -> str | None:
        for pattern, label in signatures:
            bare = pattern.lstrip("*/")
            if any(fnmatch(Path(f).name, bare) or fnmatch(f, pattern) for f in files):
                return label
        return None

    @staticmethod
    def _detect_language(ext_counts: Counter) -> str:
        priority = [
            ({".ts", ".tsx"}, "TypeScript"),
            ({".cs"},          "C#"),
            ({".py"},          "Python"),
            ({".java"},        "Java"),
            ({".go"},          "Go"),
            ({".rb"},          "Ruby"),
            ({".js", ".jsx"},  "JavaScript"),
        ]
        for exts, lang in priority:
            if any(ext_counts.get(e, 0) > 0 for e in exts):
                return lang
        return "Unknown"

    @staticmethod
    def _detect_component_patterns(files: list[str]) -> list[str]:
        patterns = []
        if any(f.endswith(".component.ts") for f in files):
            patterns.append("Angular @Component decorator (*.component.ts)")
        if any(f.endswith(".tsx") for f in files):
            patterns.append("React functional components (*.tsx)")
        if any("module.css" in f for f in files):
            patterns.append("CSS Modules (*.module.css)")
        if any(f.endswith(".module.ts") for f in files):
            patterns.append("Angular NgModule (*.module.ts)")
        if any(f.endswith("index.ts") for f in files):
            patterns.append("Barrel index.ts exports")
        return patterns

    @staticmethod
    def _detect_service_patterns(files: list[str]) -> list[str]:
        patterns = []
        if any(f.endswith(".service.ts") for f in files):
            patterns.append("Angular @Injectable() services (*.service.ts)")
        if any("_service.py" in f for f in files):
            patterns.append("Python service modules (*_service.py)")
        if any("_repository.py" in f for f in files):
            patterns.append("Repository pattern (*_repository.py)")
        if any("_routes.py" in f for f in files):
            patterns.append("Flask Blueprint routes (*_routes.py)")
        if any("fetcher" in f.lower() or "api.ts" in f.lower() for f in files):
            patterns.append("Typed async fetchers (api.ts / fetcher functions)")
        return patterns

    @staticmethod
    def _detect_naming(files: list[str], ext_counts: Counter) -> dict:
        tsx_stems = [Path(f).stem for f in files if f.endswith(".tsx")]
        py_stems  = [Path(f).stem for f in files if f.endswith(".py")]
        cs_stems  = [Path(f).stem for f in files if f.endswith(".cs")]

        def _style(names: list[str]) -> str:
            sample = [n for n in names[:8] if n and n != "__init__"][:5]
            if not sample:
                return "N/A"
            if all(re.match(r"[A-Z][a-zA-Z0-9]*$", n) for n in sample):
                return "PascalCase"
            if all(re.match(r"[a-z][a-zA-Z0-9]*$", n) for n in sample):
                return "camelCase"
            if all(re.match(r"[a-z][a-z0-9_]*$", n) for n in sample):
                return "snake_case"
            if all(re.match(r"[a-z][a-z0-9-]*$", n) for n in sample):
                return "kebab-case"
            return "mixed"

        return {
            "tsx_components": _style(tsx_stems),
            "python_modules":  _style(py_stems),
            "csharp_classes":  _style(cs_stems),
        }

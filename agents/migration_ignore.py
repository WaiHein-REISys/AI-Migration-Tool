"""
agents/migration_ignore — .migrationignore file loader and path filter
=======================================================================

Reads a ``.migrationignore`` file (gitignore-style) and exposes a
``should_skip(path, root)`` helper used by ScopingAgent and ConversionAgent
to exclude paths that must never be scanned, planned, or converted.

Pattern semantics (gitignore subset)
--------------------------------------
* Blank lines and lines starting with ``#`` are silently ignored.
* Lines starting with ``!`` negate a previous match (allow-list override).
* Lines ending with ``/`` are **directory patterns** — they match a directory
  *and all files/subdirectories inside it*.
* A pattern **without** a ``/`` (after stripping a trailing ``/``) is matched
  against every individual path segment (directory name or filename).
* A pattern **containing** a ``/`` (other than a trailing one) is matched
  against the full POSIX-style relative path.
* ``*`` matches any characters except ``/``.
* ``**`` is expanded to match across path separators (any depth).
* Matching is case-insensitive on Windows, case-sensitive on all other
  platforms (mirrors filesystem behaviour).

Usage
-----
::

    from agents.migration_ignore import MigrationIgnore

    mi = MigrationIgnore()                  # reads <repo_root>/.migrationignore
    mi = MigrationIgnore(Path("my.ignore")) # alternate path

    # Filter a file list
    clean = [f for f in files if not mi.should_skip(f, root=feature_root)]

    # Single path check
    if mi.should_skip(path, root=feature_root):
        logger.info("RULE-011 skip: %s", path)
"""

from __future__ import annotations

import fnmatch
import logging
import os
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Repo root — file lives in agents/, so parent.parent is the repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent


class MigrationIgnore:
    """
    Gitignore-style path filter backed by ``.migrationignore``.

    Parameters
    ----------
    ignore_file : Path | None
        Path to the ignore file.  Defaults to ``<repo_root>/.migrationignore``.
        If the file does not exist no patterns are loaded and ``should_skip``
        always returns ``False`` (safe default — nothing is skipped).
    """

    DEFAULT_PATH: Path = _REPO_ROOT / ".migrationignore"

    # Internal pattern tuple: (negate, pattern_str, dir_only)
    #   negate    — True when the line started with "!"
    #   pattern   — the cleaned glob pattern string
    #   dir_only  — True when the original line ended with "/"
    _Pattern = tuple[bool, str, bool]

    def __init__(self, ignore_file: Path | None = None) -> None:
        self._patterns: list[MigrationIgnore._Pattern] = []
        path = ignore_file or self.DEFAULT_PATH
        if path.exists():
            self._load(path)
            logger.debug(
                "MigrationIgnore: loaded %d pattern(s) from %s",
                len(self._patterns),
                path,
            )
        else:
            logger.debug(
                "MigrationIgnore: ignore file not found at %s — no paths will be filtered.",
                path,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_skip(self, path: Path, root: Path | None = None) -> bool:
        """
        Return ``True`` if *path* matches any active ignore pattern.

        Parameters
        ----------
        path : Path
            Absolute (or relative) path to test.
        root : Path | None
            When provided the relative path from *root* is used for matching
            (recommended — gives the most accurate pattern behaviour).
        """
        if not self._patterns:
            return False

        # Build the relative POSIX path string and parts tuple
        if root is not None:
            try:
                rel = path.relative_to(root)
            except ValueError:
                rel = path
        else:
            rel = path

        rel_posix = rel.as_posix()          # e.g. "node_modules/lodash/index.js"
        parts     = rel.parts               # e.g. ("node_modules", "lodash", "index.js")
        basename  = parts[-1] if parts else path.name
        is_dir    = path.is_dir()

        matched = False

        for negate, pattern, dir_only in self._patterns:
            hit = self._matches(
                pattern=pattern,
                dir_only=dir_only,
                rel_posix=rel_posix,
                parts=parts,
                basename=basename,
                is_dir=is_dir,
            )
            if hit:
                matched = not negate        # "!" flips the accumulated match

        return matched

    def iter_patterns(self) -> Iterator[tuple[bool, str, bool]]:
        """Yield ``(negate, pattern, dir_only)`` tuples for introspection."""
        yield from self._patterns

    def __len__(self) -> int:
        return len(self._patterns)

    def __repr__(self) -> str:
        return f"MigrationIgnore(patterns={len(self._patterns)})"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self, path: Path) -> None:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()

            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue

            negate = line.startswith("!")
            if negate:
                line = line[1:].strip()

            dir_only = line.endswith("/")
            if dir_only:
                line = line.rstrip("/")

            if not line:
                continue

            self._patterns.append((negate, line, dir_only))

    @staticmethod
    def _matches(
        pattern: str,
        dir_only: bool,
        rel_posix: str,
        parts: tuple[str, ...],
        basename: str,
        is_dir: bool,
    ) -> bool:
        """
        Return True if the pattern fires for this path.

        Three matching strategies, applied in priority order:

        1. **``**`` patterns** — expanded to a ``fnmatch`` that spans separators.
        2. **Path patterns** (contain ``/``) — matched against the full relative
           POSIX path.
        3. **Segment patterns** (no ``/``) — matched against every individual
           path component (directory name or filename).

        For ``dir_only`` patterns, the match is checked against every *parent*
        directory component so that all files inside a matched directory are
        also excluded.
        """
        # Case sensitivity: Windows is case-insensitive, everything else is not.
        if os.name == "nt":
            pattern = pattern.lower()

        def _fnmatch(name: str, pat: str) -> bool:
            if os.name == "nt":
                name = name.lower()
            # Expand ** to a multi-segment wildcard
            if "**" in pat:
                import re as _re
                regex = _re.escape(pat).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
                return bool(_re.fullmatch(regex, name))
            return fnmatch.fnmatch(name, pat)

        if dir_only:
            # Match against every directory component (catches files inside ignored dirs)
            # e.g. pattern "node_modules", file "node_modules/lodash/index.js"
            # → parts[0] == "node_modules" → hit
            for i, part in enumerate(parts):
                # Check this segment
                if _fnmatch(part, pattern):
                    return True
                # Also check the cumulative path up to this segment
                cumulative = "/".join(parts[:i + 1])
                if _fnmatch(cumulative, pattern):
                    return True
            return False

        if "/" in pattern:
            # Full-path pattern: match against the whole relative POSIX path
            return _fnmatch(rel_posix, pattern) or _fnmatch("/" + rel_posix, pattern)

        # Segment pattern: match against any single component (dir name or filename)
        for part in parts:
            if _fnmatch(part, pattern):
                return True
        return False

"""
agents.job_config_populator
===========================
Auto-populate ``target_root`` and ``verification.commands`` in a job
``argparse.Namespace`` (produced by ``_job_to_args``) **before** the
pipeline starts.

Rules
-----
1. If ``ns.target_root`` is null / a placeholder string, look it up from
   ``config/wizard-registry.json`` by ``ns.target`` (the target ID).
2. If ``ns.verification_config["cwd"]`` is null, default it to
   ``ns.target_root`` (when available).
3. If ``ns.verification_config["commands"]`` is empty, auto-detect
   commands by inspecting the target codebase root:
   - ``package.json``      → Node.js / Next.js commands
   - ``pyproject.toml`` /
     ``setup.py`` /
     ``requirements.txt``  → Python / pytest commands
   - ``Makefile``          → Makefile targets
4. If commands were auto-detected and ``verification.enabled`` is still
   ``False``, flip it to ``True`` so the pipeline actually runs them.

This module is intentionally **non-fatal**: if anything goes wrong (e.g.
registry not found, codebase not reachable) it logs a warning and returns
without modifying ``ns``, so the pipeline can still proceed.

No new pip dependencies — uses only stdlib.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel values treated as "not set"
# ---------------------------------------------------------------------------

_UNSET_SENTINELS: frozenset[str] = frozenset({
    "",
    "null",
    "none",
    "<your_target_root>",
    "<your_target_root_a>",
    "<your_target_root_b>",
    "<target_root>",
    "<your_source_root>",
})


def _is_unset(value: object) -> bool:
    """Return True if *value* is None, empty, or a placeholder sentinel."""
    if value is None:
        return True
    return str(value).strip().lower() in _UNSET_SENTINELS


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def auto_populate_job_config(ns: object) -> None:
    """
    Mutate *ns* in-place to fill in ``target_root`` and
    ``verification_config`` when they are absent / placeholder.

    Parameters
    ----------
    ns : argparse.Namespace
        Job namespace produced by ``_job_to_args()``.  Modified in-place.
        Original values are NEVER overwritten — this is strictly additive.

    Returns
    -------
    None
        All errors are caught and logged; the function never raises.
    """
    try:
        _run(ns)
    except Exception as exc:  # pragma: no cover
        logger.warning(
            "auto_populate_job_config: unexpected error (non-fatal): %s", exc,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Internal orchestrator
# ---------------------------------------------------------------------------

def _run(ns: object) -> None:
    target_id = getattr(ns, "target", None) or ""

    # ── 1. Resolve target_root from wizard registry ─────────────────────────
    if _is_unset(getattr(ns, "target_root", None)):
        _fill_target_root_from_registry(ns, target_id)

    target_root: str | None = getattr(ns, "target_root", None)
    if _is_unset(target_root):
        target_root = None  # normalise

    # ── 2. Default verification.cwd to target_root ──────────────────────────
    vcfg: dict = getattr(ns, "verification_config", {})
    if isinstance(vcfg, dict):
        if _is_unset(vcfg.get("cwd")) and not _is_unset(target_root):
            vcfg["cwd"] = target_root
            logger.debug(
                "auto_populate: verification.cwd defaulted to target_root=%s",
                target_root,
            )

    # ── 3. Auto-detect verification commands ────────────────────────────────
    if isinstance(vcfg, dict) and not vcfg.get("commands"):
        search_root = (
            Path(vcfg.get("cwd"))
            if not _is_unset(vcfg.get("cwd"))
            else (Path(target_root) if not _is_unset(target_root) else None)
        )
        if search_root and search_root.exists():
            commands = detect_verification_commands(search_root)
            if commands:
                vcfg["commands"] = commands
                logger.info(
                    "auto_populate: detected %d verification command(s) for %s",
                    len(commands),
                    search_root,
                )

    # ── 4. Auto-enable verification if commands found ───────────────────────
    if isinstance(vcfg, dict):
        if vcfg.get("commands") and not vcfg.get("enabled", False):
            vcfg["enabled"] = True
            logger.info(
                "auto_populate: verification.enabled set to true "
                "(commands were auto-detected)",
            )

    # Write back (in case vcfg was replaced via dict mutation)
    ns.verification_config = vcfg  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Registry lookup
# ---------------------------------------------------------------------------

def _fill_target_root_from_registry(ns: object, target_id: str) -> None:
    """
    Set ``ns.target_root`` from ``config/wizard-registry.json`` if the
    entry for *target_id* has a ``target_root`` value.
    """
    if not target_id:
        return

    # Registry path: <repo_root>/config/wizard-registry.json
    # __file__ is agents/job_config_populator.py → parent is agents/ → parent is repo root
    registry_path = Path(__file__).parent.parent / "config" / "wizard-registry.json"

    if not registry_path.exists():
        logger.debug(
            "auto_populate: wizard-registry.json not found at %s — skipping target_root lookup",
            registry_path,
        )
        return

    try:
        registry: dict = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("auto_populate: could not read wizard-registry.json: %s", exc)
        return

    entry: dict = registry.get("targets", {}).get(target_id, {})
    stored_root: str | None = entry.get("target_root")

    if not _is_unset(stored_root):
        ns.target_root = stored_root  # type: ignore[attr-defined]
        logger.info(
            "auto_populate: target_root resolved from registry: %s → %s",
            target_id,
            stored_root,
        )
    else:
        logger.debug(
            "auto_populate: registry entry for '%s' has no target_root — skipping",
            target_id,
        )


# ---------------------------------------------------------------------------
# Verification command detection
# ---------------------------------------------------------------------------

def detect_verification_commands(target_root: "Path | str") -> list[str]:
    """
    Inspect *target_root* and return a list of verification commands to run.

    Detection priority:
    1. ``package.json``     (Node.js / Next.js)
    2. Python project files (``pyproject.toml`` / ``setup.py`` / ``requirements.txt``)
    3. ``Makefile``

    Returns an empty list if nothing actionable is found.
    """
    root = Path(target_root)
    if not root.exists():
        return []

    pkg_json = root / "package.json"
    if pkg_json.exists():
        cmds = _commands_from_package_json(pkg_json)
        if cmds:
            return cmds

    if any((root / f).exists() for f in (
        "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    )):
        cmds = _commands_from_python_project(root)
        if cmds:
            return cmds

    makefile = root / "Makefile"
    if makefile.exists():
        cmds = _commands_from_makefile(makefile)
        if cmds:
            return cmds

    return []


# ---------------------------------------------------------------------------
# Node.js / Next.js
# ---------------------------------------------------------------------------

_NODE_INSTALL_CMDS = ("npm ci", "npm install --frozen-lockfile", "yarn install --frozen-lockfile", "pnpm install --frozen-lockfile")

# Maps canonical script alias → preferred npm run command
_SCRIPT_PRIORITY: list[tuple[str, str]] = [
    # (script key pattern, full command to use)
    ("build",       "npm run build"),
    ("type-check",  "npm run type-check"),
    ("typecheck",   "npm run typecheck"),
    ("tsc",         "npm run tsc"),
    ("test",        "npm run test -- --watchAll=false --passWithNoTests"),
    ("lint",        "npm run lint"),
    ("check",       "npm run check"),
]


def _commands_from_package_json(pkg_json: Path) -> list[str]:
    """
    Build a verification command list from ``package.json``.

    Install step:  ``npm ci`` when ``package-lock.json`` is present,
                   otherwise ``npm install --frozen-lockfile``.
    Then emit ``npm run <script>`` for each matched key (build, type-check,
    test, lint) in priority order.
    """
    try:
        data: dict = json.loads(pkg_json.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("auto_populate: could not parse package.json: %s", exc)
        return []

    scripts: dict = data.get("scripts", {})
    if not scripts:
        return []

    commands: list[str] = []

    # Install step
    lock = pkg_json.parent / "package-lock.json"
    yarn_lock = pkg_json.parent / "yarn.lock"
    pnpm_lock = pkg_json.parent / "pnpm-lock.yaml"
    if lock.exists():
        commands.append("npm ci")
    elif yarn_lock.exists():
        commands.append("yarn install --frozen-lockfile")
    elif pnpm_lock.exists():
        commands.append("pnpm install --frozen-lockfile")
    else:
        commands.append("npm install")

    # Script steps — iterate in priority order, emit at most one match per slot
    emitted_slots: set[str] = set()
    for key_pattern, cmd in _SCRIPT_PRIORITY:
        slot = key_pattern.split("-")[0]  # e.g. "type-check" → "type"
        if slot in emitted_slots:
            continue
        if key_pattern in scripts:
            # Append --ci flag for known test runners when not already present
            if key_pattern == "test" and "--ci" in str(scripts[key_pattern]):
                cmd = "npm run test -- --ci"
            commands.append(cmd)
            emitted_slots.add(slot)

    return commands


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

def _commands_from_python_project(root: Path) -> list[str]:
    """
    Build verification commands for a Python project.

    Installs dependencies, then runs ``pytest`` if available.
    """
    commands: list[str] = []

    # Install
    req_txt = root / "requirements.txt"
    pyproject = root / "pyproject.toml"
    if req_txt.exists():
        commands.append("pip install -r requirements.txt -q")
    elif pyproject.exists():
        commands.append("pip install -e . -q")

    # Test runner
    pytest_cfg_files = ["pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"]
    has_pytest_cfg = any((root / f).exists() for f in pytest_cfg_files)
    tests_dir = (root / "tests").exists() or (root / "test").exists()

    if has_pytest_cfg or tests_dir:
        commands.append("python -m pytest --tb=short -q")

    return commands


# ---------------------------------------------------------------------------
# Makefile
# ---------------------------------------------------------------------------

# Targets whose names strongly suggest install / build / test / lint
_MAKEFILE_SLOT_PATTERNS: list[tuple[str, list[str]]] = [
    ("install", ["install", "deps", "setup"]),
    ("build",   ["build", "compile"]),
    ("test",    ["test", "tests", "check-test"]),
    ("lint",    ["lint", "check", "fmt", "format"]),
]


def _commands_from_makefile(makefile: Path) -> list[str]:
    """
    Parse a ``Makefile`` and return ``make <target>`` commands for
    recognized install / build / test / lint targets.

    Only phony / comment-labelled targets are matched — avoids emitting
    file-build targets.
    """
    try:
        text = makefile.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.debug("auto_populate: could not read Makefile: %s", exc)
        return []

    # Extract declared phony targets  (.PHONY: foo bar baz)
    phony_targets: set[str] = set()
    for m in re.finditer(r"^\.PHONY\s*:\s*(.+)$", text, re.MULTILINE):
        phony_targets.update(m.group(1).split())

    # Extract all rule targets  (target:  or  target: dep1 dep2)
    defined_targets: set[str] = set()
    for m in re.finditer(r"^([a-zA-Z0-9_\-]+)\s*:", text, re.MULTILINE):
        defined_targets.add(m.group(1))

    # Only consider phony targets; fall back to all defined if none declared
    candidates = phony_targets if phony_targets else defined_targets

    commands: list[str] = []
    emitted_slots: set[str] = set()

    for slot, patterns in _MAKEFILE_SLOT_PATTERNS:
        if slot in emitted_slots:
            continue
        for pat in patterns:
            if pat in candidates:
                commands.append(f"make {pat}")
                emitted_slots.add(slot)
                break  # first match per slot wins

    return commands

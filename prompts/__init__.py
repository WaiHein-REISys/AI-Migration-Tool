"""
Prompt Loader
=============
Centralised loader for all LLM prompt files stored in the prompts/ directory.

Every prompt lives as a plain text (.txt) or Markdown (.md) file so it can be
edited, reviewed, and version-controlled independently of the Python source.

Usage
-----
    from prompts import load_prompt, resolve_prompt_filename

    # Load a known file directly
    system_prompt  = load_prompt("plan_system.txt")
    doc_template   = load_prompt("plan_document_template.md")
    target_stack   = load_prompt("conversion_target_stack.txt")

    # Resolve the right file for a given target dynamically
    filename = resolve_prompt_filename("snake_case", "plan_system", "plan_system.txt")
    system_prompt = load_prompt(filename)

The loader caches each file on first read so repeated calls within a single
pipeline run are free after the initial disk access.

Prompt file naming convention
------------------------------
Target-specific files follow the pattern  ``{default_stem}_{target_id}.txt``:

    plan_system.txt                     -- default (simpler_grants)
    plan_system_hrsa_pprs.txt           -- hrsa_pprs target
    plan_system_snake_case.txt          -- snake_case target
    conversion_system.txt               -- default (simpler_grants)
    conversion_system_hrsa_pprs.txt     -- hrsa_pprs target
    conversion_target_stack.txt         -- default (simpler_grants)
    conversion_target_stack_hrsa_pprs.txt
    plan_document_template.md           -- shared scaffold (no-LLM mode, all targets)

resolve_prompt_filename() uses this convention automatically, so no
target-to-filename mappings need to be maintained anywhere in the codebase.
"""

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# Absolute path to the prompts/ directory (same folder as this file)
PROMPTS_DIR: Path = Path(__file__).parent


@lru_cache(maxsize=None)
def load_prompt(filename: str) -> str:
    """
    Load and cache a prompt file from the prompts/ directory.

    Parameters
    ----------
    filename : str
        Bare filename including extension, e.g. ``"plan_system.txt"``.

    Returns
    -------
    str
        The full content of the prompt file, with trailing whitespace stripped.

    Raises
    ------
    FileNotFoundError
        If ``filename`` does not exist under ``prompts/``.
    """
    path = PROMPTS_DIR / filename
    if not path.exists():
        available = [f.name for f in PROMPTS_DIR.iterdir()
                     if f.is_file() and f.suffix in {".txt", ".md"}]
        raise FileNotFoundError(
            f"Prompt file not found: {path}\n"
            f"Available prompts: {sorted(available)}"
        )
    content = path.read_text(encoding="utf-8").rstrip()
    logger.debug("Loaded prompt '%s' (%d chars)", filename, len(content))
    return content


def resolve_prompt_filename(target: str, role: str, default: str) -> str:
    """
    Resolve the correct prompt filename for a given target and role without
    any hardcoded target-to-file mappings.

    Resolution order
    ----------------
    1. **Wizard registry** — ``config/wizard-registry.json``
       ``targets[target].prompt_files[role]``
       Explicit, authoritative for all wizard-registered targets.

    2. **Convention** — ``{stem_of_default}_{target}.txt``
       e.g. default ``"plan_system.txt"`` + target ``"hrsa_pprs"``
            → checks for ``"plan_system_hrsa_pprs.txt"`` on disk.
       Picks up any prompt file that follows the naming convention without
       needing a registry entry.

    3. **Default** — the ``default`` argument as-is (e.g. ``"plan_system.txt"``).
       Used when no target-specific file is found via registry or convention.

    Parameters
    ----------
    target : str
        Target stack identifier, e.g. ``"snake_case"``, ``"hrsa_pprs"``.
    role : str
        Prompt role key as stored in wizard-registry.json ``prompt_files``:
        ``"plan_system"``, ``"conversion_system"``, or ``"target_stack"``.
    default : str
        Bare default filename to fall back to, e.g. ``"plan_system.txt"``.

    Returns
    -------
    str
        Filename (no path) of the resolved prompt file under ``prompts/``.
    """
    # 1. Wizard registry — explicit mapping takes priority
    try:
        from wizard.registry import load_registry
        reg_prompts = (
            load_registry().get("targets", {}).get(target, {}).get("prompt_files", {})
        )
        if role in reg_prompts:
            logger.debug(
                "resolve_prompt_filename: registry hit — target=%s role=%s → %s",
                target, role, reg_prompts[role],
            )
            return reg_prompts[role]
    except Exception:
        pass  # registry unavailable — fall through to convention

    # 2. Convention — derive candidate from default filename stem + target
    # e.g. "plan_system.txt" → "plan_system_snake_case.txt"
    stem = Path(default).stem          # "plan_system"
    conventional = f"{stem}_{target}.txt"
    if (PROMPTS_DIR / conventional).exists():
        logger.debug(
            "resolve_prompt_filename: convention hit — target=%s role=%s → %s",
            target, role, conventional,
        )
        return conventional

    # 3. Default
    logger.debug(
        "resolve_prompt_filename: using default — target=%s role=%s → %s",
        target, role, default,
    )
    return default


def list_prompts() -> list[str]:
    """Return the names of all prompt files in the prompts/ directory."""
    return sorted(
        f.name
        for f in PROMPTS_DIR.iterdir()
        if f.is_file() and f.suffix in {".txt", ".md"} and f.name != "README.md"
    )


def reload_prompt(filename: str) -> str:
    """
    Force-reload a prompt from disk, bypassing the cache.

    Useful during development when editing prompt files without restarting
    the Python process.
    """
    load_prompt.cache_clear()
    return load_prompt(filename)

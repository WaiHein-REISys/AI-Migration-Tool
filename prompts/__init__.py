"""
Prompt Loader
=============
Centralised loader for all LLM prompt files stored in the prompts/ directory.

Every prompt lives as a plain text (.txt) or Markdown (.md) file so it can be
edited, reviewed, and version-controlled independently of the Python source.

Usage
-----
    from prompts import load_prompt

    system_prompt  = load_prompt("plan_system.txt")
    doc_template   = load_prompt("plan_document_template.md")
    target_stack   = load_prompt("conversion_target_stack.txt")

The loader caches each file on first read so repeated calls within a single
pipeline run are free after the initial disk access.

Prompt files
------------
    plan_system.txt                -- System prompt for PlanAgent LLM call
    plan_document_template.md      -- Markdown template for the Plan Document
                                      (template-only / no-LLM mode)
    conversion_system.txt          -- System prompt for ConversionAgent LLM call
                                      (contains {rules_text} and
                                       {target_stack_summary} placeholders)
    conversion_target_stack.txt    -- Target stack reference injected into the
                                      conversion system prompt
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

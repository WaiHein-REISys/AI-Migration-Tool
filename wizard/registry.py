"""
wizard.registry — Wizard Registry & Skillset Config Helpers
============================================================
Provides load/save helpers for:
  config/wizard-registry.json   — tracks all registered target IDs
  config/skillset-config.json   — main skillset / stack definitions

Both files live under the project's config/ directory.
"""

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (resolved relative to the project root)
# ---------------------------------------------------------------------------

_ROOT           = Path(__file__).parent.parent          # ai-migration-tool/
WIZARD_REGISTRY = _ROOT / "config" / "wizard-registry.json"
SKILLSET_CONFIG = _ROOT / "config" / "skillset-config.json"


# ---------------------------------------------------------------------------
# wizard-registry.json
# ---------------------------------------------------------------------------

def load_registry() -> dict:
    """
    Load the wizard registry from disk.
    Returns  { "targets": {} }  if the file does not exist or is corrupt.
    """
    if WIZARD_REGISTRY.exists():
        try:
            return json.loads(WIZARD_REGISTRY.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"targets": {}}


def save_registry(registry: dict) -> None:
    """Write the registry dict to disk (creates the file if absent)."""
    WIZARD_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    WIZARD_REGISTRY.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def is_target_registered(target_id: str) -> bool:
    """Return True if *target_id* is already present in the registry."""
    return target_id in load_registry().get("targets", {})


def register_target(target_id: str, info: dict, overwrite: bool = False) -> bool:
    """
    Add *target_id* → *info* to the registry and persist.

    Parameters
    ----------
    target_id : str
        Snake_case identifier (e.g. ``"my_nextjs_flask"``).
    info : dict
        Metadata dict to store (framework_pair, prompt_files, etc.).
    overwrite : bool
        Replace an existing entry when True.  Default False (skip if present).

    Returns
    -------
    bool
        True if the registry was updated, False if entry already existed.
    """
    registry = load_registry()
    if target_id in registry["targets"] and not overwrite:
        return False
    registry["targets"][target_id] = info
    save_registry(registry)
    return True


def list_registered_targets() -> dict[str, dict]:
    """Return the full targets dict  { target_id: info }."""
    return load_registry().get("targets", {})


# ---------------------------------------------------------------------------
# skillset-config.json
# ---------------------------------------------------------------------------

def load_skillset() -> dict:
    """
    Load skillset-config.json from disk.
    Returns an empty dict if the file does not exist or cannot be parsed.
    """
    if SKILLSET_CONFIG.exists():
        try:
            return json.loads(SKILLSET_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_skillset(data: dict) -> None:
    """Overwrite skillset-config.json with *data*."""
    SKILLSET_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    SKILLSET_CONFIG.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def merge_skillset(updates: dict, overwrite_keys: bool = False) -> list[str]:
    """
    Merge top-level keys from *updates* into skillset-config.json.

    Parameters
    ----------
    updates : dict
        Keys to add / replace.
    overwrite_keys : bool
        When True, existing top-level keys are replaced.

    Returns
    -------
    list[str]
        Names of keys that were actually written (new or updated).
    """
    data    = load_skillset()
    written = []

    for k, v in updates.items():
        if k not in data or overwrite_keys:
            data[k] = v
            written.append(k)

    if written:
        save_skillset(data)

    return written

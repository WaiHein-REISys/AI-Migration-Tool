"""
wizard — Setup Wizard Package for the AI Migration Tool
=========================================================
Guides the user through defining a custom Source → Target migration pair,
analyses both codebases to detect frameworks and patterns, then generates
all the config / prompt / job-file artefacts needed to run migrations.

Sub-modules
-----------
  wizard.detector        CodebaseInspector — heuristic framework detector
  wizard.collector       Interactive Q&A helpers (collect_source_info, ...)
  wizard.generator       Prompt & config content generators
  wizard.writer          WizardWriter — file I/O with dry-run support
  wizard.registry        Registry helpers (load / save wizard-registry.json)

Public API (re-exported here for convenience)
---------------------------------------------
  from wizard import run_wizard, list_targets
  from wizard import detect_feature_folders, collect_feature_selection
"""

from wizard.runner import run_wizard, list_targets
from wizard.collector import detect_feature_folders, collect_feature_selection

__all__ = [
    "run_wizard",
    "list_targets",
    "detect_feature_folders",
    "collect_feature_selection",
]

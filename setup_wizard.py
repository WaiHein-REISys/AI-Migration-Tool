#!/usr/bin/env python3
"""
setup_wizard.py — First-Run Onboarding Wizard for the AI Migration Tool
========================================================================
Guides the user through defining a custom Source → Target migration pair,
analyses both codebases to detect frameworks and patterns, then generates
all the config / prompt / job-file artefacts needed to run migrations.

Usage:
    python setup_wizard.py                          # fully interactive
    python setup_wizard.py --config wizard.json    # load answers from JSON
    python setup_wizard.py --dry-run               # preview without writing files
    python setup_wizard.py --overwrite             # regenerate existing files
    python setup_wizard.py --list-targets          # see configured targets
    python setup_wizard.py --non-interactive --config wizard.json  # CI / agent mode

What it produces (for a new target named "my_target")
------------------------------------------------------
  prompts/
      plan_system_my_target.txt             -- LLM plan-generation prompt
      conversion_system_my_target.txt       -- LLM conversion-agent prompt
      conversion_target_stack_my_target.txt -- Target stack reference
  agent-prompts/
      _template_my_target.yaml             -- Ready-to-fill job template
  config/skillset-config.json
      → merges  target_stack_my_target  and  project_structure_my_target
  config/wizard-registry.json
      → records the target for idempotency

After the wizard finishes:
  • Copy agent-prompts/_template_my_target.yaml → migrate-<feature>.yaml
  • Set  pipeline.feature_root  and  pipeline.feature_name
  • Run:  python run_agent.py --job agent-prompts/migrate-<feature>.yaml

From an AI agent (Cursor / Copilot / Windsurf):
    "Run python setup_wizard.py to configure a new migration target"
    "Run python setup_wizard.py --config wizard.json to load saved answers"
    "Run python setup_wizard.py --list-targets to see configured targets"

Modular implementation
----------------------
The wizard logic lives in the  wizard/  package:
    wizard.detector    -- CodebaseInspector (framework heuristics)
    wizard.collector   -- Interactive Q&A helpers
    wizard.generator   -- Prompt & config content generators
    wizard.writer      -- File I/O with dry-run support
    wizard.registry    -- wizard-registry.json & skillset-config.json helpers
    wizard.runner      -- run_wizard() + list_targets() orchestration

This file is the CLI entry point only.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wizard.collector import collect_answers
from wizard.registry  import is_target_registered
from wizard.runner    import list_targets, run_wizard


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

def _safe_print(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def _yes_no(prompt: str, default: bool = True) -> bool:
    default_str = "Y/n" if default else "y/N"
    try:
        raw = input(f"  {prompt} ({default_str}): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    if not raw:
        return default
    return raw.startswith("y")


def _print_header(title: str) -> None:
    width = 64
    print(f"\n{'='*width}")
    print(f"  {title}")
    print(f"{'='*width}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI Migration Tool — Setup Wizard (first-run onboarding)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "Path to a JSON file with pre-filled wizard answers. "
            "Values are used as defaults in interactive mode, or as the "
            "complete answer set in --non-interactive mode."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what files would be written without actually writing them.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing prompt files and config entries (default: skip).",
    )
    parser.add_argument(
        "--list-targets", "-l",
        action="store_true",
        help="List all configured migration targets and exit.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Run without any user prompts. "
            "Requires --config with all required keys "
            "(source, target, target_id).  Useful for CI or agent scripting."
        ),
    )

    args = parser.parse_args()

    if args.list_targets:
        list_targets()
        return 0

    # ---- Load pre-fill from config file ----
    prefill: dict | None = None
    if args.config:
        config_path = Path(args.config)
        if not config_path.exists():
            print(f"[ERROR] Config file not found: {config_path}", file=sys.stderr)
            return 1
        try:
            prefill = json.loads(config_path.read_text(encoding="utf-8"))
            print(f"  Loaded wizard config from: {config_path}")
        except Exception as exc:
            print(f"[ERROR] Failed to parse config file: {exc}", file=sys.stderr)
            return 1

    # ---- Non-interactive mode ----
    if args.non_interactive:
        if prefill is None:
            print("[ERROR] --non-interactive requires --config <path>", file=sys.stderr)
            return 1
        required = ["source", "target", "target_id"]
        missing  = [k for k in required if k not in prefill]
        if missing:
            print(
                f"[ERROR] Config file is missing required keys: {missing}",
                file=sys.stderr,
            )
            return 1
        answers = dict(prefill)
        if "created_at" not in answers:
            answers["created_at"] = datetime.now(timezone.utc).isoformat()
    else:
        # ---- Interactive mode ----
        _print_header("AI Migration Tool — Setup Wizard")
        answers = collect_answers(prefill)

    # ---- Idempotency check ----
    target_id = answers.get("target_id", "")
    if is_target_registered(target_id) and not args.overwrite:
        print()
        _safe_print(f"  Target '{target_id}' is already configured.")
        print("  Use --overwrite to regenerate all files for this target.")
        print("  Use --list-targets to see all registered targets.")
        print()
        if args.non_interactive:
            return 0  # silent skip in non-interactive mode
        if not _yes_no("Continue anyway (skip existing files)?", default=True):
            return 0

    # ---- Run the wizard ----
    run_wizard(answers, dry_run=args.dry_run, overwrite=args.overwrite)
    return 0


if __name__ == "__main__":
    sys.exit(main())

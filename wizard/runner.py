"""
wizard.runner — Main Wizard Orchestration Logic
================================================
Coordinates the full wizard flow:
  1. Takes the *answers* dict (from wizard.collector or a JSON config file)
  2. Generates all artefacts (prompts, job template, config entries)
  3. Writes them to disk via WizardWriter
  4. Updates the wizard registry

Public functions
----------------
  run_wizard(answers, dry_run, overwrite)   — generate & write all artefacts
  list_targets()                            — print registered targets
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

from wizard import generator
from wizard.registry import (
    SKILLSET_CONFIG,
    WIZARD_REGISTRY,
    is_target_registered,
    load_skillset,
    register_target,
    list_registered_targets,
)
from wizard.writer import WizardWriter

# Paths
_ROOT           = Path(__file__).parent.parent   # ai-migration-tool/
PROMPTS_DIR     = _ROOT / "prompts"
AGENT_PROMPTS_DIR = _ROOT / "agent-prompts"


def _safe_print(text: str) -> None:
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def _print_header(title: str) -> None:
    width = 64
    print(f"\n{'='*width}")
    print(f"  {title}")
    print(f"{'='*width}")


def _print_section(title: str) -> None:
    print(f"\n  [{title}]")


# ---------------------------------------------------------------------------
# Core orchestration
# ---------------------------------------------------------------------------

def run_wizard(
    answers: dict,
    dry_run: bool = False,
    overwrite: bool = False,
) -> None:
    """
    Generate and write all setup-wizard artefacts for the given *answers*.

    Artefacts written
    -----------------
    prompts/
        plan_system_<target_id>.txt
        conversion_system_<target_id>.txt
        conversion_target_stack_<target_id>.txt
    agent-prompts/
        _template_<target_id>.yaml
    config/
        skillset-config.json  (merged with new target/source stack entries)
        wizard-registry.json  (updated with target_id entry)

    Parameters
    ----------
    answers : dict
        Dict produced by wizard.collector.collect_answers() or loaded from JSON.
    dry_run : bool
        Preview writes without touching disk.
    overwrite : bool
        Replace existing files/keys instead of skipping them.
    """
    target_id = answers["target_id"]
    source    = answers["source"]
    target    = answers["target"]

    writer = WizardWriter(dry_run=dry_run)

    _print_header(f"Generating artefacts  —  target: {target_id}")
    _safe_print(f"  Source:  {source['framework']} / {source['backend_framework']}")
    _safe_print(f"  Target:  {target['framework']} / {target['backend_framework']}")
    print()

    # ------------------------------------------------------------------ #
    # 1. Prompt files                                                      #
    # ------------------------------------------------------------------ #
    _print_section("Prompt files")

    writer.write(
        PROMPTS_DIR / f"plan_system_{target_id}.txt",
        generator.generate_plan_system_prompt(answers),
        overwrite=overwrite,
    )
    writer.write(
        PROMPTS_DIR / f"conversion_system_{target_id}.txt",
        generator.generate_conversion_system_prompt(answers),
        overwrite=overwrite,
    )
    writer.write(
        PROMPTS_DIR / f"conversion_target_stack_{target_id}.txt",
        generator.generate_target_stack_prompt(answers),
        overwrite=overwrite,
    )

    # ------------------------------------------------------------------ #
    # 2. Job template                                                      #
    # ------------------------------------------------------------------ #
    _print_section("Agent job template")

    writer.write(
        AGENT_PROMPTS_DIR / f"_template_{target_id}.yaml",
        generator.generate_job_template(answers),
        overwrite=overwrite,
    )

    # ------------------------------------------------------------------ #
    # 3. skillset-config.json                                              #
    # ------------------------------------------------------------------ #
    _print_section("skillset-config.json")

    skillset_updates: dict = {
        f"target_stack_{target_id}":      generator.build_target_stack_entry(answers),
        f"project_structure_{target_id}": generator.build_project_structure_entry(answers),
    }

    # Add source stack if it has a non-default name
    source_name = source.get("name", "")
    if source_name and source_name not in ("source", "gprs", "hab_gprs"):
        skillset_updates[f"source_stack_{source_name}"] = (
            generator.build_source_stack_entry(answers)
        )

    # Merge reference_codebases paths
    existing_skillset = load_skillset()
    ref_cbs = dict(existing_skillset.get("reference_codebases", {}))
    if source.get("root"):
        ref_cbs[f"source_{source_name}"] = source["root"]
    if target.get("root"):
        ref_cbs[target_id] = target["root"]
    if ref_cbs:
        skillset_updates["reference_codebases"] = ref_cbs

    writer.patch_json(SKILLSET_CONFIG, skillset_updates, overwrite_keys=overwrite)

    # ------------------------------------------------------------------ #
    # 4. wizard-registry.json                                              #
    # ------------------------------------------------------------------ #
    _print_section("Wizard registry")

    registry_entry = {
        "source_name":    source.get("name"),
        "source_root":    source.get("root"),
        "target_name":    target.get("name"),
        "target_root":    target.get("root"),
        "framework_pair": f"{source['framework']} -> {target['framework']}",
        "backend_pair":   f"{source['backend_framework']} -> {target['backend_framework']}",
        "created_at":     answers.get("created_at", datetime.now(timezone.utc).isoformat()),
        "prompt_files": {
            "plan_system":        f"plan_system_{target_id}.txt",
            "conversion_system":  f"conversion_system_{target_id}.txt",
            "target_stack":       f"conversion_target_stack_{target_id}.txt",
        },
        "job_template": f"_template_{target_id}.yaml",
    }

    if not dry_run:
        updated = register_target(target_id, registry_entry, overwrite=overwrite)
        if updated:
            _safe_print(f"  [OK]      {WIZARD_REGISTRY}")
        else:
            _safe_print(f"  [SKIP]    {WIZARD_REGISTRY}  (target already registered)")
    else:
        _safe_print(f"  [DRY-RUN] {WIZARD_REGISTRY}")

    # ------------------------------------------------------------------ #
    # 5. Summary & next steps                                              #
    # ------------------------------------------------------------------ #
    writer.summary()
    _print_next_steps(answers, target_id)


# ---------------------------------------------------------------------------
# list_targets
# ---------------------------------------------------------------------------

def list_targets() -> None:
    """Print all registered migration targets from the wizard registry."""
    targets = list_registered_targets()
    if not targets:
        print("\n  No targets configured yet.")
        print("  Run:  python setup_wizard.py  to configure your first target.")
        return

    _print_header(f"Configured Targets  ({WIZARD_REGISTRY})")
    for tid, info in targets.items():
        _safe_print(f"\n  [{tid}]")
        _safe_print(f"    Pair:      {info.get('framework_pair', '?')}")
        _safe_print(f"    Backend:   {info.get('backend_pair', '?')}")
        _safe_print(f"    Template:  agent-prompts/{info.get('job_template', '?')}")
        _safe_print(f"    Created:   {info.get('created_at', '?')[:10]}")

    print()
    print("  To run a migration, copy a template:")
    print("    cp agent-prompts/_template_<target_id>.yaml "
          "agent-prompts/migrate-<Feature>.yaml")
    print("    python run_agent.py --job agent-prompts/migrate-<Feature>.yaml")
    print()


# ---------------------------------------------------------------------------
# Next-steps message
# ---------------------------------------------------------------------------

def _print_next_steps(answers: dict, target_id: str) -> None:
    _print_header("Next Steps")

    print(f"  1. Review the generated prompts in  prompts/")
    _safe_print(f"       plan_system_{target_id}.txt")
    _safe_print(f"       conversion_system_{target_id}.txt")
    _safe_print(f"       conversion_target_stack_{target_id}.txt")
    print()
    print(f"  2. Copy the job template and fill in feature details:")
    _safe_print(
        f"       cp agent-prompts/_template_{target_id}.yaml "
        f"agent-prompts/migrate-<FeatureName>-{target_id}.yaml"
    )
    print(f"     Then set:  pipeline.feature_root  and  pipeline.feature_name")
    print()
    print(f"  3. Run in plan mode first (generates a Plan Document, no code written):")
    _safe_print(
        f"       python run_agent.py "
        f"--job agent-prompts/migrate-<FeatureName>-{target_id}.yaml"
    )
    print()
    print(f"  4. Review the Plan Document in  plans/")
    print(f"     Then run in full mode:")
    _safe_print(
        f"       python run_agent.py "
        f"--job agent-prompts/migrate-<FeatureName>-{target_id}.yaml"
    )
    print(f"     (after editing the job file to set  mode: full)")
    print()
    print(f"  NOTE: To use this target via main.py --target, add '{target_id}' to")
    print(f"        the --target choices in main.py's build_arg_parser().")
    print(f"        For agent mode (run_agent.py) no code change is needed.")
    print()

#!/usr/bin/env python3
"""
run_agent.py — Agent-mode entry point for the AI Migration Tool
===============================================================
Reads a YAML job file and executes the migration pipeline with
the parameters defined inside it. Designed to be invoked by AI
coding agents (Cursor, GitHub Copilot, Windsurf) without requiring
the agent to know any CLI flags.

Usage:
    python run_agent.py --job agent-prompts/migrate-action-history.yaml
    python run_agent.py --job agent-prompts/migrate-action-history.yaml --dry-run
    python run_agent.py --job agent-prompts/migrate-action-history.yaml --force
    python run_agent.py --list-jobs
    python run_agent.py --setup                          # first-run wizard (interactive)
    python run_agent.py --setup --config wizard.json    # wizard with pre-filled answers
    python run_agent.py --setup --dry-run               # preview wizard outputs
    python run_agent.py --setup --list-targets          # list configured targets
    python run_agent.py --new-job                        # interactively create a job file
    python run_agent.py --new-job --target hrsa_pprs    # pre-select migration target
    python run_agent.py --new-job --feature ActionHistory  # pre-fill feature search

From an AI agent (Cursor / Copilot / Windsurf chat):
    "Run the migration job at agent-prompts/migrate-action-history.yaml"
    "Run python run_agent.py --job agent-prompts/migrate-action-history.yaml"
    "List available migration jobs with python run_agent.py --list-jobs"
    "Configure a new migration target with python run_agent.py --setup"
    "List configured targets with python run_agent.py --setup --list-targets"
    "Create a new migration job with python run_agent.py --new-job"
"""

import argparse
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Optional YAML parser — fall back to a minimal built-in parser if PyYAML
# is not installed (avoids adding a mandatory dependency just for this script).
# ---------------------------------------------------------------------------
try:
    import yaml  # type: ignore
    def _load_yaml(path: Path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
except ImportError:
    import re as _re

    def _load_yaml(path: Path) -> dict:  # type: ignore[misc]
        """
        Minimal YAML parser: handles only the simple key: value and
        key: | multiline scalar patterns used in the job files.
        Not a full YAML implementation — install PyYAML for full support:
            pip install pyyaml
        """
        result: dict = {}
        current_section: dict = result
        section_stack: list[tuple[int, dict]] = []
        multiline_key: str | None = None
        multiline_lines: list[str] = []
        multiline_indent: int = 0

        def _coerce(v: str):
            v = v.strip()
            if v in ("null", "~", ""):
                return None
            if v in ("true", "yes"):
                return True
            if v in ("false", "no"):
                return False
            try:
                return int(v)
            except ValueError:
                pass
            try:
                return float(v)
            except ValueError:
                pass
            return v.strip('"').strip("'")

        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for raw in lines:
            line = raw.rstrip("\n")

            # Skip comment-only lines and blank lines (unless in multiline)
            stripped = line.strip()
            if not multiline_key and (stripped.startswith("#") or stripped == ""):
                continue

            # Detect indentation level
            indent = len(line) - len(line.lstrip())

            # -- Multiline scalar continuation --
            if multiline_key is not None:
                if stripped == "" or indent > multiline_indent:
                    multiline_lines.append(line[multiline_indent:])
                    continue
                else:
                    # Flush the multiline value
                    current_section[multiline_key] = "\n".join(multiline_lines).strip()
                    multiline_key = None
                    multiline_lines = []

            # -- Section header (no value after colon) --
            m = _re.match(r'^(\s*)(\w[\w_-]*):\s*$', line)
            if m:
                key = m.group(2)
                new_section: dict = {}
                current_section[key] = new_section
                section_stack = [(indent, current_section)] + [
                    s for s in section_stack if s[0] < indent
                ]
                current_section = new_section
                continue

            # -- Multiline scalar start (key: |) --
            m = _re.match(r'^(\s*)(\w[\w_-]*):\s*\|.*$', line)
            if m:
                multiline_key = m.group(2)
                multiline_indent = indent + 2
                multiline_lines = []
                continue

            # -- Simple key: value --
            m = _re.match(r'^(\s*)(\w[\w_-]*):\s*(.*)$', line)
            if m:
                key = m.group(2)
                val = _coerce(m.group(3))
                # Pop stack back to correct nesting level
                while section_stack and section_stack[0][0] >= indent:
                    section_stack.pop(0)
                if section_stack:
                    current_section = section_stack[0][1]
                current_section[key] = val
                continue

        # Flush any trailing multiline
        if multiline_key is not None:
            current_section[multiline_key] = "\n".join(multiline_lines).strip()

        return result


# ---------------------------------------------------------------------------
# Job file → argparse.Namespace mapping
# ---------------------------------------------------------------------------

def _job_to_args(job: dict, overrides: dict | None = None) -> argparse.Namespace:
    """
    Convert a loaded YAML job dict to an argparse.Namespace that
    main._run_pipeline_with_router() understands.

    CLI overrides (--dry-run, --force, --verbose passed to run_agent.py itself)
    take precedence over the job file values.
    """
    pipeline = job.get("pipeline", {})
    llm      = job.get("llm", {})
    overrides = overrides or {}

    def _get(section: dict, key: str, default=None):
        v = section.get(key, default)
        return default if v is None else v

    ns = argparse.Namespace(
        # --- Feature ---
        feature_root    = _get(pipeline, "feature_root"),
        feature_name    = _get(pipeline, "feature_name", ""),
        output_root     = _get(pipeline, "output_root"),

        # --- Pipeline control ---
        mode            = _get(pipeline, "mode", "plan"),
        target          = _get(pipeline, "target", "simpler_grants"),
        run_id          = None,
        resume          = False,
        dry_run         = overrides.get("dry_run",    _get(pipeline, "dry_run",    False)),
        auto_approve    = overrides.get("auto_approve",_get(pipeline, "auto_approve", False)),
        force           = overrides.get("force",      _get(pipeline, "force",      False)),
        verbose         = overrides.get("verbose",    False),

        # --- Configs ---
        skillset_config = str(ROOT / "config" / "skillset-config.json"),
        rules_config    = str(ROOT / "config" / "rules-config.json"),

        # --- LLM ---
        no_llm          = _get(llm, "no_llm",      False),
        llm_provider    = _get(llm, "provider"),
        llm_model       = _get(llm, "model"),
        llm_base_url    = _get(llm, "base_url"),
        llm_model_path  = _get(llm, "model_path"),
        ollama_host     = _get(llm, "ollama_host"),
        llm_max_tokens  = _get(llm, "max_tokens"),
        llm_temperature = _get(llm, "temperature"),
        llm_timeout     = _get(llm, "timeout"),
    )

    # Default feature_name from feature_root stem if not set
    if not ns.feature_name and ns.feature_root:
        ns.feature_name = Path(ns.feature_root).name

    return ns


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_print(text: str) -> None:
    """Print text, replacing any characters the terminal can't render."""
    print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
        sys.stdout.encoding or "utf-8", errors="replace"
    ))


def _list_jobs() -> None:
    jobs_dir = ROOT / "agent-prompts"
    files    = sorted(
        f for f in jobs_dir.glob("*.yaml") if not f.name.startswith("_")
    )
    if not files:
        print("No job files found in agent-prompts/")
        return

    print(f"\nAvailable migration jobs ({jobs_dir}):\n")
    for f in files:
        try:
            job  = _load_yaml(f)
            name = job.get("job", {}).get("name", f.stem)
            desc = (job.get("job", {}).get("description") or "").strip().split("\n")[0]
            pl   = job.get("pipeline", {})
            _safe_print(f"  {f.name}")
            _safe_print(f"    Job:    {name}")
            _safe_print(f"    Desc:   {desc}")
            _safe_print(f"    Mode:   {pl.get('mode', '?')}  "
                        f"Target: {pl.get('target', '?')}  "
                        f"Feature: {pl.get('feature_name', '?')}")
            print()
        except Exception as exc:
            print(f"  {f.name}  [could not parse: {exc}]")
            print()

    print(f"Run a job:  python run_agent.py --job agent-prompts/<filename>.yaml\n")


def _print_job_summary(job: dict, ns: argparse.Namespace) -> None:
    notes = job.get("notes", "").strip()
    _safe_print(f"\n  Job:     {job.get('job', {}).get('name', '(unnamed)')}")
    _safe_print(f"  Feature: {ns.feature_name}")
    _safe_print(f"  Source:  {ns.feature_root}")
    _safe_print(f"  Mode:    {ns.mode}   Target: {ns.target}")
    _safe_print(f"  Dry-run: {ns.dry_run}   Force: {ns.force}   no-LLM: {ns.no_llm}")
    if notes:
        _safe_print("\n  Notes:\n" + "\n".join(f"    {l}" for l in notes.splitlines()))
    print()


# ---------------------------------------------------------------------------
# Setup Wizard bridge
# ---------------------------------------------------------------------------

def _run_setup_wizard(args) -> int:
    """
    Bridge between run_agent.py CLI and setup_wizard.py / wizard package.

    Translates the --setup / --config / --list-targets / --overwrite /
    --non-interactive flags into a setup_wizard.main()-compatible call.
    """
    # Build a sys.argv equivalent for setup_wizard.main()
    wizard_argv = []

    if getattr(args, "list_targets", False):
        wizard_argv += ["--list-targets"]

    if getattr(args, "dry_run", False):
        wizard_argv += ["--dry-run"]

    if getattr(args, "overwrite", False):
        wizard_argv += ["--overwrite"]

    if getattr(args, "config", None):
        wizard_argv += ["--config", args.config]

    if getattr(args, "non_interactive", False):
        wizard_argv += ["--non-interactive"]

    # Import and run the wizard with the constructed argv
    import setup_wizard as _sw
    import sys as _sys
    old_argv = _sys.argv
    try:
        _sys.argv = ["setup_wizard.py"] + wizard_argv
        return _sw.main()
    finally:
        _sys.argv = old_argv


# ---------------------------------------------------------------------------
# New-job wizard
# ---------------------------------------------------------------------------

def _run_new_job(args) -> int:
    """
    Interactively create a new agent job YAML file by:
      1. Reading the wizard registry to find registered source root(s) and targets.
      2. Auto-scanning the source root for feature folders and letting the user
         pick one (or type a custom path to override).
      3. Letting the user select a migration target from the registered list.
      4. Reading the target's template YAML (agent-prompts/_template_<id>.yaml)
         and substituting the feature placeholders with the chosen values.
      5. Writing the result to agent-prompts/migrate-<feature>-<target>.yaml.

    CLI shortcuts
    -------------
    --target <id>       Pre-select a registered target (skip target prompt).
    --feature <path>    Pre-fill the feature folder path / search filter.
    """
    import re as _re
    from wizard.registry import load_registry
    from wizard.collector import (
        collect_feature_selection,
        _safe_print as _col_print,
        _safe_input as _col_input,
        _yes_no,
    )

    registry = load_registry()
    targets  = registry.get("targets", {})

    if not targets:
        print("[ERROR] No migration targets configured yet.", file=sys.stderr)
        print(
            "        Run:  python run_agent.py --setup  to configure a target first.",
            file=sys.stderr,
        )
        return 1

    # ------------------------------------------------------------------
    # 1. Resolve source_root from registry
    # ------------------------------------------------------------------
    # Collect unique, non-placeholder source roots keyed by source_name.
    source_entries: dict[str, str] = {}  # source_name → source_root
    for tid, info in targets.items():
        sname = info.get("source_name") or tid
        sroot = info.get("source_root") or ""
        if sroot and sroot not in ("<YOUR_SOURCE_ROOT>", ""):
            source_entries[sname] = sroot

    if not source_entries:
        print("[ERROR] Source root path not found in wizard registry.", file=sys.stderr)
        print(
            "        Re-run:  python run_agent.py --setup  and enter your local source path.",
            file=sys.stderr,
        )
        return 1

    if len(source_entries) == 1:
        source_name, source_root = next(iter(source_entries.items()))
    else:
        entries_list = list(source_entries.items())
        _col_print("\n  Registered source codebases:\n")
        for i, (sname, sroot) in enumerate(entries_list, start=1):
            _col_print(f"    {i:>3}.  {sname}  —  {sroot}")
        print()
        while True:
            raw = _col_input(f"Select source [1-{len(entries_list)}]", "1")
            if raw.isdigit():
                idx = int(raw)
                if 1 <= idx <= len(entries_list):
                    source_name, source_root = entries_list[idx - 1]
                    break
            _col_print(f"  (Choose 1–{len(entries_list)})")

    if not Path(source_root).is_dir():
        print(f"[ERROR] Source root not found on disk: {source_root}", file=sys.stderr)
        print(
            "        Update config/wizard-registry.json with your local source_root path,\n"
            "        or re-run the setup wizard.",
            file=sys.stderr,
        )
        return 1

    # ------------------------------------------------------------------
    # 2. Select feature folder (auto-scan + user choice or override)
    # ------------------------------------------------------------------
    prefill_feature = getattr(args, "feature", None) or ""
    feature_path, feature_name = collect_feature_selection(
        source_root, prefill=prefill_feature
    )

    # ------------------------------------------------------------------
    # 3. Pick migration target
    # ------------------------------------------------------------------
    target_arg = getattr(args, "target", None)

    if target_arg and target_arg in targets:
        target_id = target_arg
        _col_print(f"\n  Target: {target_id}")
    elif len(targets) == 1:
        target_id = next(iter(targets))
        _col_print(f"\n  Using the only configured target: {target_id}")
    else:
        target_list = list(targets.items())
        _col_print("\n  Configured migration targets:\n")
        for i, (tid, info) in enumerate(target_list, start=1):
            pair = info.get("framework_pair", "?")
            _col_print(f"    {i:>3}.  {tid}  ({pair})")
        print()
        while True:
            raw = _col_input(f"Select target [1-{len(target_list)}]", "1")
            if raw.isdigit():
                idx = int(raw)
                if 1 <= idx <= len(target_list):
                    target_id = target_list[idx - 1][0]
                    break
            _col_print(f"  (Choose 1–{len(target_list)})")

    # ------------------------------------------------------------------
    # 4. Determine output filename
    # ------------------------------------------------------------------
    safe_feature = feature_name.lower().replace(" ", "-").replace("_", "-")
    default_filename = f"migrate-{safe_feature}-{target_id}.yaml"

    _col_print(f"\n  Feature:  {feature_name}")
    _col_print(f"  Path:     {feature_path}")
    _col_print(f"  Target:   {target_id}")
    _col_print(f"  Output:   agent-prompts/{default_filename}")
    print()

    out_raw  = _col_input("Output filename (in agent-prompts/)", default_filename)
    out_path = ROOT / "agent-prompts" / out_raw

    if out_path.exists():
        _col_print(f"\n  [WARN] File already exists: {out_path.name}")
        if not _yes_no("Overwrite?", default=False):
            print("  Cancelled.")
            return 0

    # ------------------------------------------------------------------
    # 5. Load template and substitute placeholders
    # ------------------------------------------------------------------
    template_path = ROOT / "agent-prompts" / f"_template_{target_id}.yaml"
    if not template_path.exists():
        print(f"[ERROR] Template not found: {template_path}", file=sys.stderr)
        print(
            "        Re-run the setup wizard to regenerate it:\n"
            "          python run_agent.py --setup --overwrite",
            file=sys.stderr,
        )
        return 1

    content = template_path.read_text(encoding="utf-8")

    # Replace the feature_root line (handles any placeholder or path in quotes)
    content = _re.sub(
        r'(feature_root:\s*)".+"',
        f'feature_root: "{feature_path}"',
        content,
    )
    # Replace the feature_name line
    content = _re.sub(
        r'(feature_name:\s*)".+"',
        f'feature_name: "{feature_name}"',
        content,
    )
    # Replace any remaining <FeatureName> tokens in comments / strings
    content = content.replace("<FeatureName>", feature_name)

    # Update the job.name line
    content = _re.sub(
        r'(name:\s*"Migrate ).*?(")',
        f'\\g<1>{feature_name} -> {target_id}\\g<2>',
        content,
        count=1,
    )

    # ------------------------------------------------------------------
    # 6. Write job file
    # ------------------------------------------------------------------
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")

    _safe_print(f"\n  [OK] Job file created: {out_path}")
    _safe_print("\n  Next steps:")
    _safe_print(f"    1. Review:  {out_path}")
    _safe_print(
        f"    2. Run (plan mode, generates Plan Document — no code written):\n"
        f"         python run_agent.py --job agent-prompts/{out_path.name}"
    )
    _safe_print(
        "    3. After approving the plan, edit the job file → set  mode: full  and re-run."
    )
    print()
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI Migration Tool — Agent mode (reads YAML job files)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--job", "-j",
        type=str,
        metavar="PATH",
        help="Path to a YAML job file (e.g. agent-prompts/migrate-action-history.yaml).",
    )
    parser.add_argument(
        "--list-jobs", "-l",
        action="store_true",
        help="List all available job files in agent-prompts/ and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Override: run full pipeline but write NO files (overrides job file).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=None,
        help="Override: force a fresh run even if a completed run exists (overrides job file).",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        default=None,
        help="Override: skip the human approval gate (overrides job file — TESTING ONLY).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )

    # ---- New-job creation flags ----
    parser.add_argument(
        "--new-job",
        action="store_true",
        help=(
            "Interactively create a new agent job YAML file. "
            "Scans the registered source codebase for feature folders, "
            "lets you pick one (or type a custom path), choose a migration "
            "target, and writes the job file to agent-prompts/. "
            "Requires the setup wizard to have been run at least once "
            "(python run_agent.py --setup)."
        ),
    )
    parser.add_argument(
        "--feature",
        type=str,
        default=None,
        metavar="PATH_OR_NAME",
        help=(
            "[--new-job only] Pre-fill the feature folder path or name. "
            "Can be an absolute path, a path relative to the source root, "
            "or a folder name used as a search hint."
        ),
    )
    parser.add_argument(
        "--target",
        type=str,
        default=None,
        metavar="TARGET_ID",
        help=(
            "[--new-job only] Pre-select a registered migration target by its ID "
            "(e.g. 'hrsa_pprs', 'modern'). Skip the interactive target prompt."
        ),
    )

    # ---- Setup Wizard flags ----
    parser.add_argument(
        "--setup",
        action="store_true",
        help=(
            "Run the first-run setup wizard to configure a new Source -> Target "
            "migration pair. Generates custom prompts, config entries, and a job "
            "template for the new target. Delegates to setup_wizard.py."
        ),
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "[--setup only] Path to a JSON file with pre-filled wizard answers. "
            "Example: agent-prompts/example-wizard-config.json"
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="[--setup only] Overwrite existing wizard-generated files.",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="[--setup only] List all configured migration targets and exit.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "[--setup only] Run wizard without user prompts. "
            "Requires --config with all required keys."
        ),
    )

    args = parser.parse_args()

    # ---- New-job creation mode ----
    if args.new_job:
        return _run_new_job(args)

    # ---- Setup wizard mode ----
    if args.setup or args.list_targets:
        return _run_setup_wizard(args)

    if args.list_jobs:
        _list_jobs()
        return 0

    if not args.job:
        parser.error(
            "Provide a job file with --job, or use --list-jobs to see available jobs.\n"
            "Use --setup to configure a new migration target.\n"
            "Example:  python run_agent.py --job agent-prompts/migrate-action-history.yaml"
        )

    job_path = Path(args.job)
    if not job_path.is_absolute():
        job_path = ROOT / job_path
    if not job_path.exists():
        print(f"[ERROR] Job file not found: {job_path}", file=sys.stderr)
        return 1

    # Load and parse the job file
    try:
        job = _load_yaml(job_path)
    except Exception as exc:
        print(f"[ERROR] Failed to parse job file {job_path}: {exc}", file=sys.stderr)
        return 1

    # CLI overrides (only forward flags that were explicitly set)
    overrides: dict = {}
    if args.dry_run:
        overrides["dry_run"] = True
    if args.force:
        overrides["force"] = True
    if args.auto_approve:
        overrides["auto_approve"] = True
    if args.verbose:
        overrides["verbose"] = True

    # Build a namespace that looks like main.py's argparse output
    ns = _job_to_args(job, overrides)

    # Validate required fields
    if not ns.feature_root:
        print("[ERROR] Job file must set pipeline.feature_root", file=sys.stderr)
        return 1
    if not Path(ns.feature_root).exists():
        print(f"[ERROR] feature_root does not exist: {ns.feature_root}", file=sys.stderr)
        return 1

    # Configure logging
    from main import configure_logging, build_llm_router, _run_pipeline_with_router, print_banner
    configure_logging(verbose=ns.verbose)

    # Print startup banner
    llm_router = build_llm_router(ns)
    from main import _describe_router
    print_banner("AI Migration Tool v1.1 — Agent Mode")
    _print_job_summary(job, ns)
    print(f"  LLM: {_describe_router(llm_router)}")
    print()

    # Signal to PlanAgent / ConversionAgent that we are running via run_agent.py
    # (agent mode). This enables soft-fail on LLM errors — template scaffold is
    # returned instead of raising LLMConfigurationError.
    os.environ["AI_AGENT_MODE"] = "1"

    # Run the pipeline
    return _run_pipeline_with_router(ns, llm_router)


if __name__ == "__main__":
    sys.exit(main())

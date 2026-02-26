#!/usr/bin/env python3
"""
run_agent.py — Agent-mode entry point for the AI Migration Tool
===============================================================
Reads a YAML job file and executes the migration pipeline.  Designed to be
invoked by AI coding agents (Cursor, Windsurf, Copilot, AntiGravity) without
requiring the agent to know the full pipeline internals.

── Run a job ──────────────────────────────────────────────────────────────────
  python run_agent.py --job agent-prompts/migrate-action-history.yaml
  python run_agent.py --job <file> --mode plan          # override mode
  python run_agent.py --job <file> --mode full          # run full conversion
  python run_agent.py --job <file> --dry-run            # log only, no writes
  python run_agent.py --job <file> --force              # re-run from scratch
  python run_agent.py --list-jobs                       # list available jobs

── Feature discovery (agent helpers) ─────────────────────────────────────────
  python run_agent.py --list-features                   # list features in source
  python run_agent.py --list-features --json            # JSON output
  python run_agent.py --list-features --source /path   # explicit source root

── Create a new job ───────────────────────────────────────────────────────────
  python run_agent.py --new-job                                   # interactive
  python run_agent.py --new-job --feature ActionHistory --target hrsa_pprs
  python run_agent.py --new-job --feature ActionHistory --target hrsa_pprs \\
                      --non-interactive --json                    # agent mode

── Plan management ────────────────────────────────────────────────────────────
  python run_agent.py --status --job <file>             # migration status
  python run_agent.py --status --job <file> --json      # JSON status
  python run_agent.py --approve-plan --job <file>       # approve pending plan
  python run_agent.py --revise-plan  --job <file> --feedback "Step B1 is wrong..."
                                                         # regenerate with notes

── Setup wizard ───────────────────────────────────────────────────────────────
  python run_agent.py --setup                           # configure source→target
  python run_agent.py --setup --config wizard.json     # pre-filled answers
  python run_agent.py --setup --dry-run                # preview only
  python run_agent.py --setup --list-targets           # list configured targets

── Typical agent workflow ─────────────────────────────────────────────────────
  1. python run_agent.py --list-features --json
  2. python run_agent.py --new-job --feature ActionHistory --target hrsa_pprs \\
                         --non-interactive --json
  3. python run_agent.py --job agent-prompts/migrate-actionhistory-hrsa_pprs.yaml
       (runs plan mode by default → saves plan to plans/)
  4. python run_agent.py --status --job ... --json
  5a. python run_agent.py --approve-plan --job ...
      python run_agent.py --job ... --mode full
  5b. python run_agent.py --revise-plan --job ... --feedback "..."
      (back to step 4)
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
# Agent-facing commands
# ---------------------------------------------------------------------------

def _run_list_features(args) -> int:
    """
    Scan the registered (or explicitly supplied) source root for feature
    folders and print them as a numbered list or JSON array.

    Output modes
    ------------
    Default (text):  numbered list relative to the source root
    --json:          { "source_root": "...", "count": N, "features": [...] }

    The source root is resolved in this priority order:
      1. --source PATH argument
      2. First non-placeholder source_root found in wizard-registry.json
    """
    import json as _json
    from wizard.registry import load_registry
    from wizard.collector import detect_feature_folders

    source = getattr(args, "source", None)
    if not source:
        registry = load_registry()
        for _tid, info in registry.get("targets", {}).items():
            sroot = info.get("source_root") or ""
            if sroot and sroot not in ("<YOUR_SOURCE_ROOT>", ""):
                source = sroot
                break

    if not source:
        print(
            "[ERROR] No source root found.\n"
            "        Use --source PATH, or run --setup to register one.",
            file=sys.stderr,
        )
        return 1

    source_path = Path(source)
    if not source_path.is_dir():
        print(f"[ERROR] Source root not found on disk: {source}", file=sys.stderr)
        return 1

    features = detect_feature_folders(source_path)

    if getattr(args, "json_output", False):
        result = {
            "source_root": str(source_path),
            "count":       len(features),
            "features": [
                {
                    "name":     f.name,
                    "path":     str(f),
                    "relative": str(f.relative_to(source_path)),
                }
                for f in features
            ],
        }
        print(_json.dumps(result, indent=2))
    else:
        _safe_print(f"\n  Source root:  {source_path}")
        _safe_print(f"  Features found: {len(features)}\n")
        for i, feat in enumerate(features, 1):
            try:
                rel = feat.relative_to(source_path)
            except ValueError:
                rel = feat
            _safe_print(f"    {i:>3}.  {rel}")
        print()
        if features:
            _safe_print(
                "  Create a job for a feature:\n"
                "    python run_agent.py --new-job --feature <name> --target <id>"
            )
        else:
            _safe_print("  No feature folders detected.  Ensure the source root is correct.")
        print()

    return 0


def _run_status(args) -> int:
    """
    Print the migration status for a given job file: whether a plan has been
    generated, whether it has been approved, and the checkpoint state.

    Output modes
    ------------
    Default (text):  human-readable summary
    --json:          full status dict
    """
    import json as _json
    from main import DEFAULT_CHECKPOINTS_DIR, DEFAULT_PLANS_DIR, DEFAULT_LOGS_DIR, _stable_run_id

    job_path = Path(args.job)
    if not job_path.is_absolute():
        job_path = ROOT / job_path
    if not job_path.exists():
        print(f"[ERROR] Job file not found: {job_path}", file=sys.stderr)
        return 1

    try:
        job = _load_yaml(job_path)
    except Exception as exc:
        print(f"[ERROR] Cannot parse job file: {exc}", file=sys.stderr)
        return 1

    ns     = _job_to_args(job)
    target = ns.target or "simpler_grants"

    if not ns.feature_name or not ns.feature_root:
        print("[ERROR] Job file must set pipeline.feature_name and pipeline.feature_root.", file=sys.stderr)
        return 1

    run_id          = _stable_run_id(ns.feature_name, ns.feature_root, target)
    checkpoint_path = DEFAULT_CHECKPOINTS_DIR / f"{run_id}-checkpoint.json"
    output_root     = Path(ns.output_root or ROOT / "output" / ns.feature_name)
    approval_marker = output_root / ".approved"

    plan_files = sorted(
        DEFAULT_PLANS_DIR.glob(f"*{run_id[:8]}*.md"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    ) if DEFAULT_PLANS_DIR.exists() else []

    checkpoint_state: dict = {}
    if checkpoint_path.exists():
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                checkpoint_state = _json.load(f)
        except Exception:
            pass

    approval_info: dict = {}
    if approval_marker.exists():
        try:
            with open(approval_marker, "r", encoding="utf-8") as f:
                approval_info = _json.load(f)
        except Exception:
            approval_info = {"approved_at": "unknown"}

    completed = checkpoint_state.get("completed_steps", [])
    pending   = checkpoint_state.get("pending_steps",   [])
    blocked   = checkpoint_state.get("blocked_steps",   [])

    status = {
        "job_file":          str(job_path),
        "feature":           ns.feature_name,
        "feature_root":      ns.feature_root,
        "target":            target,
        "run_id":            run_id,
        "plan_generated":    bool(plan_files),
        "plan_files":        [str(p) for p in plan_files],
        "plan_approved":     bool(approval_info),
        "approval_info":     approval_info,
        "conversion_started": bool(completed),
        "completed_steps":   completed,
        "pending_steps":     pending,
        "blocked_steps":     blocked,
        "checkpoint_path":   str(checkpoint_path) if checkpoint_path.exists() else None,
    }

    if getattr(args, "json_output", False):
        print(_json.dumps(status, indent=2))
    else:
        _safe_print(f"\n  Job:     {job_path.name}")
        _safe_print(f"  Feature: {ns.feature_name}")
        _safe_print(f"  Target:  {target}")
        _safe_print(f"  Run ID:  {run_id}")
        print()
        plan_label = f"YES  {plan_files[0]}" if plan_files else "No"
        _safe_print(f"  Plan generated:    {plan_label}")
        if approval_info:
            _safe_print(f"  Plan approved:     YES  ({approval_info.get('approved_at', '?')})")
        else:
            _safe_print("  Plan approved:     No  (run --approve-plan to approve)")
        if completed or pending or blocked:
            _safe_print(f"  Completed steps:   {len(completed)}")
            _safe_print(f"  Pending steps:     {len(pending)}")
            if blocked:
                _safe_print(f"  Blocked steps:     {len(blocked)}  [!] need human review")
        else:
            _safe_print("  Conversion:        Not started")
        print()
        # Suggest next action
        if not plan_files:
            _safe_print("  Next: generate a plan:")
            _safe_print(f"    python run_agent.py --job {job_path.name} [--mode plan]")
        elif not approval_info:
            _safe_print("  Next: review the plan, then approve it:")
            _safe_print(f"    python run_agent.py --approve-plan --job {job_path.name}")
            _safe_print("  Or revise it:")
            _safe_print(f'    python run_agent.py --revise-plan --job {job_path.name} --feedback "..."')
        elif pending:
            _safe_print("  Next: run the full conversion:")
            _safe_print(f"    python run_agent.py --job {job_path.name} --mode full")
        print()

    return 0


def _run_approve_plan(args) -> int:
    """
    Approve the pending plan for a job by writing an approval marker file at
    output/<feature_name>/.approved.  The next run of the job in full mode
    will detect this marker and proceed without prompting.

    The --feedback argument is stored in the marker but does not affect
    the plan content (use --revise-plan if you want changes).
    """
    import json as _json
    from datetime import datetime, timezone

    job_path = Path(args.job)
    if not job_path.is_absolute():
        job_path = ROOT / job_path
    if not job_path.exists():
        print(f"[ERROR] Job file not found: {job_path}", file=sys.stderr)
        return 1

    try:
        job = _load_yaml(job_path)
    except Exception as exc:
        print(f"[ERROR] Cannot parse job file: {exc}", file=sys.stderr)
        return 1

    ns          = _job_to_args(job)
    output_root = Path(ns.output_root or ROOT / "output" / ns.feature_name)
    output_root.mkdir(parents=True, exist_ok=True)
    marker_path = output_root / ".approved"

    marker_data = {
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": "agent",
        "job_file":    str(job_path),
        "feature":     ns.feature_name,
        "target":      ns.target or "simpler_grants",
        "notes":       getattr(args, "feedback", None) or "",
    }
    marker_path.write_text(_json.dumps(marker_data, indent=2), encoding="utf-8")

    if getattr(args, "json_output", False):
        print(_json.dumps({
            "approved":     True,
            "marker_path":  str(marker_path),
            "approved_at":  marker_data["approved_at"],
            "feature":      ns.feature_name,
        }, indent=2))
    else:
        _safe_print(f"\n  [OK] Plan approved for:  {ns.feature_name}")
        _safe_print(f"       Marker written to:  {marker_path}")
        _safe_print("\n  Next: run the full conversion:")
        _safe_print(f"    python run_agent.py --job {job_path.name} --mode full")
        print()

    return 0


def _run_revise_plan(args) -> int:
    """
    Regenerate the migration plan with revision feedback incorporated.

    Workflow
    --------
    1. Load the job YAML and build the pipeline namespace.
    2. Run Config Ingestion (Step 1).
    3. Load the existing dependency graph from logs/ if available, otherwise
       re-run Scoping (Step 2).
    4. Load the most-recent existing plan (for context).
    5. Run PlanAgent in revision mode — injects ``revision_notes`` and the
       original plan into the LLM prompt so the model can address feedback.
    6. Save the revised plan with a ``-rev-`` filename suffix.

    The original plan file is preserved.  The approval marker (if any) is
    removed so the revised plan requires explicit re-approval.
    """
    import json as _json

    job_path = Path(args.job)
    if not job_path.is_absolute():
        job_path = ROOT / job_path
    if not job_path.exists():
        print(f"[ERROR] Job file not found: {job_path}", file=sys.stderr)
        return 1

    feedback = getattr(args, "feedback", None) or ""
    if not feedback:
        print(
            "[ERROR] --revise-plan requires --feedback with a non-empty description of changes.",
            file=sys.stderr,
        )
        return 1

    try:
        job = _load_yaml(job_path)
    except Exception as exc:
        print(f"[ERROR] Cannot parse job file: {exc}", file=sys.stderr)
        return 1

    ns = _job_to_args(job)
    if not ns.feature_root or not Path(ns.feature_root).is_dir():
        print(f"[ERROR] feature_root not found: {ns.feature_root}", file=sys.stderr)
        return 1

    from main import (
        configure_logging, build_llm_router, _stable_run_id,
        DEFAULT_LOGS_DIR, DEFAULT_PLANS_DIR,
    )
    from agents.config_ingestion_agent import ConfigIngestionAgent, ConfigValidationError
    from agents.scoping_agent import ScopingAgent
    from agents.plan_agent import PlanAgent

    configure_logging(verbose=getattr(args, "verbose", False))

    target = ns.target or "simpler_grants"
    run_id = _stable_run_id(ns.feature_name, ns.feature_root, target)

    # ---- Step 1: Config ----
    _safe_print(f"\n  Loading config...")
    try:
        config = ConfigIngestionAgent(ns.skillset_config, ns.rules_config).load_and_validate()
    except (FileNotFoundError, ConfigValidationError) as exc:
        print(f"[ERROR] Config load failed: {exc}", file=sys.stderr)
        return 1

    # ---- Step 2: Dependency graph ----
    graph_path = DEFAULT_LOGS_DIR / f"{run_id}-dependency-graph.json"
    if graph_path.exists():
        _safe_print(f"  Loading existing dependency graph: {graph_path.name}")
        with open(graph_path, "r", encoding="utf-8") as f:
            dependency_graph = _json.load(f)
    else:
        _safe_print(f"  Running scoping agent on: {ns.feature_root}")
        scoping = ScopingAgent(feature_root=ns.feature_root, config=config)
        try:
            dependency_graph = scoping.analyze()
        except FileNotFoundError as exc:
            print(f"[ERROR] Scoping failed: {exc}", file=sys.stderr)
            return 1
        scoping.save(graph_path)

    # ---- Step 3: Load original plan for context ----
    slug           = __import__("re").sub(r"[^\w-]", "-", ns.feature_name.lower())
    existing_plans = sorted(
        DEFAULT_PLANS_DIR.glob(f"{slug}-plan-*-{run_id[:8]}*.md"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    ) if DEFAULT_PLANS_DIR.exists() else []
    original_plan = existing_plans[0].read_text(encoding="utf-8") if existing_plans else ""

    if not original_plan:
        _safe_print("  [WARN] No original plan found — generating a fresh plan with feedback injected.")

    # ---- Step 4: PlanAgent in revision mode ----
    _safe_print(f"  Generating revised plan (feedback: {feedback[:60]}{'…' if len(feedback)>60 else ''})")
    llm_router = build_llm_router(ns)
    plan_agent = PlanAgent(
        dependency_graph=dependency_graph,
        config=config,
        run_id=run_id,
        plans_dir=DEFAULT_PLANS_DIR,
        llm_router=llm_router,
        target=target,
        revision_notes=feedback,
        original_plan=original_plan,
    )
    plan_md, plan_path = plan_agent.generate()

    # ---- Step 5: Remove stale approval marker ----
    output_root     = Path(ns.output_root or ROOT / "output" / ns.feature_name)
    approval_marker = output_root / ".approved"
    if approval_marker.exists():
        approval_marker.unlink()
        _safe_print("  [INFO] Stale approval marker removed — revised plan requires re-approval.")

    if getattr(args, "json_output", False):
        print(_json.dumps({
            "revised_plan_path": str(plan_path),
            "feature":           ns.feature_name,
            "run_id":            run_id,
            "feedback_applied":  feedback,
        }, indent=2))
    else:
        _safe_print(f"\n  [OK] Revised plan saved: {plan_path}")
        _safe_print("\n  Next: review the revised plan, then approve it:")
        _safe_print(f"    python run_agent.py --approve-plan --job {job_path.name}")
        _safe_print("  Or revise again:")
        _safe_print(f'    python run_agent.py --revise-plan --job {job_path.name} --feedback "..."')
        print()

    return 0


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
    Create a new agent job YAML file — interactive by default, fully
    non-interactive when ``--non-interactive`` is supplied together with
    ``--feature`` and ``--target``.

    Interactive mode
    ----------------
    1. Read wizard registry → resolve registered source root(s).
    2. Scan source root for feature folders and show a numbered list.
       User picks a number or types a custom path to override.
    3. Show registered targets; user picks one.
    4. Confirm output filename.
    5. Read target template, substitute placeholders, write job YAML.

    Non-interactive mode (``--non-interactive --feature X --target Y``)
    -------------------------------------------------------------------
    All prompts are skipped.  ``--feature`` is resolved as:
      • an absolute path if it starts with a drive letter or '/'
      • a path relative to source_root otherwise
      • a folder name matched against detected feature folders (first match)
    The output filename uses the default ``migrate-<feature>-<target>.yaml``.
    Any existing file at that path is silently overwritten.

    Output
    ------
    With ``--json``: prints ``{ "job_file": "...", "feature": "...", ... }``
    """
    import json as _json
    import re as _re
    from wizard.registry import load_registry
    from wizard.collector import (
        collect_feature_selection,
        detect_feature_folders,
        _safe_print as _col_print,
        _safe_input as _col_input,
        _yes_no,
    )

    non_interactive = getattr(args, "non_interactive", False)
    json_output     = getattr(args, "json_output", False)
    registry        = load_registry()
    targets         = registry.get("targets", {})

    if not targets:
        print("[ERROR] No migration targets configured yet.", file=sys.stderr)
        print(
            "        Run:  python run_agent.py --setup  to configure a target first.",
            file=sys.stderr,
        )
        return 1

    if non_interactive and not (getattr(args, "feature", None) and getattr(args, "target", None)):
        print(
            "[ERROR] --non-interactive requires both --feature and --target.",
            file=sys.stderr,
        )
        return 1

    # ------------------------------------------------------------------
    # 1. Resolve source_root
    # ------------------------------------------------------------------
    source_entries: dict[str, str] = {}
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

    if len(source_entries) == 1 or non_interactive:
        source_name, source_root = next(iter(source_entries.items()))
        if not non_interactive:
            _col_print(f"\n  Source: {source_name}  ({source_root})")
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
    # 2. Select feature folder
    # ------------------------------------------------------------------
    feature_arg = getattr(args, "feature", None) or ""

    if non_interactive:
        # Try to resolve feature_arg as: absolute path, source-relative path,
        # or folder name matched against detected folders.
        fp = Path(feature_arg)
        if fp.is_absolute() and fp.is_dir():
            feature_path = str(fp)
            feature_name = fp.name
        elif (Path(source_root) / feature_arg).is_dir():
            resolved = Path(source_root) / feature_arg
            feature_path = str(resolved)
            feature_name = resolved.name
        else:
            # Match by folder name (case-insensitive prefix match)
            candidates = detect_feature_folders(source_root)
            matches = [f for f in candidates if f.name.lower() == feature_arg.lower()]
            if not matches:
                matches = [f for f in candidates if f.name.lower().startswith(feature_arg.lower())]
            if not matches:
                print(
                    f"[ERROR] --feature '{feature_arg}' did not match any feature folder under:\n"
                    f"        {source_root}",
                    file=sys.stderr,
                )
                print(
                    "        Use an absolute path, a source-relative path, or a folder name.\n"
                    "        Run --list-features to see available features.",
                    file=sys.stderr,
                )
                return 1
            feature_path = str(matches[0])
            feature_name = matches[0].name
    else:
        feature_path, feature_name = collect_feature_selection(
            source_root, prefill=feature_arg
        )

    # ------------------------------------------------------------------
    # 3. Pick migration target
    # ------------------------------------------------------------------
    target_arg = getattr(args, "target", None)

    if target_arg and target_arg in targets:
        target_id = target_arg
        if not non_interactive:
            _col_print(f"\n  Target: {target_id}")
    elif len(targets) == 1:
        target_id = next(iter(targets))
        if not non_interactive:
            _col_print(f"\n  Using the only configured target: {target_id}")
    elif non_interactive:
        print(
            f"[ERROR] --target '{target_arg}' not found in registry.\n"
            f"        Available: {', '.join(targets.keys())}",
            file=sys.stderr,
        )
        return 1
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
    safe_feature     = feature_name.lower().replace(" ", "-").replace("_", "-")
    default_filename = f"migrate-{safe_feature}-{target_id}.yaml"

    if non_interactive:
        out_path = ROOT / "agent-prompts" / default_filename
    else:
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
    content = _re.sub(r'(feature_root:\s*)".+"', f'feature_root: "{feature_path}"', content)
    content = _re.sub(r'(feature_name:\s*)".+"', f'feature_name: "{feature_name}"', content)
    content = content.replace("<FeatureName>", feature_name)
    content = _re.sub(
        r'(name:\s*"Migrate ).*?(")',
        f'\\g<1>{feature_name} -> {target_id}\\g<2>',
        content, count=1,
    )

    # ------------------------------------------------------------------
    # 6. Write job file
    # ------------------------------------------------------------------
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")

    if json_output:
        print(_json.dumps({
            "job_file":    str(out_path),
            "feature":     feature_name,
            "feature_path": feature_path,
            "target":      target_id,
            "template":    str(template_path),
        }, indent=2))
    else:
        _safe_print(f"\n  [OK] Job file created: {out_path}")
        _safe_print("\n  Next steps:")
        _safe_print(f"    1. Review:   {out_path}")
        _safe_print(
            f"    2. Generate plan (no code written):\n"
            f"         python run_agent.py --job agent-prompts/{out_path.name}"
        )
        _safe_print(
            f"    3. Approve:  python run_agent.py --approve-plan "
            f"--job agent-prompts/{out_path.name}"
        )
        _safe_print(
            f"    4. Convert:  python run_agent.py --job agent-prompts/{out_path.name} --mode full"
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

    # ── Job creation ──────────────────────────────────────────────────────────
    parser.add_argument(
        "--new-job",
        action="store_true",
        help=(
            "Create a new job YAML from the registered source codebase. "
            "Interactive by default; combine with --feature, --target, "
            "--non-interactive for fully agent-driven creation."
        ),
    )
    parser.add_argument(
        "--feature",
        type=str, default=None, metavar="PATH_OR_NAME",
        help=(
            "[--new-job] Feature folder path (absolute or source-relative), "
            "or folder name for auto-detection."
        ),
    )
    parser.add_argument(
        "--target",
        type=str, default=None, metavar="TARGET_ID",
        help="[--new-job] Pre-select a registered migration target (e.g. 'hrsa_pprs').",
    )

    # ── Plan management (agent helpers) ──────────────────────────────────────
    parser.add_argument(
        "--list-features",
        action="store_true",
        help=(
            "Scan the registered source root for feature folders and list them. "
            "Use --source to specify a different root. Add --json for machine output."
        ),
    )
    parser.add_argument(
        "--source",
        type=str, default=None, metavar="PATH",
        help="[--list-features] Explicit source root to scan (overrides registry).",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help=(
            "Show migration status for a job: plan generated, approved, "
            "conversion progress.  Requires --job.  Add --json for machine output."
        ),
    )
    parser.add_argument(
        "--approve-plan",
        action="store_true",
        help=(
            "Approve the pending plan for a job by writing an approval marker. "
            "Subsequent runs with --mode full will proceed without prompting. "
            "Requires --job."
        ),
    )
    parser.add_argument(
        "--revise-plan",
        action="store_true",
        help=(
            "Regenerate the plan incorporating --feedback notes. "
            "Preserves the original plan file; writes a new -rev- version. "
            "Requires --job and --feedback."
        ),
    )
    parser.add_argument(
        "--feedback",
        type=str, default=None, metavar="TEXT",
        help=(
            "[--revise-plan / --approve-plan] Feedback notes. "
            "For --revise-plan, these are injected into the LLM prompt. "
            "For --approve-plan, stored as a comment in the approval marker."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true", dest="json_output",
        help=(
            "Output results as JSON (for --list-features, --new-job, "
            "--status, --approve-plan, --revise-plan).  Suitable for agent parsing."
        ),
    )

    # ── Job execution overrides ───────────────────────────────────────────────
    parser.add_argument(
        "--mode",
        type=str, default=None, choices=["scope", "plan", "full"],
        metavar="MODE",
        help=(
            "Override the pipeline mode from the job file "
            "(scope | plan | full). Useful for agents that want to run "
            "just the plan step without editing the YAML."
        ),
    )

    # ── Setup wizard flags ────────────────────────────────────────────────────
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
        type=str, default=None, metavar="PATH",
        help=(
            "[--setup] Path to a JSON file with pre-filled wizard answers. "
            "Example: agent-prompts/example-wizard-config.json"
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="[--setup] Overwrite existing wizard-generated files.",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="[--setup] List all configured migration targets and exit.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "[--setup / --new-job] Skip interactive prompts. "
            "For --new-job, requires --feature and --target. "
            "For --setup, requires --config with all required keys."
        ),
    )

    args = parser.parse_args()

    # ── Dispatch ─────────────────────────────────────────────────────────────

    if args.list_features:
        return _run_list_features(args)

    if args.new_job:
        return _run_new_job(args)

    if args.setup or args.list_targets:
        return _run_setup_wizard(args)

    if args.list_jobs:
        _list_jobs()
        return 0

    # Plan-management commands — all require --job
    if args.status or args.approve_plan or args.revise_plan:
        if not args.job:
            parser.error(
                "--status / --approve-plan / --revise-plan all require --job <path>."
            )
        if args.status:
            return _run_status(args)
        if args.approve_plan:
            return _run_approve_plan(args)
        if args.revise_plan:
            return _run_revise_plan(args)

    if not args.job:
        parser.error(
            "Provide a job file with --job, or use --list-jobs / --new-job / --status.\n"
            "Use --setup to configure a new migration target.\n"
            "Example:  python run_agent.py --job agent-prompts/migrate-action-history.yaml"
        )

    job_path = Path(args.job)
    if not job_path.is_absolute():
        job_path = ROOT / job_path
    if not job_path.exists():
        print(f"[ERROR] Job file not found: {job_path}", file=sys.stderr)
        return 1

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

    ns = _job_to_args(job, overrides)

    # Apply --mode override (lets agents change mode without editing the YAML)
    if args.mode:
        ns.mode = args.mode

    # Validate required fields
    if not ns.feature_root:
        print("[ERROR] Job file must set pipeline.feature_root", file=sys.stderr)
        return 1
    if not Path(ns.feature_root).exists():
        print(f"[ERROR] feature_root does not exist: {ns.feature_root}", file=sys.stderr)
        return 1

    # Check for out-of-band plan approval marker (written by --approve-plan)
    output_root     = Path(ns.output_root or ROOT / "output" / ns.feature_name)
    approval_marker = output_root / ".approved"
    if approval_marker.exists() and not ns.auto_approve:
        _safe_print(
            f"  [INFO] Plan approval marker found ({approval_marker.name}) "
            "— treating as auto-approved."
        )
        ns.auto_approve = True

    # Configure logging
    from main import configure_logging, build_llm_router, _run_pipeline_with_router, print_banner
    configure_logging(verbose=ns.verbose)

    llm_router = build_llm_router(ns)
    from main import _describe_router
    print_banner("AI Migration Tool v1.1 — Agent Mode")
    _print_job_summary(job, ns)
    print(f"  LLM: {_describe_router(llm_router)}")
    print()

    # Signal agent mode — soft-fail on LLM errors (template scaffold instead of hard crash)
    os.environ["AI_AGENT_MODE"] = "1"

    return _run_pipeline_with_router(ns, llm_router)


if __name__ == "__main__":
    sys.exit(main())

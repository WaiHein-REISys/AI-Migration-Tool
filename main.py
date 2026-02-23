#!/usr/bin/env python3
"""
AI Migration Tool -- CLI Runner
===============================
Multi-agent pipeline for migrating HAB-GPRSSubmission (Angular 2 / ASP.NET Core)
to the simpler-grants-gov target stack (Next.js 15 / Python Flask / SQLAlchemy 2.0).

Usage:
    python main.py --feature-root <path> --feature-name <name> [OPTIONS]

Examples:
    # Analyse a feature and generate a plan (no code written)
    python main.py --feature-root "Y:/Solution/HRSA/HAB-GPRSSubmission/src/GPRSSubmission.Web/wwwroot/gprs_app/ActionHistory" --feature-name "ActionHistory" --mode plan

    # Full pipeline with CLI approval gate
    python main.py --feature-root "Y:/Solution/HRSA/HAB-GPRSSubmission/src/GPRSSubmission.Web/wwwroot/gprs_app/ActionHistory" --feature-name "ActionHistory" --mode full

    # Full pipeline with a local Ollama model
    python main.py --feature-root "..." --feature-name "ActionHistory" --mode full --llm-provider ollama --llm-model llama3.2

    # Full pipeline with LM Studio (OpenAI-compatible local server)
    python main.py --feature-root "..." --feature-name "ActionHistory" --mode full --llm-provider openai_compat --llm-base-url http://localhost:1234/v1 --llm-model local-model

    # Full pipeline with a local GGUF file via llama.cpp
    python main.py --feature-root "..." --feature-name "ActionHistory" --mode full --llm-provider llamacpp --llm-model-path "C:/models/mistral-7b.Q4_K_M.gguf"

    # Dry-run (no files written, logs only)
    python main.py --feature-root "..." --feature-name "ActionHistory" --mode full --dry-run

    # Resume from checkpoint
    python main.py --run-id conv-2026-001 --resume

    # Template-only / no LLM
    python main.py --feature-root "..." --feature-name "ActionHistory" --mode full --no-llm --auto-approve
"""

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is in sys.path
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.config_ingestion_agent import ConfigIngestionAgent, ConfigValidationError
from agents.scoping_agent import ScopingAgent
from agents.plan_agent import PlanAgent
from agents.conversion_agent import ConversionAgent, AmbiguityException
from agents.conversion_log import ConversionLog
from agents.approval_gate import (
    ApprovalGate,
    ApprovalRejectedError,
    CheckpointManager,
)

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt   = "%(asctime)s [%(levelname)-8s] %(name)s -- %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%Y-%m-%d %H:%M:%S")
    # Silence noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)

logger = logging.getLogger("ai-migration-tool")

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

DEFAULT_SKILLSET_CONFIG = ROOT / "config" / "skillset-config.json"
DEFAULT_RULES_CONFIG    = ROOT / "config" / "rules-config.json"
DEFAULT_PLANS_DIR       = ROOT / "plans"
DEFAULT_LOGS_DIR        = ROOT / "logs"
DEFAULT_OUTPUT_DIR      = ROOT / "output"
DEFAULT_CHECKPOINTS_DIR = ROOT / "checkpoints"

# ---------------------------------------------------------------------------
# LLM Router construction
# ---------------------------------------------------------------------------

def build_llm_router(args: argparse.Namespace):
    """
    Build an LLMRouter from CLI arguments.

    Returns None if --no-llm is set, otherwise delegates to
    LLMRouter.from_cli_args() which falls back to env-var auto-detection
    when no explicit --llm-provider is given.
    """
    if args.no_llm:
        return None

    try:
        from agents.llm import LLMRouter
        router = LLMRouter.from_cli_args(args)
        if router is None:
            logger.warning(
                "No LLM provider could be configured from CLI args or environment variables. "
                "Falling back to template-only mode. "
                "Set ANTHROPIC_API_KEY / OPENAI_API_KEY / OLLAMA_MODEL or use --llm-provider."
            )
        return router
    except ImportError as exc:
        logger.warning("agents.llm not available (%s) -- template-only mode.", exc)
        return None


def _describe_router(router) -> str:
    """Return a short human-readable description of the active LLM provider."""
    if router is None:
        return "disabled (--no-llm)"
    try:
        p = router._primary
        return f"{p.config.provider} / {p.config.model}"
    except Exception:
        return "configured (provider details unavailable)"

# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------

def run_pipeline(args: argparse.Namespace) -> int:
    """
    Run the full migration pipeline.
    Returns exit code: 0 = success, 1 = failure, 2 = approval rejected.
    """
    # ---- Build LLM router ----
    llm_router = build_llm_router(args)

    # ---- Generate or restore run ID ----
    if args.run_id:
        run_id = args.run_id
        logger.info("Resuming run: %s", run_id)
    else:
        ts     = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        run_id = f"conv-{ts}-{uuid.uuid4().hex[:6]}"
        logger.info("Starting new run: %s", run_id)

    # ---- Step 0: Checkpoint / Resume ----
    checkpoint = CheckpointManager(
        checkpoints_dir=DEFAULT_CHECKPOINTS_DIR,
        run_id=run_id,
        feature=args.feature_name,
    )

    # ---- Step 1: Config Ingestion ----
    print_banner("Step 1: Config Ingestion")
    try:
        config_agent = ConfigIngestionAgent(
            skillset_path=args.skillset_config,
            rules_path=args.rules_config,
        )
        config = config_agent.load_and_validate()
        logger.info("[OK] Config loaded and validated.")
    except (FileNotFoundError, ConfigValidationError) as exc:
        logger.error("Config ingestion failed: %s", exc)
        return 1

    # ---- Step 2: Scoping & Analysis ----
    print_banner("Step 2: Scoping & Analysis")
    scoping_agent = ScopingAgent(
        feature_root=args.feature_root,
        config=config,
    )
    try:
        dependency_graph = scoping_agent.analyze()
    except FileNotFoundError as exc:
        logger.error("Scoping failed: %s", exc)
        return 1

    # Save graph
    graph_path = DEFAULT_LOGS_DIR / f"{run_id}-dependency-graph.json"
    scoping_agent.save(graph_path)
    logger.info("[OK] Dependency graph saved: %s", graph_path)

    # Print flags summary
    flags = dependency_graph.get("flags", [])
    if flags:
        logger.warning("[!]  %d flag(s) detected during scoping:", len(flags))
        for flag in flags:
            logger.warning("   [%s] %s -- %s", flag["severity"], flag["rule"], flag["message"])

    if args.mode == "scope":
        logger.info("Mode=scope -- stopping after analysis. Dependency graph: %s", graph_path)
        return 0

    # ---- Step 3: Plan Document Generation ----
    print_banner("Step 3: Plan Document Generation")
    plan_agent = PlanAgent(
        dependency_graph=dependency_graph,
        config=config,
        run_id=run_id,
        plans_dir=DEFAULT_PLANS_DIR,
        llm_router=llm_router,
    )
    plan_md, plan_path = plan_agent.generate()
    logger.info("[OK] Plan document generated: %s", plan_path)

    if args.mode == "plan":
        logger.info("Mode=plan -- stopping after plan generation. Review: %s", plan_path)
        print(f"\n{'='*60}")
        print(f"  Plan saved to: {plan_path}")
        print(f"  Review and run with --mode full to proceed.")
        print(f"{'='*60}\n")
        return 0

    # ---- Step 4: Human Approval Gate ----
    print_banner("Step 4: Human Approval Gate")
    approval_mode = "auto_approve" if args.auto_approve else "cli_prompt"
    if args.auto_approve:
        logger.warning("[!]  AUTO-APPROVE enabled -- skipping human review. FOR TESTING ONLY.")

    gate = ApprovalGate(mode=approval_mode)
    try:
        approved = gate.request_approval(plan_path, plan_md)
        if not approved:
            logger.info("Plan not yet approved (pr_merge mode -- marker file not found).")
            return 2
    except ApprovalRejectedError as exc:
        logger.info("Plan rejected: %s", exc)
        print(f"\n[X] Plan rejected. Reason: {exc}")
        print("Modify the plan or re-run scoping and regenerate.")
        return 2

    logger.info("[OK] Plan approved. Starting conversion execution.")

    # ---- Step 5: Conversion Execution ----
    print_banner("Step 5: Conversion Execution")

    # Prepare the approved plan dict (enriched with step data for the agent)
    approved_plan = _build_approved_plan(
        dependency_graph=dependency_graph,
        config=config,
        run_id=run_id,
        feature_root=args.feature_root,
        output_root=args.output_root or str(DEFAULT_OUTPUT_DIR / args.feature_name),
    )

    log_path = DEFAULT_LOGS_DIR / f"{run_id}-conversion-log.json"
    conv_log = ConversionLog(
        feature_name=dependency_graph["feature_name"],
        run_id=run_id,
        plan_ref=str(plan_path),
        log_path=log_path,
    )

    # Filter out already-completed steps (resume support)
    all_steps = approved_plan.get("conversion_steps", [])
    if args.run_id:  # resuming
        pending_steps = [
            s for s in all_steps
            if not checkpoint.is_completed(s["id"])
               and s["id"] not in checkpoint.get_state()["blocked_steps"]
        ]
        skipped = len(all_steps) - len(pending_steps)
        if skipped:
            logger.info("Resume: skipping %d already-completed steps.", skipped)
        approved_plan["conversion_steps"] = pending_steps

    conv_agent = ConversionAgent(
        approved_plan=approved_plan,
        config=config,
        log=conv_log,
        output_root=approved_plan["output_root"],
        dry_run=args.dry_run,
        llm_router=llm_router,
    )

    summary = conv_agent.execute()

    # Update checkpoint
    for step_id in summary.get("completed_steps", []):
        checkpoint.mark_completed(step_id, [s["id"] for s in all_steps])
    for flagged in summary.get("flagged_steps", []):
        checkpoint.mark_blocked(flagged["step"], flagged["reason"])

    # Export log as Markdown
    md_log_path = DEFAULT_LOGS_DIR / f"{run_id}-conversion-log.md"
    conv_log.export_markdown(md_log_path)

    # Print summary
    print_banner("Pipeline Complete")
    print(f"  Feature:      {dependency_graph['feature_name']}")
    print(f"  Run ID:       {run_id}")
    print(f"  Completed:    {summary['completed']} / {summary['total']} steps")
    print(f"  Flagged:      {summary['flagged']} step(s) need human review")
    print(f"  Output:       {approved_plan['output_root']}")
    print(f"  Log (JSON):   {log_path}")
    print(f"  Log (MD):     {md_log_path}")
    print(f"  Checkpoint:   {checkpoint.path}")
    print()

    if summary["flagged"]:
        logger.warning(
            "[!]  %d step(s) were flagged and NOT converted:", summary["flagged"]
        )
        for f in summary["flagged_steps"]:
            logger.warning("   [%s] %s", f["step"], f["reason"])
        logger.info(
            "Resolve flagged items and resume with: python main.py --run-id %s --resume", run_id
        )

    return 0 if summary["flagged"] == 0 else 1


# ---------------------------------------------------------------------------
# Build approved plan dict from dependency graph
# ---------------------------------------------------------------------------

def _build_approved_plan(
    dependency_graph: dict,
    config: dict,
    run_id: str,
    feature_root: str,
    output_root: str,
) -> dict:
    """
    Build the structured plan dict that ConversionAgent consumes.
    In a full pipeline this comes from the parsed Plan Document.
    Here we derive it programmatically from the dependency graph.
    """
    steps = []
    phase_labels  = {"frontend": "C", "backend": "B", "database": "A"}
    phase_counts  = {"A": 0, "B": 0, "C": 0}

    skillset       = config["skillset"]
    mappings_index = config.get("mappings_index", {})

    for node in dependency_graph.get("nodes", []):
        node_type = node.get("type", "frontend")
        phase     = phase_labels.get(node_type, "C")
        phase_counts[phase] += 1
        step_id = f"Step {phase}{phase_counts[phase]}"

        # Determine mapping
        pattern    = node.get("pattern", "")
        mapping_id = _infer_mapping_id(pattern, node_type)
        mapping    = mappings_index.get(mapping_id, {})

        # Determine target file path
        source_rel = node["id"]
        target_rel = _derive_target_path(node, mapping, skillset)

        # Determine applicable rules
        rule_ids = ["RULE-003"]
        if node.get("endpoints"):
            rule_ids.insert(0, "RULE-001")
        if node_type == "frontend":
            rule_ids.append("RULE-002")
        if node_type == "database":
            rule_ids.append("RULE-009")

        steps.append({
            "id":           step_id,
            "description":  f"Convert {source_rel} -> {target_rel}",
            "source_file":  source_rel,
            "target_file":  target_rel,
            "mapping_id":   mapping_id,
            "rule_ids":     rule_ids,
            "rationale":    mapping.get("notes", "Direct translation per RULE-003."),
        })

    # Sort: database (A) -> backend (B) -> frontend (C)
    steps.sort(key=lambda s: s["id"])

    return {
        "feature_name":     dependency_graph["feature_name"],
        "feature_root":     feature_root,
        "output_root":      output_root,
        "run_id":           run_id,
        "conversion_steps": steps,
    }


def _infer_mapping_id(pattern: str, node_type: str) -> str:
    hints = {
        "Angular 2 Component": "MAP-001",
        "Angular 2 Service":   "MAP-002",
        "NgModule":            "MAP-006",
        "Area API Controller": "MAP-003",
        "Repository":          "MAP-004",
        "C# Service":          "MAP-004",
        "Stored Procedure":    "MAP-004",
        "C# Class":            "MAP-005",
    }
    for keyword, map_id in hints.items():
        if keyword.lower() in pattern.lower():
            return map_id
    return {"frontend": "MAP-001", "backend": "MAP-003", "database": "MAP-004"}.get(node_type, "MAP-001")


def _derive_target_path(node: dict, mapping: dict, skillset: dict) -> str:
    """Derive the target file path from the source node and skillset config."""
    import re
    node_type = node.get("type", "frontend")
    exports   = node.get("exports", [])
    name      = exports[0] if exports else Path(node["id"]).stem

    def to_snake(s: str) -> str:
        s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", s)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()

    def to_kebab(s: str) -> str:
        return to_snake(s).replace("_", "-")

    target_struct = skillset.get("project_structure", {})
    feature_name  = node.get("id", "").split("/")[0].lower()

    if node_type == "frontend":
        comp_root = target_struct.get("frontend", {}).get("components_root", "frontend/src/components/{feature_name}/")
        base = comp_root.replace("{feature_name}", feature_name)
        return f"{base}{name}.tsx"

    if node_type == "backend":
        api_root = target_struct.get("backend", {}).get("api_root", "api/src/api/{feature_name}/")
        base = api_root.replace("{feature_name}", to_snake(feature_name))
        return f"{base}{to_snake(name)}_routes.py"

    if node_type == "database":
        svc_root = target_struct.get("backend", {}).get("services_root", "api/src/services/{feature_name}/")
        base = svc_root.replace("{feature_name}", to_snake(feature_name))
        return f"{base}{to_snake(name)}_service.py"

    return f"output/{to_snake(name)}.py"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def print_banner(title: str) -> None:
    width = 60
    print(f"\n{'='*width}")
    print(f"  {title}")
    print(f"{'='*width}")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AI Migration Tool -- Angular 2 / ASP.NET Core -> Next.js 15 / Python Flask",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ---- Feature targeting ----
    parser.add_argument(
        "--feature-root", "-r",
        type=str,
        help="Path to the legacy feature folder to migrate (source boundary).",
    )
    parser.add_argument(
        "--feature-name", "-n",
        type=str,
        default="",
        help="Human-readable name for the feature (used in plan/log filenames).",
    )
    parser.add_argument(
        "--output-root", "-o",
        type=str,
        default=None,
        help="Root folder for generated target files (default: output/<feature-name>/).",
    )

    # ---- Pipeline control ----
    parser.add_argument(
        "--mode", "-m",
        choices=["scope", "plan", "full"],
        default="full",
        help=(
            "Pipeline mode: "
            "'scope' = analyse only, "
            "'plan'  = analyse + generate plan, "
            "'full'  = full pipeline including code conversion (default: full)."
        ),
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Run ID to resume (from a previous interrupted run).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from the checkpoint of --run-id.",
    )
    parser.add_argument(
        "--skillset-config",
        type=str,
        default=str(DEFAULT_SKILLSET_CONFIG),
        help=f"Path to skillset-config.json (default: {DEFAULT_SKILLSET_CONFIG}).",
    )
    parser.add_argument(
        "--rules-config",
        type=str,
        default=str(DEFAULT_RULES_CONFIG),
        help=f"Path to rules-config.json (default: {DEFAULT_RULES_CONFIG}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the full pipeline but do NOT write any files to disk.",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="[TESTING ONLY] Skip the human approval gate and auto-approve the plan.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )

    # ---- LLM provider selection ----
    llm_group = parser.add_argument_group(
        "LLM provider",
        "Configure which LLM to use for plan generation and code conversion.\n"
        "If none of these flags are given, the tool auto-detects from environment\n"
        "variables (ANTHROPIC_API_KEY, OPENAI_API_KEY, OLLAMA_MODEL, etc.).\n"
        "Use --no-llm to disable LLM calls entirely (template-only / scaffold mode).",
    )
    llm_group.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM-assisted generation (template-only mode, no API calls).",
    )
    llm_group.add_argument(
        "--llm-provider",
        type=str,
        default=None,
        choices=["anthropic", "openai", "openai_compat", "ollama", "llamacpp"],
        metavar="PROVIDER",
        help=(
            "LLM provider to use. Choices: anthropic, openai, openai_compat, ollama, llamacpp. "
            "Auto-detected from env vars when omitted."
        ),
    )
    llm_group.add_argument(
        "--llm-model",
        type=str,
        default=None,
        metavar="MODEL",
        help=(
            "Model name / ID to use. Examples: "
            "claude-opus-4-5 (Anthropic), "
            "gpt-4o (OpenAI), "
            "llama3.2 (Ollama), "
            "local-model (LM Studio / openai_compat)."
        ),
    )
    llm_group.add_argument(
        "--llm-base-url",
        type=str,
        default=None,
        metavar="URL",
        help=(
            "Base URL for OpenAI-compatible servers. "
            "Examples: http://localhost:1234/v1 (LM Studio), "
            "http://localhost:8000/v1 (vLLM), "
            "https://<resource>.openai.azure.com/ (Azure OpenAI)."
        ),
    )
    llm_group.add_argument(
        "--llm-model-path",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to a local GGUF model file for llama.cpp backend.",
    )
    llm_group.add_argument(
        "--ollama-host",
        type=str,
        default=None,
        metavar="HOST",
        help="Ollama server URL (default: http://localhost:11434).",
    )
    llm_group.add_argument(
        "--llm-max-tokens",
        type=int,
        default=None,
        metavar="N",
        help="Maximum tokens to generate per LLM call (default: 8192).",
    )
    llm_group.add_argument(
        "--llm-temperature",
        type=float,
        default=None,
        metavar="T",
        help="Sampling temperature for the LLM (default: 0.2).",
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = build_arg_parser()
    args   = parser.parse_args()

    configure_logging(verbose=args.verbose)

    # Validate required args
    if not args.run_id and not args.feature_root:
        parser.error("--feature-root is required unless --run-id is provided for resume.")
    if not args.feature_name and args.feature_root:
        args.feature_name = Path(args.feature_root).name

    # Build router early so we can describe it in the startup banner
    llm_router = build_llm_router(args)

    print_banner("AI Migration Tool v1.0")
    print(f"  Feature:      {args.feature_name}")
    print(f"  Source:       {args.feature_root or '(resuming)'}")
    print(f"  Mode:         {args.mode}")
    print(f"  Dry-run:      {args.dry_run}")
    print(f"  LLM:          {_describe_router(llm_router)}")
    print(f"  Auto-approve: {args.auto_approve}")
    print()

    # Re-use already-built router; pass it into pipeline via a tiny shim
    # (run_pipeline calls build_llm_router internally, so we patch args instead)
    args._llm_router_cache = llm_router

    return _run_pipeline_with_router(args, llm_router)


def _run_pipeline_with_router(args: argparse.Namespace, llm_router) -> int:
    """
    Thin wrapper that delegates to run_pipeline but injects a pre-built router.
    Avoids constructing the router twice (once in main, once in run_pipeline).
    """
    # Temporarily monkey-patch build_llm_router in this module's scope
    # by passing the router directly -- achieved by a module-level override.
    import agents.plan_agent as _pa
    import agents.conversion_agent as _ca

    # ---- Generate or restore run ID ----
    if args.run_id:
        run_id = args.run_id
        logger.info("Resuming run: %s", run_id)
    else:
        ts     = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        run_id = f"conv-{ts}-{uuid.uuid4().hex[:6]}"
        logger.info("Starting new run: %s", run_id)

    # ---- Step 0: Checkpoint / Resume ----
    checkpoint = CheckpointManager(
        checkpoints_dir=DEFAULT_CHECKPOINTS_DIR,
        run_id=run_id,
        feature=args.feature_name,
    )

    # ---- Step 1: Config Ingestion ----
    print_banner("Step 1: Config Ingestion")
    try:
        config_agent = ConfigIngestionAgent(
            skillset_path=args.skillset_config,
            rules_path=args.rules_config,
        )
        config = config_agent.load_and_validate()
        logger.info("[OK] Config loaded and validated.")
    except (FileNotFoundError, ConfigValidationError) as exc:
        logger.error("Config ingestion failed: %s", exc)
        return 1

    # ---- Step 2: Scoping & Analysis ----
    print_banner("Step 2: Scoping & Analysis")
    scoping_agent = ScopingAgent(
        feature_root=args.feature_root,
        config=config,
    )
    try:
        dependency_graph = scoping_agent.analyze()
    except FileNotFoundError as exc:
        logger.error("Scoping failed: %s", exc)
        return 1

    graph_path = DEFAULT_LOGS_DIR / f"{run_id}-dependency-graph.json"
    scoping_agent.save(graph_path)
    logger.info("[OK] Dependency graph saved: %s", graph_path)

    flags = dependency_graph.get("flags", [])
    if flags:
        logger.warning("[!]  %d flag(s) detected during scoping:", len(flags))
        for flag in flags:
            logger.warning("   [%s] %s -- %s", flag["severity"], flag["rule"], flag["message"])

    if args.mode == "scope":
        logger.info("Mode=scope -- stopping after analysis. Dependency graph: %s", graph_path)
        return 0

    # ---- Step 3: Plan Document Generation ----
    print_banner("Step 3: Plan Document Generation")
    plan_agent = _pa.PlanAgent(
        dependency_graph=dependency_graph,
        config=config,
        run_id=run_id,
        plans_dir=DEFAULT_PLANS_DIR,
        llm_router=llm_router,
    )
    plan_md, plan_path = plan_agent.generate()
    logger.info("[OK] Plan document generated: %s", plan_path)

    if args.mode == "plan":
        logger.info("Mode=plan -- stopping after plan generation. Review: %s", plan_path)
        print(f"\n{'='*60}")
        print(f"  Plan saved to: {plan_path}")
        print(f"  Review and run with --mode full to proceed.")
        print(f"{'='*60}\n")
        return 0

    # ---- Step 4: Human Approval Gate ----
    print_banner("Step 4: Human Approval Gate")
    approval_mode = "auto_approve" if args.auto_approve else "cli_prompt"
    if args.auto_approve:
        logger.warning("[!]  AUTO-APPROVE enabled -- skipping human review. FOR TESTING ONLY.")

    gate = ApprovalGate(mode=approval_mode)
    try:
        approved = gate.request_approval(plan_path, plan_md)
        if not approved:
            logger.info("Plan not yet approved (pr_merge mode -- marker file not found).")
            return 2
    except ApprovalRejectedError as exc:
        logger.info("Plan rejected: %s", exc)
        print(f"\n[X] Plan rejected. Reason: {exc}")
        print("Modify the plan or re-run scoping and regenerate.")
        return 2

    logger.info("[OK] Plan approved. Starting conversion execution.")

    # ---- Step 5: Conversion Execution ----
    print_banner("Step 5: Conversion Execution")

    approved_plan = _build_approved_plan(
        dependency_graph=dependency_graph,
        config=config,
        run_id=run_id,
        feature_root=args.feature_root,
        output_root=args.output_root or str(DEFAULT_OUTPUT_DIR / args.feature_name),
    )

    log_path = DEFAULT_LOGS_DIR / f"{run_id}-conversion-log.json"
    conv_log = ConversionLog(
        feature_name=dependency_graph["feature_name"],
        run_id=run_id,
        plan_ref=str(plan_path),
        log_path=log_path,
    )

    all_steps = approved_plan.get("conversion_steps", [])
    if args.run_id:
        pending_steps = [
            s for s in all_steps
            if not checkpoint.is_completed(s["id"])
               and s["id"] not in checkpoint.get_state()["blocked_steps"]
        ]
        skipped = len(all_steps) - len(pending_steps)
        if skipped:
            logger.info("Resume: skipping %d already-completed steps.", skipped)
        approved_plan["conversion_steps"] = pending_steps

    conv_agent = _ca.ConversionAgent(
        approved_plan=approved_plan,
        config=config,
        log=conv_log,
        output_root=approved_plan["output_root"],
        dry_run=args.dry_run,
        llm_router=llm_router,
    )

    summary = conv_agent.execute()

    for step_id in summary.get("completed_steps", []):
        checkpoint.mark_completed(step_id, [s["id"] for s in all_steps])
    for flagged in summary.get("flagged_steps", []):
        checkpoint.mark_blocked(flagged["step"], flagged["reason"])

    md_log_path = DEFAULT_LOGS_DIR / f"{run_id}-conversion-log.md"
    conv_log.export_markdown(md_log_path)

    print_banner("Pipeline Complete")
    print(f"  Feature:      {dependency_graph['feature_name']}")
    print(f"  Run ID:       {run_id}")
    print(f"  LLM:          {_describe_router(llm_router)}")
    print(f"  Completed:    {summary['completed']} / {summary['total']} steps")
    print(f"  Flagged:      {summary['flagged']} step(s) need human review")
    print(f"  Output:       {approved_plan['output_root']}")
    print(f"  Log (JSON):   {log_path}")
    print(f"  Log (MD):     {md_log_path}")
    print(f"  Checkpoint:   {checkpoint.path}")
    print()

    if summary["flagged"]:
        logger.warning(
            "[!]  %d step(s) were flagged and NOT converted:", summary["flagged"]
        )
        for f in summary["flagged_steps"]:
            logger.warning("   [%s] %s", f["step"], f["reason"])
        logger.info(
            "Resolve flagged items and resume with: python main.py --run-id %s --resume", run_id
        )

    return 0 if summary["flagged"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

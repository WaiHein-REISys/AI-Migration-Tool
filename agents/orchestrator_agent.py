"""
Orchestrator Agent
==================
LLM-driven workflow controller that dynamically decides which pipeline stage
to run next, whether to auto-revise plans, retry failed steps, or escalate
to humans.

Activated when orchestration.enabled == true in the job YAML.

Mode selection (automatic, based on provider capability):
    native_tools  — Anthropic / OpenAI / Vertex AI have native function-calling.
                    OrchestratorAgent uses complete_with_tools() for structured
                    tool calls.  No text parsing needed.
    react_text    — Ollama / llama.cpp / subprocess providers use plain text.
                    OrchestratorAgent calls complete() and parses
                    THOUGHT / ACTION / PARAMS from the response.

Backend selection (config: orchestration.backend):
    internal      — Built-in ReAct / native-tool loop (default).
                    Uses InternalOrchestrationBackend.
    google_adk    — Google Agent Development Kit backend.
                    Uses ADKOrchestrationBackend (falls back to internal if
                    google-adk is not installed).

Fallback chain:
    1. llm_router is None → log warning → call run_pipeline() sequentially.
    2. google_adk selected + google-adk not installed → internal backend.
    3. tool_use: never in config → force react_text regardless of provider.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents.llm.registry import LLMRouter
    from agents.memory_store import MemoryStore

logger = logging.getLogger(__name__)


class OrchestratorAgent:
    """
    LLM-driven migration pipeline orchestrator.

    Wraps all pipeline agent classes as named actions and delegates workflow
    control to either the InternalOrchestrationBackend (ReAct / tool-use loop)
    or the ADKOrchestrationBackend (Google ADK), depending on configuration.

    Usage::

        orchestrator = OrchestratorAgent(args, memory_store, llm_router, config)
        exit_code = orchestrator.execute()
    """

    def __init__(
        self,
        args: argparse.Namespace,
        memory_store: "MemoryStore",
        llm_router: "LLMRouter | None",
        config: dict,
    ) -> None:
        self._args         = args
        self._memory_store = memory_store
        self._llm_router   = llm_router
        self._config       = config

        orch_cfg = getattr(args, "orchestration_config", {}) or {}
        self._orch_cfg = orch_cfg

        # Shared pipeline state dict passed through all actions
        self._state: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def execute(self) -> int:
        """
        Run the orchestrated pipeline.

        Returns an exit code compatible with run_pipeline():
            0 = success
            1 = failure
            2 = approval rejected
        """
        if self._llm_router is None:
            logger.warning(
                "OrchestratorAgent: no LLM router available. "
                "Falling back to sequential run_pipeline()."
            )
            return self._fallback_sequential()

        # ---- Step 1: Config ingestion (always synchronous) ----
        config = self._load_config()
        if config is None:
            return 1

        # ---- Step 2: Scoping (always synchronous — gives us the graph) ----
        dependency_graph, run_id = self._run_scoping(config)
        if dependency_graph is None:
            return 1

        # ---- Step 3: Load memory context ----
        memory_context = self._get_memory_context(dependency_graph)

        # ---- Step 4: Build shared state dict ----
        self._state = self._build_initial_state(
            config, dependency_graph, run_id, memory_context
        )

        # ---- Step 5: Build action registry ----
        action_registry = self._build_action_registry(
            config, dependency_graph, run_id, memory_context
        )

        # ---- Step 6: Load system prompt ----
        system_prompt = self._load_system_prompt()

        # ---- Step 7: Select backend and run ----
        backend_name = self._orch_cfg.get("backend", "internal")
        result = self._run_backend(
            backend_name, action_registry, system_prompt
        )

        # ---- Step 8: Knowledge extraction (always at end) ----
        if self._orch_cfg.get("learning", True):
            self._run_knowledge_extraction(dependency_graph, run_id)

        return result.get("exit_code", 0)

    # ------------------------------------------------------------------
    # Config + Scoping (synchronous — must happen before the LLM loop)
    # ------------------------------------------------------------------

    def _load_config(self) -> dict | None:
        """Run Step 1 (ConfigIngestion) and return the config dict."""
        from agents.config_ingestion_agent import ConfigIngestionAgent, ConfigValidationError
        try:
            agent = ConfigIngestionAgent(
                skillset_path=self._args.skillset_config,
                rules_path=self._args.rules_config,
            )
            cfg = agent.load_and_validate()
            logger.info("[OK] Config loaded.")
            return cfg
        except (FileNotFoundError, ConfigValidationError) as exc:
            logger.error("Config ingestion failed: %s", exc)
            return None

    def _run_scoping(self, config: dict) -> tuple[dict | None, str | None]:
        """Run Step 2 (ScopingAgent) and return (dependency_graph, run_id)."""
        import hashlib
        import re
        from agents.scoping_agent import ScopingAgent

        args = self._args
        feature_root = getattr(args, "feature_root", None)
        feature_name = getattr(args, "feature_name", "unknown")
        target       = getattr(args, "target", "simpler_grants")

        scoping_agent = ScopingAgent(feature_root=feature_root, config=config)
        try:
            dependency_graph = scoping_agent.analyze()
        except FileNotFoundError as exc:
            logger.error("Scoping failed: %s", exc)
            return None, None

        # Derive run_id (matches main.py logic)
        slug   = re.sub(r"[^\w]", "-", feature_name.lower())[:20].strip("-")
        abbrev = {"simpler_grants": "sg", "hrsa_pprs": "hp"}.get(target, target[:4])
        digest = hashlib.sha1(
            f"{feature_root}|{target}".encode("utf-8")
        ).hexdigest()[:8]
        run_id = f"conv-{slug}-{abbrev}-{digest}"

        # Save dependency graph
        from pathlib import Path as _Path
        logs_dir = _Path("logs")
        logs_dir.mkdir(exist_ok=True)
        graph_path = logs_dir / f"{run_id}-dependency-graph.json"
        scoping_agent.save(graph_path)
        logger.info("[OK] Dependency graph saved: %s", graph_path)

        flags = dependency_graph.get("flags", [])
        if flags:
            logger.warning("%d flag(s) detected during scoping.", len(flags))

        return dependency_graph, run_id

    def _get_memory_context(self, dependency_graph: dict):
        """Retrieve memory context for this feature."""
        if self._memory_store is None:
            return None
        try:
            feature_name = dependency_graph.get("feature_name", "")
            target       = getattr(self._args, "target", "simpler_grants")
            ctx = self._memory_store.get_context(
                feature_name=feature_name,
                dependency_graph=dependency_graph,
                target=target,
            )
            if ctx.context_summary:
                logger.info(
                    "Memory context loaded: %d patterns, %d preferences.",
                    len(ctx.similar_patterns),
                    len(ctx.user_preferences),
                )
            return ctx
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load memory context: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Initial state builder
    # ------------------------------------------------------------------

    def _build_initial_state(
        self,
        config: dict,
        dependency_graph: dict,
        run_id: str,
        memory_context,
    ) -> dict:
        """Build the shared state dict passed to every action."""
        args = self._args
        return {
            "config":           config,
            "dependency_graph": dependency_graph,
            "run_id":           run_id,
            "feature_name":     dependency_graph.get("feature_name", ""),
            "feature_root":     getattr(args, "feature_root", None),
            "output_root":      getattr(args, "output_root", None),
            "target":           getattr(args, "target", "simpler_grants"),
            "target_root":      getattr(args, "target_root", None),
            "mode":             getattr(args, "mode", "full"),
            "dry_run":          getattr(args, "dry_run", False),
            "auto_approve":     getattr(args, "auto_approve", False),
            "memory_context":   memory_context,
            "approved_plan":    None,
            "plan_md":          None,
            "plan_path":        None,
            "conversion_summary": None,
            "validation_result":  None,
            "ui_consistency_result": None,
            "integration_result":    None,
            "verification_result":   None,
            "plan_revision_count":   0,
            "exit_code":             0,
            "exit_dict":             {},
        }

    # ------------------------------------------------------------------
    # Action registry
    # ------------------------------------------------------------------

    def _build_action_registry(
        self,
        config: dict,
        dependency_graph: dict,
        run_id: str,
        memory_context,
    ) -> dict:
        """
        Return a dict mapping action names → callables.

        Each callable takes (state, **kwargs) and returns a dict result.
        The state dict is mutated in place so that subsequent actions see
        the outputs of previous ones.
        """
        return {
            "generate_plan":     self._action_generate_plan,
            "revise_plan":       self._action_revise_plan,
            "approve_plan":      self._action_approve_plan,
            "convert":           self._action_convert,
            "validate":          self._action_validate,
            "ui_audit":          self._action_ui_audit,
            "integrate":         self._action_integrate,
            "verify":            self._action_verify,
            "record_memory":     self._action_record_memory,
            "escalate_human":    self._action_escalate_human,
            "done":              self._action_done,
        }

    # ------------------------------------------------------------------
    # Action implementations
    # ------------------------------------------------------------------

    def _action_generate_plan(self, state: dict, **kwargs) -> dict:
        """Generate the migration plan document."""
        from agents.plan_agent import PlanAgent
        from pathlib import Path as _Path

        try:
            plan_agent = PlanAgent(
                dependency_graph=state["dependency_graph"],
                config=state["config"],
                run_id=state["run_id"],
                plans_dir=_Path("plans"),
                llm_router=self._llm_router,
                target=state["target"],
                memory_context=state.get("memory_context"),
            )
            plan_md, plan_path = plan_agent.generate()
            state["plan_md"]   = plan_md
            state["plan_path"] = str(plan_path)
            logger.info("[OK] Plan generated: %s", plan_path)
            return {"status": "success", "plan_path": str(plan_path)}
        except Exception as exc:  # noqa: BLE001
            logger.error("generate_plan failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    def _action_revise_plan(self, state: dict, feedback: str = "", **kwargs) -> dict:
        """Revise the plan with feedback."""
        from agents.plan_agent import PlanAgent
        from pathlib import Path as _Path

        state["plan_revision_count"] = state.get("plan_revision_count", 0) + 1

        max_revisions = self._orch_cfg.get("max_plan_revisions", 2)
        if state["plan_revision_count"] > max_revisions:
            return {
                "status": "max_revisions_reached",
                "message": f"Revision limit ({max_revisions}) reached.",
            }

        try:
            plan_agent = PlanAgent(
                dependency_graph=state["dependency_graph"],
                config=state["config"],
                run_id=state["run_id"],
                plans_dir=_Path("plans"),
                llm_router=self._llm_router,
                target=state["target"],
                revision_notes=feedback or kwargs.get("notes", ""),
                original_plan=state.get("plan_md"),
                memory_context=state.get("memory_context"),
            )
            plan_md, plan_path = plan_agent.generate()
            state["plan_md"]   = plan_md
            state["plan_path"] = str(plan_path)
            # Reset approval so the new plan must be re-approved
            state["approved_plan"] = None
            logger.info("[OK] Plan revised (revision %d): %s",
                        state["plan_revision_count"], plan_path)
            return {
                "status": "success",
                "plan_path": str(plan_path),
                "revision_count": state["plan_revision_count"],
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("revise_plan failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    def _action_approve_plan(self, state: dict, **kwargs) -> dict:
        """Run the approval gate (auto-approve or CLI prompt)."""
        from agents.approval_gate import ApprovalGate, ApprovalRejectedError

        if not state.get("plan_path") or not state.get("plan_md"):
            return {"status": "error", "error": "No plan to approve — run generate_plan first."}

        approval_mode = "auto_approve" if state.get("auto_approve") else "cli_prompt"
        gate = ApprovalGate(mode=approval_mode)

        try:
            approved = gate.request_approval(
                state["plan_path"], state["plan_md"]
            )
            if not approved:
                state["exit_code"] = 2
                return {"status": "not_approved", "message": "Plan not yet approved."}

            # Build the approved_plan dict for downstream actions
            state["approved_plan"] = self._build_approved_plan(state)
            logger.info("[OK] Plan approved.")
            return {"status": "approved"}
        except ApprovalRejectedError as exc:
            state["exit_code"] = 2
            logger.info("Plan rejected: %s", exc)
            return {"status": "rejected", "reason": str(exc)}

    def _action_convert(self, state: dict, **kwargs) -> dict:
        """Execute conversion for all approved plan steps."""
        from agents.conversion_agent import ConversionAgent, AmbiguityException
        from agents.conversion_log import ConversionLog
        from pathlib import Path as _Path

        if not state.get("approved_plan"):
            return {"status": "error", "error": "No approved plan — run approve_plan first."}

        approved_plan = state["approved_plan"]
        run_id        = state["run_id"]
        log_path      = _Path("logs") / f"{run_id}-conversion-log.json"

        conv_log = ConversionLog(
            feature_name=state["feature_name"],
            run_id=run_id,
            plan_ref=state.get("plan_path", ""),
            log_path=log_path,
        )

        conv_agent = ConversionAgent(
            approved_plan=approved_plan,
            config=state["config"],
            log=conv_log,
            output_root=approved_plan["output_root"],
            dry_run=state.get("dry_run", False),
            llm_router=self._llm_router,
            target=state["target"],
            memory_context=state.get("memory_context"),
        )

        try:
            summary = conv_agent.execute()
            state["conversion_summary"] = summary

            # Export markdown log
            md_log_path = _Path("logs") / f"{run_id}-conversion-log.md"
            conv_log.export_markdown(md_log_path)

            logger.info(
                "[OK] Conversion complete: %d/%d steps, %d flagged.",
                summary["completed"], summary["total"], summary["flagged"],
            )
            return {"status": "success", **summary}
        except AmbiguityException as exc:
            state["conversion_summary"] = {"error": str(exc), "completed": 0, "flagged": 0, "total": 0}
            return {"status": "ambiguity", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.error("convert failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    def _action_validate(self, state: dict, **kwargs) -> dict:
        """Run output validation."""
        from agents.validation_agent import ValidationAgent

        if not state.get("approved_plan"):
            return {"status": "error", "error": "No approved plan."}

        approved_plan = state["approved_plan"]
        summary       = state.get("conversion_summary", {})
        all_steps     = approved_plan.get("conversion_steps", [])

        try:
            validation_agent = ValidationAgent(
                approved_plan=approved_plan,
                output_root=approved_plan["output_root"],
                run_id=state["run_id"],
                logs_dir=Path("logs"),
                llm_router=self._llm_router,
                dry_run=state.get("dry_run", False),
            )
            result = validation_agent.execute(
                completed_step_ids=summary.get("completed_steps", []),
                all_steps=all_steps,
            )
            state["validation_result"] = result
            logger.info("[OK] Validation: %s", result.get("status"))
            return result
        except Exception as exc:  # noqa: BLE001
            logger.error("validate failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    def _action_ui_audit(self, state: dict, **kwargs) -> dict:
        """Run UI consistency audit."""
        from agents.ui_consistency_agent import UIConsistencyAgent

        if not state.get("approved_plan"):
            return {"status": "error", "error": "No approved plan."}

        approved_plan = state["approved_plan"]
        summary       = state.get("conversion_summary", {})
        all_steps     = approved_plan.get("conversion_steps", [])

        try:
            ui_agent = UIConsistencyAgent(
                approved_plan=approved_plan,
                output_root=approved_plan["output_root"],
                run_id=state["run_id"],
                logs_dir=Path("logs"),
                llm_router=self._llm_router,
                ui_consistency_config=getattr(self._args, "ui_consistency_config", {}),
                dry_run=state.get("dry_run", False),
            )
            result = ui_agent.execute(
                completed_step_ids=summary.get("completed_steps", []),
                all_steps=all_steps,
            )
            state["ui_consistency_result"] = result
            logger.info("[OK] UI audit: %s", result.get("status"))
            return result
        except Exception as exc:  # noqa: BLE001
            logger.error("ui_audit failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    def _action_integrate(self, state: dict, **kwargs) -> dict:
        """Run integration and file placement."""
        from agents.integration_agent import IntegrationAgent

        if not state.get("approved_plan"):
            return {"status": "error", "error": "No approved plan."}

        approved_plan      = state["approved_plan"]
        summary            = state.get("conversion_summary", {})
        all_steps          = approved_plan.get("conversion_steps", [])
        validation_result  = state.get("validation_result", {})
        target_root        = state.get("target_root")
        if target_root:
            target_root = Path(target_root)

        try:
            integration_agent = IntegrationAgent(
                approved_plan=approved_plan,
                output_root=approved_plan["output_root"],
                target_root=target_root,
                run_id=state["run_id"],
                logs_dir=Path("logs"),
                config=state["config"],
                llm_router=None,
                integration_config=getattr(self._args, "integration_config", {}),
                dry_run=state.get("dry_run", False),
            )
            result = integration_agent.execute(
                completed_step_ids=summary.get("completed_steps", []),
                all_steps=all_steps,
                validation_findings=validation_result.get("findings", []),
            )
            state["integration_result"] = result
            logger.info("[OK] Integration: %s", result.get("status"))
            return result
        except Exception as exc:  # noqa: BLE001
            logger.error("integrate failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    def _action_verify(self, state: dict, **kwargs) -> dict:
        """Run end-to-end verification."""
        from agents.e2e_verification_agent import E2EVerificationAgent

        if not state.get("approved_plan"):
            return {"status": "error", "error": "No approved plan."}

        target_root = state.get("target_root")
        if target_root:
            target_root = Path(target_root)

        try:
            verification_agent = E2EVerificationAgent(
                run_id=state["run_id"],
                logs_dir=Path("logs"),
                output_root=state["approved_plan"]["output_root"],
                target_root=target_root,
                verification_config=getattr(self._args, "verification_config", {}),
                dry_run=state.get("dry_run", False),
            )
            result = verification_agent.execute()
            state["verification_result"] = result
            logger.info("[OK] Verification: %s", result.get("status"))
            return result
        except Exception as exc:  # noqa: BLE001
            logger.error("verify failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    def _action_record_memory(self, state: dict, **kwargs) -> dict:
        """Persist learnings to the memory store."""
        if self._memory_store is None:
            return {"status": "skipped", "reason": "No memory store configured."}

        try:
            from agents.knowledge_extractor import KnowledgeExtractor
            extractor = KnowledgeExtractor(
                memory_store=self._memory_store,
                llm_router=self._llm_router,
            )
            run_id   = state["run_id"]
            dep_graph = state["dependency_graph"]
            conv_log_path = Path("logs") / f"{run_id}-conversion-log.json"
            val_path      = Path("logs") / f"{run_id}-validation-report.json"

            result = extractor.extract(
                run_id=run_id,
                dependency_graph=dep_graph,
                conversion_log_path=conv_log_path,
                validation_report_path=val_path if val_path.exists() else None,
            )
            logger.info("[OK] Memory recorded: %s", result)
            return {"status": "success", **result}
        except Exception as exc:  # noqa: BLE001
            logger.warning("record_memory failed (non-fatal): %s", exc)
            return {"status": "error", "error": str(exc)}

    def _action_escalate_human(self, state: dict, reason: str = "", **kwargs) -> dict:
        """Escalate to a human operator."""
        import sys
        msg = reason or kwargs.get("message", "Orchestrator requires human input.")
        print(f"\n[ESCALATE] {msg}")
        if sys.stdin.isatty():
            try:
                response = input("Human response (press Enter to continue, or type feedback): ")
                state["human_escalation_response"] = response
                logger.info("Human escalation response: %s", response[:100])
                return {"status": "escalated", "human_response": response}
            except (EOFError, KeyboardInterrupt):
                pass
        logger.warning("escalate_human: non-interactive session, continuing.")
        return {"status": "escalated", "human_response": ""}

    def _action_done(self, state: dict, summary: str = "", **kwargs) -> dict:
        """Signal pipeline completion."""
        logger.info("OrchestratorAgent: done. %s", summary or "")
        # Determine exit code from downstream results
        conv    = state.get("conversion_summary", {})
        val     = state.get("validation_result", {})
        ui      = state.get("ui_consistency_result", {})
        intg    = state.get("integration_result", {})
        verify  = state.get("verification_result", {})

        failed = (
            conv.get("flagged", 0) > 0
            or val.get("status") == "failed"
            or ui.get("status") == "failed"
            or intg.get("status") == "partial"
            or verify.get("status") == "failed"
        )
        exit_code = 1 if failed else 0
        state["exit_code"]  = exit_code
        state["exit_dict"]  = {"status": "success" if not failed else "failed",
                               "summary": summary, "exit_code": exit_code}
        return state["exit_dict"]

    # ------------------------------------------------------------------
    # Backend selection and dispatch
    # ------------------------------------------------------------------

    def _run_backend(
        self,
        backend_name: str,
        action_registry: dict,
        system_prompt: str,
    ) -> dict:
        """Instantiate the selected backend and run it."""
        try:
            if backend_name == "google_adk":
                return self._run_adk_backend(action_registry, system_prompt)
            return self._run_internal_backend(action_registry, system_prompt)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Orchestration backend '%s' failed: %s. Falling back to sequential pipeline.",
                backend_name, exc,
            )
            exit_code = self._fallback_sequential()
            return {"exit_code": exit_code}

    def _run_internal_backend(
        self, action_registry: dict, system_prompt: str
    ) -> dict:
        """Run using InternalOrchestrationBackend."""
        from agents.orchestrator_backends.internal_backend import (
            InternalOrchestrationBackend,
        )

        backend = InternalOrchestrationBackend(
            llm_router=self._llm_router,
            system_prompt=system_prompt,
            action_registry=action_registry,
            orchestration_config=self._orch_cfg,
            memory_context=self._state.get("memory_context"),
        )
        return backend.run(self._state)

    def _run_adk_backend(
        self, action_registry: dict, system_prompt: str
    ) -> dict:
        """Run using ADKOrchestrationBackend (falls back to internal if unavailable)."""
        from agents.orchestrator_backends.adk_backend import (
            ADKOrchestrationBackend,
        )

        backend = ADKOrchestrationBackend(
            llm_router=self._llm_router,
            system_prompt=system_prompt,
            action_registry=action_registry,
            orchestration_config=self._orch_cfg,
            memory_context=self._state.get("memory_context"),
        )
        return backend.run(self._state)

    # ------------------------------------------------------------------
    # System prompt loader
    # ------------------------------------------------------------------

    def _load_system_prompt(self) -> str:
        """Load the orchestrator system prompt, injecting memory context."""
        prompt_path = Path(__file__).parent.parent / "prompts" / "orchestrator_system.txt"
        try:
            base = prompt_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning(
                "prompts/orchestrator_system.txt not found — using minimal system prompt."
            )
            base = _MINIMAL_SYSTEM_PROMPT

        # Inject mode note
        tool_use_cfg = self._orch_cfg.get("tool_use", "auto")
        if tool_use_cfg == "never" or (
            tool_use_cfg == "auto"
            and self._llm_router is not None
            and not self._llm_router.supports_tool_use
        ):
            mode_note = "[REACT TEXT MODE] Use THOUGHT / ACTION / PARAMS format."
        else:
            mode_note = "[TOOL-USE MODE] Use the provided tools to call pipeline stages."

        memory_context = self._state.get("memory_context")
        if memory_context and memory_context.context_summary:
            base += (
                "\n\nMIGRATION MEMORY (apply these proven patterns and preferences):\n"
                + memory_context.context_summary
            )

        return f"{base}\n\n{mode_note}"

    # ------------------------------------------------------------------
    # Approved plan builder (mirrors main.py._build_approved_plan)
    # ------------------------------------------------------------------

    def _build_approved_plan(self, state: dict) -> dict:
        """Build approved_plan dict from state (used post-approval)."""
        import re
        from pathlib import Path as _Path

        config           = state["config"]
        dependency_graph = state["dependency_graph"]
        run_id           = state["run_id"]
        target           = state["target"]
        feature_root     = state.get("feature_root") or ""
        output_root      = state.get("output_root") or str(
            _Path("output") / state["feature_name"]
        )

        steps = []
        phase_labels = {"frontend": "C", "backend": "B", "database": "A"}
        phase_counts = {"A": 0, "B": 0, "C": 0}
        skillset     = config["skillset"]
        structure_key = (
            "project_structure_hrsa_pprs"
            if target == "hrsa_pprs"
            else "project_structure"
        )

        mappings_index = config.get("mappings_index", {})

        for node in dependency_graph.get("nodes", []):
            node_type = node.get("type", "frontend")
            phase     = phase_labels.get(node_type, "C")
            phase_counts[phase] += 1
            step_id = f"Step {phase}{phase_counts[phase]}"

            pattern    = node.get("pattern", "")
            mapping_id = self._infer_mapping_id(pattern, node_type)
            mapping    = mappings_index.get(mapping_id, {})

            source_rel = node["id"]
            target_rel = self._derive_target_path(
                node, mapping, skillset, structure_key=structure_key
            )

            rule_ids = ["RULE-003"]
            if node.get("endpoints"):
                rule_ids.insert(0, "RULE-001")
            if node_type == "frontend":
                rule_ids.append("RULE-002")
            if node_type == "database":
                rule_ids.append("RULE-009")

            steps.append({
                "id":          step_id,
                "description": f"Convert {source_rel} -> {target_rel}",
                "source_file": source_rel,
                "target_file": target_rel,
                "mapping_id":  mapping_id,
                "rule_ids":    rule_ids,
                "rationale":   mapping.get("notes", "Direct translation per RULE-003."),
            })

        steps.sort(key=lambda s: s["id"])

        return {
            "feature_name":     dependency_graph["feature_name"],
            "feature_root":     feature_root,
            "output_root":      output_root,
            "run_id":           run_id,
            "conversion_steps": steps,
        }

    @staticmethod
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
        return {
            "frontend": "MAP-001",
            "backend":  "MAP-003",
            "database": "MAP-004",
        }.get(node_type, "MAP-001")

    @staticmethod
    def _derive_target_path(
        node: dict, mapping: dict, skillset: dict, structure_key: str = "project_structure"
    ) -> str:
        import re
        from pathlib import Path as _Path

        node_type = node.get("type", "frontend")
        exports   = node.get("exports", [])
        name      = exports[0] if exports else _Path(node["id"]).stem

        def to_snake(s: str) -> str:
            s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", s)
            return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()

        target_struct = skillset.get(structure_key, skillset.get("project_structure", {}))
        feature_name  = node.get("id", "").split("/")[0].lower()

        if node_type == "frontend":
            comp_root = target_struct.get("frontend", {}).get(
                "components_root", "frontend/src/components/{feature_name}/"
            )
            base = comp_root.replace("{feature_name}", feature_name)
            return f"{base}{name}.tsx"

        if node_type == "backend":
            api_root = target_struct.get("backend", {}).get(
                "api_root", "api/src/api/{feature_name}/"
            )
            base = api_root.replace("{feature_name}", to_snake(feature_name))
            return f"{base}{to_snake(name)}_routes.py"

        if node_type == "database":
            svc_root = target_struct.get("backend", {}).get(
                "services_root", "api/src/services/{feature_name}/"
            )
            base = svc_root.replace("{feature_name}", to_snake(feature_name))
            return f"{base}{to_snake(name)}_service.py"

        return f"output/{node['id']}"

    # ------------------------------------------------------------------
    # Knowledge extraction (post-pipeline)
    # ------------------------------------------------------------------

    def _run_knowledge_extraction(self, dependency_graph: dict, run_id: str) -> None:
        """Mine run artefacts and persist learnings (non-fatal on failure)."""
        if self._memory_store is None:
            return
        try:
            from agents.knowledge_extractor import KnowledgeExtractor
            extractor = KnowledgeExtractor(
                memory_store=self._memory_store,
                llm_router=self._llm_router,
            )
            conv_log_path = Path("logs") / f"{run_id}-conversion-log.json"
            val_path      = Path("logs") / f"{run_id}-validation-report.json"
            result = extractor.extract(
                run_id=run_id,
                dependency_graph=dependency_graph,
                conversion_log_path=conv_log_path,
                validation_report_path=val_path if val_path.exists() else None,
            )
            logger.info("Knowledge extraction complete: %s", result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Knowledge extraction failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Sequential fallback
    # ------------------------------------------------------------------

    def _fallback_sequential(self) -> int:
        """Fall back to the standard sequential run_pipeline()."""
        import importlib
        try:
            main_module = importlib.import_module("main")
            logger.info("OrchestratorAgent: falling back to sequential run_pipeline().")
            return main_module.run_pipeline(self._args)
        except Exception as exc:  # noqa: BLE001
            logger.error("Fallback sequential pipeline failed: %s", exc)
            return 1


# ---------------------------------------------------------------------------
# Minimal system prompt (used if prompts/orchestrator_system.txt is missing)
# ---------------------------------------------------------------------------

_MINIMAL_SYSTEM_PROMPT = """\
You are an AI migration pipeline orchestrator. You have access to pipeline tools.
Use them to orchestrate the migration in this order:
1. generate_plan  → revise_plan (if needed) → approve_plan
2. convert → validate → ui_audit → integrate → verify
3. record_memory → done

Call one tool at a time. When you encounter failures, retry or revise as needed.
When all stages are complete, call done with a brief summary.
"""

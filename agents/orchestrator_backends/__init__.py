"""
Orchestrator Backends
======================
Pluggable workflow controllers for OrchestratorAgent.

Two backends are provided:

    internal    (default) — built-in ReAct text loop or native tool-use loop,
                            depending on provider capability.  Zero extra deps.

    google_adk  (optional) — wraps pipeline stages as Google ADK Tool objects
                             and delegates workflow control to an ADK Agent.
                             Requires: pip install google-adk

Both backends expose the same interface:

    class <Backend>:
        def run(self, state: dict) -> dict
        # state  — mutable pipeline state dict passed through all stages
        # returns — final exit dict compatible with run_pipeline()

The OrchestratorAgent selects the backend based on the 'backend' key in
orchestration_config (default: "internal").
"""

from __future__ import annotations

__all__ = ["InternalOrchestrationBackend", "ADKOrchestrationBackend"]

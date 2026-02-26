"""
wizard.generator — Prompt & Config Content Generators
======================================================
Produces the text content of all artefacts that the wizard writes:
  - LLM system prompts  (plan + conversion)
  - Target stack reference prompt
  - Agent job template (YAML)
  - skillset-config.json entries (target stack + project structure)

Every function accepts the *answers* dict returned by wizard.collector
and returns a string (or dict for JSON config entries).
"""

import textwrap
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Prompt generators
# ---------------------------------------------------------------------------

def generate_plan_system_prompt(answers: dict) -> str:
    """
    Return the content of  prompts/plan_system_<target_id>.txt.

    The prompt instructs the LLM to act as a senior migration architect for
    the specific source → target stack pair described in *answers*.
    """
    source = answers["source"]
    target = answers["target"]
    target_patterns = _format_target_patterns(target)

    return textwrap.dedent(f"""\
        You are a senior migration architect specialising in modernising legacy
        {source['framework']} / {source['backend_framework']} applications to
        {target['framework']} / {target['backend_framework']}.

        Your task is to generate a structured Plan Document in Markdown.

        STRICT RULES -- violations will be rejected by the pipeline:
        1. Do NOT generate any production code in this document. This is a PLAN only.
        2. Every component mapping MUST reference a specific rule by its RULE-XXX id.
        3. If a mapping cannot be determined with >75% confidence, mark it AMBIGUOUS
           and do NOT guess. List what information is needed to resolve it.
        4. Business logic that cannot be cleanly translated must be marked BLOCKED,
           not interpreted or improvised.
        5. Any import from flagged platform or internal libraries that have no
           equivalent in the target stack must be flagged under Risk Areas and
           marked BLOCKED until resolved.
        6. Output ONLY valid Markdown matching the schema below. No extra prose.
        7. The Plan Document is a contract. Humans will sign off before execution.

        SOURCE STACK: {source['framework']} / {source['backend_framework']}
          Language:   {source['language']}
          Database:   {source['database']}
          Patterns:   {', '.join(source.get('component_patterns', [])) or 'Unknown'}

        TARGET STACK: {target['framework']} / {target['backend_framework']}
          Language:   {target['language']}
          Database:   {target['database']}
          Patterns:   {', '.join(target.get('component_patterns', [])) or 'Unknown'}

        TARGET STACK PATTERNS (from {target['name']} reference codebase):
        {target_patterns}

        PLAN DOCUMENT SCHEMA (output exactly this Markdown structure):

        # Migration Plan: {{feature_name}}
        **Generated:** {{generated_at}}
        **Run ID:** {{run_id}}
        **Source:** {{feature_root}}
        **Target Stack:** {target['framework']} / {target['backend_framework']}

        ---

        ## 1. Current Architecture
        | Component | Type | Pattern | Source File |
        |---|---|---|---|
        ... (one row per source file) ...

        ## 2. Target Architecture
        | Source File | Target Component | Mapping | Rules | Template |
        |---|---|---|---|---|
        ... (one row per planned output file) ...

        ## 3. Conversion Steps
        ### Step A1: database files (if any)
        ### Step B1: backend files
        ### Step C1: frontend files

        ## 4. Business Logic Inventory
        | Method / Endpoint | Source | Translation | Risk |
        |---|---|---|---|

        ## 5. Risk Areas
        (BLOCKING and WARNING flags identified during scoping)

        ## 6. Acceptance Criteria
        - [ ] ...

        ## 7. Approval
        **Status:** PENDING
        **Reviewer:**
        **Date:**
    """)


def generate_conversion_system_prompt(answers: dict) -> str:
    """
    Return the content of  prompts/conversion_system_<target_id>.txt.

    Contains {{rules_text}} and {{target_stack_summary}} placeholders that
    ConversionAgent fills at runtime.
    """
    source = answers["source"]
    target = answers["target"]

    return textwrap.dedent(f"""\
        You are a code conversion engine. You translate legacy
        {source['framework']} / {source['backend_framework']} source code into
        the modern target stack: {target['framework']} / {target['backend_framework']}.

        MANDATORY GUARDRAILS -- violations will be rejected:
        {{rules_text}}

        CRITICAL CONSTRAINTS:
        - Do NOT optimize, refactor, or improve logic. Translate VERBATIM.
        - Do NOT change REST endpoint routes, HTTP methods, or payload field names.
        - Preserve ALL CSS class names from the source HTML template exactly.
        - If the source depends on a platform-specific library with no target
          equivalent, respond with:
            AMBIGUOUS: <clear description of what the library provides and why you
                        cannot proceed>
        - If you cannot translate ANY section with high confidence, respond with:
            AMBIGUOUS: <reason>
          and do NOT generate code for that section.
        - Output ONLY the converted code. No explanations, no markdown fences, no
          comments unless the original source had comments.
        - Preserve all original developer comments, translated to the target language.

        TARGET STACK PATTERNS:
        {{target_stack_summary}}
    """)


def generate_target_stack_prompt(answers: dict) -> str:
    """
    Return the content of  prompts/conversion_target_stack_<target_id>.txt.

    A human-readable reference of the target stack patterns injected into
    the conversion system prompt at runtime.
    """
    target = answers["target"]
    fe = target.get("frontend_details", {})
    be = target.get("backend_details", {})
    db = target.get("database_details", {})

    lines: list[str] = [
        f"TARGET STACK: {target['framework']} / {target['backend_framework']}",
        "",
    ]

    if fe:
        lines.append(f"FRONTEND ({target['framework']} / {target['language']}):")
        for k, v in fe.items():
            lines.append(f"  - {k}: {v}")
        lines.append("")

    if be:
        lines.append(f"BACKEND ({target['backend_framework']}):")
        for k, v in be.items():
            lines.append(f"  - {k}: {v}")
        lines.append("")

    if db:
        lines.append(f"DATABASE ({target['database']}):")
        for k, v in db.items():
            lines.append(f"  - {k}: {v}")

    if not (fe or be or db):
        lines += [
            f"  Framework: {target['framework']}",
            f"  Backend:   {target['backend_framework']}",
            f"  Database:  {target['database']}",
            f"  Language:  {target['language']}",
        ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Job template generator
# ---------------------------------------------------------------------------

def generate_job_template(answers: dict) -> str:
    """
    Return the YAML content of  agent-prompts/_template_<target_id>.yaml.
    """
    source    = answers["source"]
    target    = answers["target"]
    target_id = answers["target_id"]
    source_path = source.get("root", "")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Suggest a feature-folder hint based on detected sub-folders
    feature_hint = "<FeatureName>"
    if source_path:
        for candidate in [
            "src/app/features/",
            "src/components/",
            "wwwroot/",
            "src/",
        ]:
            if (Path(source_path) / candidate).exists():
                feature_hint = f"{candidate}<FeatureName>"
                break

    return textwrap.dedent(f"""\
        # ============================================================
        # AI Migration Tool -- Job File Template
        # Target: {target['name']} ({target['framework']} / {target['backend_framework']})
        # Generated by: setup_wizard.py on {today}
        # ============================================================
        # Copy this file, fill in your values, then run:
        #   python run_agent.py --job agent-prompts/your-job.yaml
        #
        # Naming convention: migrate-<FeatureName>-{target_id}.yaml
        # ============================================================

        job:
          name: "Migrate <FeatureName> -> {target['name']}"
          description: >
            Migrate the <FeatureName> feature from
            {source['framework']} / {source['backend_framework']} to
            {target['framework']} / {target['backend_framework']}.

        pipeline:
          # REQUIRED -- absolute path to the legacy feature folder
          feature_root: "{source_path}/{feature_hint}"

          # REQUIRED -- human-readable name (used in filenames and logs)
          feature_name: "<FeatureName>"

          # Pipeline mode: scope | plan | full
          mode: "plan"

          # Target stack identifier (registered by setup_wizard.py)
          target: "{target_id}"

          # Output directory (null = default output/<feature_name>/)
          output_root: null

          # true = no files written, logs only
          dry_run: false

          # true = skip human approval (TESTING ONLY)
          auto_approve: false

          # true = ignore completed-run cache, re-run from scratch
          force: false

        llm:
          # null = auto-detect from environment variables
          provider: null   # anthropic | openai | openai_compat | ollama | llamacpp

          # null = provider default (claude-opus-4-5 / gpt-4o / llama3.2)
          model: null

          # true = template-only mode, no API key required
          no_llm: false

          # Optional -- only set if needed
          base_url: null
          model_path: null
          ollama_host: null
          max_tokens: null
          temperature: null

        notes: |
          Source:  {source['framework']} / {source['backend_framework']}
                   Root: {source_path}
          Target:  {target['framework']} / {target['backend_framework']}
                   Root: {target.get('root', '(not set)')}

          Add any context the agent or human reviewer should know:
          - Business domain of this feature
          - Known platform library dependencies that may not have target equivalents
          - Cross-feature imports that need human review
          - Expected output files
    """)


# ---------------------------------------------------------------------------
# skillset-config.json content builders
# ---------------------------------------------------------------------------

def build_target_stack_entry(answers: dict) -> dict:
    """
    Build the  target_stack_<target_id>  dict for skillset-config.json.
    """
    target = answers["target"]
    fe = target.get("frontend_details", {})
    be = target.get("backend_details", {})
    db = target.get("database_details", {})

    return {
        "frontend": {
            "framework": target.get("framework", "Unknown"),
            "language":  target.get("language", "Unknown"),
            **fe,
        },
        "backend": {
            "framework": target.get("backend_framework", "Unknown"),
            **be,
        },
        "database": {
            "engine": target.get("database", "Unknown"),
            **db,
        },
    }


def build_project_structure_entry(answers: dict) -> dict:
    """
    Build the  project_structure_<target_id>  dict for skillset-config.json,
    inferring folder patterns from the wizard answers and codebase detection.
    """
    target = answers["target"]
    fe_root = target.get("frontend_root", "frontend/")
    be_root = target.get("backend_root", "backend/")
    fe      = target.get("frontend_details", {})
    be      = target.get("backend_details", {})

    component_dir = fe.get("components_dir", "components/")
    services_fe   = fe.get("services_dir", "services/")
    routes_dir    = be.get("routes_dir", "routes/")
    services_be   = be.get("services_dir", "services/")

    frontend_struct: dict[str, str] = {
        "components_root": f"{fe_root}{component_dir}{{feature_name}}/",
        "services_root":   f"{fe_root}{services_fe}",
        "test_suffix":     fe.get("test_suffix", ".test.tsx"),
    }
    if "styling" in fe:
        frontend_struct["styling"] = fe["styling"]
    if "barrel_export" in fe:
        frontend_struct["barrel_export"] = fe["barrel_export"]
    if "component_structure" in fe:
        frontend_struct["component_structure"] = fe["component_structure"]

    backend_struct: dict[str, str] = {
        "api_root":      f"{be_root}{routes_dir}",
        "services_root": f"{be_root}{services_be}",
    }
    if "repositories_dir" in be:
        backend_struct["repositories_root"]       = f"{be_root}{be['repositories_dir']}"
        backend_struct["repository_file_pattern"] = be.get("repository_file_pattern", "{feature_name}_repository.py")
    if "route_file_pattern" in be:
        backend_struct["route_file_pattern"]   = be["route_file_pattern"]
    if "service_file_pattern" in be:
        backend_struct["service_file_pattern"] = be["service_file_pattern"]
    if "architecture" in be:
        backend_struct["architecture"] = be["architecture"]

    return {
        "frontend": frontend_struct,
        "backend":  backend_struct,
    }


def build_source_stack_entry(answers: dict) -> dict:
    """
    Build an optional  source_stack_<source_name>  dict for skillset-config.json.
    Only generated when the source name is custom (not the built-in defaults).
    """
    source = answers["source"]
    return {
        "frontend": {
            "framework":          source.get("framework", "Unknown"),
            "language":           source.get("language", "Unknown"),
            "component_patterns": source.get("component_patterns", []),
        },
        "backend": {
            "framework": source.get("backend_framework", "Unknown"),
        },
        "database": {
            "engine": source.get("database", "Unknown"),
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _format_target_patterns(target: dict) -> str:
    """Format target frontend/backend/database details as bullet lines."""
    lines: list[str] = []
    for section in ("frontend_details", "backend_details", "database_details"):
        for k, v in target.get(section, {}).items():
            lines.append(f"  - {k}: {v}")
    return "\n".join(lines) if lines else "  (see target codebase reference)"

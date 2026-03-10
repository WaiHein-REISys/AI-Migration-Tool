# Job Files Reference

A **job file** is a self-contained YAML document that describes one migration job.
Job files live in `agent-prompts/` and are the primary interface between agents/humans
and the migration pipeline.

---

## Creating a Job File

### Option A — Non-interactive (agent mode)
```bash
python run_agent.py --new-job \
  --feature ActionHistory \
  --target snake_case \
  --non-interactive --json
```

### Option B — Copy a template
```bash
cp agent-prompts/_template_snake_case.yaml agent-prompts/migrate-MyFeature.yaml
# Edit the file — set feature_root and feature_name at minimum
```

### Available templates

| Template | Target |
|---|---|
| `_template.yaml` | `simpler_grants` / `hrsa_pprs` (built-in) |
| `_template_modern.yaml` | `modern` wizard-generated target |
| `_template_snake_case.yaml` | `snake_case` wizard-generated target |
| `_template_<id>.yaml` | Any other wizard-generated target |

---

## Full Field Reference

```yaml
# ─────────────────────────────────────────────
# job — metadata (optional, shown in logs)
# ─────────────────────────────────────────────
job:
  name: "Migrate ActionHistory -> snake_case"
  description: >
    Migrate the ActionHistory feature from
    Angular 2 / ASP.NET Core MVC to
    Next.js / Python Flask.

# ─────────────────────────────────────────────
# pipeline — core settings
# ─────────────────────────────────────────────
pipeline:

  # REQUIRED — absolute path to the legacy feature folder.
  # Examples:
  #   Windows:     "<FEATURE_ROOT_WINDOWS>"
  #   macOS/Linux: "<FEATURE_ROOT_UNIX>"
  feature_root: "<YOUR_SOURCE_ROOT>/src/FeatureName"

  # REQUIRED — human-readable name. Used in filenames, logs, and output paths.
  feature_name: "FeatureName"

  # Pipeline mode:
  #   scope  — dependency graph only (no plan, no code)
  #   plan   — scope + LLM plan generation (default, safe)
  #   full   — scope + plan + approval + code conversion
  mode: "plan"

  # Target stack identifier.
  # Built-in: simpler_grants | hrsa_pprs | snake_case
  # Custom:   any <id> registered by the Setup Wizard
  target: "snake_case"

  # Output directory. null → default: output/<feature_name>/
  output_root: null

  # true = simulate the run — no files written, logs only.
  dry_run: false

  # true = skip the human approval gate.
  # WARNING: use only for testing / CI. In agent mode, prefer --approve-plan.
  auto_approve: false

  # true = re-run even if this feature was already completed.
  # The completed-run registry in logs/completed-runs.json is ignored.
  force: false

  # OPTIONAL — absolute path to the target codebase root.
  # Used by Stage 7 (Integration) to place converted files directly into the
  # target repo. Leave null to generate output only (no placement).
  # The Setup Wizard populates this automatically from target.root.
  target_root: null

# ─────────────────────────────────────────────
# integration — Stage 7 settings
# ─────────────────────────────────────────────
integration:

  # false = skip Stage 7 entirely. Pipeline still succeeds.
  enabled: true

  # true = auto-append missing Python packages to target_root/requirements.txt
  # with a "# added by ai-migration-tool" comment. JS deps are reported only.
  add_dependencies: true

  # true = generate a DB migration script when structural checks detect new
  # columns or tables. Alembic (.py) for SQLAlchemy targets; raw SQL for
  # psycopg2/HRSA targets.
  generate_migration: true

# ─────────────────────────────────────────────
# ui_consistency — Stage 6b settings (UI audit)
# ─────────────────────────────────────────────
ui_consistency:

  # false = skip Stage 6b entirely.
  enabled: true

  # true = emit a Storybook .stories.tsx stub next to each converted UI component.
  # The stub includes a default story and an empty args table. Useful as a starting
  # point for visual regression testing.
  generate_stories: false

  # true = fail the pipeline (exit code 1) if any CSS classes from the Angular source
  # are missing from the converted React/TSX output.
  # Set false to demote missing-class findings to warnings only.
  fail_on_missing_classes: true

# ─────────────────────────────────────────────
# verification — Stage 8 settings (end-to-end checks)
# ─────────────────────────────────────────────
verification:

  # false = skip Stage 8 command-based verification.
  enabled: false

  # Working directory for commands. null = target_root if available,
  # otherwise output_root.
  cwd: null

  # Commands run in order. Use project-native checks here
  # (examples: npm test, npm run build, pytest, dotnet test).
  commands: []

  # Optional env vars for verification commands.
  env: {}

  # true = stop on first failing command and fail the pipeline.
  fail_on_error: true

# ─────────────────────────────────────────────
# llm — LLM provider settings
# ─────────────────────────────────────────────
llm:

  # true = disable all LLM calls; use Jinja2 template scaffolds only.
  # No API key needed. Useful for testing the pipeline structure.
  no_llm: false

  # LLM provider. null = auto-detect from environment variables.
  # Choices: anthropic | openai | openai_compat | ollama | llamacpp | vertex_ai | subprocess
  provider: null

  # Model name/ID. null = provider default.
  # Examples: claude-opus-4-5, gpt-4o, llama3.2, deepseek-coder
  model: null

  # Base URL for OpenAI-compatible or Azure OpenAI endpoints.
  # Example: "http://localhost:1234/v1"  (LM Studio)
  base_url: null

  # Absolute path to a local GGUF model file. (llamacpp only)
  model_path: null

  # Ollama server URL. null → default: http://localhost:11434
  ollama_host: null

  # Max tokens to generate per LLM call. null → 8192
  max_tokens: null

  # Sampling temperature. null → 0.2
  temperature: null

  # Per-call timeout in seconds. null → 120
  # Code generation can take 3-5+ min; increase for large files.
  timeout: null

  # Azure OpenAI API version (openai provider + Azure endpoint only)
  # Example: "2024-08-01-preview"
  api_version: null

# ─────────────────────────────────────────────
# orchestration — Multi-Agent Orchestrator settings
# ─────────────────────────────────────────────
orchestration:

  # false (default) = run the fixed sequential pipeline (backwards compatible).
  # true = hand control to OrchestratorAgent which dynamically decides which
  # stage to run next, auto-retries failed conversions, and auto-revises plans.
  # Requires a live LLM (no_llm: true is incompatible with orchestration).
  enabled: false

  # true (default) = extract patterns + preferences after every run and store
  # them in config/memory/*.json. Runs on BOTH sequential and orchestrated paths.
  # Memory context is injected into PlanAgent + ConversionAgent prompts.
  learning: true

  # Max number of automatic plan revisions the orchestrator may trigger before
  # escalating to a human (or failing, depending on escalate_on_fail).
  max_plan_revisions: 2

  # true = ask for human input when the orchestrator cannot resolve an ambiguity
  # after max_plan_revisions attempts.
  # false = fail the run immediately on unresolvable ambiguity.
  escalate_on_fail: true

  # Orchestration backend:
  #   internal    — built-in ReAct / native-tool loop (default, no extra packages)
  #   google_adk  — Google Agent Development Kit (requires pip install google-adk;
  #                 auto-falls-back to internal if not installed)
  backend: internal

  # Tool-use mode for the orchestrator:
  #   auto   — uses native_tools for Anthropic/OpenAI/Vertex AI; react_text for others
  #   always — force native_tools (provider must support it)
  #   never  — force react_text (THOUGHT/ACTION/PARAMS text parsing)
  tool_use: auto

# ─────────────────────────────────────────────
# notes — free-form context for agents and reviewers
# ─────────────────────────────────────────────
notes: |
  Source:  Angular 2 / ASP.NET Core MVC
           Root: <YOUR_SOURCE_ROOT>
  Target:  Next.js / Python Flask
           Root: <YOUR_TARGET_ROOT>

  Add any context the agent or human reviewer should know:
  - Business domain of this feature
  - Known platform library dependencies (pfm-*, Platform.*)
  - Cross-feature imports that need human review
  - Expected output file paths
```

---

## Field Defaults

| Field | Default | Notes |
|---|---|---|
| `job.name` | `""` | Optional — cosmetic only |
| `job.description` | `""` | Optional — cosmetic only |
| `pipeline.mode` | `"plan"` | Can be overridden with `--mode` at CLI |
| `pipeline.target` | `"simpler_grants"` | |
| `pipeline.output_root` | `output/<feature_name>/` | |
| `pipeline.dry_run` | `false` | |
| `pipeline.auto_approve` | `false` | |
| `pipeline.force` | `false` | |
| `pipeline.target_root` | `null` | Populated automatically by Setup Wizard from `target.root` |
| `integration.enabled` | `true` | Set `false` to skip Stage 7 entirely |
| `integration.add_dependencies` | `true` | Auto-append missing Python deps to `requirements.txt` |
| `integration.generate_migration` | `true` | Generate DB migration script on schema changes |
| `ui_consistency.enabled` | `true` | Set `false` to skip Stage 6b UI audit entirely |
| `ui_consistency.generate_stories` | `false` | Set `true` to emit Storybook `.stories.tsx` stubs |
| `ui_consistency.fail_on_missing_classes` | `true` | Set `false` to demote missing CSS classes to warnings |
| `verification.enabled` | `false` | Set `true` to run Stage 8 command-based verification |
| `verification.cwd` | `null` | Defaults to `target_root`, then `output_root` |
| `verification.commands` | `[]` | Ordered shell commands for build/test/lint verification |
| `verification.fail_on_error` | `true` | Fail pipeline immediately on first failing command |
| `llm.no_llm` | `false` | |
| `llm.provider` | auto-detect | `anthropic` \| `openai` \| `openai_compat` \| `ollama` \| `llamacpp` \| `vertex_ai` \| `subprocess` |
| `llm.model` | provider default | |
| `llm.max_tokens` | `8192` | |
| `llm.temperature` | `0.2` | |
| `llm.timeout` | `120` | |
| `orchestration.enabled` | `false` | `true` = LLM-driven `OrchestratorAgent`; `false` = sequential pipeline |
| `orchestration.learning` | `true` | Extract patterns + preferences after every run |
| `orchestration.max_plan_revisions` | `2` | Max automatic plan revisions before escalation |
| `orchestration.escalate_on_fail` | `true` | Ask human on unresolvable ambiguity |
| `orchestration.backend` | `internal` | `internal` \| `google_adk` |
| `orchestration.tool_use` | `auto` | `auto` \| `always` \| `never` |

---

## Naming Convention

```
agent-prompts/migrate-<featurename>-<target>.yaml
```

Examples:
- `migrate-actionhistory-snake_case.yaml`
- `migrate-grantmanagement-hrsa_pprs.yaml`
- `migrate-userauth-simpler_grants.yaml`

The `--new-job` command generates names in this format automatically.

---

## Examples

### Minimal job (plan only, auto-detect LLM)
```yaml
pipeline:
  feature_root: "<YOUR_SOURCE_ROOT>/src/ActionHistory"
  feature_name: "ActionHistory"
  mode: "plan"
  target: "snake_case"
```

### Full conversion with Ollama
```yaml
pipeline:
  feature_root: "<YOUR_SOURCE_ROOT>/src/ActionHistory"
  feature_name: "ActionHistory"
  mode: "full"
  target: "snake_case"

llm:
  provider: ollama
  model: "llama3.2"
  ollama_host: "http://localhost:11434"
  timeout: 600
```

### Template-only (no LLM, no API key)
```yaml
pipeline:
  feature_root: "<YOUR_SOURCE_ROOT>/src/ActionHistory"
  feature_name: "ActionHistory"
  mode: "full"
  target: "snake_case"
  auto_approve: true

llm:
  no_llm: true
```

### Dry run (simulate, no files written)
```yaml
pipeline:
  feature_root: "<YOUR_SOURCE_ROOT>/src/ActionHistory"
  feature_name: "ActionHistory"
  mode: "full"
  target: "snake_case"
  dry_run: true
```

### Azure OpenAI
```yaml
pipeline:
  feature_root: "<YOUR_SOURCE_ROOT>/src/ActionHistory"
  feature_name: "ActionHistory"
  mode: "plan"
  target: "snake_case"

llm:
  provider: openai
  model: "gpt-4o"
  base_url: "https://<resource>.openai.azure.com/"
  api_version: "2024-08-01-preview"
```

### Google Gemini API
```yaml
pipeline:
  feature_root: "<YOUR_SOURCE_ROOT>/src/ActionHistory"
  feature_name: "ActionHistory"
  mode: "full"
  target: "snake_case"
  auto_approve: false

llm:
  provider: vertex_ai
  model: "gemini-2.0-flash"   # or gemini-1.5-pro for higher quality
  # GOOGLE_API_KEY must be set in your environment
```

### LLM-driven orchestration (Anthropic + internal backend)
```yaml
pipeline:
  feature_root: "<YOUR_SOURCE_ROOT>/src/ActionHistory"
  feature_name: "ActionHistory"
  mode: "full"
  target: "snake_case"

llm:
  provider: anthropic
  model: "claude-opus-4-5"

orchestration:
  enabled: true          # activate OrchestratorAgent
  learning: true         # accumulate patterns in config/memory/
  max_plan_revisions: 2
  escalate_on_fail: true
  backend: internal      # built-in ReAct loop
  tool_use: auto         # native_tools for Anthropic
```

### Learning memory only (sequential pipeline + memory)
```yaml
pipeline:
  feature_root: "<YOUR_SOURCE_ROOT>/src/ActionHistory"
  feature_name: "ActionHistory"
  mode: "full"
  target: "snake_case"

llm:
  provider: anthropic

orchestration:
  enabled: false   # sequential pipeline — OrchestratorAgent NOT used
  learning: true   # but still extract patterns after the run
```

---

## Running a Job

```bash
# Basic run (uses mode from YAML)
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml

# Override mode at CLI
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml --mode full

# Dry run override
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml --dry-run

# Force re-run
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml --force
```

CLI flags always override the equivalent YAML field.

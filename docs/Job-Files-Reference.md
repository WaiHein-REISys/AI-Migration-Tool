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
  #   Windows:     "C:/Projects/MyApp/src/FeatureName"
  #   macOS/Linux: "/home/user/projects/my-app/src/FeatureName"
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

# ─────────────────────────────────────────────
# llm — LLM provider settings
# ─────────────────────────────────────────────
llm:

  # true = disable all LLM calls; use Jinja2 template scaffolds only.
  # No API key needed. Useful for testing the pipeline structure.
  no_llm: false

  # LLM provider. null = auto-detect from environment variables.
  # Choices: anthropic | openai | openai_compat | ollama | llamacpp
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
| `llm.no_llm` | `false` | |
| `llm.provider` | auto-detect | |
| `llm.model` | provider default | |
| `llm.max_tokens` | `8192` | |
| `llm.temperature` | `0.2` | |
| `llm.timeout` | `120` | |

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

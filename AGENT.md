# AGENT.md — AI Migration Tool — Canonical Workflow Reference
# ============================================================
# This is the single source of truth for all AI agent tools:
#   Claude Code (CLAUDE.md), Windsurf (.windsurfrules),
#   Cursor (.cursor/rules.mdc), GitHub Copilot (.github/copilot-instructions.md)
#
# Tool-specific files contain only their tool's unique requirements and
# reference this file for all shared workflow, configuration, and constraint content.

## Project overview

This is the **AI Migration Tool** — a multi-agent pipeline that migrates
`HAB-GPRSSubmission` (Angular 2 / ASP.NET Core) to a modern target stack.

Source repo:  <YOUR_SOURCE_ROOT>   (HAB-GPRSSubmission — Angular 2 / ASP.NET Core)
Target (A):   <YOUR_TARGET_ROOT_A>  (Next.js 15 / APIFlask / SQLAlchemy)
Target (B):   <YOUR_TARGET_ROOT_B>  (Next.js 16 / Flask 3.0 / psycopg2)

Paths are machine-specific. Run the setup wizard to register your local paths:
  python run_agent.py --setup

---

## First-run: Configuring a new migration target

If migrating to a **new/custom stack** not already registered, run the **Setup Wizard**
before creating job files. The wizard analyses your codebases and generates custom prompts.

```bash
python run_agent.py --setup                          # interactive
python run_agent.py --setup --config wizard.json    # pre-filled JSON
python run_agent.py --setup --dry-run               # preview only
python run_agent.py --setup --list-targets          # list configured targets
```

See `agent-prompts/example-wizard-config.json` for the JSON format.

After setup:
- Custom prompts land in `prompts/plan_system_<id>.txt` etc.
- Job template is at `agent-prompts/_template_<id>.yaml`
- Registry is updated at `config/wizard-registry.json`

---

## How to run a migration

**Always use `run_agent.py` — never invoke `main.py` directly.**

Migration jobs are self-contained YAML files in `agent-prompts/`.

### List available jobs
```bash
python run_agent.py --list-jobs
```

### Run a job
```bash
python run_agent.py --job agent-prompts/migrate-action-history.yaml
```

### Override flags (can append to any command)
```bash
--dry-run       # Preview only — no files written to disk
--force         # Ignore the completed-run cache, re-run from scratch
--auto-approve  # Skip human approval (for testing only)
--verbose       # Show DEBUG-level log output
--mode full     # Override pipeline.mode in the YAML (scope | plan | full)
--json          # Machine-readable JSON output (for agent parsing)
```

---

## Agent-interactive commands

These commands enable a fully autonomous, non-interactive agent workflow.

### 1 — Discover available features
```bash
python run_agent.py --list-features --source <YOUR_SOURCE_ROOT>
python run_agent.py --list-features --json   # JSON output for agent parsing
```

### 2 — Create a job file without any human prompts
```bash
python run_agent.py --new-job \
  --feature ActionHistory \
  --target snake_case \
  --non-interactive \
  --json
```
`--feature` resolves as: absolute path → source-relative path → folder name auto-match.

### 3 — Generate the migration plan (no code written)
```bash
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml
```

### 4 — Check migration status
```bash
python run_agent.py --status --job agent-prompts/migrate-actionhistory-snake_case.yaml
python run_agent.py --status --job agent-prompts/migrate-actionhistory-snake_case.yaml --json
```
Returns: plan generated, plan approved, conversion steps completed/pending/blocked.

### 5a — Approve the plan and run full conversion (agent-driven, no TTY needed)
```bash
python run_agent.py --approve-plan --job agent-prompts/migrate-actionhistory-snake_case.yaml
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml --mode full
```
`--approve-plan` writes `output/<feature>/.approved` marker — the pipeline detects it
and auto-skips the interactive TTY prompt.

### 5b — Revise the plan with feedback (re-generates, clears approval marker)
```bash
python run_agent.py --revise-plan \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml \
  --feedback "Add migration notes for the data-access layer. Flag pfm-auth imports as BLOCKED."
```
Loads the existing dependency graph (no re-scoping). Writes a `-rev.md` plan file.
Removes the `.approved` marker so the revised plan requires explicit re-approval.

---

## Creating a job for a new feature

```bash
# Option A: Fully automated (agent mode)
python run_agent.py --new-job \
  --feature MyFeature --target snake_case \
  --non-interactive --json

# Option B: Manual template copy
# 1. Check available targets (run setup wizard first if target not listed)
python run_agent.py --setup --list-targets

# 2. Copy the right template
cp agent-prompts/_template_<target_id>.yaml agent-prompts/migrate-MyFeature.yaml

# 3. Edit the file — set at minimum:
#    pipeline.feature_root  (absolute path to the legacy feature folder)
#    pipeline.feature_name  (e.g. "MyFeature")
#    pipeline.mode          (plan | scope | full)
#    pipeline.target        (simpler_grants | hrsa_pprs | <custom_target_id>)

# 4. Run it
python run_agent.py --job agent-prompts/migrate-MyFeature.yaml
```

---

## Job file structure reference

```yaml
job:
  name: "Human-readable job name"
  description: "What this migration does"

pipeline:
  feature_root:  "<YOUR_SOURCE_ROOT>/src/.../FeatureName"
  feature_name:  "FeatureName"
  mode:          "plan"           # scope | plan | full
  target:        "snake_case"     # simpler_grants | hrsa_pprs | snake_case | <custom>
  dry_run:       false
  auto_approve:  false
  force:         false
  output_root:   null             # null → default output/<feature_name>/

llm:
  no_llm:    false   # true = template-only scaffold, no API key required
  provider:  null    # null = auto-detect (anthropic|openai|vertex_ai|ollama|openai_compat|llamacpp)
  model:     null    # null = provider default
  base_url:  null    # OpenAI-compatible server URL
  model_path: null   # Path to local GGUF file (llamacpp only)
  ollama_host: null  # Ollama server URL (default: http://localhost:11434)
  max_tokens: null   # Max tokens per LLM call (default: 8192)
  temperature: null  # Sampling temperature (default: 0.2)

orchestration:
  enabled: false           # true = LLM orchestrator; false = sequential pipeline (default)
  learning: true           # extract patterns + preferences after each run
  max_plan_revisions: 2    # max auto-revisions the orchestrator can trigger
  escalate_on_fail: true   # ask human if orchestrator cannot resolve ambiguity
  backend: internal        # internal | google_adk
  tool_use: auto           # auto | always | never

notes: |
  Context for agent / reviewer. E.g.:
  - Known pfm-* platform library dependencies
  - Cross-feature imports requiring human review
  - Expected output file paths
```

---

## Pipeline stages

| Step | Agent | Output |
|---|---|---|
| 1. Config Ingestion | `ConfigIngestionAgent` | Validated config dict |
| 2. Scoping | `ScopingAgent` | `logs/<run-id>-dependency-graph.json` |
| 3. Plan Generation | `PlanAgent` | `plans/<feature>-plan-<ts>.md` |
| 4. Human/Agent Approval | `ApprovalGate` | CLI `yes` OR `output/<feature>/.approved` marker |
| 5. Conversion | `ConversionAgent` | `output/<feature>/` + `logs/<run-id>-conversion-log.*` |
| 6. Validation | `ValidationAgent` | `logs/<run-id>-validation-report.*` |
| 6b. UI Consistency Audit | `UIConsistencyAgent` | `logs/<run-id>-ui-consistency-report.*` |
| 7. Integration & Placement | `IntegrationAgent` | Files placed in `target_root` + `logs/<run-id>-integration-report.*` |
| 8. E2E Verification | `E2EVerificationAgent` | `logs/<run-id>-e2e-verification-report.*` |

---

## Target stacks

| `pipeline.target` | Frontend | Backend | Database |
|---|---|---|---|
| `simpler_grants` | Next.js 15 / React 19 | APIFlask Blueprints | SQLAlchemy 2.0 `Mapped[]` |
| `hrsa_pprs` | Next.js 16 / React 18 | Flask 3.0 Blueprints | psycopg2 raw SQL |
| `snake_case` | Next.js / TypeScript | Flask 3.0 / snake_case naming | Custom SQL scripts |
| `<custom>` | Wizard-configured | Wizard-configured | Wizard-configured |

---

## Important constraints

- **Entry point:** Always `run_agent.py` — never `main.py`
- **Output dirs:** Never modify `plans/`, `logs/`, `output/`, `checkpoints/` — pipeline outputs
- **Config files:** Never edit `config/skillset-config.json` or `config/rules-config.json` unless asked
- **Prompts:** `prompts/` are plain text — safe to read and suggest edits; control LLM behaviour
- **Mode discipline:** `plan` mode is the safe default (no code written); always review before `full`
- **`auto_approve`:** Testing/demo only — never leave set in production job files
- **Dedup:** If a run for this feature+target already completed, pipeline skips automatically; use `--force` to re-run
- **`config/memory/*.json`** — committable, team-shared learning memory; **should be committed** so the whole team shares migration patterns and preferences
- **`config/wizard-registry.json`** — machine-specific; in `.gitignore`, **do not commit**

---

## Orchestration mode (optional)

Set `orchestration.enabled: true` in a job YAML to activate the **LLM-driven orchestrator** —
an `OrchestratorAgent` that dynamically decides which pipeline stage to run next, auto-retries
failed conversions, and auto-revises plans when ambiguities are detected.

`orchestration.enabled: false` (the **default**) runs the existing fixed sequential pipeline
with zero changes — full backwards compatibility.

**Learning memory** (`learning: true`, also the default) runs on **both** paths — patterns,
preferences, and domain facts are extracted after every run and stored in `config/memory/*.json`.

**Orchestration modes** (auto-selected by provider capability):
- `native_tools` — Anthropic, OpenAI, Vertex AI: structured function/tool calling
- `react_text` — Ollama, llama.cpp, subprocess: parses `THOUGHT / ACTION / PARAMS` text

**Backends:**
- `internal` — built-in ReAct / native-tool loop (default, no extra dependencies)
- `google_adk` — delegates to a Google ADK agent (requires `pip install google-adk`;
  auto-falls-back to `internal` if not installed)

---

## LLM configuration

Auto-detection order (first found wins):
```
ANTHROPIC_API_KEY → OPENAI_API_KEY → GOOGLE_API_KEY (Gemini) → GOOGLE_CLOUD_PROJECT (Vertex AI) → OLLAMA_MODEL → LLM_BASE_URL → LLAMACPP_MODEL_PATH
```

No API key? Set `llm.no_llm: true` for template-only scaffold mode.

### LLM failure behaviour

| Context | Behaviour |
|---|---|
| Run via `run_agent.py` (agent mode) | Soft-fail — returns Jinja2 template scaffold so you can continue |
| Run via `main.py` (CLI / human) | Hard-fail — raises `LLMConfigurationError` with actionable instructions |

`run_agent.py` automatically sets `AI_AGENT_MODE=1` before calling the pipeline.
You can also force agent mode manually:
```bash
set AI_AGENT_MODE=1       # Windows CMD
$env:AI_AGENT_MODE=1      # PowerShell
export AI_AGENT_MODE=1    # bash/zsh
```

---

## Recommended workflow (human-assisted)

When a user asks to "migrate FeatureName":

1. Check configured targets: `python run_agent.py --setup --list-targets`
2. If the target doesn't exist, run the setup wizard: `python run_agent.py --setup`
3. Check: `ls agent-prompts/migrate-featurename*.yaml`
4. If no job file exists: copy `_template_<target>.yaml`, fill in the values
5. Run plan mode: `python run_agent.py --job agent-prompts/migrate-featurename.yaml`
6. Tell the user where the Plan Document was saved (`plans/`)
7. Wait for the user to review and confirm before running `full` mode
8. Run full mode: `python run_agent.py --job ... --mode full`
9. Check conversion results: `logs/<run-id>-conversion-log.md`

## Recommended workflow (autonomous agent mode)

When operating autonomously (Windsurf Cascade, Cursor, Copilot, AntiGravity):

```bash
# 1. Discover features
python run_agent.py --list-features --json

# 2. Create job file (non-interactive)
python run_agent.py --new-job --feature ActionHistory --target snake_case \
                    --non-interactive --json

# 3. Generate plan
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml

# 4. Check status
python run_agent.py --status --job agent-prompts/migrate-actionhistory-snake_case.yaml --json

# 5a. Approve + convert
python run_agent.py --approve-plan --job agent-prompts/migrate-actionhistory-snake_case.yaml
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml --mode full

# 5b. Or revise with feedback
python run_agent.py --revise-plan --job agent-prompts/migrate-actionhistory-snake_case.yaml \
                    --feedback "Flag all pfm-auth imports as BLOCKED."
# Then re-approve and re-run
```

When a user asks to "configure a new migration target" or "set up migration to X":

1. Run the setup wizard: `python run_agent.py --setup`
   - Or for CI/agent mode: fill in `agent-prompts/example-wizard-config.json`
     then: `python run_agent.py --setup --config wizard-config.json --non-interactive`
2. Review generated prompts in `prompts/` (safe to edit for tuning)
3. Use the generated `agent-prompts/_template_<target_id>.yaml` for migrations

---

## Troubleshooting

### "No LLM provider configured" / exit code 2

The pipeline will exit immediately with code `2` and a clear error when `mode: plan` or
`mode: full` is requested but no LLM is reachable.

**Fix:** set one of these environment variables before running:

```bash
# Windows CMD (set persists for this session only)
set ANTHROPIC_API_KEY=sk-ant-...
set OPENAI_API_KEY=sk-...
set GOOGLE_API_KEY=...
set OLLAMA_MODEL=llama3

# PowerShell
$env:ANTHROPIC_API_KEY="sk-ant-..."

# bash / zsh
export ANTHROPIC_API_KEY="sk-ant-..."
```

The pipeline never silently falls back to Jinja2 template scaffold — that must be explicitly
opted into via `llm.no_llm: true` in the job YAML, so you always know whether real AI
conversion happened.

**How to detect fallback in automated pipelines:** any remaining soft-fallback (rare, mid-run
API failure) emits a `[LLM_FAILURE_JSON]` line to stderr:
```json
{"event": "llm_fallback", "agent": "cursor", "context": "...", "error": "...", "action": "template_scaffold_used"}
```
The conversion summary also includes `"llm_used": false` when no LLM was invoked.

---

### Steps 7–8 skipped ("skipped_no_target" / "skipped_disabled")

#### Auto-population (recommended)

The pipeline **automatically fills in** `target_root` and `verification.commands` before
each run — no manual YAML edits needed in most cases.

**`target_root` auto-fill:** if `target_root` is `null` in the job file, the pipeline
looks up the configured path for `pipeline.target` in `config/wizard-registry.json`
(written by `python run_agent.py --setup`). If found, it is injected automatically.

**`verification.commands` auto-detect:** if `verification.commands` is empty, the
pipeline inspects the target codebase root and generates commands automatically:
- `package.json` found → `npm ci` + `npm run build` + `npm run test` + `npm run lint`
- Python project (`pyproject.toml` / `requirements.txt`) → `pip install` + `pytest`
- `Makefile` → `make install`, `make build`, `make test`, `make lint` (whichever exist)

When commands are detected, `verification.enabled` is also flipped to `true`
automatically. Nothing is overwritten if you have already set values explicitly.

#### Manual override

If auto-population does not find your paths (e.g. the target was never registered via
the setup wizard), set them explicitly in the job YAML:

```yaml
pipeline:
  target_root: "C:/path/to/your/target-codebase"   # absolute path to the target repo

verification:
  enabled: true
  cwd: "C:/path/to/your/target-codebase"   # directory to run commands in (defaults to target_root)
  commands:
    - "npm run build"          # build step
    - "npm run test -- --ci"   # test step (non-interactive)
    - "npm run lint"           # lint step
  env: {}                      # optional extra env vars
  fail_on_error: true          # true = pipeline fails if any command exits non-zero
```

After confirming values, run in `full` mode:
```bash
python run_agent.py --job agent-prompts/migrate-<name>.yaml --mode full
```

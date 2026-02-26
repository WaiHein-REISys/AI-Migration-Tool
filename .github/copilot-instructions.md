# GitHub Copilot Workspace Instructions — AI Migration Tool

This repository is the **AI Migration Tool** — a multi-agent pipeline that migrates
`HAB-GPRSSubmission` (Angular 2 / ASP.NET Core) to a modern target stack using LLMs.

---

## Running a migration (always use `run_agent.py`)

Each migration job is defined in a self-contained YAML file under `agent-prompts/`.
**Always route through `run_agent.py`** — never call `main.py` directly.

### List available jobs
```bash
python run_agent.py --list-jobs
```

### Run a job
```bash
python run_agent.py --job agent-prompts/migrate-action-history.yaml
```

### Common override flags
| Flag | Effect |
|---|---|
| `--dry-run` | Simulate — no files written |
| `--force` | Re-run even if already completed |
| `--auto-approve` | Skip human approval gate (testing only) |
| `--verbose` | Show DEBUG logs |
| `--mode full` | Override `pipeline.mode` in YAML without editing the file |
| `--json` | Output machine-readable JSON (useful for Copilot parsing) |

---

## Configuring a new migration target (first-time setup)

For a **new/custom stack** not yet registered, run the Setup Wizard:

```bash
python run_agent.py --setup                        # fully interactive
python run_agent.py --setup --config wizard.json  # pre-filled JSON answers
python run_agent.py --setup --dry-run             # preview only
python run_agent.py --setup --list-targets        # list configured targets
```

The wizard generates custom LLM prompts and a job template for the new target.
See `agent-prompts/example-wizard-config.json` for the JSON answer format.

---

## Agent-interactive commands (GitHub Copilot / autonomous mode)

For fully autonomous, non-interactive agent workflows:

### Discover features in the source codebase
```bash
python run_agent.py --list-features --source <YOUR_SOURCE_ROOT>
python run_agent.py --list-features --json
```

### Create a job file without prompts
```bash
python run_agent.py --new-job \
  --feature ActionHistory \
  --target snake_case \
  --non-interactive --json
```

### Check migration status
```bash
python run_agent.py --status --job agent-prompts/migrate-actionhistory-snake_case.yaml --json
```
Returns: `plan_generated`, `plan_approved`, `completed_steps`, `pending_steps`, `blocked_steps`.

### Approve a plan (agent-driven — no terminal interaction required)
```bash
python run_agent.py --approve-plan --job agent-prompts/migrate-actionhistory-snake_case.yaml
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml --mode full
```
Writes `output/<feature>/.approved` marker; the pipeline detects it and skips the TTY prompt.

### Revise a plan with LLM feedback
```bash
python run_agent.py --revise-plan \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml \
  --feedback "Flag all pfm-auth imports as BLOCKED. Add a dedicated DB migration section."
```
- Reuses cached dependency graph (no re-scoping needed)
- Writes a `-rev.md` plan file
- Removes the `.approved` marker so the revised plan requires re-approval

### Typical 5-step autonomous workflow
```bash
# 1. Discover features
python run_agent.py --list-features --json

# 2. Create job (non-interactive)
python run_agent.py --new-job --feature ActionHistory --target snake_case \
                    --non-interactive --json

# 3. Generate plan
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml

# 4. Check status
python run_agent.py --status --job agent-prompts/migrate-actionhistory-snake_case.yaml --json

# 5a. Approve + convert
python run_agent.py --approve-plan --job agent-prompts/migrate-actionhistory-snake_case.yaml
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml --mode full

# 5b. Or revise and re-approve
python run_agent.py --revise-plan --job agent-prompts/migrate-actionhistory-snake_case.yaml \
                    --feedback "Add BLOCKED markers for pfm-auth imports."
python run_agent.py --approve-plan --job agent-prompts/migrate-actionhistory-snake_case.yaml
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml --mode full
```

---

## Creating a job for a new feature

### Option A — Autonomous (agent mode)
```bash
python run_agent.py --new-job \
  --feature MyFeature --target snake_case \
  --non-interactive --json
```

### Option B — Manual template copy
1. List configured targets: `python run_agent.py --setup --list-targets`
2. If target not configured, run setup wizard first (see above)
3. Copy the right template: `cp agent-prompts/_template_<target>.yaml agent-prompts/migrate-<FeatureName>.yaml`
4. Set `pipeline.feature_root`, `pipeline.feature_name`, `pipeline.mode`, `pipeline.target`
5. Run: `python run_agent.py --job agent-prompts/migrate-<FeatureName>.yaml`

### Job file fields quick reference

```yaml
pipeline:
  feature_root:  "<YOUR_SOURCE_ROOT>/src/.../FeatureName"
  feature_name:  "FeatureName"
  mode:          "plan"          # scope | plan | full
  target:        "snake_case"    # simpler_grants | hrsa_pprs | snake_case | <custom>
  dry_run:       false
  auto_approve:  false
  force:         false

llm:
  no_llm:    false   # true = template-only, no API key needed
  provider:  null    # null = auto-detect from env vars
  model:     null    # null = provider default
```

---

## Target stacks

| `target` | Stack |
|---|---|
| `simpler_grants` | Next.js 15 / React 19 / APIFlask / SQLAlchemy 2.0 |
| `hrsa_pprs` | Next.js 16 / React 18 / Flask 3.0 / psycopg2 raw SQL |
| `snake_case` | Next.js / TypeScript / Flask 3.0 / snake_case naming |
| `<custom>` | Configured via `python run_agent.py --setup` |

---

## Pipeline stages

```
1. Config Ingestion  → validates skillset-config.json + rules-config.json
2. Scoping           → analyzes feature source files, produces dependency graph
3. Plan Generation   → LLM generates structured Plan Document (Markdown)
4. Approval          → human types "yes" in terminal  OR  agent writes .approved marker
5. Conversion        → LLM converts each file per the approved plan
```

- Outputs: `plans/`, `logs/`, `output/`, `checkpoints/` — **do not edit these**
- Prompts: `prompts/*.txt` — safe to read/suggest edits for tuning LLM behaviour
- Config: `config/skillset-config.json`, `config/rules-config.json` — edit only when asked
- Wizard: `setup_wizard.py`, `wizard/` — first-run target configuration wizard
- Registry: `config/wizard-registry.json` — auto-managed by setup_wizard.py

---

## LLM auto-detection order

```
ANTHROPIC_API_KEY → OPENAI_API_KEY → OLLAMA_MODEL → LLM_BASE_URL → LLAMACPP_MODEL_PATH
```

Use `llm.no_llm: true` in the job file for template-only mode (no API key needed).

### LLM failure behaviour

| Run context | Behaviour on LLM error |
|---|---|
| Via `run_agent.py` (agent mode) | Soft-fail — returns Jinja2 template scaffold so the agent can continue |
| Via `main.py` directly (CLI/human) | Hard-fail — raises `LLMConfigurationError` with fix instructions |

`run_agent.py` automatically sets `AI_AGENT_MODE=1`. To force agent mode manually:
```bash
set AI_AGENT_MODE=1       # Windows CMD
$env:AI_AGENT_MODE=1      # PowerShell
export AI_AGENT_MODE=1    # bash/zsh
```

---

## Recommended workflow for migrating a new feature

### Human-assisted
1. Check configured targets: `python run_agent.py --setup --list-targets`
2. If the target stack is not configured, run the setup wizard first
3. Check for an existing job file: does `agent-prompts/migrate-<name>.yaml` exist?
4. If not, copy the target's template: `cp agent-prompts/_template_<target>.yaml agent-prompts/migrate-<name>.yaml`
5. Run in **plan mode first** — review the Plan Document before running `full`
6. After the user approves the plan, run in `full` mode
7. Check `logs/<run-id>-conversion-log.md` for step-by-step results

### Autonomous (Copilot / agent)
```bash
python run_agent.py --list-features --json
python run_agent.py --new-job --feature <Name> --target snake_case --non-interactive --json
python run_agent.py --job agent-prompts/migrate-<name>-snake_case.yaml
python run_agent.py --status --job agent-prompts/migrate-<name>-snake_case.yaml --json
python run_agent.py --approve-plan --job agent-prompts/migrate-<name>-snake_case.yaml
python run_agent.py --job agent-prompts/migrate-<name>-snake_case.yaml --mode full
```

## Recommended workflow for setting up a new custom target

1. Copy and fill in the wizard JSON config:
   `cp agent-prompts/example-wizard-config.json agent-prompts/wizard-<target_id>.json`
2. Run the wizard: `python run_agent.py --setup --config agent-prompts/wizard-<target_id>.json`
3. Review generated prompts in `prompts/` and adjust as needed
4. Use the generated `agent-prompts/_template_<target_id>.yaml` for new migration jobs

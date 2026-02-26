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

## Creating a job for a new feature

1. List configured targets: `python run_agent.py --setup --list-targets`
2. If target not configured, run setup wizard first (see above)
3. Copy the right template: `cp agent-prompts/_template_<target>.yaml agent-prompts/migrate-<FeatureName>.yaml`
4. Set `pipeline.feature_root`, `pipeline.feature_name`, `pipeline.mode`, `pipeline.target`
5. Run: `python run_agent.py --job agent-prompts/migrate-<FeatureName>.yaml`

### Job file fields quick reference

```yaml
pipeline:
  feature_root:  "Y:/Solution/HRSA/HAB-GPRSSubmission/src/.../FeatureName"
  feature_name:  "FeatureName"
  mode:          "plan"          # scope | plan | full
  target:        "simpler_grants" # simpler_grants | hrsa_pprs
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

---

## Pipeline stages

```
1. Config Ingestion  → validates skillset-config.json + rules-config.json
2. Scoping           → analyzes feature source files, produces dependency graph
3. Plan Generation   → LLM generates structured Plan Document (Markdown)
4. Human Approval    → human reviews plan and types "yes" to proceed
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

1. Check configured targets: `python run_agent.py --setup --list-targets`
2. If the target stack is not configured, run the setup wizard first:
   `python run_agent.py --setup`
3. Check for an existing job file: does `agent-prompts/migrate-<name>.yaml` exist?
4. If not, copy the target's template: `cp agent-prompts/_template_<target>.yaml agent-prompts/migrate-<name>.yaml`
5. Run in **plan mode first** — review the Plan Document before running `full`
6. After the user approves the plan, run in `full` mode
7. Check `logs/<run-id>-conversion-log.md` for step-by-step results

## Recommended workflow for setting up a new custom target

1. Copy and fill in the wizard JSON config:
   `cp agent-prompts/example-wizard-config.json agent-prompts/wizard-<target_id>.json`
2. Run the wizard: `python run_agent.py --setup --config agent-prompts/wizard-<target_id>.json`
3. Review generated prompts in `prompts/` and adjust as needed
4. Use the generated `agent-prompts/_template_<target_id>.yaml` for new migration jobs

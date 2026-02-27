# AI Migration Tool

**AI-Driven Legacy Codebase Modernization — any legacy stack → any modern stack**

Migrates legacy codebases to a modern target stack, one feature at a time, with full
audit logging, human **and agent** approval gates, plan revision support, and
deduplication so re-running the same feature never creates duplicate artefacts.

Originally built to migrate `HAB-GPRSSubmission` (Angular 2.4 / ASP.NET Core 8), but
the **Setup Wizard** lets you configure any custom Source → Target pair in minutes.

### Built-in target stacks

| `pipeline.target` | Target stack | Notes |
|---|---|---|
| `simpler_grants` *(default)* | Next.js 15 / React 19 / APIFlask / SQLAlchemy 2.0 | — |
| `hrsa_pprs` | Next.js 16 / React 18 / Flask 3.0 / psycopg2 raw SQL | — |
| `snake_case` | Next.js / TypeScript / Flask 3.0 / snake_case naming | — |
| *(custom)* | Any stack — configured by the Setup Wizard | No code changes needed |

The tool supports **any LLM backend** — Anthropic Claude, OpenAI GPT, local Ollama models,
LM Studio / vLLM (OpenAI-compatible), and local GGUF files via llama.cpp.

It also supports **AI agent mode** for Cursor, GitHub Copilot, Windsurf, and AntiGravity — agents
can discover features, create job files, check status, approve plans, request revisions,
and trigger conversions entirely without human TTY interaction.

---

## Documentation

Full technical and guidelines documentation is in [`docs/`](docs/):

| Page | Contents |
|---|---|
| [Architecture](docs/Architecture.md) | Pipeline data-flow, agent responsibilities, module map |
| [Pipeline Stages](docs/Pipeline-Stages.md) | Per-stage inputs, outputs, config options, resume behaviour |
| [Agent Interactive Mode](docs/Agent-Interactive-Mode.md) | `--list-features`, `--status`, `--approve-plan`, `--revise-plan`, `--new-job --non-interactive` |
| [Job Files Reference](docs/Job-Files-Reference.md) | Complete YAML field reference, all defaults, examples |
| [Target Stacks](docs/Target-Stacks.md) | Built-in stacks, snake_case target, custom wizard-generated stacks |
| [LLM Providers](docs/LLM-Providers.md) | All providers, environment variables, per-provider examples |
| [Setup Wizard](docs/Setup-Wizard.md) | First-run wizard, non-interactive mode, JSON config schema |
| [Prompt Engineering](docs/Prompt-Engineering.md) | How prompts work, placeholders, per-target customisation |
| [Guardrail Rules](docs/Guardrail-Rules.md) | All RULE-XXX rules with rationale and enforcement level |
| [Extending the Tool](docs/Extending-the-Tool.md) | New providers, targets, mappings, guardrails |
| [Troubleshooting](docs/Troubleshooting.md) | Common errors and fixes |

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- At least one LLM provider configured (see [LLM Providers](docs/LLM-Providers.md)),
  **or** use `--no-llm` for template-only scaffold mode (no API key needed)

### 2. Install

```bash
cd ai-migration-tool

# Create a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
```

### 3. Configure your LLM provider

Auto-detected from environment variables (first found wins):

```bash
# Anthropic Claude (recommended)
set ANTHROPIC_API_KEY=sk-ant-...          # Windows CMD
$env:ANTHROPIC_API_KEY = "sk-ant-..."    # PowerShell

# OpenAI GPT-4o
set OPENAI_API_KEY=sk-...

# Ollama (local, no API key)
set OLLAMA_MODEL=llama3.2

# LM Studio / vLLM (OpenAI-compatible)
set LLM_BASE_URL=http://localhost:1234/v1
set LLM_MODEL=local-model

# Local GGUF via llama.cpp
set LLAMACPP_MODEL_PATH=C:\models\mistral-7b.Q4_K_M.gguf
```

### 3b. Configure a custom target (optional)

If migrating to a stack other than the built-in ones:

```bash
python run_agent.py --setup                          # interactive wizard
python run_agent.py --setup --config wizard.json     # pre-filled JSON
python run_agent.py --setup --list-targets           # list configured targets
```

See [Setup Wizard](docs/Setup-Wizard.md) for the full guide.

---

## Running a Migration

**Always use `run_agent.py` — do not call `main.py` directly from agent mode.**

### Human-driven workflow

```bash
# 1. List available job files
python run_agent.py --list-jobs

# 2. Run a migration job (generates plan only by default)
python run_agent.py --job agent-prompts/migrate-action-history.yaml

# 3. Review the plan in plans/<feature>-plan-<ts>.md

# 4. Run full conversion (edit mode: full in the YAML, then re-run)
python run_agent.py --job agent-prompts/migrate-action-history.yaml --mode full
```

### Agent-driven workflow (Cursor / Windsurf / Copilot / AntiGravity)

Agents can run the complete lifecycle without any TTY interaction:

```bash
# 1. Discover features in the source codebase
python run_agent.py --list-features --source <YOUR_SOURCE_ROOT> --json

# 2. Create a job file non-interactively
python run_agent.py --new-job \
  --feature ActionHistory \
  --target snake_case \
  --non-interactive --json

# 3. Generate the migration plan
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml

# 4. Check status
python run_agent.py --status \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml --json

# 5a. Approve the plan and run conversion (no terminal prompt)
python run_agent.py --approve-plan \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml \
  --mode full

# 5b. Or revise the plan with LLM feedback
python run_agent.py --revise-plan \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml \
  --feedback "Flag all pfm-auth imports as BLOCKED. Add a DB migration section."
# Then re-approve and re-run
```

See [Agent Interactive Mode](docs/Agent-Interactive-Mode.md) for the full command reference.

### Common override flags

| Flag | Effect |
|---|---|
| `--dry-run` | No files written — logs only |
| `--force` | Re-run even if already completed |
| `--auto-approve` | Skip human approval gate (testing only) |
| `--verbose` | Show DEBUG-level logs |
| `--mode <scope\|plan\|full>` | Override `pipeline.mode` without editing the YAML |
| `--json` | Machine-readable JSON output for agent parsing |

---

## Pipeline Stages

```
Config Ingestion → Scoping & Analysis → Plan Generation → Approval → Conversion
```

| Stage | Agent | Output |
|---|---|---|
| 1. Config Ingestion | `ConfigIngestionAgent` | Validated config dict |
| 2. Scoping & Analysis | `ScopingAgent` | `logs/<run-id>-dependency-graph.json` |
| 3. Plan Generation | `PlanAgent` | `plans/<feature>-plan-<ts>.md` |
| 4. Approval | `ApprovalGate` | Human types `yes` **or** agent writes `.approved` marker |
| 5. Conversion | `ConversionAgent` | `output/<feature>/` + `logs/<run-id>-conversion-log.*` |

Plan revision (step 3b):
- `--revise-plan --feedback "..."` re-runs `PlanAgent` with LLM feedback injected
- Writes a `-rev.md` plan file; clears the `.approved` marker automatically
- Reuses the cached dependency graph — no re-scoping needed

See [Pipeline Stages](docs/Pipeline-Stages.md) for full details.

---

## LLM Providers

| Provider | Env var / flag | Package |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | included |
| `openai` | `OPENAI_API_KEY` | `pip install openai` |
| `openai_compat` | `LLM_BASE_URL` | `pip install openai` |
| `ollama` | `OLLAMA_MODEL` | `pip install ollama` (optional) |
| `llamacpp` | `LLAMACPP_MODEL_PATH` | `pip install llama-cpp-python` |

Auto-detect order:
```
ANTHROPIC_API_KEY → OPENAI_API_KEY → OLLAMA_MODEL → LLM_BASE_URL → LLAMACPP_MODEL_PATH
```

Use `llm.no_llm: true` (or `--no-llm`) for template-only scaffold mode — no API key needed.

### LLM failure behaviour

| Run context | Behaviour |
|---|---|
| Via `run_agent.py` (agent mode) | **Soft-fail** — returns Jinja2 scaffold; pipeline continues |
| Via `main.py` directly | **Hard-fail** — raises `LLMConfigurationError` with fix instructions |

`run_agent.py` automatically sets `AI_AGENT_MODE=1` before the pipeline runs.

See [LLM Providers](docs/LLM-Providers.md) for per-provider examples.

---

## Job Files

Migration jobs are self-contained YAML files in `agent-prompts/`. Minimal example:

```yaml
pipeline:
  feature_root: "<YOUR_SOURCE_ROOT>/src/YourFeatureName"
  feature_name: "YourFeatureName"
  mode:         "plan"           # scope | plan | full
  target:       "snake_case"     # simpler_grants | hrsa_pprs | snake_case | <custom>

llm:
  no_llm:   false   # true = template scaffold, no API key required
  provider: null    # null = auto-detect from env vars
  model:    null    # null = provider default
```

Available templates:

| Template | Target |
|---|---|
| `agent-prompts/_template.yaml` | `simpler_grants` / `hrsa_pprs` (built-in) |
| `agent-prompts/_template_modern.yaml` | `modern` target |
| `agent-prompts/_template_snake_case.yaml` | `snake_case` target |
| `agent-prompts/_template_<id>.yaml` | Wizard-generated custom target |

See [Job Files Reference](docs/Job-Files-Reference.md) for every field.

---

## Setup Wizard

The wizard configures new migration targets without any code changes:

```bash
python run_agent.py --setup                          # fully interactive
python run_agent.py --setup --config wizard.json     # pre-filled JSON
python run_agent.py --setup --non-interactive \      # silent (CI / agent)
  --config wizard.json
python run_agent.py --setup --dry-run                # preview only
python run_agent.py --setup --list-targets           # list configured targets
```

For each new target `<id>`, the wizard creates:
- `prompts/plan_system_<id>.txt`
- `prompts/conversion_system_<id>.txt`
- `prompts/conversion_target_stack_<id>.txt`
- `agent-prompts/_template_<id>.yaml`
- Entries in `config/skillset-config.json` and `config/wizard-registry.json`

See [Setup Wizard](docs/Setup-Wizard.md) for full details and the JSON config schema.

---

## Agent Mode

The tool includes first-class support for AI coding agents. Three rule files are
auto-loaded by each IDE:

| File | Agent |
|---|---|
| `.cursor/rules.mdc` | Cursor (Cascade / Composer) |
| `.github/copilot-instructions.md` | GitHub Copilot |
| `.windsurfrules` | Windsurf (Cascade) |

### Agent-interactive commands

| Command | Purpose |
|---|---|
| `--list-features [--source PATH] [--json]` | Scan source and list feature folders |
| `--new-job --feature X --target Y --non-interactive` | Create job YAML without prompts |
| `--status --job FILE [--json]` | Report plan/approval/conversion status |
| `--approve-plan --job FILE` | Write `.approved` marker (no TTY needed) |
| `--revise-plan --job FILE --feedback "..."` | Re-generate plan with LLM feedback |
| `--mode <scope\|plan\|full>` | Override `pipeline.mode` at CLI |
| `--json` | Machine-readable output throughout |

See [Agent Interactive Mode](docs/Agent-Interactive-Mode.md) for the complete guide.

---

## Project Structure

```
ai-migration-tool/
├── main.py                          # Core pipeline CLI (direct use)
├── run_agent.py                     # Agent entry point — reads YAML job files
├── setup_wizard.py                  # Setup wizard CLI entry point
├── requirements.txt
│
├── docs/                            # Technical documentation & wiki pages
│   ├── Architecture.md
│   ├── Pipeline-Stages.md
│   ├── Agent-Interactive-Mode.md
│   ├── Job-Files-Reference.md
│   ├── Target-Stacks.md
│   ├── LLM-Providers.md
│   ├── Setup-Wizard.md
│   ├── Prompt-Engineering.md
│   ├── Guardrail-Rules.md
│   ├── Extending-the-Tool.md
│   └── Troubleshooting.md
│
├── wizard/                          # Setup wizard package
│   ├── detector.py                  # Heuristic framework/language detector
│   ├── collector.py                 # Interactive Q&A helpers
│   ├── generator.py                 # Prompt & config content generators
│   ├── writer.py                    # File I/O with dry-run support
│   ├── registry.py                  # wizard-registry.json helpers
│   └── runner.py                    # run_wizard() / list_targets() orchestration
│
├── agent-prompts/                   # Migration job files (one per feature)
│   ├── _template.yaml               # Built-in target template
│   ├── _template_modern.yaml        # modern target template
│   ├── _template_snake_case.yaml    # snake_case target template
│   ├── _template_<id>.yaml          # Wizard-generated custom template
│   ├── migrate-action-history.yaml
│   ├── migrate-action-history-hrsa.yaml
│   ├── example-wizard-config.json   # Sample non-interactive wizard answers
│   └── wizard-myapp.json            # Concrete custom target wizard example
│
├── .cursor/rules.mdc                # Cursor agent rules
├── .github/copilot-instructions.md  # GitHub Copilot workspace instructions
├── .windsurfrules                   # Windsurf agent rules
│
├── agents/
│   ├── config_ingestion_agent.py
│   ├── scoping_agent.py
│   ├── plan_agent.py                # Supports revision mode (--revise-plan)
│   ├── conversion_agent.py
│   ├── conversion_log.py
│   ├── approval_gate.py             # Detects .approved marker for agent approval
│   └── llm/
│       ├── base.py                  # LLMMessage, LLMResponse, LLMConfig, BaseLLMProvider
│       ├── registry.py              # LLMRouter — provider factory + auto-detect
│       └── providers/
│           ├── anthropic_provider.py
│           ├── openai_provider.py
│           ├── openai_compat_provider.py
│           ├── ollama_provider.py
│           └── llamacpp_provider.py
│
├── prompts/                         # LLM prompt text files (edit freely)
│   ├── plan_system.txt              # PlanAgent — simpler_grants
│   ├── plan_system_hrsa_pprs.txt    # PlanAgent — hrsa_pprs
│   ├── plan_system_snake_case.txt   # PlanAgent — snake_case
│   ├── plan_system_<id>.txt         # Wizard-generated
│   ├── plan_document_template.md    # Shared scaffold (no-LLM mode)
│   ├── conversion_system*.txt       # ConversionAgent prompts (per-target)
│   └── conversion_target_stack*.txt # Stack references (per-target)
│
├── config/
│   ├── skillset-config.json         # Stack definitions + component mappings
│   ├── rules-config.json            # Guardrail rules (RULE-001 – RULE-010)
│   ├── wizard-registry.json         # Registered wizard targets
│   └── schemas/                     # JSON Schemas for config validation
│
├── templates/                       # Jinja2 conversion scaffolds (no-LLM mode)
│
├── plans/                           # ⬅ pipeline output — do not edit
├── logs/                            # ⬅ pipeline output — do not edit
├── output/                          # ⬅ pipeline output — do not edit
└── checkpoints/                     # ⬅ pipeline output — do not edit
```

---

## Component Mappings

| ID | Source pattern | Target pattern |
|---|---|---|
| MAP-001 | Angular 2 `@Component` (NgModule + RxJS Subject) | Next.js React functional component with hooks |
| MAP-002 | Angular 2 `@Injectable()` service | Typed async fetcher / server action |
| MAP-003 | ASP.NET Core Area Controller | Python Flask Blueprint route |
| MAP-004 | EF Core / Dapper Repository | SQLAlchemy 2.0 service function |
| MAP-005 | C# Model / Entity | SQLAlchemy 2.0 `Mapped[]` + Pydantic schema |
| MAP-006 | Angular 2 `@NgModule` | Next.js feature folder + barrel exports |

---

## Guardrail Rules

| ID | Name | Level |
|---|---|---|
| RULE-001 | Preserve API contracts | Blocking |
| RULE-002 | Preserve UI CSS class names | Blocking |
| RULE-003 | No business logic reinterpretation | Blocking |
| RULE-004 | Flag ambiguous mappings | Blocking |
| RULE-005 | No out-of-boundary changes | Blocking |
| RULE-006 | Log every transformation | Blocking |
| RULE-007 | Preserve TypeScript types | Warning |
| RULE-008 | External library halt (pfm-*, Platform.*) | Blocking |
| RULE-009 | SQLAlchemy `Mapped[]` syntax required | Blocking |
| RULE-010 | Audit events required for mutations | Warning |

See [Guardrail Rules](docs/Guardrail-Rules.md) for rationale and enforcement details.

---

## Extending the Tool

| Task | Where to start |
|---|---|
| Add a new LLM provider | [Extending the Tool](docs/Extending-the-Tool.md#new-llm-provider) |
| Add a new target stack | `python run_agent.py --setup` or [manual steps](docs/Extending-the-Tool.md#new-target-stack) |
| Edit a prompt | `prompts/<name>.txt` — no Python changes needed |
| Add a component mapping | `config/skillset-config.json` + `templates/` |
| Add a guardrail rule | `config/rules-config.json` |

---

*AI Migration Tool v1.4 | Multi-agent pipeline with interactive agent mode, plan revision, and Setup Wizard*

# AI Migration Tool

**AI-Driven Legacy Codebase Modernization — GPRS -> modern target stack**

Transforms `HAB-GPRSSubmission` (Angular 2.4 / ASP.NET Core 8) into your chosen
target stack, one feature at a time, with full audit logging and human approval gates.

### Supported target stacks

| `--target` | Target stack | Reference codebase |
|---|---|---|
| `simpler_grants` *(default)* | Next.js 15 / React 19 / APIFlask / SQLAlchemy 2.0 | `Y:\Solution\HRSA\simpler-grants-gov` |
| `hrsa_pprs` | Next.js 16 / React 18 / Flask 3.0 / psycopg2 raw SQL | `Y:\Solution\HRSA\HRSA-Simpler-PPRS` |

The tool supports **any LLM backend** — Anthropic Claude, OpenAI GPT, local Ollama models,
LM Studio / vLLM (OpenAI-compatible), and local GGUF files via llama.cpp.

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- At least one LLM provider configured (see [LLM Providers](#llm-providers) below),
  **or** use `--no-llm` for template-only scaffold mode (no API key needed)

### 2. Setup

```bash
cd Y:\Solution\HRSA\ai-migration-tool

# Create a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

# Install core dependencies
pip install -r requirements.txt
```

### 3. Configure your LLM provider

The tool auto-detects providers from environment variables. Set whichever applies:

```bash
# --- Anthropic Claude (default, recommended) ---
set ANTHROPIC_API_KEY=sk-ant-...          # Windows CMD
$env:ANTHROPIC_API_KEY = "sk-ant-..."    # Windows PowerShell

# --- OpenAI GPT-4o ---
set OPENAI_API_KEY=sk-...

# --- Ollama (local, no API key needed) ---
set OLLAMA_MODEL=llama3.2                # Ollama must be running on localhost:11434

# --- LM Studio / vLLM / any OpenAI-compatible server ---
set LLM_BASE_URL=http://localhost:1234/v1
set LLM_MODEL=local-model

# --- llama.cpp local GGUF file ---
set LLAMACPP_MODEL_PATH=C:\models\mistral-7b.Q4_K_M.gguf
```

### 4. Run the pipeline

**Mode 1: Analyse only** (no plan, no code — just the dependency graph)
```bash
python main.py \
  --feature-root "Y:/Solution/HRSA/HAB-GPRSSubmission/src/GPRSSubmission.Web/wwwroot/gprs_app/ActionHistory" \
  --feature-name "ActionHistory" \
  --mode scope
```

**Mode 2: Generate Plan Document** (analysis + plan, no code written)
```bash
python main.py \
  --feature-root "Y:/Solution/HRSA/HAB-GPRSSubmission/src/GPRSSubmission.Web/wwwroot/gprs_app/ActionHistory" \
  --feature-name "ActionHistory" \
  --mode plan
```

**Mode 3: Full Pipeline** (analysis -> plan -> human approval -> code conversion)
```bash
python main.py \
  --feature-root "Y:/Solution/HRSA/HAB-GPRSSubmission/src/GPRSSubmission.Web/wwwroot/gprs_app/ActionHistory" \
  --feature-name "ActionHistory" \
  --mode full
```

**Dry run** (generates code in memory / logs but writes no files)
```bash
python main.py --feature-root "..." --feature-name "ActionHistory" --mode full --dry-run
```

**Resume after interruption**
```bash
python main.py --run-id conv-20260223-143012-abc123 --resume --feature-root "..."
```

**Template-only mode** (no LLM — produces scaffolds only, no API key required)
```bash
python main.py --feature-root "..." --feature-name "ActionHistory" --mode full --no-llm --auto-approve
```

**Target HRSA-Simpler-PPRS** (Next.js 16 / Flask 3.0 / psycopg2 raw SQL)
```bash
python main.py \
  --feature-root "Y:/Solution/HRSA/HAB-GPRSSubmission/src/GPRSSubmission.Web/wwwroot/gprs_app/ActionHistory" \
  --feature-name "ActionHistory" \
  --mode full \
  --target hrsa_pprs
```

---

## LLM Providers

The LLM backend is fully modular. Providers are configured either via CLI flags
or environment variables. When no explicit provider is given, the tool
auto-detects in this order:

```
LLM_PROVIDER env var > LLAMACPP_MODEL_PATH > OLLAMA_MODEL > LLM_BASE_URL > OPENAI_API_KEY > ANTHROPIC_API_KEY
```

### Supported providers

| Provider | CLI flag | Package required | Use case |
|---|---|---|---|
| `anthropic` | `--llm-provider anthropic` | `anthropic` (included) | Anthropic Claude API |
| `openai` | `--llm-provider openai` | `pip install openai` | OpenAI GPT-4o, Azure OpenAI |
| `openai_compat` | `--llm-provider openai_compat` | `pip install openai` | LM Studio, vLLM, Together AI, Fireworks, Ollama /v1 |
| `ollama` | `--llm-provider ollama` | `pip install ollama` (optional) | Local Ollama server (native REST) |
| `llamacpp` | `--llm-provider llamacpp` | `pip install llama-cpp-python` | Local GGUF models via llama.cpp |

### Provider examples

```bash
# Anthropic Claude (auto-detected if ANTHROPIC_API_KEY is set)
python main.py --feature-root "..." --feature-name "MyFeature" --mode full

# Explicit Anthropic with a specific model
python main.py --feature-root "..." --feature-name "MyFeature" --mode full \
  --llm-provider anthropic --llm-model claude-3-5-sonnet-20241022

# OpenAI GPT-4o
python main.py --feature-root "..." --feature-name "MyFeature" --mode full \
  --llm-provider openai --llm-model gpt-4o

# Azure OpenAI
python main.py --feature-root "..." --feature-name "MyFeature" --mode full \
  --llm-provider openai \
  --llm-base-url "https://<resource>.openai.azure.com/" \
  --llm-model gpt-4o \
  --llm-api-version 2024-08-01-preview

# Ollama (local — requires Ollama running on localhost:11434)
python main.py --feature-root "..." --feature-name "MyFeature" --mode full \
  --llm-provider ollama --llm-model llama3.2

# Ollama on a different host
python main.py --feature-root "..." --feature-name "MyFeature" --mode full \
  --llm-provider ollama --llm-model deepseek-coder-v2 \
  --ollama-host http://192.168.1.100:11434

# LM Studio (OpenAI-compatible local server)
python main.py --feature-root "..." --feature-name "MyFeature" --mode full \
  --llm-provider openai_compat \
  --llm-base-url http://localhost:1234/v1 \
  --llm-model local-model

# vLLM
python main.py --feature-root "..." --feature-name "MyFeature" --mode full \
  --llm-provider openai_compat \
  --llm-base-url http://localhost:8000/v1 \
  --llm-model mistralai/Mistral-7B-Instruct-v0.3

# Local GGUF file via llama.cpp (CPU)
python main.py --feature-root "..." --feature-name "MyFeature" --mode full \
  --llm-provider llamacpp \
  --llm-model-path "C:/models/mistral-7b.Q4_K_M.gguf"

# Local GGUF file via llama.cpp (GPU — requires GPU build of llama-cpp-python)
python main.py --feature-root "..." --feature-name "MyFeature" --mode full \
  --llm-provider llamacpp \
  --llm-model-path "C:/models/codellama-13b.Q5_K_M.gguf"

# No LLM — template scaffold output only (no API key needed)
python main.py --feature-root "..." --feature-name "MyFeature" --mode full \
  --no-llm --auto-approve
```

### Target stack selection

| Flag | Choices | Default | Description |
|---|---|---|---|
| `--target` | `simpler_grants` \| `hrsa_pprs` | `simpler_grants` | Select which target stack to migrate to. Controls project structure paths and which LLM prompts are used. |

### LLM CLI flags reference

| Flag | Default | Description |
|---|---|---|
| `--no-llm` | off | Disable all LLM calls; use Jinja2 template scaffolds only |
| `--llm-provider` | auto-detect | `anthropic` \| `openai` \| `openai_compat` \| `ollama` \| `llamacpp` |
| `--llm-model` | provider default | Model name/ID (e.g. `claude-opus-4-5`, `gpt-4o`, `llama3.2`) |
| `--llm-base-url` | — | Base URL for OpenAI-compatible or Azure endpoints |
| `--llm-model-path` | — | Path to a local GGUF file (llama.cpp only) |
| `--ollama-host` | `http://localhost:11434` | Ollama server URL |
| `--llm-max-tokens` | `8192` | Maximum tokens to generate per call |
| `--llm-temperature` | `0.2` | Sampling temperature |

### Installing optional provider packages

```bash
# OpenAI / Azure OpenAI / OpenAI-compatible servers
pip install openai>=1.50.0

# Ollama native SDK (optional — falls back to httpx/requests if not installed)
pip install ollama>=0.3.0

# llama.cpp CPU-only
pip install llama-cpp-python>=0.2.90

# llama.cpp with CUDA GPU acceleration
CMAKE_ARGS="-DLLAMA_CUDA=on" pip install llama-cpp-python>=0.2.90
# Windows (PowerShell):
# $env:CMAKE_ARGS="-DLLAMA_CUDA=on"; pip install llama-cpp-python
```

---

## Pipeline Stages

```
Config Ingestion -> Scoping & Analysis -> Plan Generation -> Human Approval Gate -> Conversion Execution
```

| Stage | Agent | Output |
|---|---|---|
| 1. Config Ingestion | `ConfigIngestionAgent` | Validated config dict |
| 2. Scoping & Analysis | `ScopingAgent` | `logs/<run-id>-dependency-graph.json` |
| 3. Plan Generation | `PlanAgent` | `plans/<feature>-plan-<timestamp>.md` |
| 4. Human Approval | `ApprovalGate` | CLI prompt — `yes` to proceed |
| 5. Conversion Execution | `ConversionAgent` | `output/<feature>/` + `logs/<run-id>-conversion-log.json` |

---

## Project Structure

```
ai-migration-tool/
├── main.py                          # CLI runner (entry point)
├── requirements.txt
├── README.md
│
├── agents/
│   ├── config_ingestion_agent.py    # Validates skillset/rules configs
│   ├── scoping_agent.py             # AST + regex analysis of source files
│   ├── plan_agent.py                # LLM-powered Plan Document generator
│   ├── conversion_agent.py          # LLM code-writing agent (approved plan only)
│   ├── conversion_log.py            # Real-time append-only conversion log
│   ├── approval_gate.py             # Human approval gate + checkpoint/resume
│   │
│   └── llm/                         # Modular LLM provider abstraction
│       ├── __init__.py              # Public exports (LLMRouter, LLMConfig, etc.)
│       ├── base.py                  # LLMMessage, LLMResponse, LLMConfig, BaseLLMProvider
│       ├── registry.py              # LLMRouter -- provider factory + fallback chain
│       └── providers/
│           ├── anthropic_provider.py    # Anthropic Claude API
│           ├── openai_provider.py       # OpenAI GPT + Azure OpenAI
│           ├── openai_compat_provider.py # LM Studio, vLLM, Together AI, etc.
│           ├── ollama_provider.py       # Ollama native REST (local)
│           └── llamacpp_provider.py     # Local GGUF files via llama.cpp
│
├── prompts/                         # LLM prompt files (edit without touching Python)
│   ├── __init__.py                  # load_prompt() / list_prompts() loader with LRU cache
│   ├── README.md                    # Prompt editing guide
│   ├── plan_system.txt                      # PlanAgent system prompt (simpler_grants target)
│   ├── plan_system_hrsa_pprs.txt            # PlanAgent system prompt (hrsa_pprs target)
│   ├── plan_document_template.md            # Shared Markdown scaffold (template-only mode)
│   ├── conversion_system.txt                # ConversionAgent system prompt (simpler_grants)
│   ├── conversion_system_hrsa_pprs.txt      # ConversionAgent system prompt (hrsa_pprs)
│   ├── conversion_target_stack.txt          # Target stack reference (simpler_grants)
│   └── conversion_target_stack_hrsa_pprs.txt # Target stack reference (hrsa_pprs)
│
├── config/
│   ├── skillset-config.json         # Source/target stack + component mappings
│   ├── rules-config.json            # Guardrails (RULE-001 to RULE-010)
│   └── schemas/
│       ├── skillset-schema.json     # JSON Schema for skillset config
│       └── rules-schema.json        # JSON Schema for rules config
│
├── templates/                       # Jinja2 conversion scaffolds
│   ├── ng-component-to-react.jinja2         # MAP-001: Angular -> React
│   ├── ng-service-to-fetcher.jinja2         # MAP-002: Angular service -> fetcher
│   ├── mvc-controller-to-flask-route.jinja2 # MAP-003: C# controller -> Flask route
│   ├── repository-to-sqlalchemy-service.jinja2 # MAP-004: Repository -> SQLAlchemy
│   ├── csharp-model-to-sqlalchemy.jinja2    # MAP-005: C# model -> SA model
│   ├── ng-module-to-nextjs-feature.jinja2   # MAP-006: NgModule -> feature folder
│   └── passthrough.jinja2                   # Fallback (no scaffold)
│
├── plans/                           # Generated Plan Documents (Markdown)
├── logs/                            # Dependency graphs + conversion logs
├── output/                          # Generated converted code
├── checkpoints/                     # Run state for resume support
│
└── examples/
    ├── legacy_source/               # Real GPRS source files (for testing)
    └── target_reference/            # simpler-grants-gov patterns (reference)
```

---

## Component Mappings

| ID | Source Pattern | Target Pattern |
|---|---|---|
| MAP-001 | Angular 2 `@Component` (NgModule + RxJS Subject) | Next.js React functional component with hooks |
| MAP-002 | Angular 2 `@Injectable()` service (BaseService) | Typed async fetcher function (server-only or client hook) |
| MAP-003 | ASP.NET Core Area Controller (SolutionBaseController) | Python APIFlask Blueprint route |
| MAP-004 | EF Core / Dapper Repository class | SQLAlchemy 2.0 service function with `db.Session` |
| MAP-005 | C# Model / Entity class | SQLAlchemy 2.0 `Mapped[]` model + Pydantic schema |
| MAP-006 | Angular 2 `@NgModule` | Next.js feature folder + barrel exports |

---

## Guardrail Rules

| ID | Name | Enforcement |
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

---

## Reference Codebases

| Role | `--target` | Path |
|---|---|---|
| Source (legacy) | *(all)* | `HAB-GPRSSubmission` |
| Target — simpler-grants-gov | `simpler_grants` | `simpler-grants-gov` |
| Target — HRSA-Simpler-PPRS | `hrsa_pprs` | `HRSA-Simpler-PPRS` |

---

## Prompts

All LLM prompts live in the `prompts/` directory as plain `.txt` / `.md` files.
They are loaded at runtime via `prompts.load_prompt()` and cached in memory,
so they can be edited independently of the Python source code.

Each target stack (`--target`) has its own set of system prompts:

| File | Target | Purpose |
|---|---|---|
| `prompts/plan_system.txt` | `simpler_grants` | System prompt sent to the LLM for plan generation |
| `prompts/plan_system_hrsa_pprs.txt` | `hrsa_pprs` | System prompt for plan generation (HRSA-Simpler-PPRS) |
| `prompts/plan_document_template.md` | *(shared)* | Markdown scaffold used in template-only (no-LLM) mode |
| `prompts/conversion_system.txt` | `simpler_grants` | System prompt for code conversion (has `{rules_text}` and `{target_stack_summary}` placeholders) |
| `prompts/conversion_system_hrsa_pprs.txt` | `hrsa_pprs` | System prompt for code conversion (HRSA-Simpler-PPRS) |
| `prompts/conversion_target_stack.txt` | `simpler_grants` | Target stack reference injected into `conversion_system.txt` |
| `prompts/conversion_target_stack_hrsa_pprs.txt` | `hrsa_pprs` | Target stack reference for HRSA-Simpler-PPRS (Flask/psycopg2 patterns) |

To tune a prompt, open the file directly and edit the text — no Python changes needed.
See [`prompts/README.md`](prompts/README.md) for full details.

---

## Extending the Tool

### Add a new LLM provider
1. Create `agents/llm/providers/<name>_provider.py` implementing `BaseLLMProvider`
2. Register it in `agents/llm/registry.py` under `_load_provider()` and add a `PROVIDER_*` constant
3. Add the new provider name to the `--llm-provider` choices in `main.py`

### Add or edit a prompt
1. Open (or create) a `.txt` / `.md` file in `prompts/`
2. Edit the text directly — `{placeholder}` tokens are filled at runtime by the agent
3. If creating a new file, load it in your agent with `from prompts import load_prompt` then `load_prompt("my_prompt.txt")`
4. Document it in `prompts/README.md` and the table in this file

### Add a new target stack
1. Add `target_stack_<name>` and `project_structure_<name>` blocks to `config/skillset-config.json`
2. Create prompt files `prompts/plan_system_<name>.txt`, `prompts/conversion_system_<name>.txt`, `prompts/conversion_target_stack_<name>.txt`
3. Register the target in `PlanAgent._SYSTEM_PROMPT_FILES` and `ConversionAgent._PROMPT_FILES` in the respective agent files
4. Add the new target name to `--target` choices in `main.py`'s `build_arg_parser()` and add the `structure_key` mapping in `_build_approved_plan()`
5. Update this README and `prompts/README.md`

### Add a new component mapping
1. Add a new entry to `config/skillset-config.json` -> `component_mappings`
2. Create a corresponding Jinja2 template in `templates/`
3. The `ScopingAgent` and `ConversionAgent` will pick it up automatically

### Add a new guardrail rule
1. Add a new entry to `config/rules-config.json` -> `guardrails`
2. Reference the new `RULE-XXX` id in any relevant template or agent logic

### Migrate a new feature
```bash
python main.py \
  --feature-root "Y:/Solution/HRSA/HAB-GPRSSubmission/src/.../YourFeature" \
  --feature-name "YourFeature" \
  --mode full
```

---

*AI Migration Tool v1.2 | Built for HRSA GPRS -> simpler-grants-gov / HRSA-Simpler-PPRS migration*

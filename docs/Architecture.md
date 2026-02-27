# Architecture

## Overview

The AI Migration Tool is a **multi-agent pipeline** that converts legacy source files
into modern target code, one feature folder at a time. All stages are orchestrated by
`main.py` and exposed to AI coding agents via `run_agent.py`.

```
                      ┌─────────────────────────────────┐
   CLI / Agent        │         run_agent.py             │
   (Cursor, etc.)  ──▶│  reads YAML job file → main()   │
                      └──────────────┬──────────────────┘
                                     │
                      ┌──────────────▼──────────────────┐
                      │            main.py               │
                      │       Pipeline orchestrator      │
                      └──┬───────────────────────────────┘
          ┌──────────────┤
          │              │  Stage 1           Stage 2
          ▼              ▼
  ConfigIngestionAgent  ScopingAgent
  Validates config      AST + regex scan
  + skillset-config     of source files
          │              │
          └──────────────┤  Stage 3
                         ▼
                      PlanAgent
                      LLM-powered Markdown plan generator
                      (revision mode: --revise-plan)
                         │
                  Stage 4│
                         ▼
                    ApprovalGate
                    Human "yes" OR .approved marker file
                         │
                  Stage 5│
                         ▼
                   ConversionAgent
                   LLM converts each file per the plan
                         │
                         ▼
                   output/<feature>/
                   logs/<run-id>-conversion-log.*
```

---

## Module Map

### Entry Points

| File | Role |
|---|---|
| `run_agent.py` | **Primary entry point for all use cases.** Reads YAML job files, provides all agent-interactive commands, sets `AI_AGENT_MODE=1` |
| `main.py` | Core pipeline runner. Called by `run_agent.py`; can also be called directly for advanced CLI use |
| `setup_wizard.py` | Setup Wizard entry point (thin wrapper around `wizard/runner.py`) |

### Agents (`agents/`)

| Module | Class | Responsibility |
|---|---|---|
| `config_ingestion_agent.py` | `ConfigIngestionAgent` | Loads and validates `skillset-config.json` and `rules-config.json`; merges CLI overrides |
| `scoping_agent.py` | `ScopingAgent` | Walks the source feature folder; classifies files via AST + regex; produces a dependency graph |
| `plan_agent.py` | `PlanAgent` | Sends dependency graph to LLM; parses the structured Plan Document; supports revision mode |
| `approval_gate.py` | `ApprovalGate` | Prompts human for `yes` or detects `output/<feature>/.approved` marker for agent approval |
| `conversion_agent.py` | `ConversionAgent` | Iterates the approved plan; calls LLM per file; writes converted output; maintains checkpoint |
| `conversion_log.py` | `ConversionLog` | Append-only JSON + Markdown audit log written during conversion |
| `agent_context.py` | `AgentContext` | Shared immutable context passed through all pipeline stages |

### LLM Abstraction (`agents/llm/`)

```
agents/llm/
├── base.py           # LLMMessage, LLMResponse, LLMConfig, BaseLLMProvider (ABC)
├── registry.py       # LLMRouter — provider factory, auto-detect, soft/hard-fail logic
└── providers/
    ├── anthropic_provider.py      # Claude API
    ├── openai_provider.py         # OpenAI + Azure OpenAI
    ├── openai_compat_provider.py  # LM Studio, vLLM, Together AI
    ├── ollama_provider.py         # Ollama native REST
    └── llamacpp_provider.py       # Local GGUF via llama.cpp
```

`LLMRouter` exposes a single `.complete(messages, config)` method. Provider selection
follows the auto-detect chain or the explicit `llm.provider` value from the job file.

### Setup Wizard (`wizard/`)

| Module | Role |
|---|---|
| `detector.py` | `CodebaseInspector` — heuristic framework/language/database detection by scanning directory trees |
| `collector.py` | Interactive Q&A helpers (`collect_answers`, `collect_feature_selection`) |
| `generator.py` | Generates prompt text and skillset-config content for new targets |
| `writer.py` | `WizardWriter` — all file I/O with dry-run support |
| `registry.py` | Reads/writes `config/wizard-registry.json` and `config/skillset-config.json` |
| `runner.py` | `run_wizard()` / `list_targets()` orchestration |

### Prompts (`prompts/`)

Plain text files loaded at runtime by `prompts/__init__.py::load_prompt()` with an
LRU cache. Template tokens (`{placeholder}`) are filled at call time by each agent.

### Config (`config/`)

| File | Schema | Purpose |
|---|---|---|
| `skillset-config.json` | `schemas/skillset-schema.json` | Stack definitions (source + target stacks, component mappings, project structure) |
| `rules-config.json` | `schemas/rules-schema.json` | Guardrail rules (RULE-001 to RULE-010) |
| `wizard-registry.json` | *(inline)* | Idempotency registry for wizard-generated targets |

---

## Data Flow

### 1. Job file → validated config

`run_agent.py` loads the YAML job file, merges any CLI overrides (e.g. `--mode full`),
and passes a clean `namespace` object to `main.py`.

`ConfigIngestionAgent` reads `skillset-config.json` (selecting the right `target_stack_*`
and `project_structure_*` keys based on `pipeline.target`), merges `rules-config.json`,
and validates required fields. Output: a config dict consumed by all downstream agents.

### 2. Scoping

`ScopingAgent` walks `pipeline.feature_root`. For each file it:
- Detects file type (Angular component, service, C# controller, etc.)
- Applies AST parsing or regex to extract: imports, exports, classes, methods, decorators
- Classifies the file against component mapping IDs (MAP-001 to MAP-006)
- Writes `logs/<run-id>-dependency-graph.json`

The dependency graph is cached; `--revise-plan` reuses it without re-scanning.

### 3. Plan generation

`PlanAgent` constructs an LLM prompt from:
- System prompt (`prompts/plan_system[_<target>].txt`)
- Target stack reference (`prompts/conversion_target_stack[_<target>].txt`)
- Serialised dependency graph
- (Revision mode only) `REVISION FEEDBACK` and `ORIGINAL PLAN` sections

The LLM returns a structured Markdown Plan Document. The agent saves it as
`plans/<feature>-plan-<ts>-<run_id[:8]>.md` (or `...-rev.md` for revisions).

### 4. Approval

`ApprovalGate` checks for `output/<feature>/.approved` (written by `--approve-plan`).
If present, auto-approves. Otherwise, prompts the user to type `yes`.

### 5. Conversion

`ConversionAgent` reads the approved plan, iterates each file entry, and for each:
1. Reads the source file
2. Selects the appropriate Jinja2 template from `templates/`
3. Calls the LLM with the file content + target stack context
4. Validates the response (AMBIGUOUS / BLOCKED detection)
5. Writes the output file(s)
6. Appends a step to `ConversionLog`
7. Updates the checkpoint (enables `--resume`)

---

## Run ID and Deduplication

Each pipeline run is identified by a **run ID** derived deterministically from:
```python
hash(feature_name + feature_root + target)[:8]  # stable prefix
timestamp                                         # uniqueness
```

`run_agent.py --status` and `--revise-plan` use `_stable_run_id()` — which uses
only the hash portion — to locate existing artefacts without running the pipeline.

The `CompletedRunRegistry` (in `main.py`) persists completed run IDs in
`logs/completed-runs.json`. If a run is found there, the pipeline skips automatically
unless `--force` is passed.

---

## Agent Mode (`AI_AGENT_MODE`)

When `run_agent.py` is used, it sets `AI_AGENT_MODE=1` before calling the pipeline.
This env var switches all LLM failure handling from **hard-fail** to **soft-fail**:

| Mode | LLM error behaviour |
|---|---|
| `AI_AGENT_MODE=0` (default / `main.py` direct) | Raises `LLMConfigurationError` — pipeline halts |
| `AI_AGENT_MODE=1` (`run_agent.py`) | Returns a Jinja2 template scaffold — pipeline continues |

This allows agents (Cursor, Windsurf, etc.) to receive partial output even when the
LLM is unavailable, rather than crashing the session.

# AI Migration Tool — Documentation

Welcome to the technical documentation for the **AI Migration Tool** — a multi-agent
pipeline that migrates legacy codebases to modern target stacks using LLMs.

---

## Quick Navigation

### Getting Started
- **[README](../README.md)** — Quick start, installation, prerequisites
- **[Setup Wizard](Setup-Wizard.md)** — Configure a new migration target
- **[Job Files Reference](Job-Files-Reference.md)** — YAML job file field reference

### Technical Reference
- **[Architecture](Architecture.md)** — Pipeline data-flow, module map, agent responsibilities
- **[Pipeline Stages](Pipeline-Stages.md)** — Per-stage inputs, outputs, and config options
- **[Agent Interactive Mode](Agent-Interactive-Mode.md)** — `--list-features`, `--status`, `--approve-plan`, `--revise-plan`
- **[LLM Providers](LLM-Providers.md)** — All supported LLM backends with examples
- **[Target Stacks](Target-Stacks.md)** — Built-in stacks (`simpler_grants`, `hrsa_pprs`, `snake_case`) and custom targets

### Customisation
- **[Prompt Engineering](Prompt-Engineering.md)** — How prompts work, placeholders, per-target tuning
- **[Guardrail Rules](Guardrail-Rules.md)** — All RULE-XXX rules, enforcement levels, rationale
- **[Extending the Tool](Extending-the-Tool.md)** — New providers, stacks, mappings, rules

### Operations
- **[Troubleshooting](Troubleshooting.md)** — Common errors and fixes

---

## The Autonomous Agent Workflow

The tool is designed for AI coding agents (Cursor, Windsurf, Copilot, AntiGravity)
to drive the complete migration lifecycle without human TTY interaction:

```bash
# 1. Discover features
python run_agent.py --list-features --json

# 2. Create job file (no prompts)
python run_agent.py --new-job --feature ActionHistory --target snake_case \
                    --non-interactive --json

# 3. Generate migration plan
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml

# 4. Check status
python run_agent.py --status \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml --json

# 5a. Approve + convert
python run_agent.py --approve-plan \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml --mode full

# 5b. Or revise plan with feedback
python run_agent.py --revise-plan \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml \
  --feedback "Flag pfm-auth imports as BLOCKED."
```

See **[Agent Interactive Mode](Agent-Interactive-Mode.md)** for the complete reference.

---

## Pipeline Overview

```
Config Ingestion → Scoping & Analysis → Plan Generation → Approval → Conversion
```

| Stage | Agent | Output |
|---|---|---|
| 1. Config Ingestion | `ConfigIngestionAgent` | Validated config |
| 2. Scoping | `ScopingAgent` | `logs/<run-id>-dependency-graph.json` |
| 3. Plan | `PlanAgent` | `plans/<feature>-plan-<ts>.md` |
| 4. Approval | `ApprovalGate` | Human `yes` or `.approved` marker |
| 5. Conversion | `ConversionAgent` | `output/<feature>/` |

See **[Pipeline Stages](Pipeline-Stages.md)** for full details.

---

## Key Concepts

| Concept | Where to learn more |
|---|---|
| Job files | [Job Files Reference](Job-Files-Reference.md) |
| Plan revision (`--revise-plan`) | [Agent Interactive Mode](Agent-Interactive-Mode.md#--revise-plan) |
| Approval marker (`.approved`) | [Pipeline Stages](Pipeline-Stages.md#stage-4--approval-gate) |
| LLM soft-fail / `AI_AGENT_MODE` | [Architecture](Architecture.md#agent-mode-ai_agent_mode) |
| Custom target registration | [Setup Wizard](Setup-Wizard.md) |
| Guardrail rules | [Guardrail Rules](Guardrail-Rules.md) |
| Deduplication / `--force` | [Pipeline Stages](Pipeline-Stages.md#deduplication) |
| Prompt customisation | [Prompt Engineering](Prompt-Engineering.md) |

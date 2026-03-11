# GitHub Copilot Workspace Instructions — AI Migration Tool

> **Before starting any task:** Read `AGENT.md` in the repo root.
> It is the canonical workflow reference — job file format, pipeline stages,
> target stacks, LLM configuration, orchestration mode, and all workflows.

---

## Critical rules

- **Entry point:** Always `run_agent.py` — never `main.py`
- **Output dirs:** Never modify `plans/`, `logs/`, `output/`, `checkpoints/`, `reports/` — pipeline outputs
- **Mode discipline:** Run `plan` first → review the Plan Document → then `full`
- **Config:** Never edit `config/skillset-config.json` or `config/rules-config.json` unless asked
- **Memory files:** `config/memory/*.json` — team-shared, **committed**. Do NOT gitignore.
- **Registry:** `config/wizard-registry.json` — machine-specific, NOT committed
- **Dedup:** Use `--force` to re-run a migration that already completed
- **Orchestration:** `orchestration.enabled: false` (default) = sequential pipeline;
  set `true` in job YAML to enable the LLM-driven orchestrator (see `AGENT.md`)

---

## New target setup

Before creating jobs for a stack not already registered, run the wizard:

```bash
python run_agent.py --setup                          # interactive
python run_agent.py --setup --config wizard.json     # pre-filled JSON (CI/agent)
python run_agent.py --setup --list-targets           # see registered targets
python run_agent.py --setup --dry-run                # preview without writing
```

Wizard generates: `prompts/plan_system_<id>.txt`, `prompts/conversion_system_<id>.txt`,
`prompts/conversion_target_stack_<id>.txt`, and `agent-prompts/_template_<id>.yaml`.

---

## Autonomous workflow (quick reference)

```bash
# 1. Discover features
python run_agent.py --list-features --json

# 2. Create job file
python run_agent.py --new-job --feature <Name> --target snake_case \
                    --non-interactive --json

# 3. Generate plan (no code written)
python run_agent.py --job agent-prompts/migrate-<name>-snake_case.yaml

# 4. Check status
python run_agent.py --status --job agent-prompts/migrate-<name>-snake_case.yaml --json

# 5a. Approve + convert
python run_agent.py --approve-plan --job agent-prompts/migrate-<name>-snake_case.yaml
python run_agent.py --job agent-prompts/migrate-<name>-snake_case.yaml --mode full

# 5b. Revise plan with feedback (clears approval, no re-scoping)
python run_agent.py --revise-plan \
  --job agent-prompts/migrate-<name>-snake_case.yaml \
  --feedback "Flag pfm-auth imports as BLOCKED."
# Then re-approve and re-run full
```

---

## Job YAML sections reference

Beyond `pipeline:` and `llm:`, job files include:

```yaml
integration:          # Stage 7 — place output into target repo
  enabled: true
  add_dependencies: true
  generate_migration: true
  update_barrel_files: true
  update_python_inits: true
  post_placement_command: null   # e.g. "npm run build"

ui_consistency:       # Stage 6b — diff CSS/HTML/bindings vs source template
  enabled: true
  fail_on_missing_classes: true

verification:         # Stage 8 — run build/test/lint in target repo
  enabled: false
  tool: commands      # commands | playwright
  commands: []        # auto-detected if empty

orchestration:        # optional LLM-driven orchestrator (default: disabled)
  enabled: false
  learning: true      # always extract patterns, even in sequential mode
  backend: internal   # internal | google_adk
```

---

## Domain knowledge — load on demand

Reference docs only when needed to avoid bloating context:

| Topic | File |
|---|---|
| Pipeline stages 1–8 detail | `docs/Pipeline-Stages.md` |
| Architecture & agent graph | `docs/Architecture.md` |
| Job file YAML reference | `docs/Job-Files-Reference.md` |
| Setup wizard guide | `docs/Setup-Wizard.md` |
| Target stacks & skillset-config | `docs/Target-Stacks.md` |
| Guardrail rules (RULE-XXX) | `docs/Guardrail-Rules.md` |
| LLM provider configuration | `docs/LLM-Providers.md` |
| Troubleshooting | `docs/Troubleshooting.md` |

See `AGENT.md` for the complete reference: full command list, job YAML structure,
pipeline stages, target stacks, LLM setup, and orchestration mode.

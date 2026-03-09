# GitHub Copilot Workspace Instructions — AI Migration Tool

> **Before starting any task:** Read `AGENT.md` in the repo root.
> It is the canonical workflow reference — job file format, pipeline stages,
> target stacks, LLM configuration, orchestration mode, and all workflows.

---

## Critical rules

- **Entry point:** Always `run_agent.py` — never `main.py`
- **Output dirs:** Never modify `plans/`, `logs/`, `output/`, `checkpoints/` — pipeline outputs
- **Mode discipline:** Run `plan` first → review the Plan Document → then `full`
- **Config:** Never edit `config/skillset-config.json` or `config/rules-config.json` unless asked
- **Memory files:** `config/memory/*.json` — team-shared, **committed**. Do NOT gitignore.
- **Registry:** `config/wizard-registry.json` — machine-specific, NOT committed
- **Dedup:** Use `--force` to re-run a migration that already completed
- **Orchestration:** `orchestration.enabled: false` (default) = sequential pipeline;
  set `true` in job YAML to enable the LLM-driven orchestrator (see `AGENT.md`)

---

## Autonomous workflow (quick reference)

```bash
python run_agent.py --list-features --json
python run_agent.py --new-job --feature <Name> --target snake_case \
                    --non-interactive --json
python run_agent.py --job agent-prompts/migrate-<name>-snake_case.yaml
python run_agent.py --status --job agent-prompts/migrate-<name>-snake_case.yaml --json
python run_agent.py --approve-plan --job agent-prompts/migrate-<name>-snake_case.yaml
python run_agent.py --job agent-prompts/migrate-<name>-snake_case.yaml --mode full
```

See `AGENT.md` for the complete reference: full command list, job YAML structure,
pipeline stages, target stacks, LLM setup, and orchestration mode.

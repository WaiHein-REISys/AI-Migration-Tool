# CLAUDE.md — Claude Code Instructions

## Working directory

**Always make code changes directly in the repository root:**
```
Y:\Solution\HRSA\ai-migration-tool\
```

Never create, modify, or test files inside `.claude/`. That folder is
reserved for Claude Code session state (conversation history, settings)
and is excluded from version control.

## Worktrees

Worktrees under `.claude/worktrees/` are for **context and reference only**.
Do not:
- Write or edit source files inside a worktree
- Run tests or verify changes from inside a worktree
- Use a worktree as a sandbox or staging area for code

Always apply changes and run verification commands from the repo root.

## Branches and commits

- Create feature branches from `main` in the repo root
- Stage and commit from the repo root
- Never commit from inside `.claude/`

## Running the tool

```bash
# Always activate the venv first (repo root)
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS/Linux

# Always route through run_agent.py — never call main.py directly
python run_agent.py --job agent-prompts/<file>.yaml
```

See `.github/copilot-instructions.md` for the full command reference,
pipeline stages, and recommended workflows.

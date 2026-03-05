# User Guide — Agent Chat Mode
## For Cursor Agent, Windsurf Cascade, GitHub Copilot, and Other AI Coding Agents

**AI Migration Tool · 2026-03-05**

---

## What Is Agent Chat Mode?

Agent Chat Mode is the fully autonomous, non-interactive workflow for AI coding agents such as
**Cursor Agent** (Composer), **Windsurf Cascade**, **GitHub Copilot Chat**, and similar tools.
In this mode the agent drives the entire migration lifecycle — feature discovery, plan generation,
plan review, approval, full code conversion, and validation — without any terminal prompts or
human typing.

Key design principles:
- Every command produces **machine-readable JSON** (`--json`) so the agent can parse success/failure
- The human **approval gate** is satisfied by writing a `.approved` marker file instead of a TTY `yes`
- If the LLM is unavailable the pipeline **soft-fails** to a Jinja2 scaffold; it never crashes the agent
- All outputs (`plans/`, `logs/`, `output/`, `checkpoints/`, `reports/`) are read-only from code — the agent may read them but must never edit them directly

---

## Quick-Start: The 5-Step Autonomous Workflow

Copy this workflow into Cursor Composer or Windsurf Cascade as the starting point for any migration task.

```bash
# ── STEP 1: Discover features available in the source codebase ──────────────
python run_agent.py --list-features \
  --source examples/legacy_source \
  --json

# ── STEP 2: Create a job file non-interactively ──────────────────────────────
python run_agent.py --new-job \
  --feature ActionHistory \
  --target hrsa_simpler_pprs_repo \
  --non-interactive \
  --json

# ── STEP 3: Generate the migration plan ──────────────────────────────────────
python run_agent.py --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml

# ── STEP 4: Check status and read the plan ───────────────────────────────────
python run_agent.py --status \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml \
  --json
# Then read the plan file to verify it:
# cat plans/actionhistory-plan-<timestamp>-<run-id>.md

# ── STEP 5a: Approve the plan and run full conversion ────────────────────────
python run_agent.py --approve-plan \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml

python run_agent.py --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml \
  --mode full

# ── STEP 5b (if plan needs changes): Revise, re-approve, then convert ────────
python run_agent.py --revise-plan \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml \
  --feedback "The pfm-auth service has no Flask equivalent. Mark all pfm-auth imports BLOCKED. Add a DB migration section for the audit_events table."

python run_agent.py --approve-plan \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml

python run_agent.py --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml \
  --mode full
```

---

## Setting Up for Agent Chat Mode

### 1. Activate the virtual environment

```bash
source .venv/bin/activate          # macOS/Linux
# .venv\Scripts\activate           # Windows
```

### 2. Set required environment variables

The pipeline auto-detects LLM providers from environment variables. At least one must be set.

```bash
# Option A — Anthropic Claude API (recommended for best results)
export ANTHROPIC_API_KEY="sk-ant-..."

# Option B — OpenAI API
export OPENAI_API_KEY="sk-..."

# Option C — Use Claude Code CLI (no API key needed if claude CLI is on PATH)
# No env var required — detected automatically

# Option D — Template-only mode (no API key, no LLM)
# Use --no-llm flag or set llm.no_llm: true in YAML
```

### 3. Verify the tool is operational

```bash
python run_agent.py --list-jobs --json
```

Expected output format:
```json
{
  "jobs": [
    { "name": "migrate-actionhistory-hrsa_pprs", "path": "agent-prompts/migrate-actionhistory-hrsa_pprs.yaml" },
    { "name": "migrate-commands-hrsa_simpler_pprs_repo", "path": "agent-prompts/migrate-commands-hrsa_simpler_pprs_repo.yaml" }
  ]
}
```

---

## Cursor Agent Setup

### Rule file location

The file `.cursor/rules.mdc` is already present in the repository. Cursor automatically loads it
when you open this project. It instructs Cursor Composer to:
- Always use `run_agent.py` (never `main.py`)
- Use `--json` on status/list commands
- Use `--non-interactive` for job creation
- Write `.approved` marker instead of TTY approval
- Never edit files in `plans/`, `logs/`, `output/`, `checkpoints/`, `reports/`

### Triggering a migration from Cursor Composer

Open Cursor Composer (⌘+I on macOS / Ctrl+I on Windows) and type:

> "Migrate the ActionHistory feature from the Reactivites source to Flask/Python using the hrsa_simpler_pprs_repo target. Run the full pipeline autonomously."

Cursor will execute the 5-step workflow above, reading JSON output at each stage to decide
whether to proceed, revise, or report an error to you.

### Parsing `--status` output in Cursor

```json
{
  "run_id": "conv-actionhistory-hrsa-a1b2c3d4",
  "pipeline_mode": "plan",
  "plan": {
    "status": "generated",
    "path": "plans/actionhistory-plan-20260305-120000-conv-act.md",
    "approved": false
  },
  "conversion": { "status": "pending" },
  "validation": { "status": "pending" }
}
```

**Decision logic for the agent:**
- `plan.status == "generated"` AND `plan.approved == false` → Read plan → call `--approve-plan` or `--revise-plan`
- `plan.approved == true` AND `conversion.status == "pending"` → Call `--mode full`
- `conversion.status == "completed"` AND `validation.status == "passed"` → Done ✅
- `validation.status == "failed"` → Read validation report → call `--revise-plan` with specific feedback

---

## Windsurf Cascade Setup

### Rule file location

The file `.windsurfrules` is already present in the repository root. Windsurf Cascade reads it
automatically when the project workspace is opened.

### Triggering a migration from Windsurf Cascade

Open the Cascade panel and type a natural language instruction such as:

> "Use the AI Migration Tool to migrate the Commands feature. Discover features, create a job file, generate and review the plan, then run the full conversion."

Cascade will execute the CLI commands, parse JSON output, and report progress back to you in
the chat interface.

### Windsurf-specific tips

- Cascade can read generated plan files directly: use `cat plans/*.md` to surface the plan in context
- After conversion, Cascade can read output files and suggest additional fixes
- Use `--dry-run` to preview what Cascade would do before committing to writes

---

## All Agent-Mode Commands Reference

### Feature Discovery

```bash
# List features — returns JSON array of discovered feature folders
python run_agent.py --list-features --json

# Specify explicit source root (if different from YAML default)
python run_agent.py --list-features \
  --source /path/to/legacy/source \
  --json
```

Output example:
```json
{
  "features": [
    { "name": "ActionHistory", "path": "/path/to/source/ActionHistory", "file_count": 12 },
    { "name": "Commands",      "path": "/path/to/source/Commands",      "file_count": 8  }
  ]
}
```

> **Requirement:** Source files must be organised into **subfolders** under the source root (one folder per feature). Flat directories are not detected.

---

### Job Creation (Non-Interactive)

```bash
python run_agent.py \
  --new-job \
  --feature <FeatureName> \
  --target <target-id> \
  --non-interactive \
  --json
```

| Flag | Required | Description |
|------|----------|-------------|
| `--feature <name>` | Yes | Feature folder name (must match a subfolder in source root) |
| `--target <id>` | Yes | Target stack ID from `config/wizard-registry.json` |
| `--non-interactive` | Yes | Suppresses all TTY prompts — required for agent mode |
| `--json` | Yes | Output machine-readable result |

Output example:
```json
{
  "status": "created",
  "job_file": "agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml",
  "run_id_preview": "conv-actionhistory-hrsa-a1b2c3d4"
}
```

---

### Plan Generation

```bash
# Default mode (generates plan only, no code written)
python run_agent.py --job agent-prompts/<file>.yaml

# Explicit plan mode
python run_agent.py --job agent-prompts/<file>.yaml --mode plan
```

The plan is written to `plans/<feature>-plan-<timestamp>-<run-id>.md`.

---

### Status Check

```bash
python run_agent.py \
  --status \
  --job agent-prompts/<file>.yaml \
  --json
```

Key fields to check in the output:
| Field | Values | Meaning |
|-------|--------|---------|
| `plan.status` | `pending`, `generated` | Has a plan been written? |
| `plan.approved` | `true`, `false` | Has the plan been approved? |
| `conversion.status` | `pending`, `in_progress`, `completed` | Conversion state |
| `validation.status` | `pending`, `passed`, `failed` | Validation result |
| `validation.findings[].status` | `PASS`, `FAIL` | Per-step result |
| `validation.findings[].confidence` | 0.0–1.0 | Validator certainty |

---

### Plan Approval (Agent-Written Marker)

```bash
python run_agent.py --approve-plan --job agent-prompts/<file>.yaml
```

This writes a `.approved` marker file alongside the plan. The pipeline reads this marker at
Stage 4 (ApprovalGate) and skips the TTY prompt. No human interaction required.

> **Never** approve without reading the plan first. Always check `plan.status == "generated"`
> via `--status --json` and read the plan content before calling `--approve-plan`.

---

### Plan Revision (With LLM Feedback)

```bash
python run_agent.py \
  --revise-plan \
  --job agent-prompts/<file>.yaml \
  --feedback "<specific feedback text>"
```

- Reuses the existing dependency graph — no re-scoping pass
- Injects feedback into the LLM prompt alongside the previous plan
- Writes a `-rev.md` revised plan file
- **Automatically clears** the `.approved` marker — you must call `--approve-plan` again

**Good feedback examples:**
```
"The pfm-auth service import has no Flask equivalent. Mark all pfm-auth usages as BLOCKED."

"Step B2 is missing a migration section for the audit_events database table. Add it."

"The date format in Step B1 should use ISO 8601 strings, not Python datetime objects."
```

---

### Full Conversion

```bash
python run_agent.py --job agent-prompts/<file>.yaml --mode full
```

**Prerequisites before calling this:**
1. `--status` confirms `plan.approved == true`
2. Plan has been reviewed (read the `.md` file)

**After completion**, check:
```bash
python run_agent.py --status --job agent-prompts/<file>.yaml --json
# Look for: validation.status == "passed"

# Read the validation report
cat logs/<run-id>-validation-report.md
```

---

### Force Re-Run from Scratch

```bash
python run_agent.py --job agent-prompts/<file>.yaml --force
```

Clears all checkpoints for the run and re-generates from Stage 1. Use when:
- You have modified the source files
- You have updated prompt files in `prompts/`
- A previous run was interrupted and the checkpoint is corrupt

---

### Dry Run (No Writes)

```bash
python run_agent.py --job agent-prompts/<file>.yaml --dry-run
```

Runs the full pipeline logic but writes nothing to disk. Logs all planned actions to console/logs.
Useful for previewing what the agent would do before committing.

---

## YAML Job File — Agent-Mode Recommended Settings

```yaml
pipeline:
  feature_name: "ActionHistory"
  feature_root: "/path/to/source/ActionHistory"
  mode: "plan"          # Start with plan; switch to full after approval
  target: "hrsa_simpler_pprs_repo"
  output_root: null     # Default: output/<feature_name>/
  dry_run: false
  auto_approve: false   # ALWAYS false in production
  force: false

llm:
  provider: null        # Auto-detect from environment
  model: null           # Provider default
  no_llm: false
  timeout: 600          # Seconds per LLM call

notes: |
  Agent context:
  - Source: ASP.NET Core / Entity Framework
  - Target: Flask / SQLAlchemy / marshmallow
  - Known external dependencies: none
  - Required DB tables: activities, people
```

> **`auto_approve: true`** — Use **only for testing or demos**. In production agent workflows,
> always have the agent call `--approve-plan` explicitly after reading the plan. This preserves
> the human-in-the-loop checkpoint.

---

## Reading Output After Conversion

After `--mode full` completes, the agent should:

```bash
# 1. Check validation result
python run_agent.py --status --job agent-prompts/<file>.yaml --json

# 2. Read the human-readable validation report
cat logs/<run-id>-validation-report.md

# 3. Read individual converted files
ls output/<feature_name>/api/src/api/

# 4. If validation failed, read the structured report and revise
python run_agent.py \
  --revise-plan \
  --job agent-prompts/<file>.yaml \
  --feedback "$(cat logs/<run-id>-validation-report.md | head -50)"
```

---

## Error Handling Cheat Sheet

| Error / Symptom | Cause | Fix |
|----------------|-------|-----|
| `LLMConfigurationError: No provider detected` | No API key set, no CLI on PATH | Set `ANTHROPIC_API_KEY` or add `--no-llm` |
| `ApprovalGate: no .approved marker found` | `--approve-plan` not called | Call `--approve-plan` before `--mode full` |
| `FeatureNotFoundError` | Feature folder doesn't exist in source root | Check `--list-features` output |
| `CheckpointConflict` | Previous run exists for same feature/target | Add `--force` to re-run from scratch |
| `ValidationFailed (confidence < 0.35)` | Validator simulation errored (CLI model mismatch) | Fix validator CLI config; see `logs/<run-id>-validation-report.json` |
| `plan.status == "pending"` after `--status` | Plan not yet generated | Run without `--mode full` first |
| JSON output contains `"status": "error"` | Pipeline stage failed | Read `error.message` and `error.traceback` fields |

---

## Security Notes for Agent Usage

- Never set `auto_approve: true` in production job files
- The agent should always **read the plan** before calling `--approve-plan` — the plan may
  contain BLOCKED items or ambiguous mappings that require human decision
- Output directories are **append-only during a run** — the agent must not delete or overwrite
  previously converted files from earlier steps
- Prompt files (`prompts/*.txt`) are safe to read; the agent should not modify them unless
  explicitly asked to tune LLM behaviour

---

*AI Migration Tool · Agent Chat User Guide · 2026-03-05*

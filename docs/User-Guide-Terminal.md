# User Guide — Terminal Run
## For Developers Running the Migration Tool Interactively

**AI Migration Tool · 2026-03-05**

---

## What Is Terminal Run Mode?

Terminal Run is the **human-interactive workflow** where a developer executes commands directly
in a shell, responds to prompts, reviews generated plan files, and controls each stage of the
migration manually.

Unlike Agent Chat Mode (which is fully automated), Terminal Run is designed for:
- **First-time setup** and exploration of the tool
- **Reviewing and refining** migration plans before committing to code generation
- **Debugging** a migration that is producing unexpected output
- **Ad-hoc** migrations of individual features

---

## Prerequisites

### 1. Clone and install

```bash
git clone <repo-url>
cd AI-Migration-Tool

python -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

pip install -r requirements.txt
```

### 2. Set UTF-8 encoding (Windows only)

```bash
set PYTHONIOENCODING=utf-8
```

### 3. Set your LLM provider

At least one of the following must be configured (see the LLM Run guide for full details):

```bash
# Recommended: Anthropic Claude API
export ANTHROPIC_API_KEY="sk-ant-..."

# Or: OpenAI API
export OPENAI_API_KEY="sk-..."

# Or: Use Claude Code CLI (if the `claude` binary is on your PATH — no key needed)
# Nothing to set; auto-detected

# Or: No LLM (template-only scaffold)
# Use --no-llm flag when running commands
```

### 4. Verify installation

```bash
python run_agent.py --list-jobs
```

You should see a list of available job YAML files. If you see an error, check that `.venv` is
activated and `requirements.txt` was installed successfully.

---

## The Standard Workflow (Step by Step)

```
┌─────────────────────────────────────────────────────────────────┐
│  Plan mode (default)              Full mode                      │
│                                                                   │
│  1. List features                 ─────── After plan review ──── │
│  2. Create job file (optional)     4. Approve plan               │
│  3. Generate plan                  5. Run full conversion         │
│     └─ Review plan.md              6. Read validation report      │
│     └─ Revise if needed            7. Fix and re-run if needed    │
└─────────────────────────────────────────────────────────────────┘
```

---

### Step 1 — Discover available features

```bash
python run_agent.py --list-features --source examples/legacy_source
```

Output:
```
Features found in examples/legacy_source:
  ActionHistory    (12 files)
  Commands         ( 8 files)
  Profiles         ( 5 files)
```

> Source files must be organised into **subfolders** — one subfolder per feature.
> Flat source directories are not supported.

---

### Step 2 — Create a job file (interactive)

If you don't already have a job YAML file, run the interactive creator:

```bash
python run_agent.py --new-job
```

The tool will ask:
```
Feature name: ActionHistory
Target stack: hrsa_simpler_pprs_repo
Source root [auto-detected]: <Enter>
Output root [default: output/ActionHistory]: <Enter>
```

This creates `agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml`.

**Alternatively**, copy an existing template and edit it manually:

```bash
cp agent-prompts/_template_hrsa_simpler_pprs_repo.yaml \
   agent-prompts/migrate-myfeature-hrsa.yaml
# Edit the feature_name, feature_root, and notes fields
```

---

### Step 3 — Generate the migration plan

```bash
python run_agent.py --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml
```

This runs **Plan mode** by default. It:
1. Scans the source folder (creates `logs/<run-id>-dependency-graph.json`)
2. Sends the dependency graph to the LLM
3. Writes a structured Markdown plan to `plans/`

```
[INFO] Scoping ActionHistory... ✓  12 files analysed
[INFO] Generating migration plan via claude-3-7-sonnet...
[INFO] Plan written: plans/actionhistory-plan-20260305-120000-conv-act.md
[INFO] Pipeline paused. Review the plan, then approve and run --mode full.
```

---

### Step 4 — Review the plan

```bash
# Read in terminal (with pager)
cat plans/actionhistory-plan-*.md | less

# Or open in your editor
code plans/actionhistory-plan-*.md
```

**What to look for in the plan:**

| Section | What to check |
|---------|---------------|
| **Steps** | Do the steps match the source files you expect to be converted? |
| **BLOCKED items** | Any `[BLOCKED]` items need human decision before proceeding |
| **Transformation rationale** | Does the LLM understand the intent of each source file? |
| **HTTP mappings** | Are `[HttpGet]`/`[HttpPost]`/`[HttpPut]`/`[HttpDelete]` correctly mapped? |
| **DB schema** | Are table and column names correct for the target database? |
| **External dependencies** | Are any `pfm-*` or platform library imports flagged appropriately? |

---

### Step 4b — Revise the plan (if needed)

If the plan has issues:

```bash
python run_agent.py \
  --revise-plan \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml \
  --feedback "Step B1 maps the wrong HTTP verb — EditActivity.cs uses [HttpPut] not [HttpPost]. Fix the decorator in the plan."
```

The tool regenerates only the plan (reuses the existing dependency graph — fast).
A `-rev.md` plan file is written. The approval marker is automatically cleared.

---

### Step 5 — Approve the plan

At the terminal, when prompted:

```
Plan review required.
Plan file: plans/actionhistory-plan-20260305-120000-conv-act.md

Approve this plan and proceed with full conversion? [yes/no]: yes
```

Or pre-approve via command line and then run:

```bash
python run_agent.py --approve-plan \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml
```

---

### Step 6 — Run full conversion

```bash
python run_agent.py \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml \
  --mode full
```

You will see real-time progress for each step:

```
[INFO] Step B1 — Converting EditActivity.cs → edit_activity_routes.py
[INFO]   LLM call: claude-3-7-sonnet (in=7842 tokens, out=1204 tokens, 68s)
[INFO]   Written: output/ActionHistory/api/src/api/editactivity.cs/edit_activity_routes.py
[INFO] Step B2 — Converting CreateActivity.cs → create_activity_routes.py
[INFO]   LLM call: claude-3-7-sonnet (in=11203 tokens, out=1401 tokens, 43s)
[INFO]   Written: output/ActionHistory/api/src/api/createactivity.cs/create_activity_routes.py
[INFO] Step B3 — Converting DeleteActivity.cs → delete_activity_routes.py
...
[INFO] Validation: passed (3/3 passed)
[INFO] Run completed: conv-actionhistory-hrsa-a1b2c3d4
```

---

### Step 7 — Read the results

```bash
# Human-readable validation report
cat logs/conv-actionhistory-hrsa-*/validation-report.md

# View converted files
ls output/ActionHistory/api/src/api/

# Read a specific converted file
cat output/ActionHistory/api/src/api/editactivity.cs/edit_activity_routes.py

# View the structured conversion log
cat logs/conv-actionhistory-hrsa-*/conversion-log.md
```

If validation **failed**, read the specific reasons:

```bash
cat logs/conv-actionhistory-hrsa-*/validation-report.md
```

Then revise and re-run:

```bash
python run_agent.py \
  --revise-plan \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml \
  --feedback "B2 Person INSERT is missing Age, DateOfBirth, and Interests fields. The validator flagged this."

python run_agent.py --approve-plan \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml

python run_agent.py \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml \
  --mode full \
  --force
```

---

## Useful Flags for Terminal Use

### `--verbose` — Debug output

```bash
python run_agent.py \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml \
  --verbose
```

Shows DEBUG-level logs including:
- Exact LLM prompts being sent (truncated)
- Raw LLM response headers
- Rule injection details
- Checkpoint read/write events

---

### `--dry-run` — Preview without writing files

```bash
python run_agent.py \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml \
  --mode full \
  --dry-run
```

The pipeline runs all logic but writes **no files to disk**. Useful for:
- Confirming the tool can find all source files
- Estimating token usage before paying API costs
- Testing a new job file configuration

---

### `--select-llm` — Interactive provider picker

```bash
python run_agent.py \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml \
  --select-llm
```

Displays a numbered menu of all detected providers:

```
Available LLM providers:
  1. anthropic  (claude-3-7-sonnet)     [ANTHROPIC_API_KEY detected]
  2. subprocess (claude)                [claude binary on PATH]
  3. subprocess (codex)                 [codex binary on PATH]
  4. ollama     (llama3.2)              [OLLAMA_MODEL=llama3.2]

Select provider [1-4]: _
```

> **Note:** `--select-llm` requires an interactive TTY (a real terminal). It cannot be used in
> pipes, CI, or agent mode.

---

### `--force` — Re-run from scratch

```bash
python run_agent.py \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml \
  --mode full \
  --force
```

Clears all checkpoints for the run and restarts from Stage 1. Use when:
- Source files have changed since the last run
- Prompt files have been updated
- A previous run was interrupted mid-conversion

---

### `--no-llm` — Template scaffold (no API key)

```bash
python run_agent.py \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml \
  --mode full \
  --no-llm
```

Returns Jinja2 scaffolds instead of LLM-generated code. Useful for:
- Demos with no API key
- CI pipelines that test the pipeline structure without LLM costs
- Generating stub files that you then fill in manually

---

## Working with the Setup Wizard

If you need to configure a new source/target pair, use the interactive wizard:

```bash
python run_agent.py --setup
```

The wizard will ask:

```
Welcome to AI Migration Tool Setup Wizard.

Source codebase name: Reactivites
Source root path: /Users/me/projects/Reactivites

Target stack ID [hrsa_simpler_pprs_repo]: hrsa_simpler_pprs_repo
Target name: hrsa-simpler-pprs
Target root path: /Users/me/projects/HRSA-Simpler-PPRS

Framework pair: React -> Next.js
Backend pair: ASP.NET Core -> Flask

Generating prompt files...
  ✓ prompts/hrsa_simpler_pprs_repo/plan_system.txt
  ✓ prompts/hrsa_simpler_pprs_repo/conversion_system.txt
  ✓ prompts/hrsa_simpler_pprs_repo/conversion_target_stack.txt
  ✓ agent-prompts/_template_hrsa_simpler_pprs_repo.yaml

Setup complete. Target registered as: hrsa_simpler_pprs_repo
```

To see what targets are already configured:

```bash
python run_agent.py --setup --list-targets
```

---

## Status Checking

```bash
# Human-readable status
python run_agent.py --status \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml

# Machine-readable JSON
python run_agent.py --status \
  --job agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml \
  --json
```

Human-readable output example:

```
Run ID:         conv-actionhistory-hrsa-a1b2c3d4
Feature:        ActionHistory
Target:         hrsa_simpler_pprs_repo
Mode:           full

Plan:
  Status:       generated
  File:         plans/actionhistory-plan-20260305-120000-conv-act.md
  Approved:     true

Conversion:
  Status:       completed
  Steps:        3/3 completed

Validation:
  Status:       passed
  Passed:       3/3
  Failed:       0/3
```

---

## Listing Available Jobs

```bash
python run_agent.py --list-jobs
```

Output:
```
Available job files in agent-prompts/:
  migrate-actionhistory-hrsa_pprs.yaml
  migrate-commands-hrsa_simpler_pprs_repo.yaml
  migrate-commands-hrsa_simpler_pprs_repo_codex.yaml
  migrate-commands-hrsa_simpler_pprs_repo_gemini.yaml
```

---

## File Reference — What Gets Generated Where

After a full run you will find:

```
plans/
  actionhistory-plan-20260305-120000-conv-act.md   ← Review this before approving

output/
  ActionHistory/
    api/
      src/
        api/
          editactivity.cs/
            edit_activity_routes.py               ← Converted Flask route
          createactivity.cs/
            create_activity_routes.py
          deleteactivity.cs/
            delete_activity_routes.py

logs/
  conv-actionhistory-hrsa-a1b2c3d4-conversion-log.json    ← Structured audit
  conv-actionhistory-hrsa-a1b2c3d4-conversion-log.md      ← Human-readable
  conv-actionhistory-hrsa-a1b2c3d4-validation-report.json ← Validation JSON
  conv-actionhistory-hrsa-a1b2c3d4-validation-report.md   ← Validation readable
  conv-actionhistory-hrsa-a1b2c3d4-dependency-graph.json  ← Source analysis

checkpoints/
  conv-actionhistory-hrsa-a1b2c3d4.json           ← Resume state
```

> **Important:** Never manually edit files in `plans/`, `logs/`, `output/`, `checkpoints/`,
> or `reports/`. These are pipeline-generated artefacts.

---

## Troubleshooting Common Issues

### "No LLM provider detected"

```
LLMConfigurationError: No provider detected.
Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or ensure a supported CLI (claude/codex) is on PATH.
```

**Fix:** Export at least one provider environment variable, or add `--no-llm` for template mode.

---

### "Plan already approved — use --force to re-run"

```
PipelineError: Conversion already completed for this run.
```

**Fix:**
```bash
python run_agent.py --job <file>.yaml --mode full --force
```

---

### "ApprovalGate waiting for input"

The pipeline is paused at Stage 4 waiting for you to type `yes`.

```
Approve this plan and proceed with full conversion? [yes/no]: _
```

Type `yes` and press Enter, **or** in a separate terminal:
```bash
python run_agent.py --approve-plan --job <file>.yaml
```

---

### Plan generates but looks wrong

1. Read it carefully: `cat plans/*.md`
2. Note specific issues
3. Run `--revise-plan` with precise feedback
4. Re-read the revised plan before approving

---

### Validation fails but code looks correct

The validator is LLM-based and occasionally produces false positives. If you are confident the
code is correct:
1. Read the specific failure reason in `logs/<run-id>-validation-report.md`
2. If the reason is genuinely wrong, run `--revise-plan` with a corrective note pointing to the
   specific rule the validator mis-applied
3. Check that the validator itself is using the correct CLI — see `LLM Providers` guide

---

### Conversion resumes unexpectedly (wrong output)

If a previous partial run left checkpoints, a new run might skip steps:

```bash
# Reset cleanly
python run_agent.py --job <file>.yaml --mode full --force
```

---

## Quick Reference Card

```
python run_agent.py --list-jobs                    List all job files
python run_agent.py --list-features --json         Discover source features
python run_agent.py --new-job                      Create job file (interactive)
python run_agent.py --job <file>                   Generate plan
python run_agent.py --status --job <file>          Check run status
python run_agent.py --revise-plan --job <file> \   Revise plan with feedback
  --feedback "..."
python run_agent.py --approve-plan --job <file>    Approve plan
python run_agent.py --job <file> --mode full       Run full conversion
python run_agent.py --job <file> --mode full \     Re-run from scratch
  --force
python run_agent.py --job <file> --dry-run         Preview without writes
python run_agent.py --job <file> --verbose         Debug output
python run_agent.py --job <file> --select-llm      Pick LLM interactively
python run_agent.py --job <file> --no-llm          Template-only mode
python run_agent.py --setup                        Setup wizard
python run_agent.py --setup --list-targets         List configured targets
```

---

*AI Migration Tool · Terminal Run User Guide · 2026-03-05*

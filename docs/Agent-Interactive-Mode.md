# Agent Interactive Mode

The AI Migration Tool includes a complete non-interactive command set so that AI coding
agents (Cursor, Windsurf, GitHub Copilot, AntiGravity, and others) can drive the full
migration lifecycle autonomously — no terminal prompts, no manual YAML editing.

---

## The 5-Step Autonomous Workflow

```
Step 1  --list-features        Discover what features exist in the source
Step 2  --new-job              Create a job file without any prompts
Step 3  (run job)              Generate the migration plan
Step 4  --status               Verify plan was generated correctly
Step 5a --approve-plan         Approve the plan (writes .approved marker)
        (run job --mode full)  Execute the full conversion
Step 5b --revise-plan          Re-generate the plan with LLM feedback
        --approve-plan         Re-approve the revised plan
        (run job --mode full)  Execute conversion
```

All commands support `--json` for machine-readable output.

---

## Command Reference

### `--list-features`

Scan the source codebase and list detected feature folders.

```bash
python run_agent.py --list-features
python run_agent.py --list-features --source <YOUR_SOURCE_ROOT>
python run_agent.py --list-features --json
```

**`--source`** — Path to the source codebase root. If omitted, resolved from the
wizard registry (`config/wizard-registry.json` first registered target).

**Text output:**
```
Source root: <YOUR_SOURCE_ROOT>
Found 12 feature folders:

  1  ActionHistory       <YOUR_SOURCE_ROOT>/src/.../ActionHistory
  2  GrantManagement     <YOUR_SOURCE_ROOT>/src/.../GrantManagement
  ...
```

**JSON output (`--json`):**
```json
{
  "source_root": "<YOUR_SOURCE_ROOT>",
  "count": 12,
  "features": [
    {
      "name": "ActionHistory",
      "path": "<YOUR_SOURCE_ROOT>/src/.../ActionHistory",
      "relative": "src/.../ActionHistory"
    },
    ...
  ]
}
```

---

### `--new-job`

Create a job YAML file without any human prompts.

```bash
python run_agent.py --new-job \
  --feature ActionHistory \
  --target snake_case \
  --non-interactive \
  --json
```

**Required with `--non-interactive`:**
- `--feature NAME_OR_PATH` — Feature to migrate. Resolved as:
  1. Absolute path (used directly)
  2. Path relative to the configured source root
  3. Case-insensitive folder name match from `--list-features` output
- `--target ID` — Target stack identifier (e.g. `snake_case`, `hrsa_pprs`)

**Optional:**
- `--source PATH` — Override the source root for feature resolution
- If a job file already exists, it is silently overwritten in non-interactive mode

**JSON output:**
```json
{
  "status": "created",
  "job_file": "agent-prompts/migrate-actionhistory-snake_case.yaml",
  "feature": "ActionHistory",
  "feature_path": "<YOUR_SOURCE_ROOT>/src/.../ActionHistory",
  "target": "snake_case",
  "template": "agent-prompts/_template_snake_case.yaml"
}
```

**Text output** also shows the 4-step next-steps guide.

---

### `--status`

Report the current migration status for a job file.

```bash
python run_agent.py --status --job agent-prompts/migrate-actionhistory-snake_case.yaml
python run_agent.py --status --job agent-prompts/migrate-actionhistory-snake_case.yaml --json
```

**JSON output:**
```json
{
  "job_file": "agent-prompts/migrate-actionhistory-snake_case.yaml",
  "feature": "ActionHistory",
  "feature_root": "<YOUR_SOURCE_ROOT>/src/.../ActionHistory",
  "target": "snake_case",
  "run_id": "conv-20260226-abc12345",
  "plan_generated": true,
  "plan_files": ["plans/actionhistory-plan-20260226-abc12345.md"],
  "plan_approved": false,
  "approval_info": null,
  "conversion_started": false,
  "completed_steps": [],
  "pending_steps": [],
  "blocked_steps": [],
  "checkpoint_path": null
}
```

**Key fields:**
- `plan_generated` — at least one plan file exists for this run ID
- `plan_approved` — `output/<feature>/.approved` exists
- `plan_files` — list of all plan files (including `-rev.md` revisions)
- `completed_steps` / `pending_steps` / `blocked_steps` — conversion progress
- `checkpoint_path` — path to checkpoint file if conversion started

**Text output** also prints a **Next step** recommendation based on current status.

---

### `--approve-plan`

Write the `.approved` marker so the pipeline can run without a TTY prompt.

```bash
python run_agent.py --approve-plan \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml
```

**What it does:**
1. Creates `output/<feature>/` if it doesn't exist
2. Writes `output/<feature>/.approved`:
   ```json
   {
     "approved_at": "2026-02-26T10:30:00+00:00",
     "approved_by": "agent",
     "job_file": "agent-prompts/migrate-actionhistory-snake_case.yaml",
     "feature": "ActionHistory",
     "target": "snake_case",
     "notes": ""
   }
   ```
3. The next `run_agent.py --job FILE --mode full` detects this marker and
   auto-approves at Stage 4 without prompting.

**JSON output:**
```json
{
  "status": "approved",
  "marker_file": "output/ActionHistory/.approved",
  "feature": "ActionHistory",
  "target": "snake_case",
  "approved_at": "2026-02-26T10:30:00+00:00"
}
```

> **Important:** `--revise-plan` automatically removes the `.approved` marker so that
> a revised plan always requires explicit re-approval before conversion.

---

### `--revise-plan`

Re-generate the migration plan with LLM feedback injected.

```bash
python run_agent.py --revise-plan \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml \
  --feedback "Flag all pfm-auth imports as BLOCKED. Add a dedicated DB migration section."
```

**Required:**
- `--feedback TEXT` — Non-empty feedback string. Injected into the LLM prompt as:
  ```
  REVISION FEEDBACK (address ALL points before finalising):
  <your feedback>

  ORIGINAL PLAN (revise this — do NOT copy unchanged sections verbatim):
  <original plan markdown>
  ```

**What it does:**
1. Loads dependency graph from `logs/<run-id>-dependency-graph.json`
   (re-runs ScopingAgent only if the cache is absent)
2. Reads the most-recently-written plan file for this run ID
3. Calls `PlanAgent` with `revision_notes` + `original_plan`
4. Saves `plans/<feature>-plan-<ts>-<run_id[:8]>-rev.md`
5. Removes `output/<feature>/.approved` if it exists

**JSON output:**
```json
{
  "status": "revised",
  "plan_file": "plans/actionhistory-plan-20260226-abc12345-rev.md",
  "run_id": "conv-20260226-abc12345",
  "feedback_applied": "Flag all pfm-auth imports as BLOCKED...",
  "approval_cleared": true
}
```

---

### `--mode MODE`

Override `pipeline.mode` in the job file at the CLI — no YAML editing required.

```bash
python run_agent.py --job FILE --mode full
python run_agent.py --job FILE --mode scope
python run_agent.py --job FILE --mode plan
```

Useful for agents that want to run different modes against the same job file.

---

### `--json`

Append to any command to receive machine-readable output. All agent-interactive
commands (`--list-features`, `--status`, `--approve-plan`, `--revise-plan`,
`--new-job`) support this flag.

```bash
python run_agent.py --status --job FILE --json | python -m json.tool
```

---

## Full Autonomous Example

```bash
# 1. Discover features
python run_agent.py --list-features --json

# 2. Create job file (no prompts)
python run_agent.py --new-job \
  --feature ActionHistory \
  --target snake_case \
  --non-interactive --json
# → agent-prompts/migrate-actionhistory-snake_case.yaml

# 3. Generate plan
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml

# 4. Check status — confirm plan was generated
python run_agent.py --status \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml --json
# → { "plan_generated": true, "plan_approved": false, ... }

# 5. Review the plan (agent reads the Markdown file)
# cat plans/actionhistory-plan-<ts>-<id>.md

# 6a. Plan looks good — approve and convert
python run_agent.py --approve-plan \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml
python run_agent.py \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml \
  --mode full

# 6b. Plan needs changes — revise, re-approve, re-convert
python run_agent.py --revise-plan \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml \
  --feedback "The pfm-auth service has no Flask equivalent. Mark it BLOCKED.
              Add an explicit DB migration section for the action_history table."

python run_agent.py --approve-plan \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml

python run_agent.py \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml \
  --mode full
```

---

## IDE-Specific Rule Files

Each IDE has a rule file that documents the agent workflow and is auto-loaded:

| File | IDE |
|---|---|
| `.cursor/rules.mdc` | Cursor (Cascade / Composer) |
| `.github/copilot-instructions.md` | GitHub Copilot Chat |
| `.windsurfrules` | Windsurf (Cascade) |

These files are the canonical reference for agents operating in those environments.
They document the same commands described above in IDE-specific format.

---

## Environment Variable

`run_agent.py` automatically sets `AI_AGENT_MODE=1` before calling the pipeline.
This switches LLM failures from **hard-fail** to **soft-fail** (returns Jinja2 scaffold
instead of raising an exception), allowing agents to continue with partial output.

You can also set it manually in any shell:
```bash
set AI_AGENT_MODE=1          # Windows CMD
$env:AI_AGENT_MODE = "1"     # PowerShell
export AI_AGENT_MODE=1       # bash / zsh
```

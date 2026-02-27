# Pipeline Stages

The pipeline has five sequential stages plus an optional plan revision step.
All stages are orchestrated by `main.py` and controlled by the `pipeline.mode` setting.

| Stage | Mode | Description |
|---|---|---|
| 1. Config Ingestion | `scope` / `plan` / `full` | Always runs |
| 2. Scoping & Analysis | `scope` / `plan` / `full` | Analyses source feature folder |
| 3. Plan Generation | `plan` / `full` | LLM generates Markdown plan |
| 3b. Plan Revision | *(on demand)* | Re-generates plan with feedback |
| 4. Approval | `full` | Human or agent approves the plan |
| 5. Conversion | `full` | LLM converts each file |

---

## Stage 1 — Config Ingestion

**Agent:** `ConfigIngestionAgent`

**Inputs:**
- `config/skillset-config.json` — stack definitions, component mappings
- `config/rules-config.json` — guardrail rules
- Job file fields (`pipeline.*`, `llm.*`)
- CLI overrides (`--mode`, `--target`, `--no-llm`, etc.)

**What it does:**
1. Loads and JSON-schema-validates both config files
2. Selects the right `target_stack_<target>` and `project_structure_<target>` blocks
3. Resolves LLM provider: explicit value in job file → env var auto-detect chain
4. Builds the `AgentContext` shared by all downstream stages

**Failure modes:**
- Missing required field → `ConfigValidationError` (hard fail)
- Unknown `target` value → `ConfigValidationError`
- Invalid JSON → `json.JSONDecodeError`

---

## Stage 2 — Scoping & Analysis

**Agent:** `ScopingAgent`

**Inputs:**
- `pipeline.feature_root` — absolute path to the legacy feature folder
- Component mapping definitions from `AgentContext`

**What it does:**
1. Recursively walks `feature_root`
2. For each file, determines its type using AST parsing (Python/TS) and regex
3. Maps each file to a component mapping ID (MAP-001 to MAP-006)
4. Extracts: imports, exports, class names, method signatures, Angular decorators, C# attributes
5. Writes `logs/<run-id>-dependency-graph.json`

**Output structure (`dependency-graph.json`):**
```json
{
  "run_id": "conv-20260226-...",
  "feature_name": "ActionHistory",
  "feature_root": "<YOUR_SOURCE_ROOT>/src/...",
  "files": [
    {
      "path": "relative/path.ts",
      "type": "angular_component",
      "mapping_id": "MAP-001",
      "imports": [...],
      "exports": [...],
      "classes": [...]
    }
  ]
}
```

**Caching:** The dependency graph is cached on disk. `--revise-plan` loads it directly
without re-running this stage, saving significant time on large feature folders.

**Failure modes:**
- `feature_root` doesn't exist → `ScopingError`
- No files found → warning (empty feature — pipeline continues)

---

## Stage 3 — Plan Generation

**Agent:** `PlanAgent`

**Inputs:**
- `logs/<run-id>-dependency-graph.json` from Stage 2
- System prompt: `prompts/plan_system[_<target>].txt`
- Target stack reference: `prompts/conversion_target_stack[_<target>].txt`
- (Revision mode) `revision_notes` string + `original_plan` text

**What it does:**
1. Builds the LLM user message from the dependency graph summary
2. (Revision mode) Appends `REVISION FEEDBACK` and `ORIGINAL PLAN` sections
3. Calls the LLM with the system + user messages
4. Validates the response is valid Markdown with the required plan sections
5. Saves the plan as `plans/<feature>-plan-<ts>-<run_id[:8]>.md`
   (revision: `...-rev.md`)

**Plan document schema** (enforced by the system prompt):
- `## Overview` — feature description, migration scope
- `## Component Mappings` — file-by-file source → target mapping table
- `## Risk Areas` — AMBIGUOUS / BLOCKED items
- `## Output File Listing` — expected output files
- `## Migration Notes` — implementation guidance

**Dedup check:** If a plan file already exists for this run ID, generation is skipped
(unless `--force` or in revision mode).

**LLM soft-fail (agent mode):** If the LLM call fails and `AI_AGENT_MODE=1`, the
agent returns a Jinja2-filled plan template instead of raising an error.

---

## Stage 3b — Plan Revision

**Triggered by:** `python run_agent.py --revise-plan --job FILE --feedback "..."`

**What it does:**
1. Loads existing dependency graph from `logs/<run-id>-dependency-graph.json`
   (re-runs ScopingAgent only if the cache file is absent)
2. Reads the most recently written plan file for this run ID
3. Instantiates `PlanAgent` with `revision_notes=<feedback>` and `original_plan=<text>`
4. Generates a new plan with feedback injected into the LLM prompt:
   ```
   REVISION FEEDBACK (address ALL points before finalising):
   <feedback text>

   ORIGINAL PLAN (revise this — do NOT copy unchanged sections verbatim):
   <original plan markdown>
   ```
5. Saves as `plans/<feature>-plan-<ts>-<run_id[:8]>-rev.md`
6. **Removes** `output/<feature>/.approved` if present — revised plan needs re-approval

**Output (`--json`):**
```json
{
  "status": "revised",
  "plan_file": "plans/actionhistory-plan-...-rev.md",
  "run_id": "...",
  "feedback_applied": "Flag pfm-auth imports as BLOCKED.",
  "approval_cleared": true
}
```

---

## Stage 4 — Approval Gate

**Agent:** `ApprovalGate`

**Two paths:**

### Human approval (interactive)
The agent prints the plan summary and prompts:
```
Plan saved to: plans/actionhistory-plan-20260226-....md

Review the plan, then type 'yes' to proceed with full conversion: _
```

### Agent approval (non-interactive)
If `output/<feature>/.approved` exists (written by `--approve-plan`), the gate
auto-approves and logs:
```
[ApprovalGate] Agent approval marker found — auto-approving.
```

**Approval marker format** (`output/<feature>/.approved`):
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

**Checkpoint:** The gate saves a checkpoint after approval so that if the conversion
is interrupted, `--resume` can skip back to Stage 5 without re-approval.

**`auto_approve: true`** in the job file (or `--auto-approve` CLI flag) bypasses the
gate entirely — intended for testing only.

---

## Stage 5 — Conversion

**Agent:** `ConversionAgent`

**Inputs:**
- Approved plan from Stage 3
- `AgentContext` (config, LLM settings)
- Checkpoint state (if resuming)

**For each file in the plan:**
1. Read source file content
2. Select Jinja2 template from `templates/` based on mapping ID
3. Build LLM prompt: system prompt + target stack reference + guardrail rules + source code
4. Call LLM
5. Detect special responses:
   - `AMBIGUOUS: <reason>` → log as AMBIGUOUS, skip writing, continue
   - `BLOCKED: <reason>` → log as BLOCKED, skip writing, continue
6. Write output file(s) to `output/<feature>/`
7. Append step to `ConversionLog`
8. Update checkpoint

**Outputs:**
- `output/<feature>/` — converted source files
- `logs/<run-id>-conversion-log.json` — machine-readable step log
- `logs/<run-id>-conversion-log.md` — human-readable audit log
- `checkpoints/<run-id>.json` — resume state

**Dry run:** When `dry_run: true`, all LLM calls and file writes are skipped.
The log records what _would_ have been written.

**Resume:** If conversion was interrupted, re-running with `--resume` (or the
same run ID) picks up from the last completed checkpoint step.

---

## Deduplication

On successful completion, the run ID is recorded in `logs/completed-runs.json`.
Subsequent runs for the same `(feature_name, feature_root, target)` find their
stable run ID in this registry and exit immediately:

```
[Pipeline] Already complete for run conv-20260226-abc12345. Use --force to re-run.
```

Pass `--force` to bypass and re-run from scratch.

---

## Mode Reference

| `pipeline.mode` | Stages executed |
|---|---|
| `scope` | 1 → 2 |
| `plan` | 1 → 2 → 3 |
| `full` | 1 → 2 → 3 → 4 → 5 |

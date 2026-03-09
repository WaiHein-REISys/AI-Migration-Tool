# Pipeline Stages

The pipeline has nine sequential stages plus an optional plan revision step.
All stages are orchestrated by `main.py` and controlled by the `pipeline.mode` setting.

| Stage | Mode | Description |
|---|---|---|
| 1. Config Ingestion | `scope` / `plan` / `full` | Always runs |
| 2. Scoping & Analysis | `scope` / `plan` / `full` | Analyses source feature folder |
| 3. Plan Generation | `plan` / `full` | LLM generates Markdown plan |
| 3b. Plan Revision | *(on demand)* | Re-generates plan with feedback |
| 4. Approval | `full` | Human or agent approves the plan |
| 5. Conversion | `full` | LLM converts each file |
| 6. Validation Simulation | `full` | File checks + LLM-based old-vs-new behavior simulation before success |
| 6b. UI Consistency Audit | `full` | Static diff of Angular source vs converted React/TSX — CSS classes, elements, events, directives |
| 7. Integration & Placement | `full` | Places converted files into `target_root`, syncs deps, verifies structure, generates migration scripts |
| 8. End-to-End Verification | `full` | Runs configured shell commands (build/test/lint/e2e) against converted/integrated code |

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

## Stage 6 — Validation Simulation

**Agent:** `ValidationAgent`

**Inputs:**
- Approved conversion plan (step definitions)
- Converted output files in `output/<feature>/`
- Source files in legacy feature root
- Optional LLM router for behavior simulation

**What it does:**
1. Verifies each converted output file exists and is non-empty
2. Runs an LLM simulation to compare OLD source intent vs NEW source behavior
3. Writes validation artefacts:
   - `logs/<run-id>-validation-report.json`
   - `logs/<run-id>-validation-report.md`
4. Blocks final success if any validation item fails

**Dry run behavior:** Validation is skipped in `dry_run: true` mode.

---

## Stage 6b — UI Consistency Audit

**Agent:** `UIConsistencyAgent`

**Inputs:**
- Approved conversion plan (step definitions, source file paths)
- Converted output `.tsx` / `.jsx` files in `output/<feature>/`
- Source Angular component files (`.component.html`, `.component.ts`)
- `ui_consistency_config` — `{enabled, generate_stories, fail_on_missing_classes}`
- Optional LLM router for diff classification

**Guard checks (in order):**
1. `dry_run: true` → returns `status: skipped_dry_run`
2. `ui_consistency.enabled: false` → returns `status: skipped_disabled`
3. No UI-type steps found in the plan → returns `status: skipped_no_ui_files`

**What it does:**

For each UI step in the approved plan:
1. Extracts CSS classes, HTML elements, event bindings, and structural directives from the Angular source using regex
2. Extracts the equivalent items from the converted React/TSX output
3. Computes a diff: missing classes, missing elements, missing event handlers, directive coverage
4. Derives a per-step status: `PASS`, `WARN` (missing handlers only), or `FAIL` (missing classes or elements)
5. Optionally calls the LLM with role `UI_CONSISTENCY` to classify each diff item as:
   - `expected_idiom_change` — normal Angular→React translation (e.g. `ngClass` → `className`)
   - `expected_structural_change` — intentional restructuring
   - `potential_omission` — possible missing translation (reported as a finding)
   LLM classification can downgrade a `FAIL` to `WARN` if all diffs are `expected_idiom_change`
6. Optionally writes a Storybook `.stories.tsx` stub next to each converted component when `generate_stories: true`
7. Writes `logs/<run-id>-ui-consistency-report.json` and `.md`

**Angular extraction:**
- `.component.html` → CSS classes from `class="..."`, elements by tag name, Angular event bindings `(click)`, `(change)`, etc., `*ngIf` / `*ngFor` / `[(ngModel)]` counts
- `.component.ts` (fallback) → inline template extraction + `@Input()` / `@Output()` declarations

**React extraction:**
- `className="..."` / `className={...}` → CSS classes
- JSX element names → semantic elements
- `onClick`, `onChange`, `onSubmit`, etc. → event handlers
- Conditional renders (`&&` / `? :`) → `*ngIf` equivalents; `.map(...)` → `*ngFor` equivalents

**Angular→React event mapping:** `(click)→onClick`, `(change)→onChange`, `(submit)→onSubmit`, `(blur)→onBlur`, `(focus)→onFocus`, `(input)→onInput`, `(keyup)→onKeyUp`, `(keydown)→onKeyDown`, `(keypress)→onKeyPress`, `(mouseenter)→onMouseEnter`, `(mouseleave)→onMouseLeave`, `(scroll)→onScroll`, `(dblclick)→onDoubleClick`, `(contextmenu)→onContextMenu`, `(wheel)→onWheel`

**Status values:**

| Status | Meaning |
|---|---|
| `passed` | All UI steps have zero missing classes, elements, or events |
| `warned` | One or more steps have missing event handlers but no missing classes/elements |
| `failed` | One or more steps have missing CSS classes or HTML elements — pipeline returns exit code 1 |
| `skipped_disabled` | `ui_consistency.enabled: false` |
| `skipped_no_ui_files` | No UI-type steps found in the plan |
| `skipped_dry_run` | `dry_run: true` |

**Outputs:**
- `logs/<run-id>-ui-consistency-report.json` — machine-readable findings per step
- `logs/<run-id>-ui-consistency-report.md` — human-readable audit table
- `output/<feature>/<ComponentName>.stories.tsx` — Storybook stub(s) (if `generate_stories: true`)

**LLM fallback (no router):** Classification is skipped; raw diff results are reported directly. Status is derived purely from the static diff counts.

**Prompt file:** `prompts/ui_consistency_system.txt` (no target-specific variant needed — the check is framework-agnostic at the diff level)

---

## Stage 7 — Integration & Placement

**Agent:** `IntegrationAgent`

**Inputs:**
- Converted output files in `output/<feature>/` (via `logs/<run-id>-conversion-log.json`)
- `pipeline.target_root` — path to the target codebase (from job YAML or wizard registry)
- `integration_config` — `{enabled, add_dependencies, generate_migration}`
- Validation findings from Stage 6

**Guard checks (in order):**
1. `target_root` is `null` or does not exist on disk → returns `status: skipped_no_target`, logs a warning, pipeline exits successfully
2. `integration.enabled: false` → returns `status: skipped`
3. `dry_run: true` → logs all planned actions but writes nothing, returns `status: skipped_dry_run`

**What it does:**
1. Reads `logs/<run-id>-conversion-log.json` to collect all `wrote_file` entries
2. Classifies each output file as `ui`, `backend`, `test`, or `config`
3. Resolves the destination path inside `target_root` using `project_structure` config templates
4. Places each file with `shutil.copy2` — conflicts (existing file with different content) are **skipped with a warning** and flagged for human review; identical-content files are silently skipped (idempotent re-runs)
5. Syncs Python dependencies: parses top-level `import` statements, diffs against `target_root/requirements.txt`, appends missing third-party packages with `# added by ai-migration-tool` comment
6. Reports JavaScript dependencies: parses `import … from 'pkg'` in `.ts/.tsx` files, diffs against `target_root/package.json` — reports packages needing `npm install` but **does not auto-write** to avoid unintended installs
7. Runs LLM structural checks per file:
   - `ROLE: UI_INTEGRITY` — component structure, USWDS class names, event handlers, prop types, business logic parity
   - `ROLE: BACKEND_STRUCTURE` — route signatures, model field alignment, naming conventions, `needs_migration` flag
8. Generates a DB migration script if any backend check sets `needs_migration: true` (Alembic `.py` for SQLAlchemy targets; raw SQL for psycopg2/HRSA targets)
9. Writes `logs/<run-id>-integration-report.json` and `.md`

**Placement conflict policy:** Skip + warn. The existing file in `target_root` is never overwritten automatically. A `"conflict"` entry in `placements[]` signals the file needs human merge.

**Status values:**

| Status | Meaning |
|---|---|
| `integrated` | All files placed, no FAIL-level structural findings |
| `partial` | One or more files had conflicts or FAIL findings — pipeline returns exit code 1 |
| `skipped` | `integration.enabled: false` |
| `skipped_no_target` | `target_root` not set or does not exist — pipeline still succeeds |
| `skipped_dry_run` | `dry_run: true` — actions logged, nothing written |

**Outputs:**
- `target_root/<placed files>` — converted files placed into the target codebase
- `target_root/requirements.txt` — appended with missing Python deps (if `add_dependencies: true`)
- `logs/<run-id>-integration-report.json` — machine-readable placement + verification report
- `logs/<run-id>-integration-report.md` — human-readable summary
- `logs/<run-id>-migration-<step_id>.sql` or `.py` — generated migration script(s)

**LLM fallback (no router):** Structural checks return `PASS` with `confidence: 0.4` rather than failing.

---

## Stage 8 — End-to-End Verification

**Agent:** `E2EVerificationAgent`

**Inputs:**
- `verification.enabled`
- `verification.cwd` (optional)
- `verification.commands[]`
- `verification.env` (optional)
- `verification.fail_on_error`
- `target_root` / `output_root` (for default working directory selection)

**Guard checks (in order):**
1. `verification.enabled: false` → returns `status: skipped_disabled`
2. `dry_run: true` → returns `status: skipped_dry_run`
3. `verification.commands` empty → returns `status: skipped_no_commands`
4. resolved working directory missing → returns `status: skipped_missing_cwd`

**What it does:**
1. Resolves working directory:
   - `verification.cwd` if set
   - else `target_root` when available
   - else `output_root`
2. Executes each configured command in order using that working directory
3. Captures `exit_code`, duration, stdout/stderr snippets per command
4. Writes reports:
   - `logs/<run-id>-e2e-verification-report.json`
   - `logs/<run-id>-e2e-verification-report.md`
5. Fails pipeline when a command fails and `fail_on_error: true`

**Status values:**

| Status | Meaning |
|---|---|
| `passed` | All commands succeeded |
| `failed` | At least one command failed and `fail_on_error: true` |
| `completed_with_failures` | One or more commands failed but `fail_on_error: false` |
| `skipped_disabled` | `verification.enabled: false` |
| `skipped_no_commands` | No commands configured |
| `skipped_missing_cwd` | Verification working directory does not exist |
| `skipped_dry_run` | Dry run mode |

---

## Mode Reference

| `pipeline.mode` | Stages executed |
|---|---|
| `scope` | 1 → 2 |
| `plan` | 1 → 2 → 3 |
| `full` | 1 → 2 → 3 → 4 → 5 → 6 → 6b → 7 → 8 |

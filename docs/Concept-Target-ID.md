# Concept Guide — Target ID (`target_id`)

**AI Migration Tool · 2026-03-09**

---

## What Is a Target ID?

A **Target ID** (`target_id`) is the short, unique string key that identifies a
registered migration target in the AI Migration Tool. It is the single value
that connects a job file to everything the pipeline needs to know about a
destination technology stack:

- Which **prompt files** to load (plan, conversion, target-stack context)
- Where the **target codebase** lives on disk (`target_root`)
- Which **YAML job template** to copy when creating a new job
- How to **name** generated job files and artefacts

Think of the target ID as the "stack profile name" — register it once via the
setup wizard, then reference it in every job that migrates to that stack.

---

## Where Target IDs Live

All registered target IDs are stored in `config/wizard-registry.json`:

```json
{
  "targets": {
    "hrsa_simpler_pprs_repo": {
      "source_name": "reactivites",
      "source_root": "/path/to/source",
      "target_name": "hrsa-simpler-pprs",
      "target_root": "/path/to/target",
      "framework_pair": "React -> Next.js",
      "backend_pair": "ASP.NET Core -> Flask",
      "created_at": "2026-03-03T05:49:55Z",
      "prompt_files": {
        "plan_system":       "hrsa_simpler_pprs_repo/plan_system.txt",
        "conversion_system": "hrsa_simpler_pprs_repo/conversion_system.txt",
        "target_stack":      "hrsa_simpler_pprs_repo/conversion_target_stack.txt"
      },
      "job_template": "_template_hrsa_simpler_pprs_repo.yaml"
    },
    "modern": { ... },
    "snake_case": { ... }
  }
}
```

> **Note:** `config/wizard-registry.json` is machine-specific and is excluded
> from version control (`.gitignore`). Each developer registers targets on
> their own machine via `python run_agent.py --setup`.

---

## Currently Registered Target IDs

List your registered targets at any time:

```bash
python run_agent.py --list-targets --json
```

Or inspect the registry directly:

```bash
python3 -c "
import json
r = json.load(open('config/wizard-registry.json'))
for tid, v in r['targets'].items():
    print(f'{tid:35s} → {v[\"framework_pair\"]} | {v[\"backend_pair\"]}')
"
```

Example output:

```
modern                              → Angular 2 -> Next.js | ASP.NET Core MVC -> Python Flask
snake_case                          → Angular 2 -> Next.js | ASP.NET Core MVC -> Python Flask
hrsa_simpler_pprs_repo              → React -> Next.js | ASP.NET Core -> Flask
```

---

## How the Target ID Flows Through the Pipeline

```
pipeline.target: "hrsa_simpler_pprs_repo"     ← set once in your job YAML
        │
        ▼
config/wizard-registry.json  ["targets"]["hrsa_simpler_pprs_repo"]
        │
        ├── target_root ──────────────────────→ auto-populated into ns.target_root
        │                                        (used by Stage 7 Integration +
        │                                         Stage 8 Verification)
        │
        ├── prompt_files
        │     ├── plan_system.txt ────────────→ PlanAgent system prompt
        │     ├── conversion_system.txt ──────→ ConversionAgent system prompt
        │     └── conversion_target_stack.txt → target-stack context injected
        │                                        into every conversion step
        │
        ├── job_template ────────────────────→ _template_<target_id>.yaml
        │                                       (base for --new-job)
        │
        └── framework_pair / backend_pair ──→ logged in plan header + reports
```

---

## Setting the Target ID

### In a YAML job file

```yaml
pipeline:
  feature_name: "ActionHistory"
  feature_root: "/path/to/source/ActionHistory"
  target: "hrsa_simpler_pprs_repo"    # ← target ID here
  mode: "plan"
```

### With the `--new-job` command

```bash
python run_agent.py \
  --new-job \
  --feature ActionHistory \
  --target hrsa_simpler_pprs_repo \   # ← target ID here
  --non-interactive \
  --json
```

The command validates that the target ID exists in the registry before creating
the job file.

---

## What the Target ID Drives

### 1. Prompt file selection

The pipeline resolves three prompt files using the target ID as a folder name:

| File | Path |
|---|---|
| Plan system prompt | `prompts/<target_id>/plan_system.txt` |
| Conversion system prompt | `prompts/<target_id>/conversion_system.txt` |
| Target-stack context | `prompts/<target_id>/conversion_target_stack.txt` |

These files describe the destination technology stack to the LLM and are the
primary way to tune migration output for a specific target. They are safe to
read and edit — no Python changes are required.

### 2. `target_root` auto-population

When `target_root` is `null` or a placeholder in the job YAML,
`agents/job_config_populator.py` looks up the real path from the registry:

```
registry["targets"][<target_id>]["target_root"]  →  ns.target_root
```

This means you only need to set `target_root` once (during `--setup`) — every
subsequent job for that target ID inherits it automatically.

### 3. Verification command auto-detection

Once `target_root` is resolved, the populator inspects the target codebase to
generate `verification.commands` automatically:

| File found in `target_root` | Commands generated |
|---|---|
| `package.json` | `npm ci` (or `yarn`/`pnpm`) + `npm run build` + `npm test` |
| `pyproject.toml` / `requirements.txt` | `pip install` + `pytest` |
| `Makefile` | `make install`, `make build`, `make test` (if `.PHONY` targets exist) |

`verification.enabled` is flipped to `true` automatically when commands are
detected.

### 4. Job file naming convention

Generated job files follow this pattern:

```
agent-prompts/migrate-<feature>-<target_id>.yaml
```

Example:
```
agent-prompts/migrate-actionhistory-hrsa_simpler_pprs_repo.yaml
```

### 5. YAML template selection

When `--new-job` creates a job file, it copies from:

```
agent-prompts/_template_<target_id>.yaml
```

Each template is pre-configured with the correct `target:` value and
target-specific comments.

---

## Registering a New Target ID

### Option A — Setup Wizard (Recommended)

```bash
python run_agent.py --setup
```

The interactive wizard:
1. Prompts for target stack details (name, source/target roots, framework pair)
2. Generates the three prompt files in `prompts/<target_id>/`
3. Writes the registry entry to `config/wizard-registry.json`
4. Generates `agent-prompts/_template_<target_id>.yaml`

After running `--setup`, the new target ID is immediately available for use
in `--new-job` and job YAML files.

### Option B — Manual Registration

**Step 1:** Create the three prompt files:

```
prompts/<target_id>/plan_system.txt
prompts/<target_id>/conversion_system.txt
prompts/<target_id>/conversion_target_stack.txt
```

**Step 2:** Add the entry to `config/wizard-registry.json`:

```json
"my_new_target": {
  "source_name": "legacy-app",
  "source_root": "/absolute/path/to/source",
  "target_name": "my-new-stack",
  "target_root": "/absolute/path/to/target",
  "framework_pair": "React -> Vue",
  "backend_pair": "Express -> FastAPI",
  "created_at": "2026-03-09T00:00:00Z",
  "prompt_files": {
    "plan_system":       "my_new_target/plan_system.txt",
    "conversion_system": "my_new_target/conversion_system.txt",
    "target_stack":      "my_new_target/conversion_target_stack.txt"
  },
  "job_template": "_template_my_new_target.yaml"
}
```

**Step 3:** Optionally create `agent-prompts/_template_my_new_target.yaml`
by copying an existing template and updating the `target:` value.

> **No Python changes are required.** `PlanAgent` and `ConversionAgent` resolve
> prompt files by convention — any folder under `prompts/` matching the target
> ID is picked up automatically.

---

## Updating `target_root` in the Registry

If you move your target codebase to a new location, update the registry entry
so auto-population keeps working:

```bash
# Option A — Re-run setup wizard for the target
python run_agent.py --setup

# Option B — Edit the registry directly
#   config/wizard-registry.json → targets → <target_id> → target_root
```

Alternatively, override it explicitly in the job YAML (takes priority over
the registry):

```yaml
pipeline:
  target: "hrsa_simpler_pprs_repo"
  target_root: "/new/absolute/path/to/target"   # explicit override
```

---

## Troubleshooting

### "Target ID not found in registry"

```
[ERROR] --target 'my_target' not found in registry.
```

**Cause:** The target ID was not registered on this machine.
**Fix:** Run `python run_agent.py --setup` and register the target, or add
the entry manually to `config/wizard-registry.json`.

### Stages 7–8 skipped after conversion

```
Stages 7–8 skipped — target_root not set or wizard not run
```

**Cause:** `target_root` is still `null` or a placeholder, so Integration
(Stage 7) and Verification (Stage 8) have no codebase to work with.

**Fix (auto):** Run `python run_agent.py --setup` and register the target with
a real `target_root` path. Auto-population will fill it on the next run.

**Fix (manual):** Set `target_root` explicitly in the job YAML:

```yaml
pipeline:
  target_root: "/absolute/path/to/target/repo"
```

### Prompt files not found

```
FileNotFoundError: prompts/my_target/plan_system.txt
```

**Cause:** The target ID was registered but the prompt files were not created.
**Fix:** Run `python run_agent.py --setup` to regenerate them, or create them
manually in `prompts/<target_id>/`.

---

## Quick Reference

```bash
# List all registered target IDs
python run_agent.py --list-targets --json

# Register a new target ID
python run_agent.py --setup

# Create a job using a specific target ID
python run_agent.py --new-job --feature MyFeature --target my_target_id \
  --non-interactive --json

# Override target_root for a single run (without editing the registry)
python run_agent.py --job agent-prompts/my-job.yaml \
  --target-root /new/path/to/target
```

---

*AI Migration Tool · Concept Guide: Target ID · 2026-03-09*

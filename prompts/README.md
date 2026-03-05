# prompts/

All LLM prompt files for the AI Migration Tool.

Prompts are stored as plain text or Markdown files so they can be edited,
reviewed in pull requests, and version-controlled independently of the
Python source code.

---

## Directory Structure

```
prompts/
├── plan_system.txt                    # Default plan prompt (simpler_grants fallback)
├── conversion_system.txt              # Default conversion prompt (fallback)
├── conversion_target_stack.txt        # Default target stack reference (fallback)
├── plan_document_template.md          # Shared Jinja2 scaffold (no-LLM / template mode)
│
├── modern/                            # Angular 2 / ASP.NET Core → Next.js / Flask
│   ├── plan_system.txt
│   ├── conversion_system.txt
│   └── conversion_target_stack.txt
│
├── snake_case/                        # Angular 2 / ASP.NET Core → Next.js / Flask (snake_case)
│   ├── plan_system.txt
│   ├── conversion_system.txt
│   └── conversion_target_stack.txt
│
├── hrsa_pprs/                         # React / ASP.NET Core → Next.js / Flask (HRSA PPRS)
│   ├── plan_system.txt
│   ├── conversion_system.txt
│   └── conversion_target_stack.txt
│
└── hrsa_simpler_pprs_repo/            # React / ASP.NET Core → Next.js / Flask (HRSA Simpler PPRS)
    ├── plan_system.txt
    ├── conversion_system.txt
    └── conversion_target_stack.txt
```

---

## Core Files (root level — shared defaults)

| File | Used by | Purpose |
|---|---|---|
| `plan_system.txt` | `PlanAgent` | System prompt for plan generation. Instructs the model to produce a structured Markdown Plan Document. Used as fallback when no target-specific file is found. |
| `plan_document_template.md` | `PlanAgent` | Markdown scaffold used in template-only mode (no LLM configured). Contains `{placeholder}` fields filled by the agent. |
| `conversion_system.txt` | `ConversionAgent` | System prompt for code conversion. Contains `{rules_text}` and `{target_stack_summary}` placeholders filled at runtime. |
| `conversion_target_stack.txt` | `ConversionAgent` | Target stack reference block injected into `conversion_system.txt` as `{target_stack_summary}`. |

---

## Target-specific Prompt Variants

Each target has its own subdirectory containing three files:

| File | Purpose |
|---|---|
| `<target_id>/plan_system.txt` | Migration planning instructions tailored to this source → target stack pair |
| `<target_id>/conversion_system.txt` | Code conversion instructions for this target |
| `<target_id>/conversion_target_stack.txt` | Target stack patterns, idioms, and architecture guide injected at runtime |

Resolution order is handled by `prompts.resolve_prompt_filename(...)`:
1. **Registry** — `config/wizard-registry.json` explicit `prompt_files` mapping
2. **Subdirectory convention** — `prompts/<target_id>/<stem>.txt`
3. **Legacy flat convention** — `prompts/<stem>_<target_id>.txt` (backward compat)
4. **Default** — root-level core prompt file

The Setup Wizard (`python run_agent.py --setup`) generates target subdirectories
automatically — no Python code changes needed.

---

## How to edit prompts

1. Open the relevant `.txt` or `.md` file in the appropriate target subfolder.
2. Edit the text directly — no Python changes needed.
3. Placeholders like `{rules_text}`, `{target_stack_summary}`, and `{feature_name}`
   are filled at runtime by the agent; **do not remove them**.
4. The loader (`prompts/__init__.py`) caches files on first read. During development
   call `reload_prompt(filename)` to pick up changes without restarting.

---

## How to add a new target's prompts

### Option A — Use the Setup Wizard (recommended)

```bash
python run_agent.py --setup
```

The wizard generates `prompts/<target_id>/` with all three prompt files automatically.

### Option B — Manual drop-in

1. Create the subdirectory: `prompts/<target_id>/`
2. Add `plan_system.txt`, `conversion_system.txt`, `conversion_target_stack.txt`
3. Register the target in `config/wizard-registry.json` (or rely on subdirectory convention auto-discovery — no registration required)

### Loading a prompt in your agent

```python
from prompts import load_prompt, resolve_prompt_filename

# Load a specific file by path
stack_ref = load_prompt("hrsa_simpler_pprs_repo/conversion_target_stack.txt")

# Resolve the right file for a target dynamically
filename = resolve_prompt_filename("my_target", "plan_system", "plan_system.txt")
system_prompt = load_prompt(filename)
```

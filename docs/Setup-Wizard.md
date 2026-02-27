# Setup Wizard

The **Setup Wizard** configures new migration targets — generating all prompts,
config entries, and job templates the pipeline needs — without any manual code changes.

---

## When to Use the Wizard

Use the wizard when you want to migrate to a stack that isn't already built in
(`simpler_grants`, `hrsa_pprs`, `snake_case`). After the wizard runs, the new target
works exactly like a built-in one.

You **do not** need the wizard for:
- Running existing built-in targets
- Migrating additional features to an already-configured target
- Editing prompts for fine-tuning (edit the `.txt` files directly)

---

## Running the Wizard

### Interactive mode (recommended first time)

```bash
python run_agent.py --setup
# or equivalently:
python setup_wizard.py
```

The wizard walks through six sections of questions:

1. **Source codebase** — path, framework, backend, language, database
   *(auto-detected by scanning the directory; answers shown as defaults)*
2. **Target codebase** — same fields for the destination stack
3. **Frontend structure** — component folder, services folder, styling approach, barrel exports
4. **Backend structure** — routes folder, services folder, repository layer
5. **Database details** — access pattern (ORM vs raw SQL), migration tool
6. **Target identifier** — snake_case key used in all file names (e.g. `my_nextjs_flask`)

### Interactive with pre-filled defaults

Load a JSON config as starting values; the wizard still prompts for confirmation:

```bash
python run_agent.py --setup --config agent-prompts/wizard-myapp.json
```

### Non-interactive / agent mode

Fully scripted — no prompts, no TTY required:

```bash
python run_agent.py --setup \
  --config agent-prompts/wizard-myapp.json \
  --non-interactive
```

### Preview only (dry run)

Shows everything that would be written without touching disk:

```bash
python run_agent.py --setup --dry-run
python run_agent.py --setup --config wizard.json --non-interactive --dry-run
```

### List registered targets

```bash
python run_agent.py --setup --list-targets
```

Example output:
```
Registered targets (config/wizard-registry.json):
  modern      Angular 2 -> Next.js  |  ASP.NET Core MVC -> Python Flask
  snake_case  Angular 2 -> Next.js  |  ASP.NET Core MVC -> Python Flask
```

### Regenerate existing artefacts

```bash
python run_agent.py --setup --overwrite
```

By default, existing files are not overwritten. Use `--overwrite` to regenerate.

---

## What the Wizard Produces

For a new target named `my_nextjs_flask`:

| Artefact | Path | Purpose |
|---|---|---|
| Plan prompt | `prompts/plan_system_my_nextjs_flask.txt` | LLM instructions for plan generation |
| Conversion prompt | `prompts/conversion_system_my_nextjs_flask.txt` | LLM instructions for code conversion |
| Stack reference | `prompts/conversion_target_stack_my_nextjs_flask.txt` | Target stack patterns injected at runtime |
| Job template | `agent-prompts/_template_my_nextjs_flask.yaml` | Ready-to-fill migration job template |
| Skillset entries | `config/skillset-config.json` | `target_stack_*` + `project_structure_*` blocks |
| Registry entry | `config/wizard-registry.json` | Idempotency guard; stores source/target roots |

All artefacts are skipped if they already exist. Use `--overwrite` to regenerate.

---

## Wizard JSON Config Schema

The JSON config (`agent-prompts/example-wizard-config.json` or your own copy) has
this structure:

```json
{
  "_comment": "Example wizard config for non-interactive mode.",
  "_usage": "python setup_wizard.py --config this-file.json --non-interactive",

  "target_id": "my_nextjs_flask",

  "source": {
    "name": "legacy_app",
    "root": "C:/path/to/your/legacy/codebase",
    "framework": "Angular 2",
    "backend_framework": "ASP.NET Core 8",
    "language": "TypeScript / C#",
    "database": "SQL Server + Entity Framework Core"
  },

  "target": {
    "name": "my_nextjs_flask",
    "root": "C:/path/to/your/modern/codebase",
    "framework": "Next.js 15",
    "backend_framework": "Flask 3.0",
    "language": "TypeScript / Python",
    "database": "PostgreSQL + psycopg2",

    "frontend_root": "frontend/src/",
    "backend_root": "backend/",

    "frontend_details": {
      "components_dir": "components/",
      "services_dir": "services/",
      "component_structure": "ComponentName/ with ComponentName.tsx + ComponentName.module.css + index.ts",
      "styling": "CSS Modules (*.module.css)",
      "barrel_export": "index.ts per component folder",
      "test_suffix": ".test.tsx"
    },

    "backend_details": {
      "routes_dir": "routes/",
      "services_dir": "services/",
      "repositories_dir": "repositories/",
      "route_file_pattern": "{feature_name}_routes.py",
      "service_file_pattern": "{feature_name}_service.py",
      "repository_file_pattern": "{feature_name}_repository.py",
      "architecture": "Routes -> Services -> Repositories (3-layer)"
    },

    "database_details": {
      "access_pattern": "psycopg2 raw SQL with RealDictCursor",
      "migration_tool": "custom Python scripts"
    }
  }
}
```

**Required fields:** `target_id`, `source.root`, `target.root`

**Optional fields:** All `frontend_details`, `backend_details`, `database_details`.
If omitted, the wizard uses auto-detected defaults.

See `agent-prompts/wizard-myapp.json` for a concrete filled-in example.

---

## Auto-Detection (`detector.py`)

When `source.root` or `target.root` is provided, `CodebaseInspector` scans the
directory and auto-detects:

| Property | Detection method |
|---|---|
| Frontend framework | Presence of `angular.json`, `next.config.*`, `vite.config.*`, `package.json` scripts |
| Backend framework | `*.csproj`, `Startup.cs`, `app.py`, `manage.py`, `flask` in requirements |
| Language | File extensions (`.ts`, `.cs`, `.py`, `.js`) |
| Database | ORM imports in source files, connection string patterns |
| Component patterns | File name conventions, decorator patterns |

Auto-detected values are shown as defaults in interactive mode. Override them by
editing the JSON config before passing it to `--config`.

---

## After the Wizard

```bash
# 1. Review the generated prompts — edit for fine-tuning
#    prompts/plan_system_<id>.txt
#    prompts/conversion_system_<id>.txt
#    prompts/conversion_target_stack_<id>.txt

# 2. Create a migration job
cp agent-prompts/_template_<id>.yaml agent-prompts/migrate-MyFeature-<id>.yaml

# 3. Fill in feature_root and feature_name at minimum

# 4. Run in plan mode first
python run_agent.py --job agent-prompts/migrate-MyFeature-<id>.yaml

# 5. Review plans/<feature>-plan-*.md, then run full mode
python run_agent.py --job agent-prompts/migrate-MyFeature-<id>.yaml --mode full
```

Or use the fully autonomous agent workflow:
```bash
python run_agent.py --new-job --feature MyFeature --target <id> --non-interactive
python run_agent.py --job agent-prompts/migrate-myfeature-<id>.yaml
python run_agent.py --approve-plan --job agent-prompts/migrate-myfeature-<id>.yaml
python run_agent.py --job agent-prompts/migrate-myfeature-<id>.yaml --mode full
```

---

## Wizard Module Reference

| Module | Class / function | Role |
|---|---|---|
| `wizard/detector.py` | `CodebaseInspector` | Heuristic framework detection |
| `wizard/collector.py` | `collect_answers()`, `collect_feature_selection()` | Interactive prompts |
| `wizard/generator.py` | `generate_plan_prompt()`, `generate_conversion_prompt()` | Prompt content generation |
| `wizard/writer.py` | `WizardWriter` | File I/O with dry-run support |
| `wizard/registry.py` | `WizardRegistry` | `wizard-registry.json` + `skillset-config.json` management |
| `wizard/runner.py` | `run_wizard()`, `list_targets()` | Orchestration |

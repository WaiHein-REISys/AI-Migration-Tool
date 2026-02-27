# Target Stacks

A **target stack** defines the destination technology stack for a migration. The tool
ships with three built-in targets and supports unlimited custom targets via the
Setup Wizard.

---

## Built-in Targets

### `simpler_grants` (default)

| Layer | Technology |
|---|---|
| Frontend framework | Next.js 15 / React 19 |
| CSS | CSS Modules (`*.module.css`) |
| Component structure | `ComponentName/` with `ComponentName.tsx` + `ComponentName.module.css` + `index.ts` |
| API layer | Python APIFlask Blueprints |
| Data access | SQLAlchemy 2.0 `Mapped[]` ORM |
| Database | PostgreSQL |
| Testing | `*.test.tsx` (frontend) |

LLM prompts:
- `prompts/plan_system.txt`
- `prompts/conversion_system.txt`
- `prompts/conversion_target_stack.txt`

---

### `hrsa_pprs`

| Layer | Technology |
|---|---|
| Frontend framework | Next.js 16 / React 18 |
| CSS | CSS Modules (`*.module.css`) |
| Component structure | `ComponentName/` with `ComponentName.tsx` + `ComponentName.module.css` + `index.ts` |
| API layer | Python Flask 3.0 Blueprints |
| Data access | psycopg2 raw SQL with `RealDictCursor` |
| Database | PostgreSQL |
| Testing | `*.test.tsx` (frontend) |

LLM prompts:
- `prompts/plan_system_hrsa_pprs.txt`
- `prompts/conversion_system_hrsa_pprs.txt`
- `prompts/conversion_target_stack_hrsa_pprs.txt`

---

### `snake_case`

| Layer | Technology |
|---|---|
| Frontend framework | Next.js / TypeScript |
| CSS | CSS Modules (`*.module.css`) |
| Component structure | `ComponentName/` with `ComponentName.tsx` + `ComponentName.module.css` + `index.ts` |
| API layer | Python Flask 3.0 |
| Naming convention | All files and identifiers in `snake_case` |
| Data access | Custom SQL scripts |
| Routing pattern | `{feature_name}_routes.py` |
| Service pattern | `{feature_name}_service.py` |
| Repository pattern | `{feature_name}_repository.py` |
| Architecture | Routes → Services → Repositories (3-layer) |

LLM prompts:
- `prompts/plan_system_snake_case.txt`
- `prompts/conversion_system_snake_case.txt`
- `prompts/conversion_target_stack_snake_case.txt`

Job template: `agent-prompts/_template_snake_case.yaml`

---

## Selecting a Target

Set in the job file:
```yaml
pipeline:
  target: "snake_case"
```

Or override at the CLI:
```bash
python run_agent.py --job FILE --target hrsa_pprs
```

---

## Custom Targets (Setup Wizard)

Any custom target can be registered using the Setup Wizard:

```bash
python run_agent.py --setup                           # interactive
python run_agent.py --setup --config wizard.json      # pre-filled JSON
python run_agent.py --setup --list-targets            # list registered targets
```

### What the wizard registers

For a target named `my_flask_app`, the wizard creates:

| Artefact | Path |
|---|---|
| Plan prompt | `prompts/plan_system_my_flask_app.txt` |
| Conversion prompt | `prompts/conversion_system_my_flask_app.txt` |
| Stack reference | `prompts/conversion_target_stack_my_flask_app.txt` |
| Job template | `agent-prompts/_template_my_flask_app.yaml` |
| Skillset config | `config/skillset-config.json` → `target_stack_my_flask_app` + `project_structure_my_flask_app` |
| Registry entry | `config/wizard-registry.json` → `targets.my_flask_app` |

### Using a custom target

After the wizard runs:
```bash
# 1. Review/edit the generated prompts
#    prompts/plan_system_my_flask_app.txt

# 2. Create a job file
cp agent-prompts/_template_my_flask_app.yaml agent-prompts/migrate-MyFeature.yaml
# Set feature_root, feature_name

# 3. Run
python run_agent.py --job agent-prompts/migrate-MyFeature.yaml
```

No code changes are needed — the target ID is read from the job YAML.

---

## `skillset-config.json` Structure

Each target has two blocks in `config/skillset-config.json`:

### `target_stack_<id>`
Describes the technology choices for the target:
```json
{
  "target_stack_snake_case": {
    "frontend": {
      "framework": "Next.js",
      "language": "TypeScript",
      "components_dir": "components/",
      "services_dir": "services/",
      "component_structure": "ComponentName/ with ComponentName.tsx + ...",
      "test_suffix": ".test.tsx",
      "styling": "CSS Modules (*.module.css)",
      "barrel_export": "index.ts per component folder"
    },
    "backend": {
      "framework": "Python Flask",
      "routes_dir": "routes/",
      "services_dir": "services/",
      "route_file_pattern": "{feature_name}_routes.py",
      "service_file_pattern": "{feature_name}_service.py",
      "repositories_dir": "repositories/",
      "repository_file_pattern": "{feature_name}_repository.py",
      "architecture": "Routes -> Services -> Repositories (3-layer)"
    },
    "database": {
      "engine": "Custom SQL scripts"
    }
  }
}
```

### `project_structure_<id>`
Describes the output directory layout:
```json
{
  "project_structure_snake_case": {
    "frontend": {
      "components_root": "frontend/components/{feature_name}/",
      "services_root": "frontend/services/",
      "test_suffix": ".test.tsx"
    },
    "backend": {
      "api_root": "backend/routes/",
      "services_root": "backend/services/",
      "repositories_root": "backend/repositories/"
    }
  }
}
```

Both blocks are injected into the LLM conversion prompt at runtime via the
`{target_stack_summary}` placeholder in `prompts/conversion_system_<id>.txt`.

---

## Source Stack

The source stack definition (`source_stack` in `skillset-config.json`) describes the
legacy codebase:

```json
{
  "source_stack": {
    "frontend": {
      "framework": "Angular 2",
      "language": "TypeScript",
      "state_management": "RxJS Subject / BehaviorSubject",
      "http_client": "Angular HttpClient"
    },
    "backend": {
      "framework": "ASP.NET Core 8 MVC",
      "language": "C#",
      "orm": "Entity Framework Core / Dapper"
    },
    "database": {
      "engine": "SQL Server",
      "migration_tool": "EF Core Migrations"
    }
  }
}
```

---

## Adding a Target Without the Wizard

See [Extending the Tool](Extending-the-Tool.md#new-target-stack) for manual steps.
The wizard approach is strongly preferred for consistency.

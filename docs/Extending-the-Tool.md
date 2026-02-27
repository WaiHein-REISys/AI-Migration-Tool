# Extending the Tool

This guide covers how to extend the AI Migration Tool with new LLM providers,
target stacks, component mappings, guardrail rules, and prompts.

---

## New LLM Provider

### 1. Create the provider class

Create `agents/llm/providers/<name>_provider.py`:

```python
from agents.llm.base import BaseLLMProvider, LLMConfig, LLMMessage, LLMResponse

class MyCustomProvider(BaseLLMProvider):
    """Provider for MyCustom LLM API."""

    def complete(
        self,
        messages: list[LLMMessage],
        config: LLMConfig,
    ) -> LLMResponse:
        # Build request using config.model, config.max_tokens, config.temperature
        # config.base_url, config.api_key are available if needed
        ...
        return LLMResponse(
            content=response_text,
            model=config.model or "default-model",
            provider="mycustom",
            input_tokens=...,
            output_tokens=...,
        )
```

### 2. Register in `LLMRouter`

In `agents/llm/registry.py`, add to `_load_provider()`:

```python
elif provider_name == "mycustom":
    from agents.llm.providers.mycustom_provider import MyCustomProvider
    return MyCustomProvider()
```

And add a detection rule to `_detect_provider()`:

```python
if os.getenv("MYCUSTOM_API_KEY"):
    return "mycustom"
```

### 3. Add to CLI choices

In `main.py`, find the `--llm-provider` argument and add `"mycustom"` to `choices`.

### 4. Document it

Add an entry to [LLM Providers](LLM-Providers.md).

---

## New Target Stack

### Option A — Use the Setup Wizard (recommended)

```bash
python run_agent.py --setup
```

The wizard generates all artefacts automatically. No code changes needed.
See [Setup Wizard](Setup-Wizard.md) for details.

### Option B — Manual steps

#### 1. Add skillset config entries

In `config/skillset-config.json`, add two blocks:

```json
{
  "target_stack_my_target": {
    "frontend": {
      "framework": "Vue 3",
      "language": "TypeScript",
      "components_dir": "src/components/",
      "services_dir": "src/services/",
      "styling": "Tailwind CSS"
    },
    "backend": {
      "framework": "FastAPI",
      "routes_dir": "api/routes/",
      "services_dir": "api/services/"
    }
  },
  "project_structure_my_target": {
    "frontend": {
      "components_root": "frontend/src/components/{feature_name}/",
      "services_root": "frontend/src/services/"
    },
    "backend": {
      "api_root": "backend/api/routes/",
      "services_root": "backend/api/services/"
    }
  }
}
```

#### 2. Create prompt files

```bash
# Start from the closest existing target
cp prompts/plan_system_snake_case.txt prompts/plan_system_my_target.txt
cp prompts/conversion_system_snake_case.txt prompts/conversion_system_my_target.txt
cp prompts/conversion_target_stack_snake_case.txt prompts/conversion_target_stack_my_target.txt

# Edit each file to match the new stack
```

At minimum, update:
- Source/target stack names in the plan prompt
- Target stack description in `conversion_target_stack_my_target.txt`
- Framework-specific conventions in the conversion prompt

#### 3. Register in wizard-registry.json

```json
{
  "targets": {
    "my_target": {
      "source_name": "gprs",
      "source_root": "<YOUR_SOURCE_ROOT>",
      "target_name": "my_target",
      "target_root": "<YOUR_TARGET_ROOT>",
      "framework_pair": "Angular 2 -> Vue 3",
      "backend_pair": "ASP.NET Core MVC -> FastAPI",
      "created_at": "2026-02-27T00:00:00+00:00",
      "prompt_files": {
        "plan_system": "plan_system_my_target.txt",
        "conversion_system": "conversion_system_my_target.txt",
        "target_stack": "conversion_target_stack_my_target.txt"
      },
      "job_template": "_template_my_target.yaml"
    }
  }
}
```

#### 4. Create a job template

```bash
cp agent-prompts/_template_snake_case.yaml agent-prompts/_template_my_target.yaml
# Edit: set target: "my_target", update notes section
```

#### 5. Test

```bash
python run_agent.py --new-job --feature TestFeature --target my_target \
  --non-interactive --json
python run_agent.py --job agent-prompts/migrate-testfeature-my_target.yaml --dry-run
```

---

## New Component Mapping

Component mappings define how source file types are classified and which Jinja2
template is used in no-LLM scaffold mode.

### 1. Add to `skillset-config.json`

In the `component_mappings` array:

```json
{
  "id": "MAP-007",
  "name": "Angular Pipe to React utility",
  "source_pattern": "angular_pipe",
  "target_pattern": "react_utility_function",
  "template": "ng-pipe-to-react-util.jinja2",
  "description": "Converts Angular @Pipe to a plain TypeScript utility function"
}
```

### 2. Create the Jinja2 template

Create `templates/ng-pipe-to-react-util.jinja2`:

```jinja2
// {{ feature_name }} — converted from Angular Pipe
// Source: {{ source_path }}
// Mapping: MAP-007

export function {{ class_name | snake_to_camel }}(value: {{ input_type }}): {{ output_type }} {
  // TODO: implement
  return value;
}
```

### 3. Update `ScopingAgent` detector (if needed)

In `agents/scoping_agent.py`, add detection logic for the new pattern so files are
classified as `angular_pipe` in the dependency graph.

The `ConversionAgent` picks up the new mapping ID automatically from `skillset-config.json`.

---

## New Guardrail Rule

### 1. Add to `rules-config.json`

```json
{
  "id": "RULE-011",
  "name": "No hardcoded configuration values",
  "description": "Environment-specific values (URLs, connection strings, API keys, timeouts) must be read from environment variables or config files. Do NOT hardcode them in source code.",
  "enforcement": "warning"
}
```

That's it — no Python changes needed. The rule is injected into `{rules_text}` automatically.

### 2. Optionally reference it in prompts

In `prompts/plan_system_*.txt`, add a note:

```
- Flag any hardcoded configuration values (URLs, keys, timeouts) under Risk Areas as RULE-011.
```

---

## New Prompt for an Existing Target

Prompts are plain text files — edit them directly:

```bash
# Add a new constraint to the snake_case plan prompt
code prompts/plan_system_snake_case.txt
```

Changes take effect immediately on the next pipeline run.

If you create an **entirely new prompt file** for a new use case, load it in your
agent code:

```python
from prompts import load_prompt

custom_prompt = load_prompt("my_custom_prompt.txt")
```

---

## Modifying the Pipeline

### Adding a new pipeline stage

1. Create a new agent class in `agents/`:
   ```python
   class MyNewAgent:
       def run(self, context: AgentContext) -> dict:
           ...
   ```
2. Import and call it in `main.py` at the appropriate step
3. Pass outputs forward via `AgentContext` or local variables

### Modifying approval logic

The `ApprovalGate` is responsible for:
- Checking `output/<feature>/.approved` marker
- Prompting the user if no marker exists
- Writing/reading checkpoints

To change approval behaviour, edit `agents/approval_gate.py`.

---

## Running Tests

```bash
python -m pytest tests/ -v
```

After adding any extension, run a dry-run smoke test:

```bash
python run_agent.py --job agent-prompts/_template.yaml --dry-run --no-llm
```

---

## Code Style

- Python 3.11+ type hints throughout
- `pathlib.Path` for all file operations (no `os.path`)
- Agent classes take `AgentContext` (immutable) and return results as dicts or typed dataclasses
- All file writes go through `WizardWriter` (wizard) or direct `pathlib` (agents)
- Logging via `logging.getLogger(__name__)` — never `print()` in agent code

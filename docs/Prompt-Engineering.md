# Prompt Engineering

All LLM prompts are stored as plain text files in `prompts/`. They can be edited
directly without touching any Python code — changes take effect on the next run.

---

## How Prompts Work

Each pipeline agent uses one or more prompt files:

| Agent | System prompt | User message |
|---|---|---|
| `PlanAgent` | `prompts/plan_system[_<target>].txt` | Serialised dependency graph |
| `ConversionAgent` | `prompts/conversion_system[_<target>].txt` | Source file content |
| `ConversionAgent` | `prompts/conversion_target_stack[_<target>].txt` | Injected into system prompt via `{target_stack_summary}` |

### Loading

Prompts are loaded by `prompts/__init__.py::load_prompt()`:

```python
from prompts import load_prompt

text = load_prompt("plan_system_snake_case.txt")
```

The loader:
1. Looks up the file in the `prompts/` directory
2. Returns cached content on subsequent calls (LRU cache)
3. Raises `FileNotFoundError` if the file doesn't exist

---

## Prompt File Reference

### Plan prompts (`plan_system_*.txt`)

**Purpose:** System prompt sent to the LLM when generating the Plan Document.

**Placeholders:** None — the full instructions are static.

**Key sections to tune:**
- **STRICT RULES** — The numbered rules the LLM must follow (enforced by ApprovalGate)
- **Plan Document schema** — The required Markdown sections (Overview, Component Mappings, Risk Areas, etc.)
- **AMBIGUOUS / BLOCKED handling** — How the LLM should report unmappable items
- **Source/target stack names** — Replace if you're adapting for a different source stack

**Per-target files:**
| File | Target |
|---|---|
| `plan_system.txt` | `simpler_grants` |
| `plan_system_hrsa_pprs.txt` | `hrsa_pprs` |
| `plan_system_snake_case.txt` | `snake_case` |
| `plan_system_<id>.txt` | wizard-generated |

---

### Conversion system prompts (`conversion_system_*.txt`)

**Purpose:** System prompt sent to the LLM for code conversion (Stage 5).

**Placeholders:**
| Token | Filled with |
|---|---|
| `{rules_text}` | All guardrail rules from `config/rules-config.json`, formatted as a numbered list |
| `{target_stack_summary}` | Contents of `conversion_target_stack_<target>.txt` |

**Key sections to tune:**
- **MANDATORY GUARDRAILS** — The `{rules_text}` block enforces RULE-001 through RULE-010
- **CRITICAL CONSTRAINTS** — Hard rules: no refactoring, no route changes, AMBIGUOUS/BLOCKED responses
- **Output format** — Code only, no markdown fences, preserve comments

**Per-target files:**
| File | Target |
|---|---|
| `conversion_system.txt` | `simpler_grants` |
| `conversion_system_hrsa_pprs.txt` | `hrsa_pprs` |
| `conversion_system_snake_case.txt` | `snake_case` |
| `conversion_system_<id>.txt` | wizard-generated |

---

### Target stack reference (`conversion_target_stack_*.txt`)

**Purpose:** Describes the target stack's conventions in detail. Injected into the
conversion system prompt via `{target_stack_summary}`.

**Content:** Human-readable description of:
- Frontend component structure, file naming, CSS approach
- Backend route/service/repository patterns
- Database access patterns
- Key naming conventions

**Per-target files:**
| File | Target |
|---|---|
| `conversion_target_stack.txt` | `simpler_grants` |
| `conversion_target_stack_hrsa_pprs.txt` | `hrsa_pprs` |
| `conversion_target_stack_snake_case.txt` | `snake_case` |
| `conversion_target_stack_<id>.txt` | wizard-generated |

---

### Plan document template (`plan_document_template.md`)

**Purpose:** Markdown scaffold used in **no-LLM mode** (`llm.no_llm: true`).
The `PlanAgent` fills in placeholder values from the dependency graph.

**Placeholders:** `{{feature_name}}`, `{{file_list}}`, `{{mapping_table}}`, etc.

Not used when the LLM is active.

---

## How to Edit Prompts

1. Open the prompt file directly in any text editor
2. Make your changes — no Python code changes needed
3. Re-run the pipeline — the updated prompt is used immediately

**Example: Tighten the plan rules for a specific target**
```bash
# Edit the snake_case plan system prompt
code prompts/plan_system_snake_case.txt
# Add a rule:
# 8. All repository methods MUST use snake_case identifiers only.
```

**Example: Update the target stack reference**
```bash
# The stack reference is injected into conversion prompts
code prompts/conversion_target_stack_snake_case.txt
# Add the new repositories_dir pattern your team uses
```

---

## Revision Mode Prompts

When `--revise-plan --feedback "..."` is used, `PlanAgent` builds a modified user
message by appending to the standard dependency graph summary:

```
[standard dependency graph summary]

---
REVISION FEEDBACK (address ALL points before finalising):
<feedback text>

ORIGINAL PLAN (revise this — do NOT copy unchanged sections verbatim):
```markdown
<original plan content>
```
```

The system prompt remains unchanged. The revision feedback and original plan are
injected as additional context in the user message.

---

## Guardrail Injection

The conversion system prompt receives a `{rules_text}` token that is filled at runtime
from `config/rules-config.json`. This means you can add, remove, or reorder guardrail
rules in the JSON without editing the prompt file.

Example injection:
```
MANDATORY GUARDRAILS -- violations will be rejected:
[RULE-001] Preserve API contracts: Do not change REST endpoint routes, HTTP methods, ...
[RULE-002] Preserve UI CSS class names: All class names in converted templates must match...
...
```

See [Guardrail Rules](Guardrail-Rules.md) for the full rule list.

---

## Creating Prompts for a New Target

The **Setup Wizard** generates initial prompts automatically. To create them manually:

1. Copy the closest existing prompt file:
   ```bash
   cp prompts/plan_system_snake_case.txt prompts/plan_system_my_target.txt
   ```
2. Edit the source/target stack names and any target-specific conventions
3. Register the new target in `config/wizard-registry.json` and `config/skillset-config.json`
   (or run `python run_agent.py --setup` to do this automatically)

See [Extending the Tool](Extending-the-Tool.md#new-target-stack) for full steps.

---

## Prompt Caching

Prompts are cached in memory with an LRU cache for the lifetime of a process.
In long-running processes, changes to prompt files require a restart to take effect.
For single-run CLI use (normal mode), there is no caching concern.

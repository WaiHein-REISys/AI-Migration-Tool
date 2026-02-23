# prompts/

All LLM prompt files for the AI Migration Tool.

Prompts are stored as plain text or Markdown files so they can be edited,
reviewed in pull requests, and version-controlled independently of the
Python source code.

---

## Files

| File | Used by | Purpose |
|---|---|---|
| `plan_system.txt` | `PlanAgent` | System prompt for the plan generation LLM call. Instructs the model to act as a migration architect and produce a structured Markdown Plan Document. |
| `plan_document_template.md` | `PlanAgent` | Markdown scaffold used in template-only mode (when no LLM is configured). Contains `{placeholder}` fields filled by the agent. |
| `conversion_system.txt` | `ConversionAgent` | System prompt for the code conversion LLM call. Contains `{rules_text}` and `{target_stack_summary}` placeholders filled at runtime. |
| `conversion_target_stack.txt` | `ConversionAgent` | Target stack reference block injected into `conversion_system.txt` as `{target_stack_summary}`. |

---

## How to edit prompts

1. Open the relevant `.txt` or `.md` file in this folder.
2. Edit the text directly — no Python changes needed.
3. Placeholders like `{rules_text}` and `{feature_name}` are filled at runtime
   by the agent; do not remove them.
4. The loader (`prompts/__init__.py`) caches files on first read. When running
   the pipeline normally this is transparent. During development you can call
   `reload_prompt(filename)` to pick up changes without restarting.

---

## How to add a new prompt

1. Create a new `.txt` or `.md` file in this folder.
2. Import and use it in your agent:
   ```python
   from prompts import load_prompt
   my_prompt = load_prompt("my_new_prompt.txt")
   ```
3. Document it in the table above.

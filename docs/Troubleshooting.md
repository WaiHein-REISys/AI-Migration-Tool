# Troubleshooting

---

## LLM / API Errors

### `LLMConfigurationError: No LLM provider configured`

**Cause:** No API key or model env var is set, and no explicit provider is in the job file.

**Fix:**
```bash
# Set any one of these env vars:
set ANTHROPIC_API_KEY=sk-ant-...
set OPENAI_API_KEY=sk-...
set OLLAMA_MODEL=llama3.2        # (Ollama must be running)
set LLM_BASE_URL=http://localhost:1234/v1

# Or use template-only mode (no LLM, no API key):
# In the job file:  llm.no_llm: true
# Or CLI:           python run_agent.py --job FILE --no-llm
```

---

### `LLMConfigurationError` in agent mode but not expected

**Cause:** `run_agent.py` sets `AI_AGENT_MODE=1` which enables soft-fail, but the
error is still raised. This happens when calling `main.py` directly.

**Fix:** Always use `run_agent.py` in agent mode. If you must call `main.py` directly,
set the env var first:
```bash
set AI_AGENT_MODE=1
python main.py ...
```

---

### Ollama timeout / slow responses

**Cause:** Code generation requests are large and can take 3–5+ minutes.

**Fix:** Increase the timeout in the job file:
```yaml
llm:
  timeout: 600   # seconds (default: 120)
```

Or at CLI: `python main.py ... --llm-timeout 600`

---

### `ollama: command not found` / `ollama` package missing

**Cause:** Ollama native SDK not installed (optional — the tool falls back to `httpx`).

**Fix:** Either install it (`pip install ollama>=0.3.0`) or ignore — the fallback works.

If Ollama server is not running:
```bash
ollama serve    # start the server
```

---

### OpenAI `401 Unauthorized`

**Cause:** Invalid or expired API key.

**Fix:**
```bash
set OPENAI_API_KEY=sk-...    # refresh your key
```

For Azure OpenAI, also check `api_version` is correct and the deployment name matches `model`.

---

### `llama-cpp-python` import error / CUDA error

**Cause:** `llama-cpp-python` not installed, or CPU build used on a system expecting GPU.

**Fix:**
```bash
# CPU-only (always works)
pip install llama-cpp-python

# CUDA GPU (Windows PowerShell)
$env:CMAKE_ARGS = "-DLLAMA_CUDA=on"
pip install llama-cpp-python --force-reinstall

# macOS Metal GPU
CMAKE_ARGS="-DLLAMA_METAL=on" pip install llama-cpp-python --force-reinstall
```

---

## Pipeline Errors

### `ScopingError: feature_root does not exist`

**Cause:** `pipeline.feature_root` in the job file points to a path that doesn't exist
on the current machine.

**Fix:** Update `feature_root` with the correct absolute path for your machine.
Remember to use forward slashes or escaped backslashes:
```yaml
feature_root: "C:/Projects/MyApp/src/FeatureName"
# or:
feature_root: "C:\\Projects\\MyApp\\src\\FeatureName"
```

---

### `ConfigValidationError: Unknown target 'my_target'`

**Cause:** The target ID in the job file isn't registered in `config/wizard-registry.json`
or doesn't have a matching `target_stack_<id>` block in `config/skillset-config.json`.

**Fix:** Run the Setup Wizard to register the target:
```bash
python run_agent.py --setup
```

Or check `config/wizard-registry.json` for the exact registered target ID:
```bash
python run_agent.py --setup --list-targets
```

---

### Pipeline says "Already complete — skipping"

**Cause:** The `(feature_name, feature_root, target)` combination was completed in a
previous run. The run ID is in `logs/completed-runs.json`.

**Fix:** Add `--force` to re-run from scratch:
```bash
python run_agent.py --job FILE --force
```

Or delete the entry from `logs/completed-runs.json` manually.

---

### Plan generation produces AMBIGUOUS output

**Cause:** The dependency graph has imports or dependencies the LLM can't confidently map.

**Fix (preferred):** Use `--revise-plan` to provide explicit guidance:
```bash
python run_agent.py --revise-plan \
  --job agent-prompts/migrate-myfeature.yaml \
  --feedback "The pfm-auth service has no target equivalent. Mark it BLOCKED.
              The DataTableService should map to a custom React hook, not a server action."
```

**Fix (manual):** Edit the generated plan file in `plans/` to resolve AMBIGUOUS items,
then proceed with `--approve-plan`.

---

### Conversion step is BLOCKED

**Cause:** The source file imports from a flagged platform library (RULE-008).

**Fix:** The BLOCKED item is logged in `logs/<run-id>-conversion-log.md`. It requires
manual resolution — the human developer must:
1. Identify the equivalent in the target stack
2. Either update the conversion prompt to handle this import pattern, or
3. Write the converted file manually and add it to `output/<feature>/`

---

### `FileNotFoundError: prompts/plan_system_my_target.txt`

**Cause:** The target is registered in `wizard-registry.json` but the prompt file
doesn't exist (e.g. file was deleted or target was registered manually without
creating prompt files).

**Fix:** Create the missing prompt file:
```bash
cp prompts/plan_system_snake_case.txt prompts/plan_system_my_target.txt
# Edit to match your target stack
```

Or re-run the wizard with `--overwrite`:
```bash
python run_agent.py --setup --overwrite
```

---

## Agent Mode Errors

### `--revise-plan` says "No plan file found"

**Cause:** Plan generation (Step 3) hasn't been run yet, or plan files are in a
different location.

**Fix:** Run the plan stage first:
```bash
python run_agent.py --job agent-prompts/migrate-myfeature.yaml
```

---

### `--approve-plan` says "output directory cannot be created"

**Cause:** Permissions issue writing to the `output/` directory.

**Fix:** Check that the process has write access to `output/`. On Windows, run as
Administrator if necessary; on Linux/macOS: `chmod -R 755 output/`.

---

### `--status` shows `plan_generated: false` when plan files exist

**Cause:** The run ID computed from `(feature_name, feature_root, target)` doesn't
match the IDs in existing plan filenames. This happens if `feature_root` or `target`
changed after the plan was generated.

**Fix:** Verify the job file has the same `feature_root`, `feature_name`, and `target`
as when the plan was originally generated. Use `--force` to regenerate if needed.

---

### `--new-job --non-interactive` fails feature resolution

**Cause:** `--feature` argument doesn't match any folder in the source codebase.

**Fix:**
```bash
# First, list available features to find the exact name
python run_agent.py --list-features --json

# Use the exact name shown in the output
python run_agent.py --new-job --feature "ActionHistory" --target snake_case \
  --non-interactive --json

# Or use an absolute path
python run_agent.py --new-job \
  --feature "<YOUR_SOURCE_ROOT>/src/.../ActionHistory" \
  --target snake_case --non-interactive --json
```

---

## Config / JSON Errors

### `json.JSONDecodeError` in skillset-config.json

**Cause:** Manual edit introduced invalid JSON (trailing comma, missing quote, etc.).

**Fix:** Validate the JSON:
```bash
python -c "import json; json.load(open('config/skillset-config.json'))"
```

Use a JSON linter or VS Code's built-in JSON validator to find the exact error.

---

### Schema validation error in rules-config.json

**Cause:** New rule entry is missing a required field (`id`, `name`, `description`, or `enforcement`).

**Fix:** Check `config/schemas/rules-schema.json` for the required structure and
ensure your new rule entry has all fields.

---

## Getting Help

1. Run with `--verbose` for full DEBUG-level logs:
   ```bash
   python run_agent.py --job FILE --verbose
   ```
2. Check `logs/<run-id>-conversion-log.md` for step-by-step conversion results
3. Check `logs/<run-id>-dependency-graph.json` to verify scoping output
4. Check `checkpoints/<run-id>.json` to inspect resume state

If the issue persists, open an issue on GitHub with:
- The exact command run
- The error message and stack trace
- The relevant section of `config/skillset-config.json` (no secrets)
- Python version (`python --version`) and OS

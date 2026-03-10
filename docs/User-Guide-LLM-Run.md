# User Guide — LLM Run
## Configuring and Using LLM Providers with the AI Migration Tool

**AI Migration Tool · 2026-03-09**

---

## Overview

The AI Migration Tool uses LLMs at two stages of the pipeline:

| Stage | Agent | What the LLM does |
|-------|-------|-------------------|
| **Stage 3 — Plan Generation** | `PlanAgent` | Analyses the dependency graph and produces a structured Markdown migration plan |
| **Stage 5 — Conversion** | `ConversionAgent` | Converts each source file to the target language/framework using the approved plan |

Both stages use the **same LLM provider** (configured once per job). The LLM is given:
- A system prompt with stack-specific rules and guardrails
- The source file content
- The migration plan step for context

This guide covers all supported providers, how to configure each, and how to choose the best
provider for your situation.

---

## Provider Auto-Detection Order

The tool auto-detects available providers at startup in this priority order:

```
1. ANTHROPIC_API_KEY      → Anthropic Claude API
2. OPENAI_API_KEY         → OpenAI GPT / o-series API
3. GOOGLE_API_KEY         → Google Gemini API (direct)
4. GOOGLE_CLOUD_PROJECT   → Google Vertex AI (requires GOOGLE_APPLICATION_CREDENTIALS)
5. OLLAMA_MODEL           → Local Ollama server
6. LLM_BASE_URL           → OpenAI-compatible endpoint (LM Studio, vLLM, Azure OpenAI, etc.)
7. LLAMACPP_MODEL_PATH    → Local GGUF model via llama.cpp
8. LLM_SUBPROCESS_CMD     → CLI tool (any command that reads stdin, writes stdout)
9. PATH detection         → Auto-detect claude, codex, or gemini binary on PATH
```

The first matching provider is used. To override, set `llm.provider` in the job YAML or pass
`--llm-subprocess-cmd <cmd>` on the command line.

> **Pre-flight check:** if the pipeline needs an LLM (any mode except `scope` / `no_llm: true`)
> but none is reachable, it exits immediately with **code 2** and a structured error — it never
> silently falls back to Jinja2 scaffold. Remaining mid-run soft-fallbacks emit a
> `[LLM_FAILURE_JSON]` line to stderr (see Troubleshooting).

---

## Provider 1 — Anthropic Claude API (Recommended)

### Setup

```bash
export ANTHROPIC_API_KEY="sk-ant-api03-..."
```

### Job YAML

```yaml
llm:
  provider: anthropic
  model: claude-3-7-sonnet-20250219   # null = auto (latest claude-3-7-sonnet)
  timeout: 600
```

### CLI Override

```bash
python run_agent.py --job agent-prompts/<file>.yaml
# No extra flags needed — ANTHROPIC_API_KEY auto-detected
```

### Models Available

| Model ID | Context Window | Speed | Best For |
|----------|---------------|-------|----------|
| `claude-opus-4-5` | 200K tokens | Slow | Complex multi-file migrations with deep business logic |
| `claude-sonnet-4-5` | 200K tokens | Medium | Standard migrations (recommended default) |
| `claude-3-7-sonnet-20250219` | 200K tokens | Fast | Most migrations — good balance of quality and speed |
| `claude-haiku-4-5` | 200K tokens | Very fast | Simple scaffolds, cost-sensitive runs |

### Notes

- Best **code generation quality** across all providers tested
- Follows prompt rules most faithfully out of the box
- The validator (`ValidationAgent`) also uses this provider by default — ensure the same key is set
- Typical latency: 3–10 s per step for most migration tasks
- Typical cost: ~$0.003–$0.015 per step at sonnet rates

---

## Provider 2 — OpenAI API

### Setup

```bash
export OPENAI_API_KEY="sk-..."
```

### Job YAML

```yaml
llm:
  provider: openai
  model: gpt-4o          # or o3, o4-mini, gpt-4-turbo
  timeout: 120
```

### CLI Override

```bash
python run_agent.py --job agent-prompts/<file>.yaml
# OPENAI_API_KEY auto-detected
```

### Models Available

| Model ID | Context Window | Speed | Best For |
|----------|---------------|-------|----------|
| `gpt-4o` | 128K tokens | Fast | Standard migrations |
| `gpt-4-turbo` | 128K tokens | Medium | Complex business logic |
| `o4-mini` | 128K tokens | Medium (reasoning) | Multi-step logic with deep reasoning |
| `o3` | 128K tokens | Slow (reasoning) | Highest-quality reasoning tasks |

### Notes

- `o4-mini` supports `reasoning_effort` tuning: `low` / `medium` / `high`
  - `high` (default): best quality, ~3–8 min per step for complex files
  - `medium`: ~30–120 s per step (recommended for batch runs)
  - `low`: ~15–45 s per step (suitable for simple CRUD files)
- Set via subprocess profile if using Codex CLI (see Provider 6 below)

---

## Provider 3 — Google Gemini API (Direct)

### Setup

```bash
export GOOGLE_API_KEY="AIza..."
```

### Job YAML

```yaml
llm:
  provider: google_gemini   # or just leave provider: null — auto-detected
  model: gemini-2.5-pro     # null = default (gemini-2.5-pro)
  timeout: 300
```

### Models Available

| Model ID | Context Window | Speed | Best For |
|----------|---------------|-------|----------|
| `gemini-2.5-pro` | 1M tokens | Medium | Complex migrations with large context |
| `gemini-2.0-flash` | 1M tokens | Fast | Standard migrations, cost-efficient |
| `gemini-2.5-flash` | 1M tokens | Very fast | Simple scaffolds, batch runs |

### Notes

- **1M token context window** — handles very large codebases without chunking
- Passes validation on first attempt in testing (strong prompt rule compliance)
- Token usage is tracked and recorded in the conversion log automatically
- May produce `flask_smorest` imports instead of plain `flask` — add an explicit import
  rule to `prompts/conversion_system_<target>.txt` if this occurs

---

## Provider 4 — Google Vertex AI

For **Google Cloud** environments where you authenticate via service account rather than API key.

### Setup

```bash
export GOOGLE_CLOUD_PROJECT="my-gcp-project"
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
```

Or use Application Default Credentials (ADC):
```bash
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT="my-gcp-project"
```

### Job YAML

```yaml
llm:
  provider: vertex_ai
  model: gemini-2.5-pro     # null = default
  timeout: 300
```

### Notes

- Uses the same Gemini models as Provider 3 but routes through Vertex AI's regional endpoints
- Suitable for enterprise / regulated environments where data must stay within a GCP region
- Requires `google-cloud-aiplatform` in the Python environment (`pip install google-cloud-aiplatform`)
- Set `GOOGLE_CLOUD_LOCATION` to override the default region (`us-central1`)

---

## Provider 5 — Ollama (Local, Free)

### Setup

```bash
# Install Ollama: https://ollama.com
ollama pull llama3.2        # or codellama, deepseek-coder, qwen2.5-coder, etc.
export OLLAMA_MODEL=llama3.2
```

### Job YAML

```yaml
llm:
  provider: ollama
  model: llama3.2           # must match the pulled model name
  base_url: http://localhost:11434   # default; change if Ollama is on a different host
  timeout: 300
```

### Notes

- **No API cost** — runs entirely on your hardware
- Quality depends heavily on the model chosen:
  - `codellama:34b` — good for code migration tasks
  - `qwen2.5-coder:32b` — strong code generation
  - `deepseek-coder-v2:16b` — efficient and accurate
- Context window is model-dependent — most 7B–13B models handle 8K–32K tokens
- Not recommended for production migrations without quality validation

---

## Provider 6 — OpenAI-Compatible Endpoint

For **LM Studio**, **vLLM**, **Ollama OpenAI-compat mode**, **Azure OpenAI**, and other
OpenAI-protocol servers.

### Setup

```bash
export LLM_BASE_URL="http://localhost:1234/v1"    # LM Studio default
# OR
export LLM_BASE_URL="https://my-azure.openai.azure.com/openai/deployments/gpt-4o"
export OPENAI_API_KEY="azure-api-key"              # Azure requires this
```

### Job YAML

```yaml
llm:
  provider: openai_compat
  model: "local-model-name"
  base_url: "http://localhost:1234/v1"
  timeout: 300
```

### LM Studio Setup

1. Download LM Studio from https://lmstudio.ai
2. Load a model (recommended: `Qwen2.5-Coder-32B-Instruct-GGUF`)
3. Start the local server: **Local Server** tab → Start Server
4. Set `LLM_BASE_URL=http://localhost:1234/v1`

### Azure OpenAI Setup

```bash
export LLM_BASE_URL="https://<resource>.openai.azure.com/openai/deployments/<deployment>"
export OPENAI_API_KEY="<azure-api-key>"
```

```yaml
llm:
  provider: openai_compat
  model: gpt-4o            # must match your Azure deployment name
  base_url: "https://<resource>.openai.azure.com/openai/deployments/<deployment>"
```

---

## Provider 7 — llama.cpp (GGUF Local Model)

For running quantized GGUF models directly without a server.

### Setup

```bash
export LLAMACPP_MODEL_PATH="/path/to/model.gguf"
```

### Job YAML

```yaml
llm:
  provider: llamacpp
  model_path: "/path/to/qwen2.5-coder-32b-instruct-Q4_K_M.gguf"
  timeout: 600
```

### Recommended GGUF models

| Model | Size | Quality | Notes |
|-------|------|---------|-------|
| `Qwen2.5-Coder-32B-Instruct-Q4_K_M.gguf` | ~20 GB | ⭐⭐⭐⭐⭐ | Best local code model |
| `DeepSeek-Coder-V2-Lite-Instruct-Q5_K_M.gguf` | ~10 GB | ⭐⭐⭐⭐ | Good quality, smaller |
| `CodeLlama-34B-Instruct-Q4_K_M.gguf` | ~22 GB | ⭐⭐⭐ | Older but reliable |

---

## Provider 8 — Subprocess CLI (Claude Code CLI)

Delegates LLM calls to the **claude** command-line tool. Best when you want Claude's quality
without managing an API key directly in environment variables.

### Setup

```bash
# Install Claude Code CLI
npm install -g @anthropic-ai/claude-code
# or: brew install claude

# Authenticate
claude auth
```

### Job YAML

```yaml
llm:
  provider: subprocess
  subprocess_cmd: "claude"
  model: null              # null = claude's default model
  timeout: 120
```

### CLI Override

```bash
python run_agent.py --job agent-prompts/<file>.yaml \
  --llm-subprocess-cmd claude
```

### How It Works

The pipeline calls:
```bash
echo "<full prompt>" | claude --print --dangerously-skip-permissions --no-session-persistence
```

- `--print` — Non-interactive output mode (stdout only)
- `--dangerously-skip-permissions` — No confirmation prompts
- `--no-session-persistence` — Each call is stateless
- Full prompt is passed via stdin; response is plain text on stdout

### Notes

- **Best quality** among all providers tested on this codebase
- No API key required if Claude Code CLI is already authenticated
- Latency: typically 3–15 s per step (fast for most migration files)
- The validator uses codex CLI by default for simulation — override this in the validator config
  if you want Claude CLI to handle validation as well

---

## Provider 9 — Subprocess CLI (Codex CLI)

Delegates LLM calls to **OpenAI Codex CLI** (`o4-mini` reasoning model).

### Setup

```bash
# Install Codex CLI
npm install -g @openai/codex
# or: brew install codex

# Set API key
export OPENAI_API_KEY="sk-..."
```

### Job YAML

```yaml
llm:
  provider: subprocess
  subprocess_cmd: "codex"
  model: null              # null = codex's default (o4-mini)
  timeout: 180
```

### Recommended YAML with reasoning effort

```yaml
llm:
  provider: subprocess
  subprocess_cmd: "codex"
  model: null
  timeout: 180
  subprocess_env:
    CODEX_REASONING_EFFORT: "medium"
```

### Reasoning Effort Comparison

| Setting | Latency per step | Quality | Use when |
|---------|-----------------|---------|----------|
| `low` | 15–45 s | Good | Simple CRUD routes (DELETE, list) |
| `medium` | 30–120 s | Very good | Most migration steps (recommended) |
| `high` | 3–8 min | Excellent | Complex business logic with multiple dependencies |

### Known Limitations with Codex

Based on testing against this codebase:
- Tends to generate **dynamic SQL** (`for key, value in payload.items()`) instead of explicit field mapping
- May miss fields in INSERT statements (computed but not saved)
- Uses `@_blueprint.post("/")` for all routes instead of matching HTTP verbs
- Requires more explicit few-shot examples in the prompt to match Claude's output quality

---

## Provider 10 — Subprocess CLI (Gemini CLI)

Delegates LLM calls to **Google Gemini CLI** (`gemini-2.5-pro`).

### Setup

```bash
# Install Gemini CLI
npm install -g @google/gemini-cli
# or: brew install gemini-cli

# Authenticate
gemini auth login
```

### Job YAML

```yaml
llm:
  provider: subprocess
  subprocess_cmd: "gemini"
  model: null              # null = gemini-2.5-pro (default)
  timeout: 120
```

### How It Works

The pipeline calls:
```bash
echo "<full prompt>" | gemini -p "" --yolo -o json
```

- `-p ""` — Triggers non-interactive (headless) mode
- `--yolo` — Auto-approves all tool calls
- `-o json` — Emits a single JSON response `{"session_id": "...", "response": "...", "stats": {...}}`
- Full prompt is passed via stdin

### Token Tracking

Gemini emits token usage in `stats.models.<name>.tokens.{input, candidates}`. The pipeline
parses this automatically and records it in the conversion log.

### Notes

- **Passes validation on first attempt** in testing (3/3 pass rate)
- Strong prompt rule compliance
- Missing `_blueprint` definition in some output files (post-processor can fix this)
- Uses `flask_smorest` import in some cases instead of `flask` — add explicit import rule to prompt

---

## Configuring Multiple Providers: Job YAML Reference

### Minimal (auto-detect)

```yaml
llm:
  provider: null    # First available provider wins
```

### Explicit Anthropic

```yaml
llm:
  provider: anthropic
  model: claude-3-7-sonnet-20250219
  timeout: 600
```

### Explicit OpenAI

```yaml
llm:
  provider: openai
  model: gpt-4o
  timeout: 120
```

### Explicit Ollama

```yaml
llm:
  provider: ollama
  model: qwen2.5-coder:32b
  base_url: http://localhost:11434
  timeout: 300
```

### Subprocess Claude CLI

```yaml
llm:
  provider: subprocess
  subprocess_cmd: "claude"
  model: null
  timeout: 120
```

### Subprocess Codex CLI (with medium reasoning)

```yaml
llm:
  provider: subprocess
  subprocess_cmd: "codex"
  model: null
  timeout: 180
```

### Subprocess Gemini CLI

```yaml
llm:
  provider: subprocess
  subprocess_cmd: "gemini"
  model: null
  timeout: 120
```

### Google Gemini API (direct)

```yaml
llm:
  provider: google_gemini
  model: gemini-2.5-pro
  timeout: 300
```

### Google Vertex AI

```yaml
llm:
  provider: vertex_ai
  model: gemini-2.5-pro
  timeout: 300
```

### No LLM (template scaffold only)

```yaml
llm:
  no_llm: true
```

---

## No-LLM Mode

When `no_llm: true` is set (or `--no-llm` is passed), the pipeline returns Jinja2 scaffolds
instead of LLM-generated code. This is useful for:

- **Demos** with no API key available
- **CI pipelines** that test pipeline structure without API costs
- **Stub generation** — create the file skeleton, then fill in logic manually

```bash
python run_agent.py --job agent-prompts/<file>.yaml --mode full --no-llm
```

Output files will look like:
```python
# TODO: Implement edit_activity route
# Source: EditActivity.cs
# Target: api/src/api/editactivity.cs/edit_activity_routes.py
# Rules: RULE-001, RULE-003
from flask import Blueprint
_blueprint = Blueprint("edit_activity", __name__)

@_blueprint.put("/<string:activity_id>")
def edit_activity(activity_id: str):
    # SCAFFOLD: Replace with actual implementation
    raise NotImplementedError
```

---

## Provider Comparison Table

| Provider | API Key / Auth | Cost | Latency | Quality | Offline | Best For |
|----------|---------------|------|---------|---------|---------|----------|
| **Anthropic API** | ANTHROPIC_API_KEY | ~$0.003–0.015/step | 3–10 s | ⭐⭐⭐⭐⭐ | No | Production migrations |
| **OpenAI API (gpt-4o)** | OPENAI_API_KEY | ~$0.005–0.020/step | 5–15 s | ⭐⭐⭐⭐ | No | Standard use |
| **OpenAI API (o4-mini)** | OPENAI_API_KEY | ~$0.003–0.012/step | 30–120 s | ⭐⭐⭐⭐⭐ | No | Complex reasoning tasks |
| **Google Gemini API** | GOOGLE_API_KEY | Varies (~$0.002–0.010/step) | 10–30 s | ⭐⭐⭐⭐⭐ | No | Large context (1M tokens), Google Cloud |
| **Google Vertex AI** | GCP service account / ADC | Varies | 10–30 s | ⭐⭐⭐⭐⭐ | No | Enterprise GCP, data residency |
| **Ollama (local)** | None | Free | 20–120 s | ⭐⭐⭐ | Yes | Cost-sensitive / air-gapped |
| **LM Studio / vLLM** | None | Free | 20–120 s | ⭐⭐⭐ | Yes | Enterprise local deployment |
| **llama.cpp** | None | Free | 30–300 s | ⭐⭐⭐ | Yes | Minimal dependencies |
| **Claude Code CLI** | CLI auth | Same as API | 3–15 s | ⭐⭐⭐⭐⭐ | No | Dev machines with claude installed |
| **Codex CLI** | OPENAI_API_KEY | ~$0.003–0.012/step | 30–120 s | ⭐⭐⭐⭐ | No | OpenAI-first environments |
| **Gemini CLI** | CLI auth | Varies | 42–91 s/step | ⭐⭐⭐⭐⭐ | No | Google Cloud via CLI |
| **No-LLM (scaffold)** | None | Free | <1 s | N/A | Yes | Demos, CI, stubs |

---

## Switching Providers Mid-Project

Each job YAML specifies one provider. To compare outputs from different providers for the same
feature, create separate job files with unique `feature_name` values:

```bash
# Claude API run
cp agent-prompts/migrate-commands-hrsa_simpler_pprs_repo.yaml \
   agent-prompts/migrate-commands-claude.yaml
# Edit: feature_name: "Commands-claude"

# Codex CLI run
cp agent-prompts/migrate-commands-hrsa_simpler_pprs_repo.yaml \
   agent-prompts/migrate-commands-codex.yaml
# Edit: feature_name: "Commands-codex", subprocess_cmd: "codex"

# Gemini CLI run
cp agent-prompts/migrate-commands-hrsa_simpler_pprs_repo.yaml \
   agent-prompts/migrate-commands-gemini.yaml
# Edit: feature_name: "Commands-gemini", subprocess_cmd: "gemini"
```

> **Why `feature_name` matters:** The `feature_name` is hashed with the source hash to produce
> the `run_id`. If two jobs share the same `feature_name` and same source, they will collide on
> checkpoints and logs. Always use a unique `feature_name` per CLI variant.

---

## Token Usage and Cost Estimation

### Gemini example (from test run)

| Step | Input Tokens | Output Tokens | Total | Notes |
|------|-------------|--------------|-------|-------|
| B1 — EditActivity.cs | 7,784 | 1,286 | 9,070 | Moderate-size source file |
| B2 — CreateActivity.cs | 11,715 | 1,188 | 12,903 | Larger file with person logic |
| B3 — DeleteActivity.cs | 8,013 | 754 | 8,767 | Simple delete handler |
| **Total** | **27,512** | **3,228** | **30,740** | ~$0.02 at Gemini Pro rates |

### Claude API example (estimated, from prompt size)

| Step | Input Tokens (est.) | Output Tokens (est.) | Est. Cost |
|------|---------------------|----------------------|-----------|
| Plan generation | ~8,000 | ~2,500 | ~$0.003 |
| Conversion (3 steps) | ~25,000 | ~4,000 | ~$0.011 |
| Validation (3 steps) | ~18,000 | ~1,500 | ~$0.007 |
| **Total** | **~51,000** | **~8,000** | **~$0.021** |

Costs are estimates based on claude-3-7-sonnet pricing at $3/$15 per MTok (in/out).

---

## Troubleshooting LLM Issues

### "No LLM provider configured" (exit code 2)

The pipeline exits immediately with **exit code 2** (before any files are touched) when no LLM
is reachable and the mode requires one. The structured error looks like:

```json
{
  "status": "error",
  "error_type": "LLM_NOT_CONFIGURED",
  "message": "No LLM provider is configured or reachable.",
  "fix": [
    "set ANTHROPIC_API_KEY=sk-ant-...         (Anthropic Claude)",
    "set OPENAI_API_KEY=sk-...                (OpenAI GPT)",
    "set GOOGLE_API_KEY=...                   (Google Gemini)",
    "set GOOGLE_CLOUD_PROJECT=my-project      (Vertex AI + GOOGLE_APPLICATION_CREDENTIALS)",
    "set OLLAMA_MODEL=llama3                  (local Ollama server)",
    "add  llm.no_llm: true  in the job YAML  (template-only scaffold — no AI conversion)"
  ]
}
```

**Fix:** Export at least one provider key. Check with:
```bash
echo $ANTHROPIC_API_KEY    # should print your key (masked is fine)
echo $GOOGLE_API_KEY       # Google Gemini
which claude               # should print a path if Claude CLI is installed
```

**Mid-run soft-fallback** (rare, if the API fails partway through): a `[LLM_FAILURE_JSON]`
line is emitted to stderr and the conversion summary includes `"llm_used": false`. Check
this field if you suspect scaffold output slipped through.

---

### "Model metadata not found" (Codex CLI)

```
{"type":"error","message":"Model metadata for `claude` not found."}
```

This happens when the job YAML sets `model: "claude"` (or another CLI command name) and
Codex tries to use it as a model identifier.

**Fix:** Set `model: null` in the YAML. The tool's guard prevents CLI profile names from
being passed as model flags.

---

### LLM call times out

```
LLMTimeoutError: subprocess timed out after 120 seconds
```

**Fix:** Increase `timeout` in the YAML:
```yaml
llm:
  timeout: 300    # 5 minutes
```

For Codex CLI with high reasoning effort, use 600 seconds (10 minutes) for complex steps.

---

### Output contains prose before code (Codex)

Codex sometimes outputs chain-of-thought reasoning before the Python code block.

The tool's `_strip_code_fences()` post-processor handles this automatically via two passes:
1. **Pass 1:** Extracts content from ` ```python...``` ` markdown blocks
2. **Pass 2:** Strips lines before the first `import` / `from` / `#` / `def` / `class` / `@`

If prose is still appearing in output files, check that `conversion_agent.py` has the latest
version of `_strip_code_fences()`.

---

### Validation fails only for one provider

If Claude output passes validation but Codex/Gemini fails, the issue is almost always:
1. A missing field in an INSERT statement (check `logs/<run-id>-validation-report.md`)
2. Wrong HTTP verb (`@_blueprint.post` instead of `put`/`delete`)
3. Missing `_blueprint` definition (results in Python `NameError` at runtime)
4. Dynamic SQL where explicit columns are expected

For each of these, add a corresponding rule to `prompts/conversion_system_<target>.txt`.
See the CLI Comparison Report (`reports/cli-comparison-report-2026-03-05.md`) for the full list
of recommended prompt rules per issue.

---

### Validator confidence is 0.35 (simulation failed)

The validator tries to simulate the output using a subprocess CLI. If the CLI is misconfigured,
the simulation fails and the validator falls back to file sanity checks only (confidence=0.35).

Check:
```bash
cat logs/<run-id>-validation-report.json | python -m json.tool | grep "reason"
```

If the reason contains `"Model metadata for 'claude' not found"`, the validator is trying to
use a CLI with the wrong model name. Fix the validator's CLI configuration or set
`validation.subprocess_cmd` explicitly in the job YAML.

---

## Environment Variables Quick Reference

```bash
# ── API providers ──────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...          # Anthropic Claude
OPENAI_API_KEY=sk-...                 # OpenAI / Codex
GOOGLE_API_KEY=AIza...                # Google Gemini API (direct)

# ── Google Cloud (Vertex AI) ───────────────────────────────────────────────
GOOGLE_CLOUD_PROJECT=my-gcp-project         # enables Vertex AI provider
GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json  # or use ADC: gcloud auth application-default login
GOOGLE_CLOUD_LOCATION=us-central1           # optional; defaults to us-central1

# ── Local providers ────────────────────────────────────────────────────────
OLLAMA_MODEL=llama3.2                 # Ollama model name
LLM_BASE_URL=http://localhost:1234/v1 # OpenAI-compat server URL
LLAMACPP_MODEL_PATH=/path/to/file.gguf # llama.cpp GGUF model

# ── CLI subprocess providers ───────────────────────────────────────────────
LLM_SUBPROCESS_CMD=claude             # Override: use this CLI for all LLM calls

# ── Encoding (Windows) ─────────────────────────────────────────────────────
PYTHONIOENCODING=utf-8                # Required on Windows for Unicode logs
```

---

*AI Migration Tool · LLM Run User Guide · 2026-03-09*

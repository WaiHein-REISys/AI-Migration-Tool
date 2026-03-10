# LLM Providers

The AI Migration Tool supports seven LLM backends. The provider is selected automatically
from environment variables or explicitly in the job file / CLI flags.

---

## Auto-Detection Order

When `llm.provider` is `null` (the default), the Python runtime detects the provider
in this priority order:

```
LLM_PROVIDER (explicit override)
  → LLAMACPP_MODEL_PATH   →  llamacpp
  → OLLAMA_MODEL          →  ollama
  → LLM_BASE_URL          →  openai_compat   (only when OPENAI_API_KEY is unset)
  → OPENAI_API_KEY        →  openai
  → ANTHROPIC_API_KEY     →  anthropic
  → GOOGLE_API_KEY        →  vertex_ai       (Gemini direct API)
  → GOOGLE_CLOUD_PROJECT  →  vertex_ai       (Vertex AI / ADC)
  → LLM_SUBPROCESS_CMD    →  subprocess      (explicit CLI)
  → claude on PATH        →  subprocess:claude
  → codex  on PATH        →  subprocess:codex
```

First found wins. If none are set and `no_llm` is false, a `LLMConfigurationError`
is raised (or a soft-fail scaffold is returned in agent mode).

> **Note:** `run_full.sh` uses its own bash-side detection order that prefers cloud
> providers first (Anthropic → OpenAI → Google → Ollama → …) for demo convenience.
> The Python code preferring local providers first is intentional — it avoids accidental
> API calls when a local server is already running.

---

## Providers

### `anthropic` — Anthropic Claude

**Environment variable:** `ANTHROPIC_API_KEY`

**Package:** `anthropic` (included in `requirements.txt`)

**Default model:** `claude-opus-4-5`

```bash
# Environment
set ANTHROPIC_API_KEY=sk-ant-...

# CLI
python main.py --feature-root "..." --feature-name "F" --mode plan \
  --llm-provider anthropic \
  --llm-model claude-3-5-sonnet-20241022

# Job file
llm:
  provider: anthropic
  model: claude-3-5-sonnet-20241022
```

**Recommended models:**
- `claude-opus-4-5` — highest quality (default)
- `claude-3-5-sonnet-20241022` — good quality, faster
- `claude-3-5-haiku-20241022` — fastest, lower cost

---

### `openai` — OpenAI GPT / Azure OpenAI

**Environment variable:** `OPENAI_API_KEY`

**Package:** `pip install openai>=1.50.0`

**Default model:** `gpt-4o`

```bash
# OpenAI
set OPENAI_API_KEY=sk-...

python main.py --feature-root "..." --feature-name "F" --mode plan \
  --llm-provider openai --llm-model gpt-4o

# Job file
llm:
  provider: openai
  model: gpt-4o
```

**Azure OpenAI:**
```bash
python main.py --feature-root "..." --feature-name "F" --mode plan \
  --llm-provider openai \
  --llm-base-url "https://<resource>.openai.azure.com/" \
  --llm-model gpt-4o \
  --llm-api-version 2024-08-01-preview
```

```yaml
# Job file
llm:
  provider: openai
  model: gpt-4o
  base_url: "https://<resource>.openai.azure.com/"
  api_version: "2024-08-01-preview"
```

---

### `openai_compat` — OpenAI-Compatible Servers

Supports any server that implements the OpenAI `/v1/chat/completions` API:
LM Studio, vLLM, Together AI, Fireworks AI, Groq, Ollama `/v1`, etc.

**Environment variable:** `LLM_BASE_URL`

**Package:** `pip install openai>=1.50.0`

```bash
# LM Studio
python main.py --feature-root "..." --feature-name "F" --mode plan \
  --llm-provider openai_compat \
  --llm-base-url http://localhost:1234/v1 \
  --llm-model local-model
```

```bash
# vLLM
python main.py --feature-root "..." --feature-name "F" --mode plan \
  --llm-provider openai_compat \
  --llm-base-url http://localhost:8000/v1 \
  --llm-model mistralai/Mistral-7B-Instruct-v0.3
```

```bash
# Together AI
set LLM_BASE_URL=https://api.together.xyz/v1
set OPENAI_API_KEY=<together_api_key>
python main.py --feature-root "..." --feature-name "F" --mode plan \
  --llm-provider openai_compat \
  --llm-model meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo
```

```yaml
# Job file (LM Studio)
llm:
  provider: openai_compat
  base_url: "http://localhost:1234/v1"
  model: "local-model"
```

---

### `ollama` — Local Ollama Server

Connects to an Ollama server via its native REST API (no OpenAI compatibility needed).

**Environment variable:** `OLLAMA_MODEL`

**Package:** `pip install ollama>=0.3.0` (optional — falls back to `httpx` if not installed)

**Default host:** `http://localhost:11434`

```bash
# Start Ollama first
ollama serve

# Pull a model
ollama pull llama3.2
ollama pull deepseek-coder-v2

# Run
set OLLAMA_MODEL=llama3.2
python main.py --feature-root "..." --feature-name "F" --mode plan
```

```bash
# Explicit provider + remote host
python main.py --feature-root "..." --feature-name "F" --mode plan \
  --llm-provider ollama \
  --llm-model deepseek-coder-v2 \
  --ollama-host http://192.168.1.100:11434
```

```yaml
# Job file
llm:
  provider: ollama
  model: "llama3.2"
  ollama_host: "http://localhost:11434"
  timeout: 600   # code generation can take several minutes
```

**Recommended models for code migration:**
- `llama3.2` — balanced speed/quality
- `deepseek-coder-v2` — best for code tasks
- `qwen2.5-coder` — strong code understanding
- `codellama` — specialised for code

**Performance tip:** Code generation requests can take 3–5+ minutes. Set
`llm.timeout: 600` (or higher) to avoid premature timeouts.

---

### `llamacpp` — Local GGUF Files

Runs quantised GGUF models entirely locally via llama.cpp. No network required.

**Environment variable:** `LLAMACPP_MODEL_PATH`

**Package:**
```bash
# CPU only
pip install llama-cpp-python>=0.2.90

# CUDA GPU (Windows PowerShell)
$env:CMAKE_ARGS = "-DLLAMA_CUDA=on"
pip install llama-cpp-python>=0.2.90

# CUDA GPU (Linux/macOS)
CMAKE_ARGS="-DLLAMA_CUDA=on" pip install llama-cpp-python>=0.2.90
```

```bash
# Run
python main.py --feature-root "..." --feature-name "F" --mode plan \
  --llm-provider llamacpp \
  --llm-model-path "<MODEL_PATH>"
```

```yaml
# Job file
llm:
  provider: llamacpp
  model_path: "<MODEL_PATH>"
```

**Recommended GGUF models:**
- `mistral-7b.Q4_K_M.gguf` — fast, good quality
- `codellama-13b.Q5_K_M.gguf` — best for code (GPU recommended)
- `deepseek-coder-6.7b.Q4_K_M.gguf` — code-specialised, CPU-friendly

---

### `vertex_ai` — Google Gemini API / Vertex AI

Supports both the Gemini direct API (via `GOOGLE_API_KEY`) and Google Vertex AI
(via service account or Application Default Credentials).

**Supports structured tool-use** — used automatically by `OrchestratorAgent` in
`native_tools` mode (same as Anthropic and OpenAI).

**Environment variables:**
- `GOOGLE_API_KEY` — Gemini direct API (no GCP project needed)
- `GOOGLE_CLOUD_PROJECT` — Vertex AI via ADC / service account

**Packages** (install whichever path you use):
```bash
pip install google-generativeai   # Gemini direct API
pip install vertexai              # Vertex AI SDK
```
Both are optional soft-dependencies — the tool falls back gracefully when neither
is installed.

**Default model:** `gemini-2.0-flash`

```bash
# Gemini API
export GOOGLE_API_KEY=AIza...
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml

# Vertex AI
export GOOGLE_CLOUD_PROJECT=my-gcp-project
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml
```

```yaml
# Job file (Gemini API)
llm:
  provider: vertex_ai
  model: gemini-2.0-flash
```

```yaml
# Job file (Vertex AI — specific model)
llm:
  provider: vertex_ai
  model: gemini-1.5-pro
```

**Recommended models:**
- `gemini-2.0-flash` — fast, strong code understanding (default)
- `gemini-1.5-pro` — higher quality, larger context
- `gemini-1.5-flash` — fastest, lower cost

---

### `subprocess` — CLI Tool Delegation (Claude Code CLI / Codex CLI)

The `subprocess` provider shells out to an installed CLI tool rather than calling
an API directly. No API key is required — authentication is handled by the CLI tool
itself. Supports any CLI that reads a prompt from stdin and writes a response to stdout.

**Uses ReAct text mode** for orchestration (THOUGHT / ACTION / PARAMS parsing).

**Environment variable:** `LLM_SUBPROCESS_CMD` (optional — auto-detected if `claude`
or `codex` is on PATH)

**Package:** none (CLI must already be installed)

```bash
# Claude Code CLI (auto-detected when on PATH, or set explicitly)
export LLM_SUBPROCESS_CMD=claude
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml

# Or pass at CLI
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml \
  --llm-subprocess-cmd claude

# OpenAI Codex CLI
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml \
  --llm-subprocess-cmd codex
```

```yaml
# Job file
llm:
  provider: subprocess
  subprocess_cmd: claude   # or: codex
```

**Extra arguments and env vars:**
```bash
# Forward extra args to the CLI
export LLM_SUBPROCESS_ARGS="--verbose"

# Pass env vars to the subprocess
export LLM_SUBPROCESS_CMD=claude
```

**Cursor / Windsurf agents (non-interactive):**
```bash
# Full agent workflow without any TTY interaction
python run_agent.py --list-features --json
python run_agent.py --new-job --feature ActionHistory --target snake_case \
  --non-interactive --json
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml \
  --llm-subprocess-cmd claude
python run_agent.py --approve-plan \
  --job agent-prompts/migrate-actionhistory-snake_case.yaml
python run_agent.py --job agent-prompts/migrate-actionhistory-snake_case.yaml \
  --mode full --llm-subprocess-cmd claude
```

---

## No-LLM Mode (Template Scaffold)

When `llm.no_llm: true` (or `--no-llm` at CLI), all LLM calls are replaced with
Jinja2 template renders. No API key or server is needed.

Output files are skeletal scaffolds with `TODO` placeholders instead of real
converted code. Useful for:
- Testing pipeline structure
- Generating file skeletons for human completion
- CI/CD pipelines where LLM access is unavailable

```yaml
llm:
  no_llm: true
```

```bash
python main.py --feature-root "..." --feature-name "F" --mode full \
  --no-llm --auto-approve
```

---

## LLM CLI Flags Reference

| Flag | Default | Description |
|---|---|---|
| `--no-llm` | off | Disable LLM; use Jinja2 scaffold only |
| `--llm-provider` | auto-detect | `anthropic` \| `openai` \| `openai_compat` \| `ollama` \| `llamacpp` \| `vertex_ai` \| `subprocess` |
| `--llm-model` | provider default | Model name or ID |
| `--llm-base-url` | — | Base URL for OpenAI-compatible or Azure endpoints |
| `--llm-model-path` | — | Path to local GGUF file (llamacpp only) |
| `--ollama-host` | `http://localhost:11434` | Ollama server URL |
| `--llm-max-tokens` | `8192` | Max tokens per LLM call |
| `--llm-temperature` | `0.2` | Sampling temperature |
| `--llm-subprocess-cmd` | — | CLI tool name for subprocess provider (`claude`, `codex`, etc.) |
| `--select-llm` | off | Show interactive provider picker (human TTY only; not for agents) |

---

## LLM Failure Behaviour

| Run context | Behaviour on LLM error |
|---|---|
| `run_agent.py` (agent mode) | **Soft-fail** — returns Jinja2 scaffold; pipeline continues |
| `main.py` direct | **Hard-fail** — raises `LLMConfigurationError` with fix instructions |

Agent mode (`AI_AGENT_MODE=1`) is set automatically by `run_agent.py`. Set it
manually for any `main.py` call that should use soft-fail:

```bash
set AI_AGENT_MODE=1          # Windows CMD
$env:AI_AGENT_MODE = "1"     # PowerShell
export AI_AGENT_MODE=1       # bash / zsh
```

---

## Adding a New Provider

See [Extending the Tool](Extending-the-Tool.md#new-llm-provider).

# LLM Providers

The AI Migration Tool supports five LLM backends. The provider is selected automatically
from environment variables or explicitly in the job file / CLI flags.

---

## Auto-Detection Order

When `llm.provider` is `null` (the default), the tool detects the provider in this order:

```
ANTHROPIC_API_KEY  →  anthropic
OPENAI_API_KEY     →  openai
OLLAMA_MODEL       →  ollama
LLM_BASE_URL       →  openai_compat
LLAMACPP_MODEL_PATH → llamacpp
```

First found wins. If none are set and `no_llm` is false, a `LLMConfigurationError`
is raised (or a soft-fail scaffold is returned in agent mode).

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
  --llm-model-path "C:/models/mistral-7b.Q4_K_M.gguf"
```

```yaml
# Job file
llm:
  provider: llamacpp
  model_path: "C:/models/codellama-13b.Q5_K_M.gguf"
```

**Recommended GGUF models:**
- `mistral-7b.Q4_K_M.gguf` — fast, good quality
- `codellama-13b.Q5_K_M.gguf` — best for code (GPU recommended)
- `deepseek-coder-6.7b.Q4_K_M.gguf` — code-specialised, CPU-friendly

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
| `--llm-provider` | auto-detect | `anthropic` \| `openai` \| `openai_compat` \| `ollama` \| `llamacpp` |
| `--llm-model` | provider default | Model name or ID |
| `--llm-base-url` | — | Base URL for OpenAI-compatible or Azure endpoints |
| `--llm-model-path` | — | Path to local GGUF file (llamacpp only) |
| `--ollama-host` | `http://localhost:11434` | Ollama server URL |
| `--llm-max-tokens` | `8192` | Max tokens per LLM call |
| `--llm-temperature` | `0.2` | Sampling temperature |

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

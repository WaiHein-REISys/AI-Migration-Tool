#!/usr/bin/env bash
# =============================================================================
# run_full.sh — Full pipeline runner (no manual steps required)
#
# Usage:
#   ./run_full.sh                              # no-LLM scaffold mode (no API key)
#   ./run_full.sh --llm                        # auto-detect provider from env vars
#   ./run_full.sh --llm --provider anthropic   # explicit provider
#   ./run_full.sh --job path/to/job.yaml       # custom job file
#   ./run_full.sh --verbose                    # debug logging
#   ./run_full.sh --dry-run                    # logs only, no files written
#   ./run_full.sh --force                      # ignore resume cache, re-run
#
# Supported providers (set the matching env var before running --llm):
#   anthropic   → ANTHROPIC_API_KEY=sk-ant-...
#   openai      → OPENAI_API_KEY=sk-...
#   ollama      → OLLAMA_MODEL=llama3.2  (local, no key needed)
#   openai_compat → LLM_BASE_URL=http://localhost:1234/v1  (LM Studio, vLLM, Azure)
#   llamacpp    → LLAMACPP_MODEL_PATH=/path/to/model.gguf
#   subprocess  → LLM_SUBPROCESS_CMD=claude  (Claude Code CLI, Codex CLI)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
JOB_FILE="agent-prompts/demo-actionhistory-no-llm.yaml"
USE_LLM=false
PROVIDER=""
EXTRA_ARGS=()
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --llm)       USE_LLM=true; shift ;;
    --provider)  PROVIDER="$2"; shift 2 ;;
    --job)       JOB_FILE="$2"; shift 2 ;;
    --verbose)   EXTRA_ARGS+=("--verbose"); shift ;;
    --dry-run)   EXTRA_ARGS+=("--dry-run"); shift ;;
    --force)     EXTRA_ARGS+=("--force"); shift ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *)
      echo "[ERROR] Unknown argument: $1" >&2
      exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Python: prefer venv, fall back to system python
# ---------------------------------------------------------------------------
if [[ -f ".venv/bin/activate" ]]; then
  echo "[setup] Activating virtual environment..."
  # shellcheck disable=SC1091
  source .venv/bin/activate
elif [[ -f "venv/bin/activate" ]]; then
  source venv/bin/activate
fi

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" &>/dev/null; then
  echo "[ERROR] Python not found. Install Python 3.11+ or create a venv." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# LLM mode: auto-detect provider from environment variables
# ---------------------------------------------------------------------------
if [[ "$USE_LLM" == true ]]; then
  # Auto-detect if no explicit --provider given (same priority order as run_agent.py)
  if [[ -z "$PROVIDER" ]]; then
    if   [[ -n "${ANTHROPIC_API_KEY:-}" ]];    then PROVIDER="anthropic"
    elif [[ -n "${OPENAI_API_KEY:-}" ]];        then PROVIDER="openai"
    elif [[ -n "${OLLAMA_MODEL:-}" ]];          then PROVIDER="ollama"
    elif [[ -n "${LLM_BASE_URL:-}" ]];          then PROVIDER="openai_compat"
    elif [[ -n "${LLAMACPP_MODEL_PATH:-}" ]];   then PROVIDER="llamacpp"
    elif [[ -n "${LLM_SUBPROCESS_CMD:-}" ]];    then PROVIDER="subprocess"
    elif command -v claude &>/dev/null;         then PROVIDER="subprocess"
    elif command -v codex  &>/dev/null;         then PROVIDER="subprocess"
    else
      echo "[ERROR] --llm requires at least one provider to be configured." >&2
      echo "        Set one of the following env vars:" >&2
      echo "          ANTHROPIC_API_KEY   (Anthropic Claude)" >&2
      echo "          OPENAI_API_KEY      (OpenAI GPT)" >&2
      echo "          OLLAMA_MODEL        (Ollama local)" >&2
      echo "          LLM_BASE_URL        (LM Studio / vLLM / Azure)" >&2
      echo "          LLAMACPP_MODEL_PATH (llama.cpp GGUF)" >&2
      echo "          LLM_SUBPROCESS_CMD  (Claude Code CLI / Codex CLI)" >&2
      exit 1
    fi
  fi

  # Validate known providers
  case "$PROVIDER" in
    anthropic)     [[ -z "${ANTHROPIC_API_KEY:-}" ]]  && { echo "[ERROR] ANTHROPIC_API_KEY is not set." >&2; exit 1; } ;;
    openai)        [[ -z "${OPENAI_API_KEY:-}" ]]     && { echo "[ERROR] OPENAI_API_KEY is not set." >&2; exit 1; } ;;
    openai_compat) [[ -z "${LLM_BASE_URL:-}" ]]       && { echo "[ERROR] LLM_BASE_URL is not set." >&2; exit 1; } ;;
    llamacpp)      [[ -z "${LLAMACPP_MODEL_PATH:-}" ]] && { echo "[ERROR] LLAMACPP_MODEL_PATH is not set." >&2; exit 1; } ;;
    ollama|subprocess) ;;  # no key required
    *) echo "[ERROR] Unknown provider: $PROVIDER" >&2; exit 1 ;;
  esac

  # Patch the job file at runtime via a temp file (enables LLM, sets provider)
  TMP_JOB="$(mktemp /tmp/ai-migration-job-XXXXXX.yaml)"
  trap 'rm -f "$TMP_JOB"' EXIT
  sed \
    -e 's/^  no_llm: true/  no_llm: false/' \
    -e "s/^  provider: null/  provider: $PROVIDER/" \
    "$JOB_FILE" > "$TMP_JOB"
  JOB_FILE="$TMP_JOB"
  echo "[setup] LLM mode enabled — provider: $PROVIDER"
else
  echo "[setup] Running in no-LLM scaffold mode (template-only)."
fi

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
if ! "$PYTHON" -c "import yaml" &>/dev/null; then
  echo "[setup] Installing dependencies..."
  "$PYTHON" -m pip install -q -r requirements.txt
fi

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  AI Migration Tool — Full Pipeline Run"
echo "  Job:    $JOB_FILE"
echo "  Python: $("$PYTHON" --version)"
echo "============================================================"
echo ""

"$PYTHON" run_agent.py --job "$JOB_FILE" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
EXIT_CODE=$?

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
if [[ $EXIT_CODE -eq 0 ]]; then
  echo "[done] Pipeline completed successfully."
  echo "       Output: $SCRIPT_DIR/output/"
  echo "       Logs:   $SCRIPT_DIR/logs/"
  echo "       Plans:  $SCRIPT_DIR/plans/"
elif [[ $EXIT_CODE -eq 2 ]]; then
  echo "[done] Pipeline stopped — plan was rejected at the approval gate."
else
  echo "[fail] Pipeline exited with code $EXIT_CODE. Check logs/ for details."
fi

exit $EXIT_CODE

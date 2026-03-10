#!/usr/bin/env bash
# =============================================================================
# run_full.sh — Full pipeline runner (no manual steps required)
#
# Usage:
#   ./run_full.sh                              # no-LLM scaffold mode (no API key)
#   ./run_full.sh --llm                        # auto-detect provider from env vars
#   ./run_full.sh --llm --provider anthropic   # explicit provider
#   ./run_full.sh --llm --orchestrate          # LLM-driven orchestration mode
#   ./run_full.sh --job path/to/job.yaml       # custom job file
#   ./run_full.sh --mode full                  # set pipeline mode (plan|scope|full)
#   ./run_full.sh --verbose                    # debug logging
#   ./run_full.sh --dry-run                    # logs only, no files written
#   ./run_full.sh --force                      # ignore resume cache, re-run
#
# Supported providers (set the matching env var before running --llm):
#   anthropic      → ANTHROPIC_API_KEY=sk-ant-...
#   openai         → OPENAI_API_KEY=sk-...
#   google_gemini  → GOOGLE_API_KEY=AIza...
#   vertex_ai      → GOOGLE_CLOUD_PROJECT=my-gcp-project (+ credentials or ADC)
#   ollama         → OLLAMA_MODEL=llama3.2  (local, no key needed)
#   openai_compat  → LLM_BASE_URL=http://localhost:1234/v1  (LM Studio, vLLM, Azure)
#   llamacpp       → LLAMACPP_MODEL_PATH=/path/to/model.gguf
#   subprocess     → LLM_SUBPROCESS_CMD=claude  (Claude Code CLI, Codex CLI)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
JOB_FILE="agent-prompts/demo-actionhistory-no-llm.yaml"
USE_LLM=false
PROVIDER=""
MODE=""
ORCHESTRATE=false
EXTRA_ARGS=()
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --llm)         USE_LLM=true; shift ;;
    --provider)    PROVIDER="$2"; shift 2 ;;
    --job)         JOB_FILE="$2"; shift 2 ;;
    --mode)        MODE="$2"; shift 2 ;;
    --orchestrate) ORCHESTRATE=true; shift ;;
    --verbose)     EXTRA_ARGS+=("--verbose"); shift ;;
    --dry-run)     EXTRA_ARGS+=("--dry-run"); shift ;;
    --force)       EXTRA_ARGS+=("--force"); shift ;;
    -h|--help)
      sed -n '2,26p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *)
      echo "[ERROR] Unknown argument: $1" >&2
      exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Guard: --orchestrate requires --llm
# ---------------------------------------------------------------------------
if [[ "$ORCHESTRATE" == true && "$USE_LLM" == false ]]; then
  echo "[ERROR] --orchestrate requires --llm (the orchestrator needs a live LLM)." >&2
  echo "        Example: ./run_full.sh --llm --orchestrate --job agent-prompts/my-job.yaml" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Guard: --mode validation
# ---------------------------------------------------------------------------
if [[ -n "$MODE" ]]; then
  case "$MODE" in
    plan|scope|full) ;;
    *) echo "[ERROR] --mode must be one of: plan, scope, full (got: $MODE)" >&2; exit 1 ;;
  esac
fi

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
    if   [[ -n "${ANTHROPIC_API_KEY:-}" ]];       then PROVIDER="anthropic"
    elif [[ -n "${OPENAI_API_KEY:-}" ]];           then PROVIDER="openai"
    elif [[ -n "${GOOGLE_API_KEY:-}" ]];           then PROVIDER="google_gemini"
    elif [[ -n "${GOOGLE_CLOUD_PROJECT:-}" ]];     then PROVIDER="vertex_ai"
    elif [[ -n "${OLLAMA_MODEL:-}" ]];             then PROVIDER="ollama"
    elif [[ -n "${LLM_BASE_URL:-}" ]];             then PROVIDER="openai_compat"
    elif [[ -n "${LLAMACPP_MODEL_PATH:-}" ]];      then PROVIDER="llamacpp"
    elif [[ -n "${LLM_SUBPROCESS_CMD:-}" ]];       then PROVIDER="subprocess"
    elif command -v claude &>/dev/null;            then PROVIDER="subprocess"
    elif command -v codex  &>/dev/null;            then PROVIDER="subprocess"
    else
      echo "[ERROR] --llm requires at least one provider to be configured." >&2
      echo "        Set one of the following env vars:" >&2
      echo "          ANTHROPIC_API_KEY      (Anthropic Claude)" >&2
      echo "          OPENAI_API_KEY         (OpenAI GPT)" >&2
      echo "          GOOGLE_API_KEY         (Google Gemini direct)" >&2
      echo "          GOOGLE_CLOUD_PROJECT   (Google Vertex AI)" >&2
      echo "          OLLAMA_MODEL           (Ollama local)" >&2
      echo "          LLM_BASE_URL           (LM Studio / vLLM / Azure)" >&2
      echo "          LLAMACPP_MODEL_PATH    (llama.cpp GGUF)" >&2
      echo "          LLM_SUBPROCESS_CMD     (Claude Code CLI / Codex CLI)" >&2
      exit 1
    fi
  fi

  # Validate known providers
  case "$PROVIDER" in
    anthropic)     [[ -z "${ANTHROPIC_API_KEY:-}" ]]    && { echo "[ERROR] ANTHROPIC_API_KEY is not set." >&2;    exit 1; } ;;
    openai)        [[ -z "${OPENAI_API_KEY:-}" ]]       && { echo "[ERROR] OPENAI_API_KEY is not set." >&2;       exit 1; } ;;
    google_gemini) [[ -z "${GOOGLE_API_KEY:-}" ]]       && { echo "[ERROR] GOOGLE_API_KEY is not set." >&2;       exit 1; } ;;
    vertex_ai)     [[ -z "${GOOGLE_CLOUD_PROJECT:-}" ]] && { echo "[ERROR] GOOGLE_CLOUD_PROJECT is not set." >&2; exit 1; } ;;
    openai_compat) [[ -z "${LLM_BASE_URL:-}" ]]         && { echo "[ERROR] LLM_BASE_URL is not set." >&2;         exit 1; } ;;
    llamacpp)      [[ -z "${LLAMACPP_MODEL_PATH:-}" ]]  && { echo "[ERROR] LLAMACPP_MODEL_PATH is not set." >&2;  exit 1; } ;;
    ollama|subprocess) ;;  # no key required
    *) echo "[ERROR] Unknown provider: $PROVIDER" >&2; exit 1 ;;
  esac

  # Patch the job file at runtime via a temp file
  TMP_JOB="$(mktemp /tmp/ai-migration-job-XXXXXX.yaml)"
  trap 'rm -f "$TMP_JOB"' EXIT

  # 1. Base patches: flip no_llm off, set provider
  sed \
    -e 's/^  no_llm: true/  no_llm: false/' \
    -e "s/^  provider: null/  provider: $PROVIDER/" \
    "$JOB_FILE" > "$TMP_JOB"

  # 2. Orchestration patches: enable orchestrator + auto_approve (--orchestrate only)
  if [[ "$ORCHESTRATE" == true ]]; then
    "$PYTHON" - "$TMP_JOB" <<'PYEOF'
import sys, re

path = sys.argv[1]
text = open(path).read()

# Patch auto_approve: false → true  (appears once under pipeline:)
text = re.sub(r'^( {2}auto_approve:) false', r'\1 true', text, flags=re.MULTILINE)

# Patch orchestration.enabled: false → true  (only inside the orchestration: block)
# Matches the orchestration: block as a run of indented lines, replaces the
# first "enabled: false" found within it — leaves all other enabled: lines untouched.
def _patch_orch_block(m: re.Match) -> str:
    return m.group(0).replace('enabled: false', 'enabled: true', 1)

text = re.sub(
    r'^orchestration:[ \t]*\n(?:[ \t]+[^\n]*\n)*',
    _patch_orch_block,
    text,
    flags=re.MULTILINE,
)

open(path, 'w').write(text)
PYEOF
    echo "[setup] Orchestration mode enabled — orchestration.enabled=true, auto_approve=true"
  fi

  JOB_FILE="$TMP_JOB"
  echo "[setup] LLM mode enabled — provider: $PROVIDER"
else
  echo "[setup] Running in no-LLM scaffold mode (template-only)."
fi

# ---------------------------------------------------------------------------
# Mode override: pass --mode to run_agent.py when set
# ---------------------------------------------------------------------------
if [[ -n "$MODE" ]]; then
  EXTRA_ARGS+=("--mode" "$MODE")
  echo "[setup] Pipeline mode override: $MODE"
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
echo "  Job:      $JOB_FILE"
if [[ "$ORCHESTRATE" == true ]]; then
  echo "  Mode:     orchestrated (LLM-driven dynamic workflow)"
elif [[ -n "$MODE" ]]; then
  echo "  Mode:     $MODE"
fi
echo "  Provider: ${PROVIDER:-none (scaffold)}"
echo "  Python:   $("$PYTHON" --version)"
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
  if [[ "$USE_LLM" == false ]]; then
    echo "[info] Pipeline stopped — no LLM configured (exit 2)."
    echo "       Run with --llm to enable LLM-powered conversion."
  else
    echo "[info] Pipeline stopped at a gate (exit 2). Possible causes:"
    echo "         • Approval gate: plan awaiting review (use --orchestrate to auto-approve)"
    echo "         • LLM unreachable: API key invalid or network error mid-run"
    echo "       Check logs/ for details."
  fi
else
  echo "[fail] Pipeline exited with code $EXIT_CODE. Check logs/ for details."
fi

exit $EXIT_CODE

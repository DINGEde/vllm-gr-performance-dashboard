#!/usr/bin/env bash
#
# Launcher: main CI 32/16 baseline + candidate × (CC + JiuwenSwarm)
# Date: 2026-08-09
# Host: L20-10014-direct
# Repo: /home/zhike/dxw/AgentCache
# Commit: a7473e3 (upstream/main)
# Shape: 32/16 (task_num=32, max_concurrency=16)
# Agents: Claude Code + JiuwenSwarm (4 arms total)
# Virtualenv: /home/zhike/dxw/AgentCache/.venv-vllm-0.23.0
# Stop condition: preserve artifacts on any failure; no auto-continue

set -euo pipefail

REPO=/home/zhike/dxw/AgentCache
COMMIT=a7473e35b5c8fa5a12ecf5bdcd0b84d065a4fb38
ENV_SCRIPT="$REPO/env-main-ci-20260805.sh"
VENV="$REPO/.venv-vllm-0.23.0"
MODEL_PATH=/home/zhike/dxw/Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8
CONFIG="$REPO/agentinfer/agentbench/configs/swebench_vllm.yaml"
SHAPE="32_16"
TASK_NUM=32
MAX_CONCURRENCY=16
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BASELINE_PORT=8000
CANDIDATE_PORT=8400
TP=2
LOG_DIR="$REPO/benchkit-logs"
VLLM_TMUX_BASELINE="main-ci-baseline-vllm-$TIMESTAMP"
VLLM_TMUX_CANDIDATE="main-ci-candidate-vllm-$TIMESTAMP"

# Agent executables
CC_BIN=/home/zhike/dxw/claude-code/bin/claude
JIWEN_BIN=jiuwenswarm  # in venv PATH

# Scheduler and middleware classes
UPSTREAM_SCHEDULER=vllm.v1.core.sched.async_scheduler.AsyncScheduler
AGENTCACHE_SCHEDULER=agentinfer.agentcache.core.scheduler.AgentCacheAsyncSchedulerBridge
AGENTCACHE_IDENTITY_MIDDLEWARE=agentinfer.agentcache.core.api_adapter.AgentCacheIdentityMiddleware
AGENTCACHE_LIFECYCLE_MIDDLEWARE=agentinfer.agentcache.core.api_adapter.AgentCacheLifecycleMiddleware

# Lifecycle socket for candidate
LIFECYCLE_SOCKET="/tmp/agentinfer-main-ci-lifecycle-$TIMESTAMP.sock"
BACKEND_ID="vllm-local"
SCHEDULE_INTERVAL_SECONDS=5

SESSION_STARTED=false

# Cleanup and trap
mkdir -p "$LOG_DIR"

stop_vllm() {
  local session=$1
  if tmux has-session -t "$session" 2>/dev/null; then
    tmux kill-session -t "$session" 2>/dev/null || true
  fi
}

cleanup() {
  stop_vllm "$VLLM_TMUX_BASELINE"
  stop_vllm "$VLLM_TMUX_CANDIDATE"
  rm -f "$LIFECYCLE_SOCKET" "${LIFECYCLE_SOCKET}.dp0"
}
trap cleanup EXIT

wait_http() {
  local url=$1 attempts=$2
  for _ in $(seq 1 "$attempts"); do
    curl -fsS --max-time 2 "$url" >/dev/null 2>&1 && return 0
    sleep 2
  done
  printf 'Service did not become ready: %s\n' "$url" >&2
  return 1
}

# ── Preflight checks ──────────────────────────────────────────────

echo "=== Preflight checks ==="

# 1. Verify we're in the right repo and commit
cd "$REPO"
CURRENT_COMMIT=$(git rev-parse HEAD)
if [[ "$CURRENT_COMMIT" != "$COMMIT" ]]; then
    echo "✗ Commit mismatch: expected $COMMIT, got $CURRENT_COMMIT"
    exit 1
fi
echo "✓ Repo: $REPO @ $COMMIT"

# 2. Verify virtualenv and activate environment
if [[ ! -d "$VENV" ]]; then
    echo "✗ Virtualenv not found: $VENV"
    exit 1
fi
source "$ENV_SCRIPT"
VLLM_VERSION=$(python -c "import vllm; print(vllm.__version__)")
if [[ "$VLLM_VERSION" != "0.23.0" ]]; then
    echo "✗ vLLM version mismatch: expected 0.23.0, got $VLLM_VERSION"
    exit 1
fi
echo "✓ Virtualenv: $VENV (vLLM $VLLM_VERSION)"

# 3. Verify agentinfer import path and scheduler classes
AGENTINFER_PATH=$(python -c "import agentinfer; from pathlib import Path; print(Path(agentinfer.__file__).resolve().parent.parent)")
if [[ "$AGENTINFER_PATH" != "$REPO" ]]; then
    echo "✗ agentinfer import mismatch: expected $REPO, got $AGENTINFER_PATH"
    exit 1
fi
echo "✓ agentinfer imports from: $AGENTINFER_PATH"

# Verify scheduler and middleware classes can be imported
python - <<'PY'
from agentinfer.agentcache.core.api_adapter import AgentCacheIdentityMiddleware, AgentCacheLifecycleMiddleware
from agentinfer.agentcache.core.scheduler import AgentCacheAsyncSchedulerBridge
PY
echo "✓ Scheduler and middleware classes importable"

# 3b. Verify agent executables
if [[ ! -x "$CC_BIN" ]]; then
    echo "✗ Claude Code executable not found: $CC_BIN"
    exit 1
fi
echo "✓ Claude Code: $CC_BIN ($($CC_BIN --version 2>&1 | head -1 || echo 'ok'))"

if ! command -v "$JIWEN_BIN" >/dev/null 2>&1; then
    echo "✗ JiuwenSwarm executable not found in PATH: $JIWEN_BIN"
    exit 1
fi
echo "✓ JiuwenSwarm: $(command -v "$JIWEN_BIN")"

# 4. Verify GPUs are idle
GPU_USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | awk '{sum+=$1} END {print sum}')
if [[ "$GPU_USED" -gt 100 ]]; then
    echo "✗ GPUs not idle: ${GPU_USED} MiB used"
    exit 1
fi
echo "✓ GPUs idle (${GPU_USED} MiB used)"

# 5. Verify no conflicting tmux sessions
for session in "$VLLM_TMUX_BASELINE" "$VLLM_TMUX_CANDIDATE"; do
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "✗ Tmux session already exists: $session"
    exit 1
  fi
done
echo "✓ No conflicting tmux sessions"

# 6. Verify ports are free
for PORT in $BASELINE_PORT $CANDIDATE_PORT; do
    if ss -tuln | grep -q ":$PORT "; then
        echo "✗ Port $PORT is occupied"
        ss -tuln | grep ":$PORT "
        exit 1
    fi
done
echo "✓ Ports $BASELINE_PORT and $CANDIDATE_PORT are free"

# 7. Verify lifecycle socket doesn't exist
if [[ -e $LIFECYCLE_SOCKET || -e ${LIFECYCLE_SOCKET}.dp0 ]]; then
  printf '✗ Lifecycle socket path already exists: %s[.dp0]\n' "$LIFECYCLE_SOCKET"
  exit 1
fi
echo "✓ Lifecycle socket path clean"

# 8. Verify model path
if [[ ! -d "$MODEL_PATH" ]]; then
    echo "✗ Model not found: $MODEL_PATH"
    exit 1
fi
echo "✓ Model: $MODEL_PATH"

echo ""
echo "=== All preflight checks passed ==="
echo ""

# ── Result directories ─────────────────────────────────────────────

BASELINE_CC_RESULT="$REPO/agentinfer/agentbench/results/main-ci-baseline-cc-${SHAPE}-${TIMESTAMP}"
BASELINE_JIWEN_RESULT="$REPO/agentinfer/agentbench/results/main-ci-baseline-jiuwenswarm-${SHAPE}-${TIMESTAMP}"
CANDIDATE_CC_RESULT="$REPO/agentinfer/agentbench/results/main-ci-candidate-cc-${SHAPE}-${TIMESTAMP}"
CANDIDATE_JIWEN_RESULT="$REPO/agentinfer/agentbench/results/main-ci-candidate-jiuwenswarm-${SHAPE}-${TIMESTAMP}"

BASELINE_LOG="$LOG_DIR/main-ci-baseline-${SHAPE}-${TIMESTAMP}.log"
CANDIDATE_LOG="$LOG_DIR/main-ci-candidate-${SHAPE}-${TIMESTAMP}.log"
COMPARE_CC_LOG="$LOG_DIR/main-ci-compare-cc-${SHAPE}-${TIMESTAMP}.txt"
COMPARE_JIWEN_LOG="$LOG_DIR/main-ci-compare-jiuwenswarm-${SHAPE}-${TIMESTAMP}.txt"

echo "Baseline CC result:       $BASELINE_CC_RESULT"
echo "Baseline JiuwenSwarm:     $BASELINE_JIWEN_RESULT"
echo "Candidate CC result:      $CANDIDATE_CC_RESULT"
echo "Candidate JiuwenSwarm:    $CANDIDATE_JIWEN_RESULT"
echo "Baseline log: $BASELINE_LOG"
echo "Candidate log: $CANDIDATE_LOG"
echo ""

# ── Service lifecycle ──────────────────────────────────────────────

start_baseline() {
  echo "=== Starting baseline vLLM service ==="
  tmux new-session -d -s "$VLLM_TMUX_BASELINE" \
    "source '$ENV_SCRIPT' && vllm serve '$MODEL_PATH' --served-model-name Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 --tensor-parallel-size '$TP' --async-scheduling --scheduler-cls '$UPSTREAM_SCHEDULER' --enable-prefix-caching --enable-prompt-tokens-details --enable-auto-tool-choice --tool-call-parser qwen3_coder --port '$BASELINE_PORT' 2>&1 | tee '$BASELINE_LOG'"

  wait_http "http://127.0.0.1:$BASELINE_PORT/v1/models" 180
  echo "✓ Baseline vLLM service ready"
}

start_candidate() {
  echo "=== Starting candidate vLLM service ==="

  # Build additional-config JSON
  local additional_config
  additional_config=$(printf '{"agentcache":{"backend_id":"%s","lifecycle_socket_path":"%s","controller_factory":"agentinfer.agentcache.core.factory.build_progress_ttl_controller","schedule_interval_seconds":%s,"progress_ttl":{"ttl_prefill_seconds_per_1k_uncached_tokens":0.29,"target_min_segment_rounds":9,"target_max_segment_rounds":14,"resume_capacity_ratio":0.9,"pause_capacity_ratio":0.95,"pause_capacity_lookahead_rounds":2,"privileged_lookahead_rounds":14,"privileged_max_context_tokens":262144,"paused_program_ttl_seconds":1800}}}' "$BACKEND_ID" "$LIFECYCLE_SOCKET" "$SCHEDULE_INTERVAL_SECONDS")

  tmux new-session -d -s "$VLLM_TMUX_CANDIDATE" \
    "source '$ENV_SCRIPT' && export AGENTCACHE_VLLM_LIFECYCLE_SOCKET='$LIFECYCLE_SOCKET' && vllm serve '$MODEL_PATH' --served-model-name Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 --tensor-parallel-size '$TP' --async-scheduling --scheduler-cls '$AGENTCACHE_SCHEDULER' --middleware '$AGENTCACHE_IDENTITY_MIDDLEWARE' --middleware '$AGENTCACHE_LIFECYCLE_MIDDLEWARE' --additional-config '$additional_config' --enable-prefix-caching --enable-prompt-tokens-details --enable-auto-tool-choice --tool-call-parser qwen3_coder --port '$CANDIDATE_PORT' 2>&1 | tee '$CANDIDATE_LOG'"

  wait_http "http://127.0.0.1:$CANDIDATE_PORT/v1/models" 180

  # Verify lifecycle socket was created
  if [[ ! -S $LIFECYCLE_SOCKET && ! -S ${LIFECYCLE_SOCKET}.dp0 ]]; then
    printf '✗ Lifecycle socket was not created: %s[.dp0]\n' "$LIFECYCLE_SOCKET" >&2
    exit 2
  fi
  echo "✓ Candidate vLLM service ready (lifecycle socket created)"
}

# ── Benchmark arms ─────────────────────────────────────────────────

# run_arm: runs one benchmark arm (CC or JiuwenSwarm)
# $1 = result_dir
# $2 = port
# $3+ = extra CLI flags (e.g. --agent-type, --agent-executable, etc.)
run_arm() {
  local result_dir=$1
  local port=$2
  shift 2
  local extra_flags=("$@")

  echo "=== Running benchmark ==="
  echo "Result dir: $result_dir"
  echo "vLLM port: $port"
  echo "Extra flags: ${extra_flags[*]:-none}"

  vllm bench serve --agentinfer run \
    --config "$CONFIG" \
    --base-url "http://127.0.0.1:$port" \
    --task-num "$TASK_NUM" \
    --max-concurrency "$MAX_CONCURRENCY" \
    --timeout 14400 \
    --result-dir "$result_dir" \
    "${extra_flags[@]}"

  local EXIT_CODE=$?

  # Check manifest status
  if [[ -f "$result_dir/manifest.json" ]]; then
    MANIFEST_STATUS=$(python -c "import json; print(json.load(open('$result_dir/manifest.json'))['status'])")
    echo "Manifest status: $MANIFEST_STATUS"

    if [[ "$MANIFEST_STATUS" != "completed" ]]; then
      echo "⚠ WARNING: Manifest status is not 'completed'"
    fi
  else
    echo "⚠ WARNING: No manifest.json found"
  fi

  return $EXIT_CODE
}

# ── Main ───────────────────────────────────────────────────────────

cd "$REPO"

# ════════════════ BASELINE (port 8000) ════════════════

start_baseline

# Arm 1: Claude Code baseline
echo ""
echo "=== Arm 1/4: Baseline · Claude Code ==="
if ! run_arm "$BASELINE_CC_RESULT" "$BASELINE_PORT" \
  --agent-executable "$CC_BIN" \
  --agent-profile plan-subagent; then
  echo "✗ Baseline CC failed, stopping"
  exit 1
fi

# Arm 2: JiuwenSwarm baseline
echo ""
echo "=== Arm 2/4: Baseline · JiuwenSwarm ==="
if ! run_arm "$BASELINE_JIWEN_RESULT" "$BASELINE_PORT" \
  --agent-type jiuwenswarm \
  --agent-executable "$JIWEN_BIN" \
  --agent-profile code.normal \
  --endpoint /v1/chat/completions; then
  echo "✗ Baseline JiuwenSwarm failed, stopping"
  exit 1
fi

stop_vllm "$VLLM_TMUX_BASELINE"
echo ""

# ════════════════ CANDIDATE (port 8400) ════════════════

start_candidate

# Arm 3: Claude Code candidate
echo ""
echo "=== Arm 3/4: Candidate · Claude Code ==="
if ! run_arm "$CANDIDATE_CC_RESULT" "$CANDIDATE_PORT" \
  --agent-executable "$CC_BIN" \
  --agent-profile plan-subagent; then
  echo "✗ Candidate CC failed, stopping"
  exit 1
fi

# Arm 4: JiuwenSwarm candidate
echo ""
echo "=== Arm 4/4: Candidate · JiuwenSwarm ==="
if ! run_arm "$CANDIDATE_JIWEN_RESULT" "$CANDIDATE_PORT" \
  --agent-type jiuwenswarm \
  --agent-executable "$JIWEN_BIN" \
  --agent-profile code.normal \
  --endpoint /v1/chat/completions; then
  echo "✗ Candidate JiuwenSwarm failed, stopping"
  exit 1
fi

stop_vllm "$VLLM_TMUX_CANDIDATE"
echo ""

# ── Verify candidate scheduler activity ─────────────────────────────

echo "=== Verifying candidate scheduler activity ==="
if ! grep -q "AgentCacheAsyncSchedulerBridge" "$CANDIDATE_LOG"; then
  echo "⚠ WARNING: No AgentCacheAsyncSchedulerBridge evidence in candidate log"
fi
echo ""

# ── Compare ─────────────────────────────────────────────────────────

echo "=== Comparing CC: baseline vs candidate ==="
vllm bench serve --agentinfer compare \
  --baseline "$BASELINE_CC_RESULT" \
  --candidate "$CANDIDATE_CC_RESULT" \
  | tee "$COMPARE_CC_LOG"

echo ""
echo "=== Comparing JiuwenSwarm: baseline vs candidate ==="
vllm bench serve --agentinfer compare \
  --baseline "$BASELINE_JIWEN_RESULT" \
  --candidate "$CANDIDATE_JIWEN_RESULT" \
  | tee "$COMPARE_JIWEN_LOG"

# ── Done ────────────────────────────────────────────────────────────

echo ""
echo "=== All 4 arms completed ==="
echo "Baseline CC:          $BASELINE_CC_RESULT"
echo "Baseline JiuwenSwarm: $BASELINE_JIWEN_RESULT"
echo "Candidate CC:         $CANDIDATE_CC_RESULT"
echo "Candidate JiuwenSwarm:$CANDIDATE_JIWEN_RESULT"
echo "Baseline log:  $BASELINE_LOG"
echo "Candidate log: $CANDIDATE_LOG"
echo "Compare CC:         $COMPARE_CC_LOG"
echo "Compare JiuwenSwarm: $COMPARE_JIWEN_LOG"
echo ""
echo "Next steps:"
echo "1. CC compare:    cat $COMPARE_CC_LOG"
echo "2. JiuwenSwarm:   cat $COMPARE_JIWEN_LOG"
echo "3. Manifests:     cat $BASELINE_CC_RESULT/manifest.json $CANDIDATE_CC_RESULT/manifest.json"

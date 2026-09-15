#!/usr/bin/env bash
set -euo pipefail

run_dir="${1:-/opt/vllm-gr/results/daily/current}"
mkdir -p "$run_dir"

if pgrep -f '[v]llm-gr serve' >/dev/null; then
  echo "vllm-gr service is already running" >&2
  exit 2
fi

cd /opt/vllm-gr
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"
export LD_LIBRARY_PATH="/usr/local/cuda-13.0/compat:${LD_LIBRARY_PATH:-}"
export VLLM_SERVER_DEV_MODE=1
unset VLLM_ENABLE_V1_MULTIPROCESSING || true

exec vllm-gr serve OpenOneRec/OneRec-1.7B \
  --max-logprobs 1024 \
  --beam-max-width 1024 \
  --default-chat-template-kwargs '{"enable_thinking": false}' \
  --max-num-seqs 1024 \
  --max-num-batched-tokens 16384 \
  --scheduling-policy fcfs \
  --gpu-memory-utilization 0.90 \
  --catalog-path /opt/vllm-gr/test_profilling/video_constraint_triples.json \
  --constraint-backend constraint_table \
  >"$run_dir/server.log" 2>&1

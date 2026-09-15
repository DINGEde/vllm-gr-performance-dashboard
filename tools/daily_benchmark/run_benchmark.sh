#!/usr/bin/env bash
set -euo pipefail

run_dir="${1:?usage: run_benchmark.sh RUN_DIR NUM_PROMPTS MAX_CONCURRENCY [RESULT_FILENAME] [LOG_FILENAME] [BEAM_WIDTH] [INPUT_LENGTH]}"
num_prompts="${2:?num prompts is required}"
max_concurrency="${3:?max concurrency is required}"
result_filename="${4:-raw-result.json}"
log_filename="${5:-benchmark.log}"
beam_width="${6:-128}"
input_length="${7:-1024}"

if (( beam_width < 1 || input_length < 1 )); then
  echo "beam width and input length must be positive integers" >&2
  exit 2
fi

mkdir -p "$run_dir"
cd /opt/vllm-gr
export LD_LIBRARY_PATH="/usr/local/cuda-13.0/compat:${LD_LIBRARY_PATH:-}"
unset VLLM_ENABLE_V1_MULTIPROCESSING || true

python3 -m benchmarks.open_one_rec.one_rec_main bench serve \
  --endpoint /v1/chat/completions \
  --backend openai-chat \
  --model OpenOneRec/OneRec-1.7B \
  --dataset-name onerec \
  --dataset-path /opt/vllm-gr/data \
  --task-types video \
  --num-prompts "$num_prompts" \
  --max-concurrency "$max_concurrency" \
  --request-rate inf \
  --temperature 0 \
  --use-beam-search \
  --n "$beam_width" \
  --custom-input-len "$input_length" \
  --percentile-metrics ttft,tpot,itl,e2el \
  --metric-percentiles 50,90,95,99 \
  --save-detailed \
  --save-result \
  --result-dir "$run_dir" \
  --result-filename "$result_filename" \
  2>&1 | tee "$run_dir/$log_filename"

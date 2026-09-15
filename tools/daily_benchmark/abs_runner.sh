#!/usr/bin/env bash
set -Eeuo pipefail

# Server-side ABSOLUTE benchmark executor (no baseline/head — one commit).
# Runs the offline beam-search benchmark for a list of beam_width × input_length
# scenarios, N repeats each, on the current decode_graph tip, then aggregates a
# team-comparable matrix with abs_matrix.py. Invoked over ssh; do not run by hand.
#
# Unlike ab_runner.sh (A-B-B-A PR comparison), this forces lightweight_timing=1
# regardless of benchmark.env (which sets LIGHTWEIGHT_TIMING=0) so the full
# ~22-metric breakdown (prefill/decode/total_beam/overhead/engine_*/cpu_*) is
# captured.

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
config_file="${BENCHMARK_CONFIG:-$script_dir/benchmark.env}"
if [[ -f "$config_file" ]]; then
  # shellcheck disable=SC1090
  source "$config_file"
fi

project_dir="${PROJECT_DIR:-$(cd -- "$script_dir/../.." && pwd -P)}"
container="${CONTAINER_NAME:-vllm-gr-benchmark-gpu1}"
gpu_index="${GPU_INDEX:-1}"
host_name="${HOST_NAME:-$(hostname -s)}"
container_project_dir="${CONTAINER_PROJECT_DIR:-/opt/vllm-gr}"
container_data_dir="${CONTAINER_DATA_DIR:-$container_project_dir/data}"
model_id="${MODEL_ID:-OpenOneRec/OneRec-1.7B}"
hf_endpoint="${HF_ENDPOINT:-https://hf-mirror.com}"
num_prompts="${NUM_PROMPTS:-100}"
warmup_requests="${WARMUP_REQUESTS:-4}"
gpu_idle_limit_mib="${GPU_IDLE_LIMIT_MIB:-1024}"
# Force full timing regardless of benchmark.env's LIGHTWEIGHT_TIMING=0.
lightweight_timing=1

scenario_specs=()
repeats=2

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario) scenario_specs+=("$2"); shift 2 ;;
    --repeats) repeats="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ ${#scenario_specs[@]} -eq 0 ]]; then
  scenario_specs=("128:1024")
fi
if ! [[ "$repeats" =~ ^[0-9]+$ ]] || (( repeats < 1 )); then
  echo "repeats must be a positive integer" >&2; exit 2
fi

container_gpu_healthy() {
  timeout 10 docker exec "$container" \
    nvidia-smi --query-gpu=index,name,memory.used --format=csv,noheader >/dev/null 2>&1 &&
    timeout 20 docker exec "$container" python3 -c \
      'import sys, torch; sys.exit(0 if torch.cuda.is_available() and torch.cuda.device_count() >= 1 else 1)' \
      >/dev/null 2>&1
}

# ---- container / GPU preflight ----
if [[ "$(docker inspect -f '{{.State.Running}}' "$container")" != true ]]; then
  echo "container $container is not running" >&2
  exit 2
fi
if docker exec "$container" pgrep -f "vllm-gr serve" >/dev/null; then
  echo "an existing vllm-gr service is running; skipping" >&2
  exit 75
fi
if docker exec "$container" pgrep -f "run_offline_benchmark.py" >/dev/null; then
  echo "an existing offline benchmark is running; skipping" >&2
  exit 75
fi
# Clear any orphan VLLM::EngineCore left behind by a previously interrupted
# run (killing run_offline_benchmark.py does not kill its EngineCore child).
# Safe here: we already exited 75 above if a live benchmark is running.
docker exec "$container" pkill -9 -f "VLLM::EngineCore" >/dev/null 2>&1 || true
if ! container_gpu_healthy; then
  echo "container $container cannot access CUDA/NVML" >&2
  exit 2
fi
gpu_used="$(nvidia-smi --id="$gpu_index" --query-gpu=memory.used --format=csv,noheader,nounits | tr -d ' ')"
if (( gpu_used > gpu_idle_limit_mib )); then
  echo "GPU $gpu_index uses ${gpu_used} MiB (> ${gpu_idle_limit_mib}); skipping" >&2
  exit 75
fi

# ---- worktree must be clean (any branch is fine; we switch to decode_graph) ----
if [[ -n "$(git -C "$project_dir" status --porcelain --untracked-files=no)" ]]; then
  echo "benchmark worktree has tracked changes" >&2
  exit 2
fi

initial_branch="$(git -C "$project_dir" branch --show-current)"
# shellcheck disable=SC2064
trap 'git -C "$project_dir" checkout --quiet "$initial_branch" >/dev/null 2>&1 || true' EXIT INT TERM

# ---- checkout the current decode_graph tip ----
git -C "$project_dir" fetch origin decode_graph --quiet
sha="$(git -C "$project_dir" rev-parse origin/decode_graph)"
short="${sha:0:8}"
subject="$(git -C "$project_dir" log -1 --format=%s "$sha")"
git -C "$project_dir" checkout --quiet --detach origin/decode_graph

echo "ABS: sha=$sha scenarios=${scenario_specs[*]} repeats=$repeats gpu=$gpu_index"

# ---- ensure the dataset is present ----
docker exec "$container" python3 "$container_project_dir/tools/daily_benchmark/download_onerec_dataset.py" \
  --data-dir "$container_data_dir" --tasks video --verify-only >/dev/null

image="$(docker inspect -f '{{.Config.Image}}' "$container")"
ld_library_path=/usr/local/cuda-13.0/compat:/usr/local/nvidia/lib64:/usr/local/cuda/lib64

run_one() {
  local beam_width="$1" input_length="$2" r="$3"
  local scenario_dir="$project_dir/results/abs/$short/bw${beam_width}-in${input_length}/run$r"
  local container_scenario_dir="$container_project_dir/results/abs/$short/bw${beam_width}-in${input_length}/run$r"
  mkdir -p "$scenario_dir/cpu-timing"
  rm -f "$scenario_dir/cpu-timing"/cpu-timing-*.json

  echo "[bw$beam_width-in$input_length run$r] at $(date --iso-8601=seconds)"

  timing_env=(-e PYTHONPATH="$container_project_dir/tools/daily_benchmark/instrumentation:$container_project_dir")
  if [[ "$lightweight_timing" == 1 ]]; then
    timing_env+=(
      -e VLLM_GR_LIGHTWEIGHT_TIMING=1
      -e VLLM_GR_LIGHTWEIGHT_TIMING_DIR="$container_scenario_dir/cpu-timing"
    )
  fi
  if ! docker exec -w "$container_project_dir" "${timing_env[@]}" \
    -e HF_ENDPOINT="$hf_endpoint" -e HF_HUB_DISABLE_XET=1 \
    -e LD_LIBRARY_PATH="$ld_library_path" \
    "$container" python3 "$container_project_dir/tools/daily_benchmark/run_offline_benchmark.py" \
    --output "$container_scenario_dir/raw-result.json" \
    --data-dir "$container_data_dir" \
    --model "$model_id" \
    --num-prompts "$num_prompts" \
    --warmup-requests "$warmup_requests" \
    --beam-width "$beam_width" \
    --input-length "$input_length" \
    >"$scenario_dir/benchmark.log" 2>&1; then
    echo "[bw$beam_width-in$input_length run$r] benchmark FAILED; see $scenario_dir/benchmark.log" >&2
    return 1
  fi

  docker exec -e LD_LIBRARY_PATH="$ld_library_path" "$container" \
    python3 "$container_project_dir/tools/daily_benchmark/generate_summary.py" \
    --raw-result "$container_scenario_dir/raw-result.json" \
    --dataset-manifest "$container_data_dir/.onerec-manifest.json" \
    --output "$container_scenario_dir/vllm-gr-summary.json" \
    --run-id "abs-${short}-bw${beam_width}-in${input_length}-run${r}" \
    --warmup-requests "$warmup_requests" \
    --beam-width "$beam_width" \
    --input-length "$input_length" \
    --cpu-timing-dir "$container_scenario_dir/cpu-timing" \
    --execution-mode offline \
    --host-name "$host_name" \
    --container-name "$container" \
    --git-sha "$sha" \
    --git-subject "$subject" \
    --git-branch "decode_graph" \
    --container-image "$image"
  return 0
}

# ---- loop scenarios × repeats; a single failed run does not abort the flow ----
for spec in "${scenario_specs[@]}"; do
  IFS=: read -r beam_width input_length <<<"$spec"
  if [[ "$beam_width" -lt 1 || "$input_length" -lt 1 ]]; then
    echo "invalid scenario '$spec'" >&2; exit 2
  fi
  for ((r = 1; r <= repeats; r++)); do
    run_one "$beam_width" "$input_length" "$r" || true
  done
done

# ---- aggregate the matrix (only when ≥1 summary exists) ----
python3 "$script_dir/abs_matrix.py" \
  --root "$project_dir/results/abs/$short" \
  --output-md "$project_dir/results/abs/$short/abs-matrix.md" \
  --output-json "$project_dir/results/abs/$short/abs-matrix.json" \
  --git-sha "$sha" \
  --warmup "$warmup_requests" \
  --num-prompts "$num_prompts"

echo "ABS done: sha=$sha"

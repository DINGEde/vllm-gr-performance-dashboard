#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
config_file="${BENCHMARK_CONFIG:-$script_dir/benchmark.env}"
if [[ -f "$config_file" ]]; then
  # shellcheck disable=SC1090
  source "$config_file"
fi

default_project_dir="$(cd -- "$script_dir/../.." && pwd -P)"
project_dir="${PROJECT_DIR:-$default_project_dir}"
source_branch="decode_graph"
container="${CONTAINER_NAME:-vllm-gr-benchmark}"
host_name="${HOST_NAME:-$(hostname -s)}"
gpu_index="${GPU_INDEX:-0}"
dashboard_dir="${DASHBOARD_DIR:-}"
container_project_dir="${CONTAINER_PROJECT_DIR:-/opt/vllm-gr}"
container_data_dir="${CONTAINER_DATA_DIR:-$container_project_dir/data}"
model_id="${MODEL_ID:-OpenOneRec/OneRec-1.7B}"
hf_endpoint="${HF_ENDPOINT:-https://hf-mirror.com}"
worktree_root="${WORKTREE_ROOT:-$(dirname -- "$project_dir")/vllm-gr-worktrees}"
num_prompts="${NUM_PROMPTS:-100}"
warmup_requests="${WARMUP_REQUESTS:-4}"
max_concurrency="${MAX_CONCURRENCY:-1}"
gpu_idle_limit_mib="${GPU_IDLE_LIMIT_MIB:-1024}"
push_dashboard="${PUSH_DASHBOARD:-0}"
dry_run="${DRY_RUN:-0}"
run_tag="${RUN_TAG:-}"
lightweight_timing="${LIGHTWEIGHT_TIMING:-0}"
diagnostic_prompts="${DIAGNOSTIC_PROMPTS:-20}"
worker_diagnostic_prompts="${WORKER_DIAGNOSTIC_PROMPTS:-20}"
prefill_graph_kv_bound="${VLLM_GR_PREFILL_GRAPH_KV_BOUND:-4096}"
if [[ ! "$worker_diagnostic_prompts" =~ ^[1-9][0-9]*$ ]]; then
  echo "WORKER_DIAGNOSTIC_PROMPTS must be positive" >&2
  exit 2
fi
auto_restart_container="${AUTO_RESTART_CONTAINER:-1}"
if [[ "$lightweight_timing" != 0 && "$lightweight_timing" != 1 ]]; then
  echo "LIGHTWEIGHT_TIMING must be 0 or 1" >&2
  exit 2
fi
if [[ ! "$diagnostic_prompts" =~ ^[0-9]+$ ]]; then
  echo "DIAGNOSTIC_PROMPTS must be a non-negative integer" >&2
  exit 2
fi
if [[ ! "$prefill_graph_kv_bound" =~ ^[1-9][0-9]*$ ]]; then
  echo "VLLM_GR_PREFILL_GRAPH_KV_BOUND must be a positive integer" >&2
  exit 2
fi
if [[ "$auto_restart_container" != 0 && "$auto_restart_container" != 1 ]]; then
  echo "AUTO_RESTART_CONTAINER must be 0 or 1" >&2
  exit 2
fi

beam_widths=(64 128 256)
input_lengths=(512 2048 4096)
if [[ -n "$run_tag" && ! "$run_tag" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]]; then
  echo "RUN_TAG must match ^[a-z0-9][a-z0-9-]{0,31}$" >&2
  exit 2
fi
run_suffix="${run_tag:+-$run_tag}"
run_date="$(TZ=Asia/Shanghai date +%F)"
lock_file="$project_dir/results/daily/.daily-benchmark.lock"
offline_started=0

mkdir -p "$(dirname "$lock_file")"
exec 9>"$lock_file"
if ! flock -n 9; then
  echo "another daily benchmark owns $lock_file; skipping"
  exit 75
fi

cleanup() {
  if [[ "$offline_started" == 1 ]]; then
    docker exec "$container" pkill -TERM -f "run_offline_benchmark.py" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

container_gpu_healthy() {
  timeout 10 docker exec "$container" \
    nvidia-smi --query-gpu=index,name,memory.used --format=csv,noheader >/dev/null 2>&1 &&
    timeout 20 docker exec "$container" python3 -c \
      'import sys, torch; sys.exit(0 if torch.cuda.is_available() and torch.cuda.device_count() >= 1 else 1)' \
      >/dev/null 2>&1
}

if [[ "$(docker inspect -f '{{.State.Running}}' "$container")" != true ]]; then
  echo "container $container is not running" >&2
  exit 2
fi

device_requests="$(docker inspect -f '{{json .HostConfig.DeviceRequests}}' "$container")"
if [[ "$device_requests" != *"\"$gpu_index\""* ]]; then
  echo "container $container is not bound to GPU $gpu_index: $device_requests" >&2
  exit 2
fi

if docker exec "$container" pgrep -f "vllm-gr serve" >/dev/null; then
  echo "an existing vllm-gr service is running; skipping" >&2
  exit 75
fi
if docker exec "$container" pgrep -f "run_offline_benchmark.py" >/dev/null; then
  echo "an existing offline benchmark is running outside this runner; skipping" >&2
  exit 75
fi

if ! container_gpu_healthy; then
  if [[ "$auto_restart_container" != 1 ]]; then
    echo "container $container cannot access CUDA/NVML and AUTO_RESTART_CONTAINER=0" >&2
    exit 2
  fi
  echo "container $container failed the CUDA/NVML health check; restarting it once"
  docker restart --time 20 "$container" >/dev/null
  container_recovered=0
  for _ in $(seq 1 15); do
    if [[ "$(docker inspect -f '{{.State.Running}}' "$container")" == true ]] && container_gpu_healthy; then
      container_recovered=1
      break
    fi
    sleep 2
  done
  if [[ "$container_recovered" != 1 ]]; then
    echo "container $container still cannot access CUDA/NVML after restart" >&2
    exit 2
  fi
  echo "container $container CUDA/NVML health check recovered"
fi

gpu_used="$(nvidia-smi --id="$gpu_index" --query-gpu=memory.used --format=csv,noheader,nounits | tr -d ' ')"
if (( gpu_used > gpu_idle_limit_mib )); then
  echo "GPU $gpu_index uses ${gpu_used} MiB (> ${gpu_idle_limit_mib}); skipping"
  exit 75
fi

# The checkout may be used for experiments. Benchmark only the fetched remote
# ref and archive that exact commit, without switching branches or reading
# tracked working-tree files as runtime source.
git -C "$project_dir" fetch origin "$source_branch"
remote_ref="refs/remotes/origin/$source_branch"
git_branch="$source_branch"
git_sha="$(git -C "$project_dir" rev-parse "$remote_ref")"
git_short="${git_sha:0:8}"
git_subject="$(git -C "$project_dir" log -1 --format=%s "$remote_ref")"
if [[ "$max_concurrency" != 1 ]]; then
  echo "offline daily benchmark requires MAX_CONCURRENCY=1" >&2
  exit 2
fi
matrix_id="daily-offline-matrix-${run_date}-${git_short}-c1${run_suffix}"
matrix_dir="$project_dir/results/daily/$matrix_id"
if [[ "$dry_run" != 1 && -f "$matrix_dir/dataset-verification.json" ]]; then
  # Preserve previous attempts, including raw data and failures, on same-day reruns.
  run_suffix="${run_suffix}-$(date +%H%M%S)-$$"
  matrix_id="daily-offline-matrix-${run_date}-${git_short}-c1${run_suffix}"
  matrix_dir="$project_dir/results/daily/$matrix_id"
fi
container_matrix_dir="$container_project_dir/results/daily/$matrix_id"
mkdir -p "$matrix_dir"
source_change_file="$matrix_dir/source-change.json"
summary_root="${dashboard_dir:-$project_dir/results/daily}/runs/vllm-gr"
if [[ -z "$dashboard_dir" ]]; then
  summary_root="$project_dir/results/daily"
fi
python3 "$script_dir/collect_daily_changes.py" \
  --repo-dir "$project_dir" \
  --summary-root "$summary_root" \
  --current-sha "$git_sha" \
  --current-date "$run_date" \
  --output "$source_change_file"

scenario_specs=()
if [[ -n "${SCENARIO_SPECS:-}" ]]; then
  IFS=, read -r -a scenario_specs <<<"$SCENARIO_SPECS"
else
  for beam_width in "${beam_widths[@]}"; do scenario_specs+=("$beam_width:1024"); done
  for input_length in "${input_lengths[@]}"; do scenario_specs+=("128:$input_length"); done
fi

for spec in "${scenario_specs[@]}"; do
  if [[ ! "$spec" =~ ^(64|128|256):[1-9][0-9]*$ ]]; then
    echo "invalid scenario $spec; expected beam:input, separated by commas" >&2
    exit 2
  fi
  IFS=: read -r beam_width _ <<<"$spec"
  case "$beam_width" in
    64|128|256) ;;
    *)
      echo "unsupported beam width $beam_width; allowed values: 64, 128, 256" >&2
      exit 2
      ;;
  esac
done

docker exec "$container" python3 "$container_project_dir/tools/daily_benchmark/download_onerec_dataset.py" \
  --data-dir "$container_data_dir" --tasks video --verify-only >"$matrix_dir/dataset-verification.json"

if [[ "$dry_run" == 1 ]]; then
  echo "preflight OK: $matrix_id branch=$git_branch sha=$git_sha scenarios=${#scenario_specs[@]}"
  exit 0
fi

image="$(docker inspect -f '{{.Config.Image}}' "$container")"
python3 "$script_dir/prepare_native_source.py" --repo "$project_dir" --sha "$git_sha" --output "$matrix_dir/source"
runtime_project_dir="$container_matrix_dir/source"
image_digest="$(docker image inspect -f '{{index .RepoDigests 0}}' "$image" 2>/dev/null || true)"
summary_paths=()
failed_count=0
matrix_exit=0
status_file="$matrix_dir/scenario-status.tsv"
printf 'time\tscenario\tstage\texit_code\n' >"$status_file"
record_status() {
  printf '%s\t%s\t%s\t%s\n' "$(date --iso-8601=seconds)" "$scenario_id" "$1" "$2" >>"$status_file"
}
source_unchanged() {
  [[ "$(git -C "$project_dir" rev-parse "$remote_ref")" == "$git_sha" ]]
}
scenario_index=0
scenario_count=${#scenario_specs[@]}
for spec in "${scenario_specs[@]}"; do
  scenario_index=$((scenario_index + 1))
  IFS=: read -r beam_width input_length <<<"$spec"
  scenario_id="daily-offline-${run_date}-${git_short}-bw${beam_width}-in${input_length}-c1${run_suffix}"
  scenario_dir="$matrix_dir/$scenario_id"
  container_scenario_dir="$container_matrix_dir/$scenario_id"
  mkdir -p "$scenario_dir"
  mkdir -p "$scenario_dir/cpu-timing"
  rm -f "$scenario_dir/cpu-timing"/cpu-timing-*.json

  echo "[$scenario_index/$scenario_count] starting beam=$beam_width input=$input_length at $(date --iso-8601=seconds); live log: $scenario_dir/benchmark.log"
  record_status running 0
  if ! source_unchanged; then
    record_status source_changed 2
    failed_count=$((failed_count + 1))
    echo "source changed during matrix; stopping further scenarios and publishing only earlier successes" >&2
    break
  fi

  offline_started=1
  # Formal samples are always probe-free, even if legacy callers pass 1.
  timing_env=(-e PYTHONPATH="$runtime_project_dir" -e VLLM_GR_LIGHTWEIGHT_TIMING=0 -e VLLM_GR_LIGHTWEIGHT_TIMING_DIR= -e VLLM_GR_PREFILL_GRAPH_KV_BOUND="$prefill_graph_kv_bound")
  if docker exec -w "$container_project_dir" "${timing_env[@]}" \
    -e HF_ENDPOINT="$hf_endpoint" -e HF_HUB_DISABLE_XET=1 \
    -e LD_LIBRARY_PATH=/usr/local/cuda-13.0/compat:/usr/local/nvidia/lib64:/usr/local/cuda/lib64 \
    "$container" python3 "$container_project_dir/tools/daily_benchmark/run_offline_benchmark.py" \
    --output "$container_scenario_dir/raw-result.json" \
    --data-dir "$container_data_dir" \
    --model "$model_id" \
    --num-prompts "$num_prompts" \
    --warmup-requests "$warmup_requests" \
    --diagnostic-prompts "$diagnostic_prompts" \
    --beam-api v1 \
    --beam-width "$beam_width" \
    --input-length "$input_length" \
    >"$scenario_dir/benchmark.log" 2>&1; then
    benchmark_exit=0
  else
    benchmark_exit=$?
  fi
  offline_started=0
  if ! source_unchanged; then
    record_status source_changed 2
    failed_count=$((failed_count + 1))
    echo "source changed during scenario; discarding this scenario from publication" >&2
    break
  fi
  if (( benchmark_exit != 0 )); then
    record_status benchmark_failed "$benchmark_exit"
    failed_count=$((failed_count + 1))
    echo "[$scenario_index/$scenario_count] FAILED benchmark (exit=$benchmark_exit); see $scenario_dir/benchmark.log; continuing" >&2
    continue
  fi

  summary_args=(
    python3 "$container_project_dir/tools/daily_benchmark/generate_summary.py"
    --raw-result "$container_scenario_dir/raw-result.json"
    --dataset-manifest "$container_data_dir/.onerec-manifest.json"
    --output "$container_scenario_dir/vllm-gr-summary.json"
    --run-id "$scenario_id"
    --warmup-requests "$warmup_requests"
    --beam-width "$beam_width"
    --input-length "$input_length"
    --cpu-timing-dir "$container_scenario_dir/cpu-timing"
    --execution-mode offline
    --host-name "$host_name"
    --container-name "$container"
    --git-sha "$git_sha"
    --git-subject "$git_subject"
    --git-branch "$git_branch"
    --source-change "$container_matrix_dir/source-change.json"
    --container-image "$image"
  )
  if [[ -n "$image_digest" ]]; then summary_args+=(--container-digest "$image_digest"); fi
  if docker exec -e LD_LIBRARY_PATH=/usr/local/cuda-13.0/compat:/usr/local/nvidia/lib64:/usr/local/cuda/lib64 \
    "$container" "${summary_args[@]}" >"$scenario_dir/summary.log" 2>&1; then
    summary_exit=0
  else
    summary_exit=$?
  fi
  if (( summary_exit != 0 )) || [[ ! -s "$scenario_dir/vllm-gr-summary.json" ]]; then
    if (( summary_exit == 0 )); then summary_exit=1; fi
    record_status summary_failed "$summary_exit"
    failed_count=$((failed_count + 1))
    echo "[$scenario_index/$scenario_count] FAILED summary; raw data retained; see $scenario_dir/summary.log; continuing" >&2
    continue
  fi
  summary_paths+=("$scenario_dir/vllm-gr-summary.json")
  declare -p summary_args >"$scenario_dir/summary-command.sh"
  record_status success 0
  echo "[$scenario_index/$scenario_count] completed beam=$beam_width input=$input_length at $(date --iso-8601=seconds)"
done

# All formal samples finish before any instrumented Worker process starts.
for summary_path in "${summary_paths[@]}"; do
  scenario_dir="$(dirname "$summary_path")"
  scenario_id="$(basename "$scenario_dir")"
  container_scenario_dir="$container_matrix_dir/$scenario_id"
  if ! source_unchanged; then
    record_status worker_source_changed 2
    failed_count=$((failed_count + 1))
    break
  fi
  beam_width="${scenario_id##*-bw}"; beam_width="${beam_width%%-*}"
  input_length="${scenario_id##*-in}"; input_length="${input_length%%-*}"
  mkdir -p "$scenario_dir/worker-diagnostic/cpu-timing"
  record_status worker_running 0
  echo "Worker diagnostic: beam=$beam_width input=$input_length; separate process"
  offline_started=1
  worker_sample_count="$worker_diagnostic_prompts"
  if (( diagnostic_prompts > worker_sample_count )); then worker_sample_count="$diagnostic_prompts"; fi
  if docker exec -w "$container_project_dir" \
    -e PYTHONPATH="$container_project_dir/tools/daily_benchmark/instrumentation:$runtime_project_dir" \
    -e VLLM_GR_LIGHTWEIGHT_TIMING=1 \
    -e VLLM_GR_LIGHTWEIGHT_TIMING_DIR="$container_scenario_dir/worker-diagnostic/cpu-timing" \
    -e VLLM_GR_PREFILL_GRAPH_KV_BOUND="$prefill_graph_kv_bound" \
    -e HF_ENDPOINT="$hf_endpoint" -e HF_HUB_DISABLE_XET=1 \
    -e LD_LIBRARY_PATH=/usr/local/cuda-13.0/compat:/usr/local/nvidia/lib64:/usr/local/cuda/lib64 \
    "$container" python3 "$container_project_dir/tools/daily_benchmark/run_offline_benchmark.py" \
    --output "$container_scenario_dir/worker-diagnostic/raw-result.json" \
    --data-dir "$container_data_dir" --model "$model_id" \
    --num-prompts "$worker_sample_count" --warmup-requests "$warmup_requests" \
    --diagnostic-prompts 0 --beam-api v1 --beam-width "$beam_width" --input-length "$input_length" \
    >"$scenario_dir/worker-diagnostic/benchmark.log" 2>&1; then
    worker_exit=0
  else
    worker_exit=$?
  fi
  offline_started=0
  if (( worker_exit == 0 )) && source_unchanged; then
    source "$scenario_dir/summary-command.sh"
    if ! docker exec -w "$runtime_project_dir" -e PYTHONPATH="$runtime_project_dir" \
      -e VLLM_GR_LIGHTWEIGHT_TIMING=0 \
      -e LD_LIBRARY_PATH=/usr/local/cuda-13.0/compat:/usr/local/nvidia/lib64:/usr/local/cuda/lib64 \
      "$container" "${summary_args[@]}" \
      --raw-result "$container_scenario_dir/worker-diagnostic/raw-result.json" \
      --output "$container_scenario_dir/worker-diagnostic/summary.json" \
      >"$scenario_dir/worker-diagnostic/summary.log" 2>&1; then
      record_status worker_summary_failed 1
      failed_count=$((failed_count + 1))
      continue
    fi
    if docker exec -w "$container_project_dir" -e PYTHONPATH="$container_project_dir" \
      -e VLLM_GR_LIGHTWEIGHT_TIMING=0 \
      -e LD_LIBRARY_PATH=/usr/local/cuda-13.0/compat:/usr/local/nvidia/lib64:/usr/local/cuda/lib64 \
      "$container" python3 "$container_project_dir/tools/daily_benchmark/attach_worker_diagnostic.py" \
      --summary "$container_scenario_dir/vllm-gr-summary.json" \
      --worker-dir "$container_scenario_dir/worker-diagnostic" \
      >"$scenario_dir/worker-diagnostic/attach.log" 2>&1; then
      record_status worker_success 0
      continue
    fi
  fi
  record_status worker_failed 1
  failed_count=$((failed_count + 1))
  echo "Worker diagnostic failed for $scenario_id; formal summary retained for publication" >&2
done

if (( failed_count > 0 )); then matrix_exit=1; fi
echo "matrix results: success=${#summary_paths[@]} failed=$failed_count; status=$status_file"

if [[ "$push_dashboard" != 1 ]]; then
  echo "daily benchmark matrix complete: $matrix_id"
  echo "PUSH_DASHBOARD=0; results were kept locally at $matrix_dir"
  exit "$matrix_exit"
fi

if [[ -z "$dashboard_dir" || ! -d "$dashboard_dir/.git" ]]; then
  echo "matrix summaries ready at $matrix_dir"
  echo "dashboard repository is not configured at $dashboard_dir; not publishing"
  exit 2
fi

if (( ${#summary_paths[@]} == 0 )); then
  echo "no successful scenarios to publish; logs retained at $matrix_dir" >&2
  exit "$matrix_exit"
fi

if [[ -n "$(git -C "$dashboard_dir" status --porcelain)" ]]; then
  echo "dashboard working tree is dirty; refusing to publish" >&2
  exit 2
fi
git -C "$dashboard_dir" pull --ff-only

published_files=()
for summary_path in "${summary_paths[@]}"; do
  scenario_id="$(basename "$(dirname "$summary_path")")"
  target_dir="$dashboard_dir/runs/vllm-gr/$host_name/$run_date/$scenario_id"
  mkdir -p "$target_dir"
  cp "$summary_path" "$target_dir/vllm-gr-summary.json"
  published_files+=("$target_dir/vllm-gr-summary.json")
done

git -C "$dashboard_dir" add "${published_files[@]}"
if ! git -C "$dashboard_dir" diff --cached --quiet; then
  git -C "$dashboard_dir" commit -m "Add vllm-gr matrix benchmark $matrix_id"
fi
# Also retry an already committed but previously unsuccessful push.
git -C "$dashboard_dir" push

echo "daily benchmark matrix complete: $matrix_id"
exit "$matrix_exit"

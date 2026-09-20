#!/usr/bin/env bash
# Validate the configuration without touching the GPU, then dry-run the runner.
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
config_file="${BENCHMARK_CONFIG:-$script_dir/benchmark.env}"
if [[ ! -f "$config_file" ]]; then
  echo "missing config: $config_file" >&2
  echo "copy benchmark.env.example to benchmark.env and edit it first" >&2
  exit 2
fi

# shellcheck disable=SC1090
source "$config_file"

# PROJECT_DIR has to be explicit. daily_runner.sh falls back to "$script_dir/../.."
# when it is unset, which used to resolve to the vllm-gr checkout because these
# scripts lived inside it. They now live in the dashboard repository, so that
# fallback resolves to the dashboard clone instead: the runner would try to
# fetch the benchmark branch from it and drop a results/ directory into it,
# which in turn makes the publish step refuse to run. Catch it here rather than
# three hours into a matrix.
project_dir="${PROJECT_DIR:-}"
dashboard_root="$(cd -- "$script_dir/../.." && pwd -P)"
if [[ -z "$project_dir" ]]; then
  echo "PROJECT_DIR is not set in $config_file" >&2
  echo "it must point at the vllm-gr checkout the container bind-mounts" >&2
  exit 2
fi
if [[ "$(cd -- "$project_dir" 2>/dev/null && pwd -P)" == "$dashboard_root" ]]; then
  echo "PROJECT_DIR=$project_dir is the dashboard repository, not the vllm-gr checkout" >&2
  exit 2
fi

# SYNC_DASHBOARD=0 keeps the preflight offline and pins whatever is on disk;
# the cron path (sync_scripts.sh) is what refreshes from the repository.
BENCHMARK_CONFIG="$config_file" PUSH_DASHBOARD=0 DRY_RUN=1 SYNC_DASHBOARD=0 \
  exec /usr/bin/bash "$script_dir/sync_scripts.sh"

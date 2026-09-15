#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
config_file="${BENCHMARK_CONFIG:-$script_dir/benchmark.env}"
if [[ ! -f "$config_file" ]]; then
  echo "missing config: $config_file" >&2
  echo "copy benchmark.env.example to benchmark.env and edit it first" >&2
  exit 2
fi

BENCHMARK_CONFIG="$config_file" PUSH_DASHBOARD=0 DRY_RUN=1 \
  exec /usr/bin/bash "$script_dir/daily_runner.sh"

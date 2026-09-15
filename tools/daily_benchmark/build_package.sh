#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_dir="$(cd -- "$script_dir/../.." && pwd -P)"
output="${1:-$project_dir/dist/vllm-gr-daily-benchmark-kit.tar.gz}"

files=(
  tools/daily_benchmark/.gitignore
  tools/daily_benchmark/README.md
  tools/daily_benchmark/benchmark.env.example
  tools/daily_benchmark/build_package.sh
  tools/daily_benchmark/collect_daily_changes.py
  tools/daily_benchmark/daily_runner.sh
  tools/daily_benchmark/download_onerec_dataset.py
  tools/daily_benchmark/generate_summary.py
  tools/daily_benchmark/install_cron.sh
  tools/daily_benchmark/instrumentation/sitecustomize.py
  tools/daily_benchmark/instrumentation/vllm_gr_lightweight_timing.py
  tools/daily_benchmark/preflight.sh
  tools/daily_benchmark/print_matrix_summary.py
  tools/daily_benchmark/run_offline_benchmark.py
  tools/daily_benchmark/validate_lightweight_timing.py
)

for file in "${files[@]}"; do
  if [[ ! -f "$project_dir/$file" ]]; then
    echo "missing package file: $file" >&2
    exit 2
  fi
done

mkdir -p "$(dirname -- "$output")"
tar -C "$project_dir" -czf "$output" "${files[@]}"
(cd -- "$(dirname -- "$output")" && sha256sum "$(basename -- "$output")" >"$(basename -- "$output").sha256")
echo "$output"
cat "$output.sha256"

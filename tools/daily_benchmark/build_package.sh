#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# The checkout these scripts live in -- which is the dashboard repository, not
# the vllm-gr source tree. Everything below is addressed relative to it.
project_dir="$(cd -- "$script_dir/../.." && pwd -P)"
# Defaults outside the repository on purpose. $project_dir is the dashboard
# clone, and the publish step refuses to run while that clone has untracked
# files (daily_runner.sh checks `git status --porcelain`), so writing a kit into
# $project_dir/dist/ would silently block every future publish until someone
# noticed the stray directory.
output="${1:-${TMPDIR:-/tmp}/vllm-gr-daily-benchmark-kit.tar.gz}"

# Everything daily_runner.sh shells out to at run time has to be here, or the
# kit extracts cleanly and then dies hours in. Two files were missing from this
# list and did exactly that: prepare_native_source.py (called under `set -e`
# before the first scenario, so the whole run aborts) and
# attach_worker_diagnostic.py (called once per scenario, so every diagnostic
# stage fails). sync_scripts.sh is what the cron entry point runs.
files=(
  tools/daily_benchmark/.gitignore
  tools/daily_benchmark/README.md
  tools/daily_benchmark/attach_worker_diagnostic.py
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
  tools/daily_benchmark/prepare_native_source.py
  tools/daily_benchmark/print_matrix_summary.py
  tools/daily_benchmark/run_offline_benchmark.py
  tools/daily_benchmark/sync_scripts.sh
  tools/daily_benchmark/tests/test_attach_worker.py
  tools/daily_benchmark/tests/test_canonical_measurement.py
  tools/daily_benchmark/tests/test_partial_runner.py
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

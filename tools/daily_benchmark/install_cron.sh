#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
config_file="${BENCHMARK_CONFIG:-$script_dir/benchmark.env}"
action="${1:-install}"
begin_marker="# BEGIN vllm-gr daily benchmark (managed)"
end_marker="# END vllm-gr daily benchmark (managed)"

if [[ "$action" != install && "$action" != remove ]]; then
  echo "usage: $0 [install|remove]" >&2
  exit 2
fi
if [[ "$action" == install && ! -f "$config_file" ]]; then
  echo "missing config: $config_file" >&2
  exit 2
fi

existing="$(crontab -l 2>/dev/null || true)"
filtered="$(printf '%s\n' "$existing" | awk -v begin="$begin_marker" -v end="$end_marker" '
  $0 == begin { skip = 1; next }
  $0 == end { skip = 0; next }
  index($0, "/tools/daily_benchmark/daily_runner.sh") { next }
  !skip { print }
')"

if [[ "$action" == remove ]]; then
  printf '%s\n' "$filtered" | crontab -
  echo "removed managed vllm-gr benchmark cron entry"
  exit 0
fi

# shellcheck disable=SC1090
source "$config_file"
default_project_dir="$(cd -- "$script_dir/../.." && pwd -P)"
project_dir="${PROJECT_DIR:-$default_project_dir}"
schedule="${CRON_SCHEDULE:-30 2 * * *}"
cron_log="${CRON_LOG:-$project_dir/results/daily/cron.log}"
mkdir -p "$(dirname -- "$cron_log")"

printf -v quoted_config '%q' "$config_file"
printf -v quoted_runner '%q' "$script_dir/daily_runner.sh"
printf -v quoted_log '%q' "$cron_log"
cron_command="$schedule BENCHMARK_CONFIG=$quoted_config /usr/bin/bash $quoted_runner >> $quoted_log 2>&1"

{
  printf '%s\n' "$filtered"
  printf '%s\n' "$begin_marker" "$cron_command" "$end_marker"
} | crontab -

echo "installed managed cron entry: $schedule"
echo "log: $cron_log"

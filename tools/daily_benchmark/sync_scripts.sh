#!/usr/bin/env bash
# Cron entry point: refresh this script directory from the dashboard repository,
# then hand off to daily_runner.sh.
#
# The scripts cannot simply be run out of a clone. daily_runner.sh invokes every
# Python file as "$container_project_dir/tools/daily_benchmark/<script>.py"
# (see the docker exec calls in daily_runner.sh), and container_project_dir is
# the container-side bind mount of PROJECT_DIR. Those paths are concatenated,
# not resolved at runtime, so the scripts have to physically sit at
# $PROJECT_DIR/tools/daily_benchmark/ no matter where they were authored. The
# repository is the source; this directory is a deployment target refreshed
# from it.
#
# SYNC_DASHBOARD=0 skips the refresh and runs whatever is on disk.
set -Eeuo pipefail

# The whole body is wrapped in a function on purpose. Bash reads a script
# incrementally from disk as it executes, so a step that replaces
# sync_scripts.sh while sync_scripts.sh is running could make it execute
# garbage. Parsing a function definition forces the entire body into memory
# before the first command runs, which makes the rsync below safe to run even
# though it overwrites this very file.
main() {
  local script_dir config_file
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  config_file="${BENCHMARK_CONFIG:-$script_dir/benchmark.env}"

  if [[ -f "$config_file" ]]; then
    # shellcheck disable=SC1090
    source "$config_file"
  fi

  local project_dir="${PROJECT_DIR:-}"
  local dashboard_dir="${DASHBOARD_DIR:-}"

  # Deliberately not derived from "$script_dir/../.." the way daily_runner.sh
  # and install_cron.sh do it. That fallback only ever worked because
  # benchmark.env sets PROJECT_DIR explicitly; now that these scripts live in
  # the dashboard repository it would quietly resolve to the dashboard
  # checkout, and every path built from it would be wrong. Guessing here is
  # worse than stopping.
  if [[ -z "$project_dir" ]]; then
    echo "PROJECT_DIR is not set; set it in $config_file" >&2
    exit 2
  fi
  if [[ ! -f "$script_dir/daily_runner.sh" ]]; then
    echo "daily_runner.sh is missing from $script_dir" >&2
    exit 2
  fi

  case "${SYNC_DASHBOARD:-1}" in
    1)
      if [[ -z "$dashboard_dir" ]]; then
        echo "DASHBOARD_DIR is not set; running the scripts already on disk" >&2
      elif [[ ! -d "$dashboard_dir/tools/daily_benchmark" ]]; then
        echo "no tools/daily_benchmark under DASHBOARD_DIR=$dashboard_dir; running the scripts already on disk" >&2
      else
        refresh_scripts "$dashboard_dir" "$project_dir" "$script_dir"
      fi
      ;;
    0)
      echo "SYNC_DASHBOARD=0; running the scripts already on disk"
      ;;
    *)
      echo "SYNC_DASHBOARD must be 0 or 1" >&2
      exit 2
      ;;
  esac

  echo "daily_runner.sh: $script_dir/daily_runner.sh"
  exec /usr/bin/bash "$script_dir/daily_runner.sh" "$@"
}

refresh_scripts() {
  local dashboard_dir="$1" project_dir="$2" script_dir="$3"
  local source_dir="$dashboard_dir/tools/daily_benchmark"
  local target_dir="$project_dir/tools/daily_benchmark"
  local expected_branch="${SYNC_BRANCH:-main}"
  local -a options=(
    # Not -a: the archive form implies -o -g, and this tree contains
    # __pycache__ directories owned by root because the container runs as root
    # over the bind mount. A failed chown makes rsync exit 23, which under
    # set -e would abort the whole night's run for no reason.
    -rlptD
    # These are ~30 small files; comparing checksums costs nothing and removes
    # any chance of a same-size, same-mtime edit being skipped.
    --checksum
    --delete
    # Kept out of --delete's path too: excludes apply in both directions.
    --exclude=benchmark.env
    --exclude=__pycache__/
    --exclude=backups/
    --exclude='*.bak*'
    --exclude='*.before-*'
  )
  # Do not add --delete-excluded: it would re-attempt deleting the root-owned
  # __pycache__ trees and fail with exit 23 on every single run.

  # Refuse to deploy a branch nobody asked for. The clone this reads from is the
  # same one the runner commits its results into, so it can be left on any
  # branch by hand; syncing from whatever it happens to point at would swap the
  # toolkit revision with no record of it.
  local branch
  branch="$(git -C "$dashboard_dir" symbolic-ref --quiet --short HEAD || echo "")"
  if [[ "$branch" != "$expected_branch" ]]; then
    echo "warning: $dashboard_dir is on '${branch:-detached HEAD}', not '$expected_branch'; running the scripts already on disk" >&2
    return 0
  fi

  # --ff-only rather than reset --hard: with PUSH_DASHBOARD=1 the runner commits
  # into this same clone, and a hard reset would silently discard a commit whose
  # push failed. A refresh that cannot fast-forward is not worth losing a day's
  # benchmark over, so it warns and the run continues against the scripts
  # already on disk.
  if ! git -C "$dashboard_dir" pull --ff-only; then
    echo "warning: could not fast-forward $dashboard_dir; running the scripts already on disk" >&2
    return 0
  fi

  if [[ -n "${SYNC_DRY_RUN:-}" ]]; then
    options+=(-n -i)
    echo "SYNC_DRY_RUN: would update $target_dir from $source_dir"
    rsync "${options[@]}" "$source_dir/" "$target_dir/"
    echo "SYNC_DRY_RUN: nothing was written"
    exit 0
  fi

  # A half-updated script set is worse than a skipped day: the caller would be
  # running an unknown mix of revisions and could publish data it cannot
  # attribute. Fail loudly rather than let set -e kill the run without a reason.
  if ! rsync "${options[@]}" "$source_dir/" "$target_dir/"; then
    echo "failed to refresh $target_dir from $source_dir; not running" >&2
    exit 2
  fi
  # --delete removes files the source does not have, so a repository missing
  # daily_runner.sh would delete it here and make the exec in main() fail with a
  # bare "No such file". Say what actually happened.
  if [[ ! -f "$script_dir/daily_runner.sh" ]]; then
    echo "after refresh, $script_dir/daily_runner.sh is missing; check the source checkout" >&2
    exit 2
  fi
  echo "refreshed $target_dir from $source_dir"
}

main "$@"

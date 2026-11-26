#!/usr/bin/env bash
# Daily NPU v2 (raw.v2 protocol) benchmark runner for the vllm-gr performance
# dashboard.
#
# This runner produces the GPU-aligned v2 NPU dashboard (docs/npu-vllm-gr-v2.md)
# from tools/daily_benchmark/run_offline_benchmark_npu.py. It does NOT touch the
# legacy NPU artifacts (docs/npu-vllm-gr.md, docs/npu-vllm-gr-data.json,
# tools/daily_benchmark/npu_daily_runner.sh): those stay owned by the legacy
# runner.
#
# Steps:
#   1. git pull both repositories (best effort)
#   2. copy run_offline_benchmark_npu.py into the mounted project repo (the
#      dashboard repo is not container-mounted, so the benchmark script must
#      land inside $PROJECT_DIR before it can be invoked in the container)
#   3. run the raw.v2 miss/hit benchmark matrix inside the NPU container
#   4. archive raw results into runs/vllm-gr-npu-v2/910B1/<date>/<model>/<slug>/
#   5. build docs/npu-vllm-gr-v2-data.json + docs/npu-vllm-gr-v2.md
#   6. commit + push the v2 snapshot + runs archive so GitHub Pages republishes
#
# Usage:
#   ./npu_v2_daily_runner.sh                    # full run incl. push
#   PUSH_DASHBOARD=0 ./npu_v2_daily_runner.sh   # build snapshot only, no push
#   SCENARIO_SPECS="4:256:1024" ./npu_v2_daily_runner.sh   # rerun one scenario
#   NPU_DEVICE_ID=3 ./npu_v2_daily_runner.sh    # run on another idle NPU card
#
# Cron example (02:30 daily, after the legacy runner finishes):
#   30 2 * * * cd /home/wangwei/vllm-gr/vllm-gr-performance-dashboard/tools/daily_benchmark && ./npu_v2_daily_runner.sh >> /tmp/npu_v2_daily_runner.log 2>&1

set -u -o pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DASHBOARD_DIR="${DASHBOARD_DIR:-/home/wangwei/vllm-gr/vllm-gr-performance-dashboard}"
PROJECT_DIR="${PROJECT_DIR:-/home/wangwei/vllm-gr/vllm-gr}"   # vllm-gr repo root (host side)
CONTAINER_NAME="${CONTAINER_NAME:-vllm-gr-npu3}"
CONTAINER_WORKDIR="${CONTAINER_WORKDIR:-/home/wangwei/vllm-gr/vllm-gr}"  # repo mount point inside the container (same path as host)
MODEL_PATH="${MODEL_PATH:-/home/wangwei/vllm-gr/models/OneRec-1.7B/}"
CATALOG="${CATALOG:-/home/wangwei/vllm-gr/models/video_constraint_triples.json}"  # fixed external path (mounted into the container at the same path); survives git pull/checkout
PROMPT="${PROMPT:-tests/resources/single_one_rec_prompt.txt}"                    # project-repo relative
BENCH_SCRIPT_REL="${BENCH_SCRIPT_REL:-examples/offline_inference/beam_search/run_offline_benchmark_npu.py}"  # copy target inside the project repo
RESULTS_REL="${RESULTS_REL:-examples/offline_inference/beam_search/npu-v2-results}"
PUSH_DASHBOARD="${PUSH_DASHBOARD:-1}"
# batch:beam:input triplets; 30 scenarios = batch 1/2/4 x (beam 128/256 x input
# 128/512/1024/5120/10240), mirroring the GPU matrix (input 2048 intentionally absent).
SCENARIO_SPECS="${SCENARIO_SPECS:-1:128:128 1:128:512 1:128:1024 1:128:5120 1:128:10240 1:256:128 1:256:512 1:256:1024 1:256:5120 1:256:10240 2:128:128 2:128:512 2:128:1024 2:128:5120 2:128:10240 2:256:128 2:256:512 2:256:1024 2:256:5120 2:256:10240 4:128:128 4:128:512 4:128:1024 4:128:5120 4:128:10240 4:256:128 4:256:512 4:256:1024 4:256:5120 4:256:10240}"
NPU_DEVICE_ID="${NPU_DEVICE_ID-7}"   # exported as ASCEND_RT_VISIBLE_DEVICES inside the container; NPU_DEVICE_ID="" uses the container default (all mounted cards)
# vllm-ascend registers its own custom ops (AddRmsNormBias, ...) under this
# vendor dir; without ASCEND_CUSTOM_OPP_PATH the CANN runtime cannot find their
# tiling functions (EZ1008 "tiling function does not exist"). The path is
# container-internal (the vllm-ascend editable install), not host-side.
ASCEND_CUSTOM_OPP_PATH="${ASCEND_CUSTOM_OPP_PATH:-/vllm-workspace/vllm-ascend/vllm_ascend/_cann_ops_custom/vendors/custom_transformer}"
# OneRec is loaded as float, so the norm_quant graph fusion has no benefit and
# its pattern references a custom op whose tiling older CANN packages lack. Keep
# it off (env-gated in the benchmark) until a control run proves
# ASCEND_CUSTOM_OPP_PATH alone suffices.
NPU_FUSE_NORM_QUANT="${NPU_FUSE_NORM_QUANT:-0}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-16384}"   # passed as --max-model-len to every run
NUM_PROMPTS="${NUM_PROMPTS:-100}"         # must be a multiple of every batch size (1/2/4)
WARMUP_REQUESTS="${WARMUP_REQUESTS:-4}"
DIAGNOSTIC_PROMPTS="${DIAGNOSTIC_PROMPTS:-100}"
GENERATED_STEPS="${GENERATED_STEPS:-3}"   # decoded tokens per request (1..3)
EXECUTION="${EXECUTION:-graph}"           # beam decode ACL graph (graph) or eager fallback
PER_SCENARIO_TIMEOUT="${PER_SCENARIO_TIMEOUT:-3600}"
VLLM_GR_BRANCH="${VLLM_GR_BRANCH:-main}"  # branch the NPU benchmark code lives on

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

[ -d "$DASHBOARD_DIR/.git" ] || die "dashboard repo not found at $DASHBOARD_DIR (set DASHBOARD_DIR)"
[ -d "$PROJECT_DIR/.git" ] || die "vllm-gr repo not found at $PROJECT_DIR (set PROJECT_DIR)"
[ -f "$script_dir/run_offline_benchmark_npu.py" ] || die "benchmark script not found at $script_dir/run_offline_benchmark_npu.py"
docker inspect "$CONTAINER_NAME" >/dev/null 2>&1 \
  || die "container '$CONTAINER_NAME' is not running (start it first or set CONTAINER_NAME)"
[ -f "$CATALOG" ] || die "constraint catalog not found at $CATALOG (download video_constraint_triples.json there once; see tools/AB_Tests/README.md)"

# ---- 1. update both repositories; a pull failure never aborts the benchmark ----
if [ "${SKIP_PULL:-0}" = "1" ]; then
  log "SKIP_PULL=1 -> skip git pull, run on current local code"
else
  # The NPU benchmark code lives on VLLM_GR_BRANCH (default main). Switch first
  # so a worktree left on an old branch never silently benchmarks the wrong code.
  current_branch="$(git -C "$PROJECT_DIR" branch --show-current)"
  if [ "$current_branch" != "$VLLM_GR_BRANCH" ]; then
    log "vllm-gr on branch '$current_branch', switching to '$VLLM_GR_BRANCH'"
    git -C "$PROJECT_DIR" checkout "$VLLM_GR_BRANCH" || log "WARN: checkout $VLLM_GR_BRANCH failed, staying on '$current_branch'"
  fi
  log "git pull $PROJECT_DIR"
  git -C "$PROJECT_DIR" pull --ff-only || log "WARN: vllm-gr pull failed, keeping local state"
  log "git pull $DASHBOARD_DIR"
  git -C "$DASHBOARD_DIR" pull --ff-only || log "WARN: dashboard pull failed, keeping local state"
fi

# Capture the exact vllm-gr revision being benchmarked (host-side, after pull)
# so the raw.v2 results can record git provenance and the merged-PR delta.
git_sha="$(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null || true)"
git_subject="$(git -C "$PROJECT_DIR" log -1 --format=%s "$git_sha" 2>/dev/null || true)"
git_branch="$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
[ -n "$git_branch" ] || git_branch="$VLLM_GR_BRANCH"

# ---- 2. stage the benchmark script into the mounted project repo ----
# The benchmark script lives in the dashboard repo, which is not mounted inside
# the container. Copy it next to the legacy offline_beam_search_v1.py so the
# container can invoke it through the shared $PROJECT_DIR mount.
mkdir -p "$PROJECT_DIR/$(dirname "$BENCH_SCRIPT_REL")"
cp "$script_dir/run_offline_benchmark_npu.py" "$PROJECT_DIR/$BENCH_SCRIPT_REL" \
  || die "failed to copy run_offline_benchmark_npu.py into $PROJECT_DIR"
log "staged benchmark script at $PROJECT_DIR/$BENCH_SCRIPT_REL"

run_date="$(date +%F)"
mkdir -p "$PROJECT_DIR/$RESULTS_REL"

# Merged-PR delta vs. the previous successful NPU v2 snapshot (best-effort).
source_change_file="$(mktemp)"
if [ -z "$git_sha" ] || ! python3 "$script_dir/collect_npu_v2_changes.py" \
     --repo-dir "$PROJECT_DIR" \
     --summary-root "$DASHBOARD_DIR/runs/vllm-gr-npu-v2/910B1" \
     --current-sha "$git_sha" \
     --current-date "$run_date" \
     --branch "$git_branch" \
     --repository-url "${VLLM_GR_REPO_URL:-https://github.com/vllm-project/vllm-gr}" \
     --output "$source_change_file"; then
  log "WARN: source-change computation failed; PR tracking disabled for this run"
  rm -f "$source_change_file"
  source_change_file=""
fi
dev_export=""
[ -n "$NPU_DEVICE_ID" ] && dev_export="export ASCEND_RT_VISIBLE_DEVICES='$NPU_DEVICE_ID' && "
env_export="export ASCEND_CUSTOM_OPP_PATH='$ASCEND_CUSTOM_OPP_PATH' && export NPU_FUSE_NORM_QUANT='$NPU_FUSE_NORM_QUANT' && "
log "NPU device: ${NPU_DEVICE_ID:-<container default>}"

# ---- 3. benchmark matrix inside the container; archive successes inline ----
failed=()
succeeded=0
for spec in $SCENARIO_SPECS; do
  batch="${spec%%:*}"; rest="${spec#*:}"
  beam="${rest%%:*}"; input="${rest##*:}"
  model_tag="$(basename "${MODEL_PATH%/}")"
  slug="b${batch}-bw${beam}-in${input}"                      # scenario_key the dashboard indexes on
  name="${model_tag}_batch${batch}_beam${beam}_input${input}" # matches the GPU result naming style
  log "=== scenario $spec -> $slug"
  mkdir -p "$PROJECT_DIR/$RESULTS_REL/$name"
  if timeout "$PER_SCENARIO_TIMEOUT" docker exec "$CONTAINER_NAME" bash -lc \
    "${dev_export}${env_export}cd '$CONTAINER_WORKDIR' && python '$BENCH_SCRIPT_REL' --output '$RESULTS_REL/$name/vllm-gr-npu-v2.json' --model '$MODEL_PATH' --catalog '$CATALOG' --prompt '$PROMPT' --beam-width $beam --input-length $input --batch-size $batch --max-model-len $MAX_MODEL_LEN --generated-steps $GENERATED_STEPS --execution $EXECUTION --num-prompts $NUM_PROMPTS --warmup-requests $WARMUP_REQUESTS --diagnostic-prompts $DIAGNOSTIC_PROMPTS > '$RESULTS_REL/$name/benchmark.log' 2>&1"; then
    log "ok: $slug"
    succeeded=$((succeeded + 1))
  else
    log "FAILED: $slug (see $PROJECT_DIR/$RESULTS_REL/$name/benchmark.log)"
    failed+=("$spec")
    continue
  fi

  # ---- 4a. archive the raw.v2 result into runs/vllm-gr-npu-v2 (GPU-style) ----
  archive_dir="$DASHBOARD_DIR/runs/vllm-gr-npu-v2/910B1/$run_date/$model_tag/$slug"
  mkdir -p "$archive_dir"
  cp "$PROJECT_DIR/$RESULTS_REL/$name/vllm-gr-npu-v2.json" "$archive_dir/vllm-gr-npu-v2.json" \
    || die "archive copy failed for $slug"
done
log "matrix done: $succeeded ok, ${#failed[@]} failed"

# ---- 4b. enrich today's archived results with git provenance + merged PRs ----
if [ -n "$source_change_file" ] && [ -f "$source_change_file" ]; then
  python3 - "$git_sha" "$git_subject" "$git_branch" "$source_change_file" "$DASHBOARD_DIR/runs/vllm-gr-npu-v2/910B1/$run_date" <<'PY'
import glob, json, sys
_, git_sha, git_subject, git_branch, change_file, today_root = sys.argv
change = json.load(open(change_file, encoding="utf-8"))
files = sorted(glob.glob(today_root + "/**/vllm-gr-npu-v2.json", recursive=True))
for path in files:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data["source"] = {
        "repository": "vllm-gr",
        "branch": git_branch,
        "git_sha": git_sha,
        "git_subject": git_subject,
        "change_since_previous": change,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
print("enriched %d archived v2 result(s) with git metadata + merged PRs" % len(files))
PY
  log "enriched today's v2 archive with git metadata"
else
  log "WARN: no source-change file; archived v2 results keep no git metadata"
fi

# ---- 5. build the v2 snapshot + page (host side; results + archive are mounts) ----
log "building v2 snapshot and page"
python3 "$DASHBOARD_DIR/scripts/build_npu_v2_dashboard.py" \
  --source "$PROJECT_DIR/$RESULTS_REL" "$DASHBOARD_DIR/runs/vllm-gr-npu-v2/910B1" \
  --output "$DASHBOARD_DIR/docs" || die "snapshot build failed"

python3 - "$DASHBOARD_DIR/docs/npu-vllm-gr-v2-data.json" <<'PY' || die "snapshot validation failed"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"v2 snapshot ok: {len(data['runs'])} scenario-day points, {len(data['scenarios'])} scenarios")
PY

# ---- 6. publish the v2 snapshot + runs archive; CI rebuilds GitHub Pages ----
if [ "$PUSH_DASHBOARD" = "1" ]; then
  cd "$DASHBOARD_DIR" || die "cannot cd $DASHBOARD_DIR"
  pending="$(git status --porcelain -- docs/npu-vllm-gr-v2-data.json docs/npu-vllm-gr-v2.md runs/vllm-gr-npu-v2)"
  if [ -z "$pending" ]; then
    log "v2 snapshot unchanged, nothing to push"
  else
    git add docs/npu-vllm-gr-v2-data.json docs/npu-vllm-gr-v2.md runs/vllm-gr-npu-v2
    git commit -m "Add NPU v2 daily benchmark $(date +%F)" || die "git commit failed"
    # The remote main is actively shared; sync onto the fresh remote BEFORE
    # pushing (must run after the commit above so nothing blocks the rebase).
    git pull --rebase --autostash origin main || die "git pull --rebase failed (resolve manually)"
    # Retry loop kept as a safety net for pushes racing with remote activity.
    pushed=0
    for attempt in 1 2 3; do
      if git push origin main; then
        pushed=1
        break
      fi
      log "push rejected (attempt $attempt/3); rebasing onto origin/main and retrying"
      git fetch origin main || die "git fetch failed"
      git rebase --autostash origin/main || die "git rebase onto origin/main failed (resolve manually)"
    done
    [ "$pushed" = "1" ] || die "git push failed after 3 attempts (check credentials / network)"
    log "pushed; GitHub Actions will republish the page in 1-2 minutes"
  fi
else
  log "PUSH_DASHBOARD=0, v2 snapshot kept local"
fi

if [ "${#failed[@]}" -gt 0 ]; then
  log "WARNING: scenarios failed: ${failed[*]-}"
  exit 1
fi
log "all done"

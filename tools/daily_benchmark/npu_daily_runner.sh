#!/usr/bin/env bash
# Daily NPU (Ascend 910B1) benchmark runner for the vllm-gr performance dashboard.
#
# Steps:
#   1. git pull both repositories (best effort)
#   2. run the offline beam-search benchmark matrix inside the NPU container
#   3. archive raw results into runs/vllm-gr-npu/910B1/<date>/ (GPU-style on-repo storage)
#   4. merge new results into docs/npu-vllm-gr-data.json (30-day retention)
#   5. commit + push the snapshot + runs archive so GitHub Pages republishes
#
# Usage:
#   ./npu_daily_runner.sh                     # full run incl. push
#   PUSH_DASHBOARD=0 ./npu_daily_runner.sh    # merge snapshot only, no push
#   SCENARIO_SPECS="4:256:1024" ./npu_daily_runner.sh   # rerun a single scenario
#   NPU_DEVICE_ID=3 ./npu_daily_runner.sh   # run on another idle NPU card
#
# Cron example (02:00 daily):
#   0 2 * * * cd /home/wangwei/vllm-gr/vllm-gr-performance-dashboard/tools/daily_benchmark && ./npu_daily_runner.sh >> /tmp/npu_daily_runner.log 2>&1

set -u -o pipefail

DASHBOARD_DIR="${DASHBOARD_DIR:-/home/wangwei/vllm-gr/vllm-gr-performance-dashboard}"
PROJECT_DIR="${PROJECT_DIR:-/home/wangwei/vllm-gr/vllm-gr}"   # vllm-gr repo root (host side)
CONTAINER_NAME="${CONTAINER_NAME:-vllm-gr-npu2}"
CONTAINER_WORKDIR="${CONTAINER_WORKDIR:-/home/wangwei/vllm-gr/vllm-gr}"  # repo mount point inside the container (same path as host)
MODEL_PATH="${MODEL_PATH:-/home/wangwei/vllm-gr/models/OneRec-1.7B/}"
RESULTS_REL="${RESULTS_REL:-examples/offline_inference/beam_search/test-results}"
PUSH_DASHBOARD="${PUSH_DASHBOARD:-1}"
# batch:beam:input triplets; 30 scenarios = batch 1/2/4 x (beam 128/256 x input
# 128/512/1024/5120/10240), mirroring the GPU matrix (input 2048 intentionally absent).
SCENARIO_SPECS="${SCENARIO_SPECS:-1:128:128 1:128:512 1:128:1024 1:128:5120 1:128:10240 1:256:128 1:256:512 1:256:1024 1:256:5120 1:256:10240 2:128:128 2:128:512 2:128:1024 2:128:5120 2:128:10240 2:256:128 2:256:512 2:256:1024 2:256:5120 2:256:10240 4:128:128 4:128:512 4:128:1024 4:128:5120 4:128:10240 4:256:128 4:256:512 4:256:1024 4:256:5120 4:256:10240}"
NPU_DEVICE_ID="${NPU_DEVICE_ID-7}"   # exported as ASCEND_RT_VISIBLE_DEVICES inside the container; NPU_DEVICE_ID="" uses the container default (all mounted cards)
MAX_MODEL_LEN="${MAX_MODEL_LEN:-16384}"   # passed as --max-model-len to every run
PER_SCENARIO_TIMEOUT="${PER_SCENARIO_TIMEOUT:-3600}"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

[ -d "$DASHBOARD_DIR/.git" ] || die "dashboard repo not found at $DASHBOARD_DIR (set DASHBOARD_DIR)"
[ -d "$PROJECT_DIR/.git" ] || die "vllm-gr repo not found at $PROJECT_DIR (set PROJECT_DIR)"
docker inspect "$CONTAINER_NAME" >/dev/null 2>&1 \
  || die "container '$CONTAINER_NAME' is not running (start it first or set CONTAINER_NAME)"

# ---- 1. update both repositories; a pull failure never aborts the benchmark ----
if [ "${SKIP_PULL:-0}" = "1" ]; then
  log "SKIP_PULL=1 -> skip git pull, run on current local code"
else
  log "git pull $PROJECT_DIR"
  git -C "$PROJECT_DIR" pull --ff-only || log "WARN: vllm-gr pull failed, keeping local state"
  log "git pull $DASHBOARD_DIR"
  git -C "$DASHBOARD_DIR" pull --ff-only || log "WARN: dashboard pull failed, keeping local state"
fi

# ---- 2. benchmark matrix inside the container ----
mkdir -p "$PROJECT_DIR/$RESULTS_REL"
dev_export=""
[ -n "$NPU_DEVICE_ID" ] && dev_export="export ASCEND_RT_VISIBLE_DEVICES='$NPU_DEVICE_ID' && "
log "NPU device: ${NPU_DEVICE_ID:-<container default>}"
failed=()
succeeded=0
for spec in $SCENARIO_SPECS; do
  batch="${spec%%:*}"; rest="${spec#*:}"
  beam="${rest%%:*}"; input="${rest##*:}"
  model_tag="$(basename "${MODEL_PATH%/}")"
  name="${model_tag}_batch${batch}_beam${beam}_input${input}"   # e.g. OneRec-1.7B_batch4_beam256_input5120, matching the GPU naming style
  log "=== scenario $spec -> $name"
  if timeout "$PER_SCENARIO_TIMEOUT" docker exec "$CONTAINER_NAME" bash -lc \
    "${dev_export}cd '$CONTAINER_WORKDIR' && python examples/offline_inference/beam_search/offline_beam_search_v1.py --model '$MODEL_PATH' --catalog tools/AB_Tests/constraints/video_constraint_triples.json --prompt tests/resources/single_one_rec_prompt.txt --constraint-backend npu --execution graph --batch-size $batch --beam-width $beam --input-len $input --max-model-len $MAX_MODEL_LEN --repeat 5 --warmup-runs 1 --output-json '$RESULTS_REL/$name.json' > '$RESULTS_REL/$name.log' 2>&1"; then
    log "ok: $name"
    succeeded=$((succeeded + 1))
  else
    log "FAILED: $name (see $PROJECT_DIR/$RESULTS_REL/$name.log)"
    failed+=("$spec")
  fi
done
log "matrix done: $succeeded ok, ${#failed[@]} failed"

# ---- 3. archive raw results into runs/vllm-gr-npu (GPU-style, one run dir per scenario) ----
log "archiving raw results into runs/vllm-gr-npu"
python3 "$DASHBOARD_DIR/scripts/build_npu_run_summary.py" \
  --source "$PROJECT_DIR/$RESULTS_REL" \
  --repo "$PROJECT_DIR" \
  --output "$DASHBOARD_DIR" \
  --device "${NPU_DEVICE_ID:-}" || die "runs archive failed"

# ---- 4. merge into the dashboard snapshot (host side; runs are on the mount) ----
log "merging results into snapshot"
python3 "$DASHBOARD_DIR/scripts/build_npu_dashboard.py" \
  --source "$PROJECT_DIR/$RESULTS_REL" "$DASHBOARD_DIR/runs/vllm-gr-npu/910B1" \
  --output "$DASHBOARD_DIR/docs" || die "snapshot merge failed"

python3 - "$DASHBOARD_DIR/docs/npu-vllm-gr-data.json" <<'PY' || die "snapshot validation failed"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"snapshot ok: {len(data['runs'])} scenario-day points, {len(data['scenarios'])} scenarios")
PY

# ---- 5. publish the snapshot + runs archive; CI rebuilds GitHub Pages ----
if [ "$PUSH_DASHBOARD" = "1" ]; then
  cd "$DASHBOARD_DIR" || die "cannot cd $DASHBOARD_DIR"
  # NOTE: intentionally push only the legacy snapshot for now; the v2 snapshot
  # (raw.v2 protocol) stays local until the v2 dashboard is validated.
  pending="$(git status --porcelain -- docs/npu-vllm-gr-data.json runs/vllm-gr-npu)"
  if [ -z "$pending" ]; then
    log "snapshot unchanged, nothing to push"
  else
    git add docs/npu-vllm-gr-data.json runs/vllm-gr-npu
    git commit -m "Add NPU daily benchmark $(date +%F)" || die "git commit failed"
    # The remote main is actively shared; push may be rejected (non-fast-forward).
    # Auto-rebase onto the fresh remote and retry, up to 3 attempts total.
    pushed=0
    for attempt in 1 2 3; do
      if git push origin main; then
        pushed=1
        break
      fi
      log "push rejected (attempt $attempt/3); rebasing onto origin/main and retrying"
      git fetch origin main || die "git fetch failed"
      git rebase origin/main || die "git rebase onto origin/main failed (resolve manually)"
    done
    [ "$pushed" = "1" ] || die "git push failed after 3 attempts (check credentials / network)"
    log "pushed; GitHub Actions will republish the page in 1-2 minutes"
  fi
else
  log "PUSH_DASHBOARD=0, snapshot kept local"
fi

if [ "${#failed[@]}" -gt 0 ]; then
  log "WARNING: scenarios failed: ${failed[*]-}"
  exit 1
fi
log "all done"

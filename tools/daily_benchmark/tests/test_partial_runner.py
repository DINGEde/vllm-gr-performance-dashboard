"""Exercise the actual runner's matrix/publish shell code with fake Docker/Git."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "daily_runner.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH or os.name == "nt", reason="requires POSIX bash")


def run_section(tmp_path, failure, specs):
    """Run the runner's matrix/publish section against fake Docker and Git."""
    section = RUNNER.read_text().split('image="$(docker inspect', 1)[1]
    section = 'image="$(docker inspect' + section
    preamble = r'''
set -Eeuo pipefail
matrix_dir="$TEST_ROOT/matrix"
container_matrix_dir="$matrix_dir"
dashboard_dir="$TEST_ROOT/dashboard"
mkdir -p "$matrix_dir" "$dashboard_dir/.git"
scenario_specs=(__SPECS__)
container=fake
container_project_dir="$TEST_ROOT"
container_data_dir="$TEST_ROOT"
run_date=2026-09-08
git_short=abcdef12
git_sha=abcdef12
git_subject='Example change (#123)'
git_branch=decode_graph
source_branch=decode_graph
project_dir="$TEST_ROOT"
script_dir="$TEST_ROOT"
remote_ref=refs/remotes/origin/decode_graph
run_suffix=''
matrix_id=test-matrix
lightweight_timing=0
hf_endpoint=unused
model_id=unused
num_prompts=100
warmup_requests=4
diagnostic_prompts=20
worker_diagnostic_prompts=20
prefill_graph_kv_bound=4096
host_name=L20
push_dashboard=1
python3() { mkdir -p "$matrix_dir/source"; }
docker() {
  if [[ "$1" == inspect || "$1" == image ]]; then echo fake; return 0; fi
  if [[ "$*" == *run_offline_benchmark.py* ]]; then
    printf '%s\n' "$*" >>"$TEST_ROOT/docker-calls"
    if [[ "$FAILURE" == worker && "$*" == *worker-diagnostic* ]]; then return 19; fi
    if [[ "$FAILURE" == all || ( "$FAILURE" == benchmark && "$beam_width" == 128 ) ]]; then return 17; fi
    printf '{}' >"$scenario_dir/raw-result.json"
  elif [[ "$*" == *generate_summary.py* ]]; then
    if [[ "$FAILURE" == summary && "$beam_width" == 128 ]]; then echo 'missing --git-subject' >&2; return 2; fi
    printf '{}' >"$scenario_dir/vllm-gr-summary.json"
  fi
}
git() {
  printf '%s\n' "$*" >>"$TEST_ROOT/git-calls"
  if [[ "$*" == *'branch --show-current'* ]]; then echo decode_graph; fi
  if [[ "$*" == *'rev-parse'* ]]; then echo abcdef12; fi
  if [[ "$*" == *'diff --cached --quiet'* ]]; then return 1; fi
  return 0
}
'''.replace("__SPECS__", specs)
    env = dict(os.environ, TEST_ROOT=str(tmp_path), FAILURE=failure)
    return subprocess.run([BASH, "-c", preamble + section], env=env, capture_output=True, text=True)


@pytest.mark.parametrize("failure,expected,exit_code", [
    ("benchmark", 2, 1), ("summary", 2, 1), ("all", 0, 1), ("none", 3, 0),
    ("worker", 3, 1),
])
def test_partial_publication(tmp_path, failure, expected, exit_code):
    result = run_section(tmp_path, failure, "64:1024 128:1024 256:1024")
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert len(list((tmp_path / "dashboard").rglob("vllm-gr-summary.json"))) == expected
    status = (tmp_path / "matrix/scenario-status.tsv").read_text()
    assert status.count("\tsuccess\t") == expected
    assert status.count("\trunning\t") == 3
    if expected:
        assert " push" in (tmp_path / "git-calls").read_text()
    if failure == "summary":
        assert len(list((tmp_path / "matrix").rglob("raw-result.json"))) == 3
    calls = (tmp_path / "docker-calls").read_text().splitlines()
    assert all("VLLM_GR_LIGHTWEIGHT_TIMING=0" in c for c in calls[:3])
    assert all("VLLM_GR_LIGHTWEIGHT_TIMING=1" in c for c in calls[3:])


def test_batched_scenario_reports_batch_and_skips_worker(tmp_path):
    """A batch above 1 keeps its own row and needs no Worker CPU-timing pass."""
    result = run_section(tmp_path, "none", "128:1024:4 128:2048")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(list((tmp_path / "dashboard").rglob("vllm-gr-summary.json"))) == 2

    status = (tmp_path / "matrix/scenario-status.tsv").read_text()
    assert status.count("\tsuccess\t") == 2
    assert status.count("\trunning\t") == 2
    assert status.count("\tworker_running\t") == 1
    assert status.count("\tworker_skipped\t") == 1

    dirs = sorted(item.name for item in (tmp_path / "matrix").iterdir() if item.is_dir())
    assert "daily-offline-2026-09-08-abcdef12-bw128-in1024-b4-c1" in dirs
    # An explicit batch of 1 is the historical single-request row: same id, no
    # extra token, so its existing history and trend line stay attached.
    assert "daily-offline-2026-09-08-abcdef12-bw128-in2048-c1" in dirs

    calls = (tmp_path / "docker-calls").read_text().splitlines()
    assert len(calls) == 3, calls
    assert calls[0].endswith("--batch-size 4"), calls[0]
    assert calls[1].endswith("--batch-size 1"), calls[1]
    assert "worker-diagnostic" in calls[2]
    assert "run_offline_benchmark.py" in calls[2]

#!/usr/bin/env python3
"""Archive raw NPU benchmark results into GPU-style runs/ directories.

Converts the per-scenario JSON files produced by ``offline_beam_search_v1.py``
(``OneRec-1.7B_batch4_beam256_input5120.json`` style, kept in the vllm-gr repo's
``test-results/``) into the on-repo layout used by the GPU dashboard:

    runs/vllm-gr-npu/910B1/<YYYY-MM-DD>/<run-slug>/vllm-gr-summary.json

one run directory per scenario per day. Each summary wraps the raw benchmark
JSON verbatim inside a ``vllm-gr-npu.daily.v1`` envelope with run/source/
environment/scenario metadata, so ``build_npu_dashboard.py`` can rebuild the
snapshot purely from repository contents.

The transformation is idempotent: re-running over the same inputs overwrites
the same summaries in place.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SUMMARY_SCHEMA = "vllm-gr-npu.daily.v1"
SUMMARY_NAME = "vllm-gr-summary.json"
HARDWARE_DEFAULT = "910B1"
RUNS_SUBDIR = "runs/vllm-gr-npu"

# OneRec-1.7B_batch4_beam256_input5120.json — the suffix-free daily naming.
# Legacy ``..._1.7b.json`` and ``bench_*.json`` files do not match because the
# input segment must be pure digits directly before ".json".
FILENAME_RE = re.compile(
    r"^(?P<model>.+)_batch(?P<batch>\d+)_beam(?P<beam>\d+)_input(?P<input>\d+)\.json$"
)
TIMESTAMP_KEYS = ("timestamp", "generated_at", "started_at", "finished_at")


def git_info(repo: Path) -> tuple[str, str]:
    """Return (full sha, branch) of the vllm-gr repo, tolerating failures."""
    sha, branch = "unknown", "unknown"
    try:
        sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"warning: cannot read git info from {repo}: {exc}", file=sys.stderr)
    return sha, branch


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def timestamp_of(data: dict[str, Any], mtime: float, key: str) -> str:
    """Prefer an explicit timestamp inside the raw JSON; fall back to mtime."""
    value = data.get(key)
    if isinstance(value, str) and value:
        return value
    for candidate in TIMESTAMP_KEYS:
        value = data.get(candidate)
        if isinstance(value, str) and value:
            return value
    return iso(mtime)


def build_summary(
    data: dict[str, Any],
    *,
    slug: str,
    date: str,
    started_at: str,
    finished_at: str,
    sha: str,
    branch: str,
    host: str,
    hardware: str,
    device: str | None,
    batch: int,
    beam: int,
    input_len: int,
) -> dict[str, Any]:
    scenario = {
        "batch": batch,
        "beam_width": beam,
        "input_length": input_len,
        "key": f"b{batch}-bw{beam}-in{input_len}",
    }
    environment: dict[str, Any] = {"host": host, "hardware": hardware}
    if device is not None and device != "":
        environment["device_id"] = device
    return {
        "schema_version": SUMMARY_SCHEMA,
        "run": {
            "id": slug,
            "date": date,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": "success",
            "trend_eligible": True,
        },
        "source": {"repository": "vllm-gr", "git_sha": sha, "branch": branch},
        "environment": environment,
        "scenario": scenario,
        "benchmark": data,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path,
                        help="directory with raw OneRec-1.7B_batch*_beam*_input*.json results")
    parser.add_argument("--repo", type=Path, default=None,
                        help="vllm-gr repo root for git sha/branch metadata")
    parser.add_argument("--output", required=True, type=Path,
                        help="dashboard repo root; summaries land in <output>/runs/vllm-gr-npu")
    parser.add_argument("--date", default=None,
                        help="override the run date (YYYY-MM-DD); default is each file's mtime date")
    parser.add_argument("--device", default=None, help="NPU device id used for the run")
    parser.add_argument("--hardware", default=HARDWARE_DEFAULT)
    args = parser.parse_args()

    if not args.source.is_dir():
        raise SystemExit(f"source directory not found: {args.source}")
    sha, branch = ("unknown", "unknown")
    if args.repo is not None and args.repo.is_dir():
        sha, branch = git_info(args.repo)

    host = socket.gethostname()
    written = 0
    for path in sorted(args.source.glob("*.json")):
        match = FILENAME_RE.match(path.name)
        if match is None:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warning: skip unreadable {path.name}: {exc}", file=sys.stderr)
            continue
        if not (isinstance(data, dict) and isinstance(data.get("batch_latency_ms"), dict)):
            print(f"warning: skip non-benchmark JSON {path.name}", file=sys.stderr)
            continue

        mtime = path.stat().st_mtime
        date = args.date or datetime.fromtimestamp(mtime).date().isoformat()
        batch = int(match.group("batch"))
        beam = int(match.group("beam"))
        input_len = int(match.group("input"))
        sha8 = sha[:8] if sha != "unknown" else "unknown0"
        slug = f"daily-offline-{date}-{sha8}-b{batch}-bw{beam}-in{input_len}-c1"
        started = timestamp_of(data, mtime, "started_at")
        finished = timestamp_of(data, mtime, "finished_at")

        summary = build_summary(
            data, slug=slug, date=date, started_at=started, finished_at=finished,
            sha=sha, branch=branch, host=host, hardware=args.hardware,
            device=args.device, batch=batch, beam=beam, input_len=input_len,
        )
        target = args.output / RUNS_SUBDIR / args.hardware / date / slug / SUMMARY_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written += 1

    print(f"wrote {written} run summaries under {args.output / RUNS_SUBDIR / args.hardware}")


if __name__ == "__main__":
    main()

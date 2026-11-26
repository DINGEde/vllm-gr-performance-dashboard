#!/usr/bin/env python3
"""Describe commits and merged PRs since the previous successful NPU v2 run.

Mirrors ``collect_daily_changes.py`` (the GPU daily collector), but locates the
baseline revision in the NPU v2 raw.v2 archive
(``runs/vllm-gr-npu-v2/910B1/<date>/<model>/<slug>/vllm-gr-npu-v2.json``). Once
``npu_v2_daily_runner.sh`` enriches those files with their ``source`` block
(git_sha / branch / change_since_previous), this script reads the latest
pre-today ``source.git_sha`` on the same branch and diffs it against the current
HEAD using the shared ``diff_pull_requests`` helper.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from collect_daily_changes import diff_pull_requests

SUMMARY_FILENAME = "vllm-gr-npu-v2.json"


def _finished_at(data: dict[str, Any]) -> datetime | None:
    raw = data.get("finished_at")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def previous_npu_v2_sha(summary_root: Path, current_date: str, branch: str) -> str | None:
    """Return the latest pre-today NPU v2 ``source.git_sha`` on ``branch``."""
    if not summary_root.is_dir():
        return None
    candidates: list[tuple[datetime, str]] = []
    for path in summary_root.rglob(SUMMARY_FILENAME):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source = data.get("source")
        if not isinstance(source, dict):
            continue
        sha = source.get("git_sha")
        if not sha or source.get("branch") != branch:
            continue
        finished = _finished_at(data)
        if finished is None:
            continue
        if finished.date().isoformat() >= current_date:
            continue
        candidates.append((finished, str(sha)))
    return max(candidates)[1] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-dir", type=Path, required=True)
    parser.add_argument("--summary-root", type=Path, required=True)
    parser.add_argument("--current-sha", required=True)
    parser.add_argument("--current-date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--branch",
        default="main",
        help="Branch the runner benchmarks; only matching archived results count as the baseline.",
    )
    parser.add_argument(
        "--repository-url",
        default="https://github.com/vllm-project/vllm-gr",
    )
    args = parser.parse_args()

    previous_sha = previous_npu_v2_sha(args.summary_root, args.current_date, args.branch)
    payload = diff_pull_requests(args.repo_dir, previous_sha, args.current_sha, args.repository_url)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
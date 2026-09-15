#!/usr/bin/env python3
"""Describe commits and merged PRs since the previous successful daily run."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path


PR_PATTERN = re.compile(r"\(#(?P<number>\d+)\)")


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def previous_successful_sha(summary_root: Path, current_date: str) -> str | None:
    candidates: list[tuple[datetime, str]] = []
    if not summary_root.is_dir():
        return None
    for path in summary_root.rglob("vllm-gr-summary.json"):
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
            run = summary["run"]
            source = summary["source"]
            sha = str(source["git_sha"])
            if (
                run.get("status") != "success"
                or run.get("trend_eligible") is not True
                or source.get("branch") != "decode_graph"
            ):
                continue
            if str(run.get("date", "")) >= current_date:
                continue
            timestamp = datetime.fromisoformat(str(run["finished_at"]).replace("Z", "+00:00"))
            candidates.append((timestamp, sha))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return max(candidates)[1] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-dir", type=Path, required=True)
    parser.add_argument("--summary-root", type=Path, required=True)
    parser.add_argument("--current-sha", required=True)
    parser.add_argument("--current-date", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--repository-url",
        default="https://github.com/JiusiServe/vllm-gr",
    )
    args = parser.parse_args()

    previous_sha = previous_successful_sha(args.summary_root, args.current_date)
    payload: dict[str, object] = {
        "previous_git_sha": previous_sha,
        "current_git_sha": args.current_sha,
        "comparison_scope": "previous successful daily HEAD to current daily HEAD",
        "commit_count": 0,
        "commits": [],
        "pull_requests": [],
    }
    if previous_sha:
        is_ancestor = subprocess.run(
            ["git", "-C", str(args.repo_dir), "merge-base", "--is-ancestor", previous_sha, args.current_sha],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
        payload["previous_is_ancestor"] = is_ancestor
        if is_ancestor:
            log = git(
                args.repo_dir,
                "log",
                "--first-parent",
                "--reverse",
                "--format=%H%x1f%s%x1f%aI",
                f"{previous_sha}..{args.current_sha}",
            )
            commits = []
            prs: dict[int, dict[str, object]] = {}
            for line in log.splitlines():
                if not line:
                    continue
                sha, subject, committed_at = line.split("\x1f", 2)
                pr_numbers = [int(match.group("number")) for match in PR_PATTERN.finditer(subject)]
                commits.append(
                    {
                        "git_sha": sha,
                        "subject": subject,
                        "committed_at": committed_at,
                        "pull_request_numbers": pr_numbers,
                    }
                )
                for number in pr_numbers:
                    prs[number] = {
                        "number": number,
                        "title": PR_PATTERN.sub("", subject).strip(),
                        "url": f"{args.repository_url}/pull/{number}",
                        "git_sha": sha,
                    }
            payload["commit_count"] = len(commits)
            payload["commits"] = commits
            payload["pull_requests"] = list(prs.values())
    else:
        payload["initial_baseline"] = True

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

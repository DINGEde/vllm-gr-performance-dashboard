#!/usr/bin/env python3
"""Export a pinned git SHA as a clean, unmodified source snapshot.

Unlike ``prepare_native_source.py``, this script does NOT instrument any
source file: the official trend must be measured on the exact, unmodified
target commit. It only runs ``git archive <sha> | tar -x`` and writes a small
provenance manifest.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import tarfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=False)
    archive = subprocess.check_output(["git", "-C", args.repo, "archive", args.sha])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        # The archive is a local pinned commit, not an external uploaded tar.
        tar.extractall(args.output, filter="data")

    manifest = {
        "version": "vllm-gr-source-snapshot-v1",
        "git_sha": args.sha,
        "instrumented": False,
    }
    (args.output / "snapshot.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

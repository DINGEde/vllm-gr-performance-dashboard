"""Build an unmodified source snapshot pinned to the tested Git commit."""
import argparse
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    archive = subprocess.check_output(["git", "-C", args.repo, "archive", args.sha])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        # The archive is a local pinned commit, not an external uploaded tar.
        tar.extractall(args.output, filter="data")
    target = args.output / "vllm_gr/entrypoints/gr.py"
    source = target.read_bytes()
    manifest = {
        "version": "vllm-gr-pinned-source-v1",
        "git_sha": args.sha,
        "entrypoint_sha256": hashlib.sha256(source).hexdigest(),
        "modified": False,
    }
    (args.output / "source-snapshot.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

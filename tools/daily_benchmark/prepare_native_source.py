"""Build an unmodified source snapshot pinned to the tested Git commit."""
import argparse
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path


def source_archive_filter(member, destination):
    try:
        return tarfile.data_filter(member, destination)
    except tarfile.LinkOutsideDestinationError:
        # Some Python 3.10 tarfile backports resolve this nested symlink
        # against the extraction root instead of against its parent directory.
        if not member.issym() or (member.name, member.linkname) != (
            "configs/default_config.yaml", "../vllm_gr/config/default_config.yaml",
        ):
            raise
        root = Path(destination).resolve()
        target = (root / Path(member.name).parent / member.linkname).resolve()
        if not target.is_relative_to(root):
            raise
        # Retain the data filter's member-path, mode and ownership checks,
        # then restore the exact link target stored by Git.
        checked = tarfile.data_filter(member.replace(linkname="."), destination)
        return checked.replace(linkname=member.linkname)


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
        tar.extractall(args.output, filter=source_archive_filter)
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

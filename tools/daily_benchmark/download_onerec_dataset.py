#!/usr/bin/env python3
"""Download and verify a pinned subset of the gated OpenOneRec-RecIF dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from huggingface_hub import hf_hub_download
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError


DEFAULT_REPO_ID = "OpenOneRec/OpenOneRec-RecIF"
DEFAULT_REVISION = "8f7cf2ee0b949e955a87a708d02024687be232c8"
TASK_FILES = {
    "ad": "benchmark_data/ad/ad_test.parquet",
    "interactive": "benchmark_data/interactive/interactive_test.parquet",
    "item_understand": "benchmark_data/item_understand/item_understand_test.parquet",
    "label_cond": "benchmark_data/label_cond/label_cond_test.parquet",
    "label_pred": "benchmark_data/label_pred/label_pred_test.parquet",
    "product": "benchmark_data/product/product_test.parquet",
    "rec_reason": "benchmark_data/rec_reason/rec_reason_test.parquet",
    "video": "benchmark_data/video/video_test.parquet",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_tasks(value: str) -> list[str]:
    tasks = [task.strip() for task in value.split(",") if task.strip()]
    unknown = sorted(set(tasks) - TASK_FILES.keys())
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown task(s): {', '.join(unknown)}; available: {', '.join(TASK_FILES)}"
        )
    if not tasks:
        raise argparse.ArgumentTypeError("at least one task is required")
    return tasks


def destination_for(data_dir: Path, task: str) -> Path:
    return data_dir / task / f"{task}_test.parquet"


def manifest_entry(path: Path, task: str, source_file: str) -> dict[str, object]:
    return {
        "task": task,
        "source_file": source_file,
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def install_file(cache_path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    if temporary.exists():
        temporary.unlink()
    try:
        os.link(cache_path, temporary)
    except OSError:
        shutil.copy2(cache_path, temporary)
    temporary.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-id", default=DEFAULT_REPO_ID)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--tasks", type=parse_tasks, default=parse_tasks("video"))
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("HF_ENDPOINT", "https://hf-mirror.com"),
    )
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    manifest_path = data_dir / ".onerec-manifest.json"
    entries: list[dict[str, object]] = []

    for task in args.tasks:
        source_file = TASK_FILES[task]
        destination = destination_for(data_dir, task)
        if not args.verify_only:
            try:
                cached = hf_hub_download(
                    repo_id=args.repo_id,
                    filename=source_file,
                    repo_type="dataset",
                    revision=args.revision,
                    endpoint=args.endpoint,
                )
            except GatedRepoError:
                print(
                    "OpenOneRec-RecIF access is not authorized. Accept the dataset terms "
                    "in Hugging Face, then run `hf auth login` inside vllm-gr-dev.",
                    file=sys.stderr,
                )
                return 3
            except HfHubHTTPError as exc:
                print(f"dataset download failed: {exc}", file=sys.stderr)
                return 4
            install_file(Path(cached), destination)

        if not destination.is_file():
            print(f"missing dataset file: {destination}", file=sys.stderr)
            return 2
        entries.append(manifest_entry(destination, task, source_file))

    manifest = {
        "schema_version": "vllm-gr.dataset.v1",
        "repo_id": args.repo_id,
        "revision": args.revision,
        "endpoint": args.endpoint,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": entries,
    }
    data_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

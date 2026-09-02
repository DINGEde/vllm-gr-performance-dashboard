#!/usr/bin/env python3
"""Convert a vLLM serving benchmark result into the dashboard daily schema."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import socket
import statistics
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import torch
import vllm
import vllm_gr


SHANGHAI = timezone(timedelta(hours=8))


def run_text(*command: str) -> str:
    return subprocess.check_output(command, text=True).strip()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sample_ids_sha256(dataset_path: Path, count: int) -> str:
    frame = pd.read_parquet(dataset_path, columns=["metadata"])
    indices = [str(index) for index in frame.sample(n=count, random_state=42).index]
    random.Random(0).shuffle(indices)
    payload = json.dumps([f"video_{index}" for index in indices], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def latency(raw: dict, metric: str) -> dict[str, float | str]:
    return {
        "mean": raw[f"mean_{metric}_ms"],
        "std": raw.get(f"std_{metric}_ms"),
        "p50": raw[f"p50_{metric}_ms"],
        "p90": raw[f"p90_{metric}_ms"],
        "p95": raw[f"p95_{metric}_ms"],
        "p99": raw[f"p99_{metric}_ms"],
        "unit": "ms",
    }


def distribution_ms(values: list[float]) -> dict[str, float | str]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("cannot summarize an empty latency sample")

    def percentile(percent: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = (len(ordered) - 1) * percent / 100.0
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    return {
        "mean": statistics.fmean(ordered),
        "std": statistics.pstdev(ordered),
        "p50": percentile(50),
        "p90": percentile(90),
        "p95": percentile(95),
        "p99": percentile(99),
        "unit": "ms",
    }


def artifact(path: Path, name: str) -> dict[str, object] | None:
    if not path.is_file():
        return None
    return {
        "name": name,
        "path": path.name,
        "sha256": file_sha256(path),
        "bytes": path.stat().st_size,
        "retention": "runner",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-result", type=Path, required=True)
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--repo-dir", type=Path, default=Path("/opt/vllm-gr"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--warmup-requests", type=int, required=True)
    parser.add_argument("--beam-width", type=int, required=True)
    parser.add_argument("--input-length", type=int, required=True)
    parser.add_argument("--service-metrics", type=Path)
    parser.add_argument("--server-log", type=Path)
    parser.add_argument("--execution-mode", choices=("online", "offline"), default="online")
    parser.add_argument("--host-name", default=os.environ.get("HOST_NAME") or socket.gethostname())
    parser.add_argument("--container-name", default="vllm-gr-benchmark")
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--git-branch")
    parser.add_argument("--tracked-clean", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--container-image", default="vllm-gr:dev")
    parser.add_argument("--container-digest")
    args = parser.parse_args()

    raw = json.loads(args.raw_result.read_text(encoding="utf-8"))
    offline = args.execution_mode == "offline" or raw.get("execution_mode") == "offline"
    if not offline and args.service_metrics is None:
        parser.error("--service-metrics is required for online summaries")
    service_metrics = (
        json.loads(args.service_metrics.read_text(encoding="utf-8"))
        if args.service_metrics is not None
        else {}
    )
    dataset_manifest = json.loads(args.dataset_manifest.read_text(encoding="utf-8"))
    dataset_file = next(item for item in dataset_manifest["files"] if item["task"] == "video")
    dataset_path = Path(dataset_file["path"])
    if offline:
        started_utc = datetime.fromisoformat(raw["started_at"])
        finished_utc = datetime.fromisoformat(raw["finished_at"])
    else:
        started_utc = datetime.strptime(raw["date"], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
        finished_utc = datetime.fromtimestamp(args.raw_result.stat().st_mtime, tz=timezone.utc)
    started = started_utc.astimezone(SHANGHAI)
    finished = finished_utc.astimezone(SHANGHAI)

    gpu_line = run_text(
        "nvidia-smi",
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
        "--id=0",
    )
    gpu_name, gpu_memory, driver_version = [part.strip() for part in gpu_line.split(",")]
    num_prompts = int(raw["num_prompts"])
    failed = int(raw["failed"])
    cache = service_metrics.get("prefix_cache")
    reasons = []
    if num_prompts < 100:
        reasons.append("smoke run uses fewer than 100 measured requests")
    if failed:
        reasons.append("one or more measured requests failed")
    if not offline and (cache is None or cache.get("hit_rate_percent") is None):
        reasons.append("prefix cache counters were unavailable")
    qualified = not reasons
    observed_input_lengths = [int(value) for value in raw["input_lens"]]
    scenario_key = f"bw{args.beam_width}-in{args.input_length}"
    sweep_axis = "beam_width" if args.input_length == 1024 else "input_length"

    if offline:
        distributions = raw["distributions"]
        prefill_common = distribution_ms(raw["miss"]["prefill_ms"] + raw["hit"]["prefill_ms"])
        decode_common = distribution_ms(raw["miss"]["decode_ms"] + raw["hit"]["decode_ms"])
        latency_metrics = {
            "e2el": distributions["e2e_ms_miss"],
            "e2el_hit": distributions["e2e_ms_hit"],
            "prefill_miss": distributions["prefill_ms_miss"],
            "prefill_hit": distributions["prefill_ms_hit"],
            "prefill": prefill_common,
            "decode": decode_common,
            "sort": distribution_ms(raw["miss"]["sort_ms"] + raw["hit"]["sort_ms"]),
            "total_beam": distribution_ms(
                raw["miss"]["total_beam_ms"] + raw["hit"]["total_beam_ms"]
            ),
            "decode_miss": distributions["decode_ms_miss"],
            "decode_hit": distributions["decode_ms_hit"],
            "overhead_miss": distributions["decode_overhead_ms_miss"],
            "overhead_hit": distributions["decode_overhead_ms_hit"],
            "engine_prefill_miss": distributions["engine_prefill_ms_miss"],
            "engine_prefill_hit": distributions["engine_prefill_ms_hit"],
            "engine_decode_miss": distributions["engine_decode_ms_miss"],
            "engine_decode_hit": distributions["engine_decode_ms_hit"],
            "beam_entry_overhead_miss": distributions["beam_entry_overhead_ms_miss"],
            "beam_entry_overhead_hit": distributions["beam_entry_overhead_ms_hit"],
            "cpu_prepare": distribution_ms(
                raw["miss"]["cpu_prepare_ms"] + raw["hit"]["cpu_prepare_ms"]
            ),
            "cpu_decision": distribution_ms(
                raw["miss"]["cpu_decision_ms"] + raw["hit"]["cpu_decision_ms"]
            ),
            "cpu_eos": distribution_ms(
                raw["miss"]["cpu_eos_ms"] + raw["hit"]["cpu_eos_ms"]
            ),
            "cpu_topk": distribution_ms(
                raw["miss"]["cpu_topk_ms"] + raw["hit"]["cpu_topk_ms"]
            ),
            "cpu_materialize": distribution_ms(
                raw["miss"]["cpu_materialize_ms"] + raw["hit"]["cpu_materialize_ms"]
            ),
        }
        duration_seconds = float(raw["duration_seconds"])
        output_total = int(raw["aggregate_output_tokens"])
        input_total = sum(observed_input_lengths)
        requests_per_second = num_prompts / duration_seconds
        output_tokens_per_second = output_total / duration_seconds
        total_tokens_per_second = (input_total + output_total) / duration_seconds
        beam_search_metrics = {
            "requests": num_prompts,
            "prefill_mean_ms": prefill_common["mean"],
            "decode_mean_ms": decode_common["mean"],
            "sort_mean_ms": latency_metrics["sort"]["mean"],
            "total_mean_ms": latency_metrics["total_beam"]["mean"],
        }
        notes = [
            "Offline GRLLM.beam_search; max_concurrency=1 and one prompt per call.",
            "Each measured sample is a cold-cache call followed by an identical warm-cache call.",
            "Offline E2E excludes HTTP, SSE, serialization, and network round-trip overhead.",
            "Phase definition vllm-gr-serving-internal-v3: Prefill starts at the internal beam token loop, token 0 is Prefill, and Decode runs from token 1 preparation through beam_search return.",
            "Offline E2E also includes beam entry preparation before the internal Prefill boundary.",
            "Decode is one common distribution over miss/hit observations; cache state is not an additive Decode component.",
            "Mean is phase wall-time sum divided by request observations; Decode common uses 2 * num_prompts observations.",
            "Decode overhead is Decode wall time minus the token>0 engine-step time.",
            "Sort measures only the final completed-beam sorted() call.",
            "CPU pipeline stages (prepare/decision/eos/topk/materialize) sum per-decode-token CPU time across tokens >= 1; their total plus Sort approximates Decode overhead.",
            "cpu_topk is 0 on the worker-decision path, where the accelerator pre-selects the surviving beams.",
            "Total Beam is the online-compatible Prefill + Decode + Sort aggregate; because Decode already contains Sort, it is not a non-overlapping wall-clock total.",
        ]
    else:
        latency_metrics = {name: latency(raw, name) for name in ("ttft", "tpot", "itl", "e2el")}
        duration_seconds = raw["duration"]
        input_total = raw["total_input_tokens"]
        output_total = raw["total_output_tokens"]
        requests_per_second = raw["request_throughput"]
        output_tokens_per_second = raw["output_throughput"]
        total_tokens_per_second = raw["total_token_throughput"]
        beam_search_metrics = service_metrics.get("beam_search")
        notes = [
            "Real RecIF data; warmup result is stored separately.",
            "TPOT and ITL use beam-aggregate output-token semantics; use E2EL for the primary latency trend.",
            "Prefix cache is reset after warmup and before the measured requests.",
        ]

    summary = {
        "schema_version": "vllm-gr.daily.v1",
        "run": {
            "id": args.run_id,
            "date": started.date().isoformat(),
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "status": "success" if failed == 0 else "failed",
            "trend_eligible": qualified,
            "baseline_eligible": qualified,
            "qualification_reasons": reasons,
            "notes": notes,
        },
        "source": {
            "repository": "vllm-gr",
            "git_sha": args.git_sha,
            "branch": args.git_branch,
            "tracked_clean": args.tracked_clean,
        },
        "environment": {
            "host": args.host_name,
            "hardware": "L20",
            "gpu": {
                "count": 1,
                "name": gpu_name,
                "memory_mib": int(gpu_memory),
                "driver_version": driver_version,
            },
            "container": {
                "name": args.container_name,
                "image": args.container_image,
                "image_digest": args.container_digest,
            },
            "runtime": {
                "vllm_version": vllm.__version__,
                "vllm_gr_version": vllm_gr.__version__,
                "torch_version": torch.__version__,
                "cuda_forward_compat": True,
            },
        },
        "model": {
            "id": raw["model"] if offline else raw["model_id"],
            "revision": None,
            "generation_config_source": "model",
        },
        "dataset": {
            "name": dataset_manifest["repo_id"],
            "kind": "real",
            "representative": True,
            "task": "video",
            "path": str(dataset_path),
            "revision": dataset_manifest["revision"],
            "sha256": dataset_file["sha256"],
            "selection": {
                "strategy": "seeded-sample",
                "seed": 42,
                "sample_count": num_prompts,
                "sample_ids_sha256": sample_ids_sha256(dataset_path, num_prompts),
                "shuffle": True,
            },
        },
        "scenario": {
            "key": scenario_key,
            "name": f"RecIF video · beam-{args.beam_width} · input-{args.input_length} · offline-c1" if offline else f"RecIF video · beam-{args.beam_width} · input-{args.input_length} · concurrency-{raw['max_concurrency']}",
            "execution_mode": "offline" if offline else "online",
            "endpoint": "GRLLM.beam_search" if offline else "/v1/chat/completions",
            "backend": "vllm-gr-offline" if offline else raw["backend"],
            "num_prompts": num_prompts,
            "max_concurrency": raw["max_concurrency"],
            "request_rate": "sequential" if offline else raw["request_rate"],
            "beam_search": True,
            "n": args.beam_width,
            "input_tokens_target": args.input_length,
            "input_tokens_observed": {
                "min": min(observed_input_lengths),
                "mean": sum(observed_input_lengths) / len(observed_input_lengths),
                "max": max(observed_input_lengths),
            },
            "sweep": {
                "axis": sweep_axis,
                "beam_width": args.beam_width,
                "input_tokens": args.input_length,
            },
            "warmup_requests": args.warmup_requests,
            "server_args": raw["engine_config"] if offline else {
                "max_logprobs": 1024,
                "beam_max_width": 1024,
                "max_num_seqs": 1024,
                "max_num_batched_tokens": 16384,
                "scheduling_policy": "priority",
                "enable_thinking": False,
            },
            "benchmark_args": {
                "temperature": 0,
                "max_tokens": raw["max_tokens"] if offline else None,
                "phase_definition": raw.get("phase_definition") if offline else None,
                "cache_protocol": "paired-reset-then-repeat" if offline else "reset-once-after-warmup",
                "metric_percentiles": [50, 90, 95, 99],
                "save_detailed": True,
            },
        },
        "results": {
            "requests": {"completed": raw["completed"], "failed": failed},
            "duration_seconds": duration_seconds,
            "tokens": {
                "input_total": input_total,
                "output_total": output_total,
                "output_semantics": "beam-aggregate",
            },
            "throughput": {
                "requests_per_second": requests_per_second,
                "output_tokens_per_second": output_tokens_per_second,
                "total_tokens_per_second": total_tokens_per_second,
            },
            "latency_ms": latency_metrics,
            "beam_search": beam_search_metrics,
            "samples": ({
                "e2el_ms": raw["miss"]["e2e_ms"],
                "e2el_hit_ms": raw["hit"]["e2e_ms"],
                "input_tokens": raw["input_lens"],
                "output_tokens": raw["output_lens"],
            } if offline else {
                "ttft_ms": [value * 1000 for value in raw["ttfts"]],
                "input_tokens": raw["input_lens"],
                "output_tokens": raw["output_lens"],
            }),
        },
        "artifacts": [],
    }
    if not offline:
        summary["results"]["cache"] = {"prefix": cache}

    for path, name in (
        (args.raw_result, "raw result"),
        (args.raw_result.parent / "benchmark.log", "benchmark log"),
        (args.raw_result.parent / "warmup-result.json", "warmup result"),
        (args.service_metrics, "service metrics"),
        (args.server_log, "server log"),
    ):
        if path is not None and (item := artifact(path, name)):
            summary["artifacts"].append(item)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

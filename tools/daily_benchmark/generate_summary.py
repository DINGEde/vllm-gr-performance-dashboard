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
        index = min(len(ordered) - 1, int(percent / 100.0 * (len(ordered) - 1)))
        return ordered[index]

    return {
        "mean": statistics.fmean(ordered),
        "std": statistics.pstdev(ordered),
        "p50": percentile(50),
        "p90": percentile(90),
        "p95": percentile(95),
        "p99": percentile(99),
        "unit": "ms",
    }


def load_cpu_timing(path: Path | None) -> dict[str, object] | None:
    """Merge aggregate-only worker timing files into per-call means."""
    if path is None or not path.is_dir():
        return None
    merged: dict[str, dict[str, object]] = {}
    files = sorted(path.glob("cpu-timing-*.json"))
    for timing_file in files:
        payload = json.loads(timing_file.read_text(encoding="utf-8"))
        for name, values in payload.get("metrics", {}).items():
            row = merged.setdefault(
                name,
                {
                    "count": 0,
                    "wall_ns_total": 0,
                    "thread_cpu_ns_total": 0,
                    "wall_ns_samples": [],
                    "thread_cpu_ns_samples": [],
                },
            )
            for key in ("count", "wall_ns_total", "thread_cpu_ns_total"):
                row[key] += int(values.get(key, 0))
            row["wall_ns_samples"].extend(int(value) for value in values.get("wall_ns_samples", []))
            row["thread_cpu_ns_samples"].extend(
                int(value) for value in values.get("thread_cpu_ns_samples", [])
            )
    if not merged:
        return None

    metrics: dict[str, dict[str, float | int | str]] = {}
    for name, values in merged.items():
        count = int(values["count"])
        if count < 1:
            continue
        wall_samples = [value / 1_000_000.0 for value in values["wall_ns_samples"]]
        cpu_samples = [value / 1_000_000.0 for value in values["thread_cpu_ns_samples"]]
        wall_dist = distribution_ms(wall_samples) if wall_samples else None
        cpu_dist = distribution_ms(cpu_samples) if cpu_samples else None
        metrics[name] = {
            "count": count,
            "wall_mean_ms": int(values["wall_ns_total"]) / count / 1_000_000.0,
            "wall_p50_ms": wall_dist["p50"] if wall_dist else None,
            "wall_p90_ms": wall_dist["p90"] if wall_dist else None,
            "wall_p99_ms": wall_dist["p99"] if wall_dist else None,
            "wall_total_ms": int(values["wall_ns_total"]) / 1_000_000.0,
            "thread_cpu_mean_ms": int(values["thread_cpu_ns_total"]) / count / 1_000_000.0,
            "thread_cpu_p50_ms": cpu_dist["p50"] if cpu_dist else None,
            "thread_cpu_total_ms": int(values["thread_cpu_ns_total"]) / 1_000_000.0,
            "unit": "ms",
        }

    execute_total = float(metrics.get("execute_model", {}).get("wall_total_ms", 0.0))
    execute_children = tuple(
        name
        for name in (
            "update_states",
            "prepare_inputs",
            "determine_batch",
            "prepare_attn_buffers",
            "prepare_attn_metadata",
            "preprocess_model_inputs",
            "model_state_prepare_inputs",
            "run_model_forward",
            "run_fullgraph",
        )
        if name in metrics
    )
    child_total = sum(
        float(metrics.get(name, {}).get("wall_total_ms", 0.0))
        for name in execute_children
    )
    execute_count = int(metrics.get("execute_model", {}).get("count", 0))
    residual_total = max(0.0, execute_total - child_total)
    sample_children = tuple(
        name
        for name in (
            "sample",
            "beam_worker_decision",
            "update_states_after_execute",
            "bookkeeping_sync",
            "async_output_create",
            "postprocess",
        )
        if name in metrics
    )
    sample_total = float(metrics.get("sample_tokens", {}).get("wall_total_ms", 0.0))
    sample_child_total = sum(
        float(metrics.get(name, {}).get("wall_total_ms", 0.0)) for name in sample_children
    )
    sample_count = int(metrics.get("sample_tokens", {}).get("count", 0))
    sample_residual_total = max(0.0, sample_total - sample_child_total)
    return {
        "schema_version": "vllm-gr.cpu-timing.v1",
        "method": "perf_counter_ns + thread_time_ns; in-memory aggregation; flush at worker shutdown",
        "hot_path_io": False,
        "perturbation_validation": {
            "status": "pending",
            "method": "same-scenario LIGHTWEIGHT_TIMING=0 versus 1",
            "limits_percent": {"p50": 2.0, "p90": 3.0, "p99": 5.0},
        },
        "source_files": len(files),
        "metrics": metrics,
        "execute_model": {
            "count": execute_count,
            "children_wall_total_ms": child_total,
            "residual_wall_total_ms": residual_total,
            "residual_wall_mean_ms": residual_total / execute_count if execute_count else None,
            "coverage_percent": 100.0 * child_total / execute_total if execute_total else None,
            "children": list(execute_children),
        },
        "sample_tokens": {
            "count": sample_count,
            "children_wall_total_ms": sample_child_total,
            "residual_wall_total_ms": sample_residual_total,
            "residual_wall_mean_ms": sample_residual_total / sample_count if sample_count else None,
            "coverage_percent": 100.0 * sample_child_total / sample_total if sample_total else None,
            "children": list(sample_children),
        },
        "runtime_topology": {
            "tensor_parallel_size": 1,
            "executor": "UniProcExecutor",
            "async_scheduling": True,
            "readiness_wait_thread": "EngineCore main thread",
            "reference_difference": "TP=1 has no WorkerAsyncOutputCopy CPU thread; AsyncOutputFuture.result materializes output on EngineCore.",
        },
        "caveats": [
            "Wall values are CPU-side wrapper elapsed time and may include GPU synchronization waits.",
            "run_fullgraph measures the CPU launch/replay wrapper, not isolated GPU kernel execution.",
            "Warmup requests are included; dummy/profile execute_model calls are excluded.",
            "Child totals are sequential call totals; residual is parent total minus listed child totals.",
        ],
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
    parser.add_argument("--git-subject", required=True)
    parser.add_argument("--git-branch")
    parser.add_argument("--tracked-clean", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--container-image", default="vllm-gr:dev")
    parser.add_argument("--container-digest")
    parser.add_argument("--cpu-timing-dir", type=Path)
    parser.add_argument("--source-change", type=Path)
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
    if offline and raw.get("instrumentation", {}).get("canonical_contaminated_by_diagnostic"):
        reasons.append("worker lightweight timing was enabled during canonical sampling")
    qualified = not reasons
    observed_input_lengths = [int(value) for value in raw["input_lens"]]
    scenario_key = f"bw{args.beam_width}-in{args.input_length}"
    sweep_axis = "beam_width" if args.input_length == 1024 else "input_length"

    if offline:
        distributions = raw["distributions"]
        latency_metrics = {
            "e2el": distributions["e2e_ms_miss"],
            "e2el_hit": distributions["e2e_ms_hit"],
        }
        diagnostic_raw = raw.get("diagnostic") or {}
        diagnostic_distributions = diagnostic_raw.get("distributions", {})
        diagnostic_miss = diagnostic_raw.get("miss", {})
        diagnostic_hit = diagnostic_raw.get("hit", {})
        diagnostic_latency: dict[str, object] = {}
        if diagnostic_raw.get("num_prompts", 0):
            prefill_common = distribution_ms(
                diagnostic_miss["prefill_ms"] + diagnostic_hit["prefill_ms"]
            )
            decode_common = distribution_ms(
                diagnostic_miss["decode_ms"] + diagnostic_hit["decode_ms"]
            )
            diagnostic_latency = {
                "e2el": diagnostic_distributions["e2e_ms_miss"],
                "e2el_hit": diagnostic_distributions["e2e_ms_hit"],
                "prefill_miss": diagnostic_distributions["prefill_ms_miss"],
                "prefill_hit": diagnostic_distributions["prefill_ms_hit"],
                "prefill": prefill_common,
                "decode": decode_common,
                "decode_miss": diagnostic_distributions["decode_ms_miss"],
                "decode_hit": diagnostic_distributions["decode_ms_hit"],
                "total_beam": distribution_ms(
                    diagnostic_miss["total_beam_ms"] + diagnostic_hit["total_beam_ms"]
                ),
            }
        duration_seconds = float(raw["duration_seconds"])
        output_total = int(raw["aggregate_output_tokens"])
        input_total = sum(observed_input_lengths)
        requests_per_second = num_prompts / duration_seconds
        output_tokens_per_second = output_total / duration_seconds
        total_tokens_per_second = (input_total + output_total) / duration_seconds
        beam_search_metrics = (
            {
                "requests": int(diagnostic_raw["num_prompts"]),
                "prefill_mean_ms": diagnostic_latency["prefill"]["mean"],
                "decode_mean_ms": diagnostic_latency["decode"]["mean"],
                "total_mean_ms": diagnostic_latency["total_beam"]["mean"],
            }
            if diagnostic_latency
            else None
        )
        notes = [
            "Offline GRLLM.beam_search; max_concurrency=1 and one prompt per call.",
            "Canonical E2E is measured before diagnostic sampling with one outer perf_counter_ns and no internal monkeypatch or profiler.",
            "Each canonical sample is a cold-cache call followed by an identical warm-cache call.",
            "Offline E2E excludes HTTP, SSE, serialization, and network round-trip overhead.",
            "Internal stage values come from a smaller post-canonical diagnostic sample and never contribute to the official E2E trend.",
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

    cpu_pipeline_detail = load_cpu_timing(args.cpu_timing_dir)
    source_change = (
        json.loads(args.source_change.read_text(encoding="utf-8"))
        if args.source_change is not None and args.source_change.is_file()
        else None
    )
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
            "git_subject": args.git_subject,
            "branch": args.git_branch,
            "tracked_clean": args.tracked_clean,
            "change_since_previous": source_change,
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
                "scheduling_policy": "fcfs",
                "gpu_memory_utilization": 0.90,
                "catalog_path": "/opt/vllm-gr/test_profilling/video_constraint_triples.json",
                "constraint_backend": "constraint_table",
                "enable_thinking": False,
            },
            "benchmark_args": {
                "temperature": 0,
                "max_tokens": raw["max_tokens"] if offline else None,
                "phase_definition": raw.get("phase_definition") if offline else None,
                "measurement_mode": raw.get("measurement_mode") if offline else "online",
                "instrumentation": raw.get("instrumentation") if offline else None,
                "diagnostic_prompts": (
                    int((raw.get("diagnostic") or {}).get("num_prompts", 0))
                    if offline
                    else 0
                ),
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
            "cpu_pipeline_detail": cpu_pipeline_detail,
            "diagnostic": (
                {
                    "trend_eligible": False,
                    "num_prompts": int(diagnostic_raw.get("num_prompts", 0)),
                    "method": "post-canonical minimal 2-function perf_counter monkeypatch (prefill/decode boundary only)",
                    "latency_ms": diagnostic_latency,
                    "phase_definition": diagnostic_raw.get("phase_definition"),
                }
                if offline and diagnostic_latency
                else None
            ),
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

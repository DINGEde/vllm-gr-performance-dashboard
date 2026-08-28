#!/usr/bin/env python3
"""Generate dashboard-summary.json for the 2026-08-28 AgentCache Kanban run."""

import json
from pathlib import Path


def load_summary(path):
    with open(path) as file:
        return json.load(file)


def ratio(numerator, denominator):
    return numerator / denominator if denominator else 0


def extract_metrics(summary):
    tasks = summary["tasks"]
    requests = summary["requests"]
    vllm = summary["vllm"]
    counters = vllm["counters"]
    metrics = {
        "completed_tasks": tasks["completed"],
        "failed_tasks": tasks["failed"],
        "tasks_with_patch": tasks["with_patch"],
        "configured_tasks": 32,
        "recorded_tasks": 32,
        "run_wall_time_seconds": summary["run_wall_time_seconds"],
        "requests": requests["requests"],
        "successful_requests": requests["successful_requests"],
        "failed_requests": requests["failed_requests"],
        "request_throughput": summary["request_throughput_per_second"],
        "input_tokens": requests["input_tokens"],
        "output_tokens": requests["output_tokens"],
        "input_token_throughput": summary["input_token_throughput_per_second"],
        "output_token_throughput": summary["output_token_throughput_per_second"],
        "cached_input_tokens": requests.get("cached_input_tokens") or 0,
        "prefix_cache_hit_rate": requests.get("prefix_cache_hit_rate") or 0,
        "vllm_prefix_cache_hit_rate": ratio(counters.get("vllm:prefix_cache_hits_total", 0), counters.get("vllm:prefix_cache_queries_total", 0)),
        "vllm_prompt_token_hit_rate": ratio(counters.get("vllm:prompt_tokens_cached_total", 0), counters.get("vllm:prompt_tokens_total", 0)),
        "cli.overrides.max_concurrency": 16,
        "cli.overrides.task_num": 32,
    }
    for name, value in tasks["duration_seconds"].items():
        metrics[f"task_duration_seconds.{name}"] = value
    for name, value in requests["latency_seconds"].items():
        metrics[f"latency_seconds.{name}"] = value
    for name, value in requests["ttft_seconds"].items():
        metrics[f"ttft_seconds.{name}"] = value
    for name, value in vllm["latency_breakdown_seconds"].items():
        metrics[f"vllm_{name}_seconds.mean"] = value["mean"]
    return metrics


def main():
    directory = Path(__file__).parent
    baseline = load_summary(directory / "baseline-summary.json")
    candidate = load_summary(directory / "candidate-summary.json")
    dashboard = {
        "schema_version": "1.0",
        "run": {
            "name": "main-ci-32_16",
            "date": "2026-08-28",
            "host": "L20-10014",
            "commit": "4eb38ce36247d0659a5d579a8dd7bc1c7a4e7677",
            "description": "Main CI fixed-force-resume policy experiment: 32 tasks, 16 max_concurrency; candidate min/max force-resume timeout pinned to 1800 seconds.",
        },
        "environment": {
            "model": "Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8",
            "vllm_version": "0.23.0",
            "agent": "Claude Code 2.1.181",
            "tensor_parallel": 2,
            "gpus": "2×L20 80GB",
        },
        "provenance": {
            "baseline_cc_result_dir": "results/main-ci-baseline-cc-32_16-20260827_111641",
            "candidate_cc_result_dir": "results/main-ci-candidate-cc-32_16-20260827_111641",
            "baseline_jiuwenswarm_result_dir": "results/main-ci-baseline-jiuwenswarm-32_16-20260827_111641",
            "candidate_jiuwenswarm_result_dir": "results/main-ci-candidate-jiuwenswarm-32_16-20260827_111641",
            "compare_cc_report": "benchkit-logs/main-ci-compare-cc-32_16-20260827_111641.txt",
            "compare_jiuwenswarm_report": "benchkit-logs/main-ci-compare-jiuwenswarm-32_16-20260827_111641.txt",
            "note": "Kanban folder is labeled 2026-08-28. Host launched at 2026-08-27 11:16 CST, so runtime artifact paths carry timestamp 20260827_111641. The incomplete 20260826_214646 baseline-only preflight artifact remains excluded.",
        },
        "shapes": {"32_16": {"task_num": 32, "max_concurrency": 16, "timeout_seconds": 14400}},
        "averages": {"all_shapes": {"baseline": extract_metrics(baseline), "candidate": extract_metrics(candidate)}},
        "metric_policy": {"note": "Dashboard uses the Claude Code arm aliases; JiuwenSwarm artifacts are retained separately.", "new_metrics": ["vllm_prefix_cache_hit_rate", "vllm_prompt_token_hit_rate", "vllm_queue_time_seconds.mean", "vllm_prefill_time_seconds.mean", "vllm_decode_time_seconds.mean", "vllm_inference_time_seconds.mean"]},
        "tables": {},
        "missing_artifacts": [],
    }
    with open(directory / "dashboard-summary.json", "w", newline="\n") as file:
        json.dump(dashboard, file, indent=2)
        file.write("\n")


if __name__ == "__main__":
    main()

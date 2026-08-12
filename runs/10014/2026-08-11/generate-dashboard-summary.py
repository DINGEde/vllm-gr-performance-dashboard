#!/usr/bin/env python3
"""Generate dashboard-summary.json for AgentCache Kanban from benchmark results.

Usage:
    python generate-dashboard-summary.py [--date 2026-08-06] [--dir ./]

Reads baseline-summary.json and candidate-summary.json from the target
directory and writes dashboard-summary.json alongside them.
"""

import argparse
import json
from pathlib import Path


def load_summary(path):
    """Load a summary.json file."""
    with open(path) as f:
        return json.load(f)


def extract_metrics(summary):
    """Extract metrics from a summary.json into dashboard format."""
    metrics = {}

    # Task outcomes (nested under "tasks")
    tasks = summary.get("tasks", {})
    metrics["completed_tasks"] = tasks.get("completed", 0)
    metrics["failed_tasks"] = tasks.get("failed", 0)
    metrics["tasks_with_patch"] = tasks.get("with_patch", 0)
    metrics["configured_tasks"] = 32
    metrics["recorded_tasks"] = 32

    # Task duration (nested under "tasks.duration_seconds")
    if "duration_seconds" in tasks:
        td = tasks["duration_seconds"]
        metrics["task_duration_seconds.mean"] = td.get("mean", 0)
        metrics["task_duration_seconds.p50"] = td.get("p50", 0)
        metrics["task_duration_seconds.p95"] = td.get("p95", 0)
        metrics["task_duration_seconds.p99"] = td.get("p99", 0)

    # Run metrics
    metrics["run_wall_time_seconds"] = summary.get("run_wall_time_seconds", 0)

    # Request metrics (nested under "requests")
    requests = summary.get("requests", {})
    metrics["requests"] = requests.get("requests", 0)
    metrics["successful_requests"] = requests.get("successful_requests", 0)
    metrics["failed_requests"] = requests.get("failed_requests", 0)
    metrics["request_throughput"] = summary.get("request_throughput_per_second", 0)

    # Token metrics (nested under "requests")
    metrics["input_tokens"] = requests.get("input_tokens", 0)
    metrics["output_tokens"] = requests.get("output_tokens", 0)
    metrics["input_token_throughput"] = summary.get("input_token_throughput_per_second", 0)
    metrics["output_token_throughput"] = summary.get("output_token_throughput_per_second", 0)

    # Cache metrics (Anthropic-side, nested under "requests")
    metrics["cached_input_tokens"] = requests.get("cached_input_tokens") or 0
    metrics["prefix_cache_hit_rate"] = requests.get("prefix_cache_hit_rate") or 0

    # vLLM-specific cache metrics (from vllm.counters)
    vllm = summary.get("vllm", {})
    counters = vllm.get("counters", {})
    if counters:
        hits = counters.get("vllm:prefix_cache_hits_total", 0)
        queries = counters.get("vllm:prefix_cache_queries_total", 1)
        metrics["vllm_prefix_cache_hit_rate"] = hits / queries if queries > 0 else 0
        # Prompt token hit rate (if available)
        prompt_hits = counters.get("vllm:prompt_tokens_cache_hits_total", 0)
        prompt_queries = counters.get("vllm:prompt_tokens_total", 1)
        metrics["vllm_prompt_token_hit_rate"] = prompt_hits / prompt_queries if prompt_queries > 0 else 0
    else:
        metrics["vllm_prefix_cache_hit_rate"] = 0
        metrics["vllm_prompt_token_hit_rate"] = 0

    # Latency metrics (nested under "requests.latency_seconds")
    if "latency_seconds" in requests:
        lat = requests["latency_seconds"]
        metrics["latency_seconds.mean"] = lat.get("mean", 0)
        metrics["latency_seconds.p50"] = lat.get("p50", 0)
        metrics["latency_seconds.p95"] = lat.get("p95", 0)
        metrics["latency_seconds.p99"] = lat.get("p99", 0)

    # TTFT metrics (nested under "requests.ttft_seconds")
    if "ttft_seconds" in requests:
        ttft = requests["ttft_seconds"]
        metrics["ttft_seconds.mean"] = ttft.get("mean", 0)
        metrics["ttft_seconds.p50"] = ttft.get("p50", 0)
        metrics["ttft_seconds.p95"] = ttft.get("p95", 0)
        metrics["ttft_seconds.p99"] = ttft.get("p99", 0)

    # vLLM latency breakdown (nested under "vllm.latency_breakdown_seconds")
    if "latency_breakdown_seconds" in vllm:
        breakdown = vllm["latency_breakdown_seconds"]
        for metric_name in ["queue_time", "prefill_time", "decode_time", "inference_time"]:
            if metric_name in breakdown:
                metrics[f"vllm_{metric_name}_seconds.mean"] = breakdown[metric_name].get("mean", 0)

    # Concurrency
    metrics["cli.overrides.max_concurrency"] = 16
    metrics["cli.overrides.task_num"] = 32

    return metrics


def main():
    parser = argparse.ArgumentParser(
        description="Generate dashboard-summary.json from baseline/candidate summary files"
    )
    parser.add_argument(
        "--dir",
        default=".",
        help="Directory containing baseline-summary.json and candidate-summary.json (default: .)",
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Run date in YYYY-MM-DD format (e.g., 2026-08-06)",
    )
    parser.add_argument(
        "--shape",
        default="32_16",
        help="Benchmark shape (default: 32_16)",
    )
    parser.add_argument(
        "--commit",
        required=True,
        help="Git commit hash (full, 40-char)",
    )
    parser.add_argument(
        "--host",
        default="L20-10014",
        help="Host identifier (default: L20-10014)",
    )
    parser.add_argument(
        "--model",
        default="Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8",
        help="Model identifier",
    )
    parser.add_argument(
        "--vllm-version",
        default="0.23.0",
        help="vLLM version",
    )
    parser.add_argument(
        "--agent-version",
        default="Claude Code 2.1.181",
        help="Agent version string",
    )
    args = parser.parse_args()

    target_dir = Path(args.dir)

    baseline_path = target_dir / "baseline-summary.json"
    candidate_path = target_dir / "candidate-summary.json"
    output_path = target_dir / "dashboard-summary.json"

    if not baseline_path.exists():
        print(f"Error: baseline-summary.json not found at {baseline_path}")
        return 1
    if not candidate_path.exists():
        print(f"Error: candidate-summary.json not found at {candidate_path}")
        return 1

    baseline = load_summary(baseline_path)
    candidate = load_summary(candidate_path)

    baseline_metrics = extract_metrics(baseline)
    candidate_metrics = extract_metrics(candidate)

    dashboard = {
        "schema_version": "1.0",
        "run": {
            "name": f"main-ci-{args.shape}",
            "date": args.date,
            "host": args.host,
            "commit": args.commit,
            "description": "Main CI: 32 tasks, 16 max_concurrency, AgentCache progress-TTL controller",
        },
        "environment": {
            "model": args.model,
            "vllm_version": args.vllm_version,
            "agent": args.agent_version,
            "tensor_parallel": 2,
            "gpus": "2×L20 80GB",
        },
        "provenance": {
            "baseline_result_dir": f"results/main-ci-baseline-{args.shape}_{args.date.replace('-', '')}_192051",
            "candidate_result_dir": f"results/main-ci-candidate-{args.shape}_{args.date.replace('-', '')}_192051",
            "compare_report": f"benchkit-logs/main-ci-compare-{args.shape}_{args.date.replace('-', '')}_192051.txt",
        },
        "shapes": {"32_16": {"task_num": 32, "max_concurrency": 16, "timeout_seconds": 14400}},
        "averages": {"all_shapes": {"baseline": baseline_metrics, "candidate": candidate_metrics}},
        "metric_policy": {
            "note": "This run includes vLLM-specific metrics",
            "new_metrics": [
                "vllm_prefix_cache_hit_rate",
                "vllm_prompt_token_hit_rate",
                "vllm_queue_time_seconds.mean",
                "vllm_prefill_time_seconds.mean",
                "vllm_decode_time_seconds.mean",
                "vllm_inference_time_seconds.mean",
            ],
        },
        "tables": {},
        "missing_artifacts": [],
    }

    with open(output_path, "w") as f:
        json.dump(dashboard, f, indent=2)

    print(f"Generated: {output_path}")
    print(f"  Baseline metrics: {len(baseline_metrics)}")
    print(f"  Candidate metrics: {len(candidate_metrics)}")


if __name__ == "__main__":
    raise SystemExit(main())

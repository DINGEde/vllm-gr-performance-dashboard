#!/usr/bin/env python3
"""Attach a same-scenario timing-probe perturbation check to a run summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


LIMITS = {"p50": 2.0, "p90": 3.0, "p99": 5.0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timing-on", type=Path, required=True)
    parser.add_argument("--timing-off", type=Path, required=True)
    args = parser.parse_args()

    timing_on = json.loads(args.timing_on.read_text(encoding="utf-8"))
    timing_off = json.loads(args.timing_off.read_text(encoding="utf-8"))
    on_latency = timing_on["results"]["latency_ms"]["e2el"]
    off_latency = timing_off["results"]["latency_ms"]["e2el"]
    observed = {}
    passed = True
    for percentile, limit in LIMITS.items():
        on_value = float(on_latency[percentile])
        off_value = float(off_latency[percentile])
        delta = 100.0 * (on_value / off_value - 1.0)
        observed[percentile] = {
            "timing_off_ms": off_value,
            "timing_on_ms": on_value,
            "delta_percent": delta,
            "limit_percent": limit,
            "pass": delta <= limit,
        }
        passed = passed and delta <= limit

    validation = {
        "status": "pass" if passed else "fail",
        "method": "same-scenario LIGHTWEIGHT_TIMING=0 versus 1; cold-cache offline E2E",
        "limits_percent": LIMITS,
        "observed": observed,
        "baseline_run_id": timing_off["run"]["id"],
        "interpretation": (
            "Function timings are suitable for the regression dashboard."
            if passed
            else "Function timings are diagnostic only and must not be treated as regression baselines."
        ),
    }
    detail = timing_on["results"]["cpu_pipeline_detail"]
    detail["perturbation_validation"] = validation

    # Older summaries counted the vllm-gr beam decision as opaque residual even
    # though it is a direct sample_tokens child on the legacy runner path.
    sample_detail = detail.get("sample_tokens", {})
    metrics = detail.get("metrics", {})
    children = list(sample_detail.get("children", []))
    if "beam_worker_decision" in metrics and "beam_worker_decision" not in children:
        children.insert(1 if "sample" in children else 0, "beam_worker_decision")
        child_total = sum(float(metrics[name]["wall_total_ms"]) for name in children)
        parent_total = float(metrics.get("sample_tokens", {}).get("wall_total_ms", 0.0))
        count = int(metrics.get("sample_tokens", {}).get("count", 0))
        residual = max(0.0, parent_total - child_total)
        sample_detail.update(
            children=children,
            children_wall_total_ms=child_total,
            residual_wall_total_ms=residual,
            residual_wall_mean_ms=residual / count if count else None,
            coverage_percent=100.0 * child_total / parent_total if parent_total else None,
        )
    args.timing_on.write_text(
        json.dumps(timing_on, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(validation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

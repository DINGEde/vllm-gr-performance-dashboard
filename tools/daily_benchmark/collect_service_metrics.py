#!/usr/bin/env python3
"""Capture and diff the Prometheus counters used by daily benchmarks."""

from __future__ import annotations

import argparse
import json
import math
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


METRICS = {
    "prefix_cache_queries": ("vllm:prefix_cache_queries_total", "vllm:prefix_cache_queries"),
    "prefix_cache_hits": ("vllm:prefix_cache_hits_total", "vllm:prefix_cache_hits"),
    "beam_prefill_seconds": ("vllm_gr_beam_search_prefill_time_seconds_total",),
    "beam_decode_seconds": ("vllm_gr_beam_search_decode_time_seconds_total",),
    "beam_sort_seconds": ("vllm_gr_beam_search_sort_time_seconds_total",),
    "beam_requests": ("vllm_gr_beam_search_requests_total",),
    "beams_generated": ("vllm_gr_beam_search_total_beams_generated_total",),
}
LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+([^\s]+)")


def read_metrics(url: str) -> dict[str, float]:
    with urllib.request.urlopen(url.rstrip("/") + "/metrics", timeout=10) as response:
        body = response.read().decode("utf-8")
    totals: dict[str, float] = {}
    for line in body.splitlines():
        match = LINE.match(line)
        if not match:
            continue
        try:
            value = float(match.group(2))
        except ValueError:
            continue
        if math.isfinite(value):
            totals[match.group(1)] = totals.get(match.group(1), 0.0) + value
    return totals


def normalize(totals: dict[str, float]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for key, aliases in METRICS.items():
        result[key] = next((totals[name] for name in aliases if name in totals), None)
    return result


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def snapshot(url: str, output: Path) -> None:
    write(
        output,
        {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "metrics_url": url.rstrip("/") + "/metrics",
            "counters": normalize(read_metrics(url)),
        },
    )


def delta(before_path: Path, after_path: Path, output: Path) -> None:
    before = json.loads(before_path.read_text(encoding="utf-8"))["counters"]
    after = json.loads(after_path.read_text(encoding="utf-8"))["counters"]
    counters: dict[str, float | None] = {}
    for key in METRICS:
        left, right = before.get(key), after.get(key)
        counters[key] = None if left is None or right is None else max(0.0, float(right) - float(left))

    queries = counters["prefix_cache_queries"]
    hits = counters["prefix_cache_hits"]
    hit_rate = None if queries in (None, 0) or hits is None else 100.0 * hits / queries
    requests = counters["beam_requests"]

    def mean_ms(key: str) -> float | None:
        value = counters[key]
        return None if requests in (None, 0) or value is None else 1000.0 * value / requests

    prefill = mean_ms("beam_prefill_seconds")
    decode = mean_ms("beam_decode_seconds")
    sort = mean_ms("beam_sort_seconds")
    means = [prefill, decode, sort]
    write(
        output,
        {
            "before": before_path.name,
            "after": after_path.name,
            "counters": counters,
            "prefix_cache": {
                "queries": queries,
                "hits": hits,
                "hit_rate_percent": hit_rate,
                "unit": "tokens",
            },
            "beam_search": {
                "requests": requests,
                "prefill_mean_ms": prefill,
                "decode_mean_ms": decode,
                "sort_mean_ms": sort,
                "total_mean_ms": None if any(value is None for value in means) else sum(means),
            },
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("snapshot")
    capture.add_argument("--url", default="http://127.0.0.1:8000")
    capture.add_argument("--output", type=Path, required=True)
    diff = subparsers.add_parser("delta")
    diff.add_argument("--before", type=Path, required=True)
    diff.add_argument("--after", type=Path, required=True)
    diff.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "snapshot":
        snapshot(args.url, args.output)
    else:
        delta(args.before, args.after, args.output)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Backfill engine-level latency distributions into existing vllm-gr summaries.

The current generate_summary.py emits engine_prefill/engine_decode/
beam_entry_overhead (miss/hit) fields, but the 200+ already-published summaries
were produced before those fields existed.  This script copies those six
distributions straight out of each raw-result.json (they are precomputed there)
into the matching published summary, so the CPU-stage SVG renders on historical
data without re-running the GPU benchmark.
"""

import json
import sys
from pathlib import Path

RESULTS_DIR = Path.home() / "vllm-gr-decode-graph" / "results" / "daily"
RUNS_DIR = Path.home() / "AgentCacheKanban" / "runs" / "vllm-gr" / "L20"

RAW_KEYS = {
    "engine_prefill_miss": "engine_prefill_ms_miss",
    "engine_prefill_hit": "engine_prefill_ms_hit",
    "engine_decode_miss": "engine_decode_ms_miss",
    "engine_decode_hit": "engine_decode_ms_hit",
    "beam_entry_overhead_miss": "beam_entry_overhead_ms_miss",
    "beam_entry_overhead_hit": "beam_entry_overhead_ms_hit",
}


def build_raw_index() -> dict[str, Path]:
    index: dict[str, Path] = {}
    if not RESULTS_DIR.is_dir():
        return index
    for raw in RESULTS_DIR.rglob("raw-result.json"):
        index[raw.parent.name] = raw
    return index


def main() -> int:
    raw_index = build_raw_index()
    print(f"raw-result.json indexed: {len(raw_index)}")

    updated = 0
    already = 0
    skipped_online = 0
    missing_raw = 0

    if not RUNS_DIR.is_dir():
        print(f"runs dir missing: {RUNS_DIR}", file=sys.stderr)
        return 1

    for summary_path in sorted(RUNS_DIR.rglob("vllm-gr-summary.json")):
        scenario_id = summary_path.parent.name
        raw = raw_index.get(scenario_id)
        if raw is None:
            missing_raw += 1
            continue
        raw_data = json.loads(raw.read_text(encoding="utf-8"))
        dist = raw_data.get("distributions", {})
        if "engine_prefill_ms_miss" not in dist:
            skipped_online += 1  # online run has no offline distributions
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        lm = summary["results"]["latency_ms"]
        if "engine_prefill_miss" in lm:
            already += 1
            continue
        for out_key, raw_key in RAW_KEYS.items():
            if raw_key in dist:  # older raw results lack beam_entry_overhead
                lm[out_key] = dist[raw_key]
        summary_path.write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        updated += 1

    print(
        f"updated: {updated}, already: {already}, "
        f"skipped(online): {skipped_online}, missing raw: {missing_raw}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

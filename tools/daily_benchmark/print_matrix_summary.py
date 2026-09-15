#!/usr/bin/env python3
"""Print a compact table from a daily benchmark matrix directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("matrix_dir", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--all", action="store_true", help="include unqualified smoke runs")
    args = parser.parse_args()

    rows = []
    for path in sorted(args.matrix_dir.glob("daily-*/vllm-gr-summary.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not args.all and not data["run"]["trend_eligible"]:
            continue
        sweep = data["scenario"]["sweep"]
        latency = data["results"]["latency_ms"]
        diagnostic = data["results"].get("diagnostic") or {}
        diagnostic_latency = diagnostic.get("latency_ms", latency)
        e2el = latency["e2el"]
        cache = data["results"].get("cache", {}).get("prefix", {})
        rows.append(
            {
                "beam": sweep["beam_width"],
                "input": sweep["input_tokens"],
                "e2el_p50_ms": e2el["p50"],
                "e2el_p90_ms": e2el["p90"],
                "e2el_p99_ms": e2el["p99"],
                "e2el_hit_p50_ms": latency.get("e2el_hit", {}).get("p50"),
                "prefill_miss_p50_ms": diagnostic_latency.get("prefill_miss", {}).get("p50"),
                "decode_p50_ms": diagnostic_latency.get(
                    "decode", diagnostic_latency.get("decode_miss", {})
                ).get("p50"),
                "llm_engine_decode_p50_ms": diagnostic_latency.get(
                    "llm_engine_decode", {}
                ).get("p50"),
                "cache_hit_percent": cache.get("hit_rate_percent"),
                "completed": data["results"]["requests"]["completed"],
                "qualified": data["run"]["trend_eligible"],
            }
        )

    rows.sort(key=lambda row: (row["input"] != 1024, row["input"], row["beam"]))
    if args.as_json:
        print(json.dumps(rows, indent=2))
        return 0

    print("beam\tinput\te2e_miss_p50_ms\te2e_hit_p50_ms\tprefill_miss_diag_p50_ms\tllm_engine_decode_diag_p50_ms\tdecode_diag_p50_ms\trequests\tqualified")
    for row in rows:
        optional = lambda value: "n/a" if value is None else f"{value:.2f}"
        print(
            f'{row["beam"]}\t{row["input"]}\t{row["e2el_p50_ms"]:.2f}\t'
            f'{optional(row["e2el_hit_p50_ms"])}\t{optional(row["prefill_miss_p50_ms"])}\t'
            f'{optional(row["llm_engine_decode_p50_ms"])}\t{optional(row["decode_p50_ms"])}\t'
            f'{row["completed"]}\t{row["qualified"]}'
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

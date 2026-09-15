#!/usr/bin/env python3
"""Summarize an absolute-benchmark run into a team-comparable matrix.

Scans <root>/bw<beam>-in<input>/run<N>/vllm-gr-summary.json (one summary per
repeat), averages each percentile across repeats, and writes:
  - <output-md>  : transposed markdown matrix (rows = metrics, cols = scenarios)
  - <output-json>: full metric x percentile x scenario data for later analysis

Metrics are read from results.diagnostic.latency_ms (the full ~34-metric phase
breakdown captured when VLLM_GR_LIGHTWEIGHT_TIMING=1), falling back to the
canonical results.latency_ms (e2el/e2el_hit only) when diagnostic is absent.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

KEY_METRICS = [
    "e2el",          # end-to-end, cold-cache (miss)
    "e2el_hit",      # end-to-end, warm-cache (hit)
    "prefill",       # prefill phase, combined
    "prefill_hit",   # prefill phase, warm-cache hit
    "decode",        # decode phase, combined
    "decode_hit",    # decode phase, warm-cache hit
    "total_beam",    # full beam-search aggregate
]
PERCENTILES = ("p50", "p90", "p99")


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def metric_source(s: dict) -> dict:
    """Full phase breakdown lives under results.diagnostic.latency_ms; the
    canonical e2el/e2el_hit under results.latency_ms. Prefer the former."""
    diag = s.get("results", {}).get("diagnostic")
    if diag and diag.get("latency_ms"):
        return diag["latency_ms"]
    return s.get("results", {}).get("latency_ms", {})


def sort_key(name: str):
    # "bw32-in1024" -> (32, 1024)
    beam = int(name.split("bw", 1)[1].split("-in", 1)[0])
    ilen = int(name.split("-in", 1)[1])
    return (beam, ilen)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True,
                    help="directory containing bw*-in*/run*/vllm-gr-summary.json")
    ap.add_argument("--output-md", type=Path, required=True)
    ap.add_argument("--output-json", type=Path, required=True)
    ap.add_argument("--git-sha", default="", help="commit sha for the report header")
    ap.add_argument("--warmup", default="4")
    ap.add_argument("--num-prompts", default="100")
    args = ap.parse_args()

    scenarios: dict[str, list[dict]] = {}
    for d in sorted(args.root.iterdir(), key=lambda x: x.name):
        if not d.is_dir() or "bw" not in d.name or "-in" not in d.name:
            continue
        summaries = sorted(d.glob("run*/vllm-gr-summary.json"))
        if summaries:
            scenarios[d.name] = [load(p) for p in summaries]

    if not scenarios:
        print(f"no scenario summaries found under {args.root}", file=sys.stderr)
        return 1

    # scenario -> metric -> percentile -> averaged value
    merged: dict[str, dict[str, dict[str, float]]] = {}
    repeats: dict[str, int] = {}
    for name, summaries in scenarios.items():
        repeats[name] = len(summaries)
        m: dict[str, dict[str, float]] = {}
        for metric in metric_source(summaries[0]):
            row: dict[str, float] = {}
            for p in PERCENTILES:
                vals = [
                    float(metric_source(s)[metric][p])
                    for s in summaries
                    if metric in metric_source(s) and p in metric_source(s)[metric]
                ]
                if vals:
                    row[p] = sum(vals) / len(vals)
            if row:
                m[metric] = row
        merged[name] = m

    order = sorted(merged, key=sort_key)
    all_metrics = sorted({m for v in merged.values() for m in v})
    key = [m for m in KEY_METRICS if m in all_metrics]
    rest = [m for m in all_metrics if m not in KEY_METRICS]
    n_repeats = next(iter(repeats.values()))

    def cell(metric: str, name: str) -> str:
        r = merged[name].get(metric)
        if not r:
            return "—"
        vals = [f"{r[p]:.2f}" if p in r else "—" for p in PERCENTILES]
        return " / ".join(vals)

    header = "| Metric | " + " | ".join(order) + " |"
    sep = "|---|" + "---|" * len(order)

    def emit_table(metrics: list[str]) -> list[str]:
        rows = [header, sep]
        for metric in metrics:
            rows.append(f"| {metric} | " + " | ".join(cell(metric, n) for n in order) + " |")
        return rows

    sha = args.git_sha[:8] if args.git_sha else "unknown"
    lines: list[str] = []
    lines.append(f"# Absolute Benchmark Matrix · decode_graph @ `{sha}`")
    lines.append("")
    lines.append("| Item | Value |")
    lines.append("|---|---|")
    lines.append("| Model | OpenOneRec/OneRec-1.7B (beam search, max_tokens=5) |")
    lines.append(f"| Schedule | warmup {args.warmup} + {args.num_prompts} requests per run, "
                 f"{n_repeats} repeats averaged |")
    lines.append("| Concurrency | 1 |")
    lines.append("| GPU | 1 (NVIDIA L20) |")
    lines.append("")
    lines.append("> Each cell = `p50 / p90 / p99` (ms), averaged across repeats. Lower is better.")
    lines.append("")
    lines.append("## Key metrics")
    lines.append("")
    lines.extend(emit_table(key))
    lines.append("")
    lines.append("## Full metric breakdown")
    lines.append("")
    lines.extend(emit_table(all_metrics))
    lines.append("")

    text = "\n".join(lines) + "\n"
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(text, encoding="utf-8")

    payload = {
        "git_sha": sha,
        "warmup": args.warmup,
        "num_prompts": args.num_prompts,
        "repeats": repeats,
        "percentiles": list(PERCENTILES),
        "key_metrics": key,
        "all_metrics": all_metrics,
        "scenarios": merged,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

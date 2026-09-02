#!/usr/bin/env python3
"""Build the static vllm-gr performance overview page.

Reuses discover_runs() from build_vllm_gr_dashboard.py to render a QWEN-style
"Section 1" overview: metric definitions, a key comparison table, a beam_search
pipeline schematic, and an E2E latency localization chart.  Static and
self-contained (inline SVG), unlike the interactive dashboard.
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_vllm_gr_dashboard import discover_runs  # noqa: E402

DEFAULT_INPUT_TOKENS = 1024  # fixed axis for beam-width comparison
DEFAULT_BEAM_WIDTH = 128     # fixed axis for input-length scaling


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def number(value: object, digits: int = 2, absent: str = "—") -> str:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return absent
    return f"{parsed:.{digits}f}"


def latency_p50(summary: dict[str, Any], metric: str) -> float:
    dist = summary["results"]["latency_ms"].get(metric)
    if dist is None:
        raise ValueError(f"missing latency metric {metric}")
    return float(dist["p50"])


def scenario_beam(summary: dict[str, Any]) -> int:
    return int(summary["scenario"]["n"])


def scenario_input(summary: dict[str, Any]) -> int:
    return int(summary["scenario"]["input_tokens_target"])


def latest_date(runs: list[dict[str, Any]]) -> str:
    return max(item["summary"]["run"]["date"] for item in runs)


def select_date(runs: list[dict[str, Any]], date: str) -> list[dict[str, Any]]:
    return [item for item in runs if item["summary"]["run"]["date"] == date]


# --- float metrics (shared by tables and SVG) ---


def beam_width_metrics(runs: list[dict[str, Any]], input_tokens: int) -> list[dict[str, Any]]:
    """Per-beam-width float metrics at a fixed input length."""
    items = [
        item for item in runs if scenario_input(item["summary"]) == input_tokens
    ]
    items.sort(key=lambda item: scenario_beam(item["summary"]))
    metrics: list[dict[str, Any]] = []
    for item in items:
        s = item["summary"]
        lm = s["results"]["latency_ms"]
        prefill_miss = float(lm["prefill_miss"]["p50"])
        prefill_hit = float(lm["prefill_hit"]["p50"])
        decode = float(lm["decode"]["p50"])
        e2el_miss = float(lm["e2el"]["p50"])
        e2el_hit = float(lm["e2el_hit"]["p50"])
        metrics.append(
            {
                "beam": scenario_beam(s),
                "prefill_miss": prefill_miss,
                "prefill_hit": prefill_hit,
                "decode": decode,
                "e2el_miss": e2el_miss,
                "e2el_hit": e2el_hit,
                "entry_miss": max(0.0, e2el_miss - prefill_miss - decode),
                "entry_hit": max(0.0, e2el_hit - prefill_hit - decode),
            }
        )
    return metrics


def input_metrics(runs: list[dict[str, Any]], beam_width: int) -> list[dict[str, Any]]:
    """Per-input-length float metrics at a fixed beam width."""
    items = [item for item in runs if scenario_beam(item["summary"]) == beam_width]
    items.sort(key=lambda item: scenario_input(item["summary"]))
    metrics: list[dict[str, Any]] = []
    for item in items:
        s = item["summary"]
        lm = s["results"]["latency_ms"]
        metrics.append(
            {
                "input": scenario_input(s),
                "prefill_miss": float(lm["prefill_miss"]["p50"]),
                "prefill_hit": float(lm["prefill_hit"]["p50"]),
                "decode": float(lm["decode"]["p50"]),
                "e2el_miss": float(lm["e2el"]["p50"]),
                "e2el_hit": float(lm["e2el_hit"]["p50"]),
            }
        )
    return metrics


# --- table formatting ---


def beam_width_rows(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for m in metrics:
        rows.append(
            {
                "beam": m["beam"],
                "e2el_miss": number(m["e2el_miss"]),
                "e2el_hit": number(m["e2el_hit"]),
                "prefill_miss": number(m["prefill_miss"]),
                "prefill_hit": number(m["prefill_hit"]),
                "decode": number(m["decode"]),
                "speedup": f"{m['e2el_miss'] / m['e2el_hit']:.2f}×" if m["e2el_hit"] else "—",
            }
        )
    return rows


def input_rows(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for m in metrics:
        rows.append(
            {
                "input": m["input"],
                "e2el_miss": number(m["e2el_miss"]),
                "e2el_hit": number(m["e2el_hit"]),
                "prefill_miss": number(m["prefill_miss"]),
                "prefill_hit": number(m["prefill_hit"]),
                "decode": number(m["decode"]),
            }
        )
    return rows


def table(rows: list[dict[str, Any]], fields: list[tuple[str, str]], numeric: set[str]) -> str:
    parts = ['<div class="vgr-table-wrap"><table class="vgr-overview-table"><thead><tr>']
    for _key, label in fields:
        parts.append(f"<th>{esc(label)}</th>")
    parts.append("</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>")
        for key, _label in fields:
            cls = ' class="num"' if key in numeric else ""
            parts.append(f"<td{cls}>{esc(row.get(key, ''))}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


# --- SVG renderers ---


def render_pipeline_svg(summary: dict[str, Any]) -> str:
    """Schematic of one request through entry, prefill, and decode (miss vs hit).

    Entry and decode are shared across miss/hit; only prefill differs because a
    cache hit skips KV re-encoding.  Geometry is schematic, not an exact trace.
    """
    lm = summary["results"]["latency_ms"]
    prefill_miss = float(lm["prefill_miss"]["p50"])
    prefill_hit = float(lm["prefill_hit"]["p50"])
    decode = float(lm["decode"]["p50"])
    e2el_miss = float(lm["e2el"]["p50"])
    e2el_hit = float(lm["e2el_hit"]["p50"])
    entry_miss = max(0.0, e2el_miss - prefill_miss - decode)
    entry_hit = max(0.0, e2el_hit - prefill_hit - decode)

    scale = 6.0  # px per ms
    left = 150
    lane_h = 56
    gap = 9  # arrow gap between boxes
    top_miss = 60
    lane_gap = 56
    top_hit = top_miss + lane_h + lane_gap

    miss = [("Entry", entry_miss, "vgr-fill-entry"), ("Prefill", prefill_miss, "vgr-fill-prefill"), ("Decode", decode, "vgr-fill-decode")]
    hit = [("Entry", entry_hit, "vgr-fill-entry"), ("Prefill", prefill_hit, "vgr-fill-prefill"), ("Decode", decode, "vgr-fill-decode")]

    def lane_width(segs: list[tuple[str, float, str]]) -> float:
        return sum(v for _, v, _ in segs) * scale + gap * (len(segs) - 1)

    width = left + max(lane_width(miss), lane_width(hit)) + 60
    height = top_hit + lane_h + 44

    parts = [
        '<svg class="vgr-overview-figure" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" role="img" '
        'aria-labelledby="pipeline-title pipeline-desc">',
        "<title id=\"pipeline-title\">Beam-search pipeline schematic</title>",
        "<desc id=\"pipeline-desc\">Two lanes compare a cold (miss) and warm (hit) "
        "request. Entry and decode are shared; only prefill shrinks on a cache hit.</desc>",
    ]

    def lane(y: float, label: str, segs: list[tuple[str, float, str]]) -> list[str]:
        out = [
            f'<text class="vgr-svg-label" x="{left - 16}" y="{y + lane_h / 2 + 4}" '
            f'text-anchor="end">{label}</text>'
        ]
        cursor = left
        for i, (name, value, fill) in enumerate(segs):
            w = value * scale
            out.append(
                f'<rect class="{fill}" x="{cursor:.1f}" y="{y:.1f}" width="{w:.1f}" '
                f'height="{lane_h}" rx="6"><title>{name}: {value:.1f} ms</title></rect>'
            )
            if w >= 55:
                out.append(
                    f'<text class="vgr-svg-text" x="{cursor + 10:.1f}" y="{y + lane_h / 2 + 4}">{name}</text>'
                )
            out.append(
                f'<text class="vgr-svg-muted" x="{cursor + w / 2:.1f}" y="{y + lane_h + 16}" '
                f'text-anchor="middle">{value:.1f} ms</text>'
            )
            cursor += w
            if i < len(segs) - 1:
                ax = cursor + gap
                out.append(
                    f'<path class="vgr-svg-arrow" d="M{cursor:.1f} {y + lane_h / 2 - 5} '
                    f'L{ax - 1:.1f} {y + lane_h / 2} L{cursor:.1f} {y + lane_h / 2 + 5} Z"/>'
                )
                cursor += gap
        return out

    parts += lane(top_miss, "Cold (miss)", miss)
    parts += lane(top_hit, "Warm (hit)", hit)
    parts.append("</svg>")
    return "".join(parts)


def render_e2e_stacked_svg(metrics: list[dict[str, Any]]) -> str:
    """Horizontal stacked bars: E2E = entry + prefill + decode, miss versus hit."""
    left = 120
    row_h = 34
    bar_h = 22
    top = 58
    group_gap = 26
    max_val = max(m["e2el_miss"] for m in metrics)
    scale = 660.0 / max_val  # px per ms

    height = top + len(metrics) * (2 * row_h) + (len(metrics) - 1) * group_gap + 44
    width = left + 660 + 110

    parts = [
        '<svg class="vgr-overview-figure" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" role="img" '
        'aria-labelledby="e2e-title e2e-desc">',
        "<title id=\"e2e-title\">E2E latency localization</title>",
        "<desc id=\"e2e-desc\">Stacked bars split E2E into entry, prefill, and decode "
        "for cold (miss) and warm (hit) requests across beam widths.</desc>",
    ]

    # legend
    legend = [("Prefill", "vgr-fill-prefill"), ("Decode", "vgr-fill-decode"), ("Entry", "vgr-fill-entry")]
    lx = left + 660 - 210
    for i, (name, fill) in enumerate(legend):
        x = lx + i * 80
        parts.append(f'<rect class="{fill}" x="{x}" y="22" width="13" height="13" rx="3"/>')
        parts.append(f'<text class="vgr-svg-label" x="{x + 18}" y="33">{name}</text>')

    y = top
    for m in metrics:
        y_miss = y
        y_hit = y + row_h
        mid = (y_miss + y_hit + bar_h) / 2
        parts.append(
            f'<text class="vgr-svg-label" x="{left - 16}" y="{mid + 4}" '
            f'text-anchor="end">beam {m["beam"]}</text>'
        )

        def bar(yy: float, kind: str, prefill: float, entry: float, total: float) -> list[str]:
            segs = [("entry", entry, "vgr-fill-entry"), ("prefill", prefill, "vgr-fill-prefill"), ("decode", m["decode"], "vgr-fill-decode")]
            out = []
            cursor = left
            for name, value, fill in segs:
                w = value * scale
                out.append(
                    f'<rect class="{fill}" x="{cursor:.1f}" y="{yy}" width="{w:.1f}" '
                    f'height="{bar_h}" rx="3"><title>{name}: {value:.1f} ms</title></rect>'
                )
                cursor += w
            out.append(
                f'<text class="vgr-svg-label" x="{left + 660 + 8}" y="{yy + bar_h - 7}" '
                f'text-anchor="start">{kind} · {total:.1f} ms</text>'
            )
            return out

        parts += bar(y_miss, "miss", m["prefill_miss"], m["entry_miss"], m["e2el_miss"])
        parts += bar(y_hit, "hit", m["prefill_hit"], m["entry_hit"], m["e2el_hit"])
        y = y_hit + row_h + group_gap

    parts.append("</svg>")
    return "".join(parts)


def render_cpu_pipeline_svg(summary: dict[str, Any]) -> str:
    """CPU-vs-GPU stage breakdown of one beam_search call (miss vs hit).

    Mirrors QWEN's "Async CPU pipeline mechanism": every stage is split into
    GPU engine time (engine_prefill/engine_decode) and CPU-side overhead
    (beam-entry preparation, prefill bookkeeping, decode bookkeeping/sort/
    reconstruct/detokenize).  Returns "" when the summary predates the engine
    instrumentation so the caller can drop the section.
    """
    lm = summary["results"]["latency_ms"]

    def p50(name: str) -> float | None:
        dist = lm.get(name)
        return float(dist["p50"]) if dist else None

    eng_pf_miss = p50("engine_prefill_miss")
    eng_dc_miss = p50("engine_decode_miss")
    if eng_pf_miss is None or eng_dc_miss is None:
        return ""

    prefill_miss = p50("prefill_miss")
    prefill_hit = p50("prefill_hit")
    decode = p50("decode")
    e2el_miss = p50("e2el")
    e2el_hit = p50("e2el_hit")
    entry_miss = p50("beam_entry_overhead_miss")
    entry_hit = p50("beam_entry_overhead_hit")
    if entry_miss is None:
        entry_miss = max(0.0, e2el_miss - prefill_miss - decode)
    if entry_hit is None:
        entry_hit = max(0.0, e2el_hit - prefill_hit - decode)

    def stages(
        eng_pf: float, eng_dc: float, prefill: float, entry: float, ovh: float
    ) -> list[tuple[str, list[tuple[str, float]]]]:
        return [
            ("Entry", [("CPU", entry)]),
            ("Prefill", [("GPU", eng_pf), ("CPU", max(0.0, prefill - eng_pf))]),
            ("Decode", [("GPU", eng_dc), ("CPU", ovh)]),
        ]

    miss = stages(eng_pf_miss, eng_dc_miss, prefill_miss, entry_miss, p50("overhead_miss"))
    hit = stages(p50("engine_prefill_hit"), p50("engine_decode_hit"), prefill_hit, entry_hit, p50("overhead_hit"))

    scale = 5.0  # px per ms
    left = 150
    lane_h = 58
    gap = 12  # arrow gap between stage blocks
    top_miss = 72
    lane_gap = 54
    top_hit = top_miss + lane_h + lane_gap

    def lane_width(segs: list[tuple[str, list[tuple[str, float]]]]) -> float:
        return sum(v for _, subs in segs for _, v in subs) * scale + gap * (len(segs) - 1)

    width = left + max(lane_width(miss), lane_width(hit)) + 70
    height = top_hit + lane_h + 52

    parts = [
        '<svg class="vgr-overview-figure" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" role="img" '
        'aria-labelledby="cpu-title cpu-desc">',
        '<title id="cpu-title">CPU/GPU stage breakdown</title>',
        '<desc id="cpu-desc">Each stage splits into GPU engine time and CPU-side '
        "overhead for cold (miss) and warm (hit) requests.</desc>",
    ]

    # legend
    legend = [("GPU engine", "vgr-fill-gpu"), ("CPU overhead", "vgr-fill-cpu")]
    lx = width - 210
    for i, (name, fill) in enumerate(legend):
        x = lx + i * 108
        parts.append(f'<rect class="{fill}" x="{x}" y="22" width="13" height="13" rx="3"/>')
        parts.append(f'<text class="vgr-svg-label" x="{x + 18}" y="33">{name}</text>')

    def lane(y: float, label: str, segs: list[tuple[str, list[tuple[str, float]]]]) -> list[str]:
        out = [
            f'<text class="vgr-svg-label" x="{left - 16}" y="{y + lane_h / 2 + 4}" '
            f'text-anchor="end">{label}</text>'
        ]
        cursor = left
        for i, (name, subs) in enumerate(segs):
            block_w = sum(v for _, v in subs) * scale
            out.append(
                f'<text class="vgr-svg-label" x="{cursor + block_w / 2:.1f}" y="{y - 10:.1f}" '
                f'text-anchor="middle">{name}</text>'
            )
            for kind, value in subs:
                w = value * scale
                fill = "vgr-fill-gpu" if kind == "GPU" else "vgr-fill-cpu"
                out.append(
                    f'<rect class="{fill}" x="{cursor:.1f}" y="{y:.1f}" width="{w:.1f}" '
                    f'height="{lane_h}" rx="4"><title>{name} {kind}: {value:.1f} ms</title></rect>'
                )
                if w >= 42:
                    out.append(
                        f'<text class="vgr-svg-text" x="{cursor + 7:.1f}" y="{y + lane_h / 2 + 4}">{kind}</text>'
                    )
                if w >= 30:
                    out.append(
                        f'<text class="vgr-svg-muted" x="{cursor + w / 2:.1f}" y="{y + lane_h + 16}" '
                        f'text-anchor="middle">{value:.1f}</text>'
                    )
                cursor += w
            if i < len(segs) - 1:
                ax = cursor + gap
                out.append(
                    f'<path class="vgr-svg-arrow" d="M{cursor:.1f} {y + lane_h / 2 - 5} '
                    f'L{ax - 1:.1f} {y + lane_h / 2} L{cursor:.1f} {y + lane_h / 2 + 5} Z"/>'
                )
                cursor += gap
        return out

    parts += lane(top_miss, "Cold (miss)", miss)
    parts += lane(top_hit, "Warm (hit)", hit)
    parts.append("</svg>")
    return "".join(parts)


def render_cpu_pipeline_flow_svg(summary: dict[str, Any]) -> str:
    """Per-stage CPU time of the beam_search decode loop as a swimlane chart.

    Each of the six decode CPU stages (prepare / decision / eos / top-k /
    materialize / sort) is one horizontal lane; bar length encodes its p50 wall
    time summed over all decode tokens (tokens >= 1).  Returns "" when the
    summary predates the CPU stage instrumentation so the caller can drop the
    section.
    """
    lm = summary["results"]["latency_ms"]

    def p50(name: str) -> float | None:
        dist = lm.get(name)
        return float(dist["p50"]) if dist else None

    stages: list[tuple[str, float]] = [
        ("Prepare", p50("cpu_prepare")),
        ("Decision", p50("cpu_decision")),
        ("EOS", p50("cpu_eos")),
        ("Top-k", p50("cpu_topk")),
        ("Materialize", p50("cpu_materialize")),
        ("Sort", p50("sort")),
    ]
    if any(value is None for _, value in stages):
        return ""

    values = [value for _, value in stages]
    max_val = max(values) or 1.0

    left = 150
    bar_area = 620
    lane_h = 34
    bar_h = 20
    lane_gap = 12
    top = 42
    scale = bar_area / max_val

    width = left + bar_area + 120
    axis_y = top + len(stages) * lane_h + (len(stages) - 1) * lane_gap + 6
    height = axis_y + 26

    parts = [
        '<svg class="vgr-overview-figure" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" role="img" '
        'aria-labelledby="cpuflow-title cpuflow-desc">',
        '<title id="cpuflow-title">CPU pipeline breakdown</title>',
        '<desc id="cpuflow-desc">Per-decode-token CPU time for each beam-search '
        "output-processing stage (prepare, decision, EOS, top-k, materialize, "
        "sort). Bar length encodes p50 time.</desc>",
    ]

    y = top
    for name, value in stages:
        lane_center = y + lane_h / 2
        parts.append(
            f'<text class="vgr-svg-label" x="{left - 16}" y="{lane_center + 4:.1f}" '
            f'text-anchor="end">{name}</text>'
        )
        w = value * scale
        parts.append(
            f'<rect class="vgr-fill-cpu" x="{left:.1f}" '
            f'y="{y + (lane_h - bar_h) / 2:.1f}" width="{w:.1f}" '
            f'height="{bar_h}" rx="4"><title>{name}: {value:.2f} ms</title></rect>'
        )
        parts.append(
            f'<text class="vgr-svg-muted" x="{left + w + 8:.1f}" '
            f'y="{lane_center + 4:.1f}" text-anchor="start">{value:.2f} ms</text>'
        )
        y += lane_h + lane_gap

    # x-axis baseline and 0 / max tick labels
    parts.append(
        f'<rect class="vgr-fill-entry" x="{left}" y="{axis_y}" '
        f'width="{bar_area}" height="1"/>'
    )
    parts.append(
        f'<text class="vgr-svg-muted" x="{left}" y="{axis_y + 15}" '
        'text-anchor="start">0</text>'
    )
    parts.append(
        f'<text class="vgr-svg-muted" x="{left + bar_area}" y="{axis_y + 15}" '
        f'text-anchor="end">{max_val:.1f} ms</text>'
    )

    parts.append("</svg>")
    return "".join(parts)


# --- markdown assembly ---


def overview_markdown(runs: list[dict[str, Any]]) -> str:
    date = latest_date(runs)
    day = select_date(runs, date)
    bw_metrics = beam_width_metrics(day, DEFAULT_INPUT_TOKENS)
    in_metrics = input_metrics(day, DEFAULT_BEAM_WIDTH)

    representative = next(
        item for item in day
        if scenario_beam(item["summary"]) == DEFAULT_BEAM_WIDTH
        and scenario_input(item["summary"]) == DEFAULT_INPUT_TOKENS
    )

    cpu_svg = render_cpu_pipeline_svg(representative["summary"])
    cpu_section = (
        [
            '  <section class="vgr-section">',
            '    <div class="vgr-section-head"><div><p class="vgr-kicker">Compute</p>'
            "<h3>1.4 CPU / GPU stage breakdown</h3></div>"
            "<p>GPU engine time versus CPU-side overhead per stage.</p></div>",
            cpu_svg,
            '    <p class="vgr-caption">Each stage splits into GPU engine time '
            "(engine_prefill / engine_decode) and CPU-side overhead. Decode carries "
            "the largest CPU cost — beam bookkeeping, sorting, reconstruction and "
            "detokenization between engine steps — while beam-entry preparation and "
            "prefill bookkeeping are comparatively small. Geometry is schematic, not "
            "an exact trace.</p>",
            "  </section>",
        ]
        if cpu_svg
        else []
    )

    cpu_flow_svg = render_cpu_pipeline_flow_svg(representative["summary"])
    cpu_flow_section = (
        [
            '  <section class="vgr-section">',
            '    <div class="vgr-section-head"><div><p class="vgr-kicker">Pipeline</p>'
            "<h3>1.5 CPU pipeline breakdown</h3></div>"
            "<p>Per-stage CPU time of the beam-search decode loop.</p></div>",
            cpu_flow_svg,
            '    <p class="vgr-caption">Each lane is one decode-loop CPU stage; '
            "bar length encodes p50 wall time summed over all decode tokens "
            "(tokens&nbsp;≥&nbsp;1). prepare + decision + eos + top-k + "
            "materialize + sort approximates decode overhead. Top-k is zero on "
            "the worker-decision path, where the accelerator pre-selects the "
            "surviving beams.</p>",
            "  </section>",
        ]
        if cpu_flow_svg
        else []
    )

    return "\n".join(
        [
            "## Performance overview",
            "",
            f"Static overview of the latest serving-aligned sweep ({date}). "
            "Key comparison table, beam_search pipeline schematic, E2E latency "
            "localization, and CPU/GPU + per-stage CPU pipeline breakdowns.",
            "",
            '<div class="vgr-dashboard vgr-overview" id="vgr-overview">',
            '  <div class="vgr-boundary"><strong>Goal.</strong> Locate where wall-clock '
            "time is spent in one offline <code>GRLLM.beam_search()</code> call, and how "
            "prefix-cache hit/miss changes that split.<br><strong>Measurement.</strong> "
            "All numbers are p50 over 100 paired reset-then-repeat calls; offline E2E "
            "excludes HTTP, SSE, serialization, and network round trip.</div>",
            '  <section class="vgr-section">',
            '    <div class="vgr-section-head"><div><p class="vgr-kicker">Overview</p>'
            "<h3>1.1 Performance overview</h3></div>"
            f"<p>Beam width sweep at input&nbsp;{DEFAULT_INPUT_TOKENS} tokens.</p></div>",
            '    <div class="vgr-scope-envelope"><strong>Metric definitions.</strong> '
            "<strong>E2E miss</strong> is one cold-cache <code>beam_search</code> call; "
            "<strong>E2E hit</strong> immediately repeats the identical prompt. "
            "<strong>Prefill</strong> spans the beam token-loop start through token 0 "
            "(miss rebuilds Prefix Cache; hit reuses it). <strong>Decode</strong> is "
            "token 1 preparation through <code>beam_search</code> return — a common "
            "distribution over miss/hit observations, including beam bookkeeping, "
            "sorting, reconstruction and detokenization. <strong>Entry</strong> is "
            "entry-side prompt and initial-beam preparation: part of E2E but outside "
            "both Prefill and Decode.</div>",
            table(
                beam_width_rows(bw_metrics),
                [
                    ("beam", "Beam width"),
                    ("e2el_miss", "E2E miss p50 (ms)"),
                    ("e2el_hit", "E2E hit p50 (ms)"),
                    ("prefill_miss", "Prefill miss (ms)"),
                    ("prefill_hit", "Prefill hit (ms)"),
                    ("decode", "Decode (ms)"),
                    ("speedup", "Cache speedup"),
                ],
                {"beam", "e2el_miss", "e2el_hit", "prefill_miss", "prefill_hit", "decode", "speedup"},
            ),
            '    <p class="vgr-caption">A cache hit cuts E2E almost entirely through '
            "prefill; decode and entry-side preparation are shared between miss and hit.</p>",
            "  </section>",
            '  <section class="vgr-section">',
            '    <div class="vgr-section-head"><div><p class="vgr-kicker">Mechanism</p>'
            "<h3>1.2 Beam-search pipeline</h3></div>"
            "<p>Schematic of one request through entry, prefill, and decode.</p></div>",
            render_pipeline_svg(representative["summary"]),
            '    <p class="vgr-caption">Entry and decode are shared across miss and hit; '
            "the cache shortens only prefill (skipping KV re-encoding). Geometry is "
            "schematic, not an exact trace.</p>",
            "  </section>",
            '  <section class="vgr-section">',
            '    <div class="vgr-section-head"><div><p class="vgr-kicker">Localization</p>'
            "<h3>1.3 E2E latency localization</h3></div>"
            "<p>E2E split into entry + prefill + decode, miss versus hit.</p></div>",
            render_e2e_stacked_svg(bw_metrics),
            '    <p class="vgr-caption">Decode grows with beam width (each token expands '
            "more beams), while prefill is nearly flat; the miss/hit gap is entirely "
            "prefill. Entry stays small and cache-independent.</p>",
            "  </section>",
            *cpu_section,
            *cpu_flow_section,
            '  <section class="vgr-section">',
            '    <div class="vgr-section-head"><div><p class="vgr-kicker">Scaling</p>'
            "<h3>Input-length scaling</h3></div>"
            f"<p>Beam&nbsp;{DEFAULT_BEAM_WIDTH} across input lengths.</p></div>",
            table(
                input_rows(in_metrics),
                [
                    ("input", "Input tokens"),
                    ("e2el_miss", "E2E miss p50 (ms)"),
                    ("e2el_hit", "E2E hit p50 (ms)"),
                    ("prefill_miss", "Prefill miss (ms)"),
                    ("prefill_hit", "Prefill hit (ms)"),
                    ("decode", "Decode (ms)"),
                ],
                {"input", "e2el_miss", "e2el_hit", "prefill_miss", "prefill_hit", "decode"},
            ),
            '    <p class="vgr-caption">Prefill miss grows steeply with input length '
            "while decode rises more slowly; prefill hit also grows but from a much "
            "lower base because cached prompts skip re-encoding.</p>",
            "  </section>",
            "</div>",
        ]
    )


def write_overview(source: Path, output: Path) -> None:
    """Append the static Section 1 overview onto the Performance page (vllm-gr.md).

    build_vllm_gr_dashboard.py writes vllm-gr.md first; this runs after it in CI and
    appends the "## Performance overview" section instead of emitting a separate page.
    """
    runs = discover_runs(source)
    output.mkdir(parents=True, exist_ok=True)
    target = output / "vllm-gr.md"
    if not runs:
        if not target.exists():
            target.write_text(
                "# vllm-gr Performance\n\nNo vllm-gr artifacts found.\n",
                encoding="utf-8",
            )
        return
    body = overview_markdown(runs)
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        # 幂等：重复运行时截断到已存在的 Section 1 之前
        if "## Performance overview" in existing:
            existing = existing.split("## Performance overview")[0].rstrip() + "\n"
        target.write_text(existing + "\n" + body + "\n", encoding="utf-8")
    else:
        target.write_text("# vllm-gr Performance\n\n" + body + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("runs"))
    parser.add_argument("--output", type=Path, default=Path("docs"))
    args = parser.parse_args()
    write_overview(args.source, args.output)


if __name__ == "__main__":
    main()

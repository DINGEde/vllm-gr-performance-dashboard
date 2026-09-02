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

    return "\n".join(
        [
            "# vllm-gr Performance Overview",
            "",
            f"Static overview of the latest serving-aligned sweep ({date}). "
            "Key comparison table, beam_search pipeline schematic, and E2E latency "
            "localization. For interactive trends and run history, see "
            "[vllm-gr Performance](vllm-gr.md).",
            "",
            '<div class="vgr-dashboard vgr-overview" id="vgr-overview">',
            '  <div class="vgr-boundary"><strong>Goal.</strong> Locate where wall-clock '
            "time is spent in one offline <code>GRLLM.beam_search()</code> call, and how "
            "prefix-cache hit/miss changes that split.<br><strong>Measurement.</strong> "
            "All numbers are p50 over 100 paired reset-then-repeat calls; offline E2E "
            "excludes HTTP, SSE, serialization, and network round trip.</div>",
            '  <section class="vgr-section">',
            '    <div class="vgr-section-head"><div><p class="vgr-kicker">Overview</p>'
            "<h2>1.1 Performance overview</h2></div>"
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
            "<h2>1.2 Beam-search pipeline</h2></div>"
            "<p>Schematic of one request through entry, prefill, and decode.</p></div>",
            render_pipeline_svg(representative["summary"]),
            '    <p class="vgr-caption">Entry and decode are shared across miss and hit; '
            "the cache shortens only prefill (skipping KV re-encoding). Geometry is "
            "schematic, not an exact trace.</p>",
            "  </section>",
            '  <section class="vgr-section">',
            '    <div class="vgr-section-head"><div><p class="vgr-kicker">Localization</p>'
            "<h2>1.3 E2E latency localization</h2></div>"
            "<p>E2E split into entry + prefill + decode, miss versus hit.</p></div>",
            render_e2e_stacked_svg(bw_metrics),
            '    <p class="vgr-caption">Decode grows with beam width (each token expands '
            "more beams), while prefill is nearly flat; the miss/hit gap is entirely "
            "prefill. Entry stays small and cache-independent.</p>",
            "  </section>",
            '  <section class="vgr-section">',
            '    <div class="vgr-section-head"><div><p class="vgr-kicker">Scaling</p>'
            "<h2>Input-length scaling</h2></div>"
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
    runs = discover_runs(source)
    output.mkdir(parents=True, exist_ok=True)
    if not runs:
        (output / "vllm-gr-overview.md").write_text(
            "# vllm-gr Performance Overview\n\nNo vllm-gr artifacts found.\n",
            encoding="utf-8",
        )
        return
    (output / "vllm-gr-overview.md").write_text(
        overview_markdown(runs) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("runs"))
    parser.add_argument("--output", type=Path, default=Path("docs"))
    args = parser.parse_args()
    write_overview(args.source, args.output)


if __name__ == "__main__":
    main()

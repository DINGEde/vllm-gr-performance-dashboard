"""Build the NPU v2 (raw.v2 protocol) dashboard page and data snapshot.

The v2 dashboard aligns with the GPU dashboard (``build_vllm_gr_dashboard.py``
and ``docs/javascripts/vllm-gr-dashboard.js``): the same section order and
visual language, fed by the raw.v2 result files that
``tools/daily_benchmark/run_offline_benchmark_npu.py`` produces
(``schema_version == "vllm-gr.offline.raw.v2"`` and ``device == "npu"``).

It emits two artifacts:

* ``docs/npu-vllm-gr-v2-data.json`` -- an accumulating snapshot that keeps one
  point per (scenario, day) and prunes anything older than ``RETENTION_DAYS``,
  exactly like ``build_npu_dashboard.py`` does for the legacy snapshot.
* ``docs/npu-vllm-gr-v2.md`` -- the GPU-aligned dashboard page whose container
  ids are filled by ``docs/javascripts/npu-v2-dashboard.js``.

The raw.v2 collection helpers (``collect_raw_v2_points``, ``merge_history``,
``prune``, ``build_payload``) live in ``build_npu_dashboard.py`` so the legacy
and v2 dashboards share one parser for the raw.v2 schema. This script is the
sole owner of the v2 snapshot and page; ``build_npu_dashboard.py`` continues to
own only the legacy snapshot and page.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import date
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent

PERCENTILES = ("mean", "p50", "p90", "p95", "p99")

# Canonical E2E is the official daily trend (a paired cold-miss / warm-hit
# measurement). Its miss and hit cache states are split into their own
# single-series cards ("E2E miss" / "E2E hit"); Prefill and Decode come from the
# post-canonical diagnostic pass and are likewise split into miss/hit cards so
# every core trend is a single cache-state series. A ``cache_state`` pins a card
# to one series, ``None`` draws both.
CORE_METRICS = (
    {"key": "e2e_ms", "label": "E2E miss", "unit": "ms", "measurement": "canonical", "cache_state": "miss"},
    {"key": "e2e_ms", "label": "E2E hit", "unit": "ms", "measurement": "canonical", "cache_state": "hit"},
    {"key": "prefill_ms", "label": "Prefill miss", "unit": "ms", "measurement": "diagnostic", "cache_state": "miss"},
    {"key": "prefill_ms", "label": "Prefill hit", "unit": "ms", "measurement": "diagnostic", "cache_state": "hit"},
    {"key": "decode_ms", "label": "Decode miss", "unit": "ms", "measurement": "diagnostic", "cache_state": "miss"},
    {"key": "decode_ms", "label": "Decode hit", "unit": "ms", "measurement": "diagnostic", "cache_state": "hit"},
)

# GPU compute and device-wait appear first (they mirror the GPU dashboard's
# "GPU computation first" ordering); the remaining host-side diagnostics follow.
# The raw.v2 field names keep the shared ``*_gpu_compute_ms`` spelling for the
# single parser, but the human-facing labels say "NPU" -- this is an NPU page.
DIAGNOSTIC_METRICS = (
    {"key": "prefill_gpu_compute_ms", "label": "Prefill NPU compute", "unit": "ms", "measurement": "diagnostic"},
    {"key": "decode_gpu_compute_ms", "label": "Decode NPU compute", "unit": "ms", "measurement": "diagnostic"},
    {"key": "prefill_device_idle_ms", "label": "Prefill device wait", "unit": "ms", "measurement": "diagnostic"},
    {"key": "decode_device_idle_ms", "label": "Decode device wait", "unit": "ms", "measurement": "diagnostic"},
    {"key": "prefill_dispatch_ms", "label": "Prefill dispatch wait", "unit": "ms", "measurement": "diagnostic"},
    {"key": "prefill_cpu_lead_ms", "label": "Prefill / Decode CPU lead", "unit": "ms", "measurement": "diagnostic"},
    {"key": "host_overhead_ms", "label": "Host overhead", "unit": "ms", "measurement": "diagnostic"},
)


def _load_shared() -> Any:
    """Import ``build_npu_dashboard`` by path (single raw.v2 parser)."""
    spec = importlib.util.spec_from_file_location(
        "build_npu_dashboard", _SCRIPT_DIR / "build_npu_dashboard.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


SHARED = _load_shared()
V2_SCHEMA_VERSION = SHARED.V2_SCHEMA_VERSION
RETENTION_DAYS = SHARED.RETENTION_DAYS
collect_raw_v2_points = SHARED.collect_raw_v2_points
merge_history = SHARED.merge_history
prune = SHARED.prune
build_payload = SHARED.build_payload


def build_payload_v2(runs: list[dict[str, Any]], today: date) -> dict[str, Any]:
    """Extend the shared raw.v2 payload with the GPU-aligned metric catalog."""
    payload = build_payload(runs, today, V2_SCHEMA_VERSION)
    core_metrics = list(CORE_METRICS)
    diagnostic_metrics = list(DIAGNOSTIC_METRICS)
    payload.update(
        {
            "core_metrics": core_metrics,
            "diagnostic_metrics": diagnostic_metrics,
            "metrics": core_metrics + diagnostic_metrics,
            "percentiles": list(PERCENTILES),
        }
    )
    return payload


def dashboard_markdown(has_runs: bool) -> str:
    intro = (
        "Daily offline beam-search performance on Ascend `910B1` NPU. The dashboard "
        "shows only raw.v2 results (the canonical paired cold-miss / warm-hit "
        "protocol) and aligns with the GPU dashboard's sections and metric wording. "
        "NPU device spans are a hardware-unvalidated first draft of "
        "`torch.npu.Event` timing; compare them with the GPU columns with caution."
    )
    if not has_runs:
        return "\n".join(
            [
                "# vllm-gr NPU Performance (v2)",
                "",
                intro,
                "",
                "No raw.v2 NPU artifacts found.",
                "",
                '<div class="vgr-dashboard" id="npu-v2-dashboard">',
                "</div>",
                "",
            ]
        )
    return "\n".join(
        [
            "# vllm-gr NPU Performance (v2)",
            "",
            intro,
            "",
            '<div class="vgr-dashboard" id="npu-v2-dashboard">',
            '  <div class="vgr-toolbar">',
            '    <div class="vgr-control"><label for="npu-v2-scenario">Scenario</label><select id="npu-v2-scenario"></select></div>',
            '    <div class="vgr-control"><label for="npu-v2-percentile">Statistic</label><select id="npu-v2-percentile"></select></div>',
            '    <p class="vgr-count" id="npu-v2-count"></p>',
            "  </div>",
            '  <div id="npu-v2-status"></div>',
            '  <section class="vgr-latest" id="npu-v2-latest"></section>',
            '  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Daily delivery</p><h2>PRs included in this daily snapshot</h2></div><p>Metric movement is shown only in the daily trend charts below.</p></div><div id="npu-v2-daily-change"></div></section>',
            '  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Reproducibility</p><h2>Current configuration</h2></div><p>Exact parameters for the selected run.</p></div><div id="npu-v2-config"></div></section>',
            '  <section class="vgr-section">',
            '    <div class="vgr-section-head"><div><p class="vgr-kicker">Selected run · NPU timeline</p><h2 id="npu-v2-miss-hit-title">Prefill and Decode Miss/Hit breakdown</h2></div><p>The selected statistic is shown for the diagnostic sample; device wait equals device span minus measured NPU compute.</p></div>',
            '    <div id="npu-v2-miss-hit-breakdown" aria-live="polite"></div>',
            "  </section>",
            '  <section class="vgr-section">',
            '    <div class="vgr-section-head"><div><p class="vgr-kicker">Established metrics</p><h2 id="npu-v2-core-trends-title">Core performance history</h2></div><p>Solid line: same measurement version. Dashed line: measurement or sampling changed; compare with caution.</p></div>',
            '    <div class="vgr-trend-grid" id="npu-v2-core-trend-grid" aria-live="polite"></div>',
            "  </section>",
            '  <section class="vgr-section">',
            '    <div class="vgr-section-head"><div><p class="vgr-kicker">NPU computation first</p><h2 id="npu-v2-diagnostic-trends-title">Compute and wait history</h2></div><p>NPU compute and device-wait metrics appear first; all earlier diagnostics remain available after them.</p></div>',
            '    <div class="vgr-trend-grid" id="npu-v2-diagnostic-trend-grid" aria-live="polite"></div>',
            "  </section>",
            '  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Measurement</p><h2>Latency profile</h2></div></div><div id="npu-v2-latency-grid"></div></section>',
            '  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Beam execution</p><h2>Prefill & Decode</h2></div><p>NPU device-timeline phases for the selected run.</p></div><div id="npu-v2-beam-profile"></div></section>',
            '  <section class="vgr-section">',
            '    <div class="vgr-section-head"><div><p class="vgr-kicker">Stage execution</p><h2>Additive stage decomposition · mean only</h2></div><p>Arithmetic means over the diagnostic sample: the decomposition is additive for means, so the Statistic selector above does not apply to this section.</p></div>',
            '    <div id="npu-v2-stage-figure" aria-live="polite"></div>',
            "  </section>",
            '  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Methodology</p><h2>Metric definitions</h2></div><p>How to read and compare the values.</p></div><div class="vgr-methodology">' + _methodology() + "</div></section>",
            '  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Evidence</p><h2>Run history</h2></div><p>Select a run to inspect its configuration.</p></div><div id="npu-v2-run-history"></div></section>',
            "</div>",
            "",
        ]
    )


def _methodology() -> str:
    return (
        '<p><strong>Canonical Offline E2E miss/hit</strong>: the official daily trend. Each measured pair is <code>reset → measured miss → identical measured hit</code>. The timed call uses one outer monotonic clock and a final <code>torch.npu.synchronize()</code> completion fence; output materialization happens after the clock stops. This is the difference from the legacy <em>Batch latency</em> metric, which stopped at output-object return without a device fence.</p>'
        '<p><strong>NPU compute</strong>: Prefill NPU compute brackets the model-forward plus compute-logits window; Decode NPU compute brackets each Decode forward plus the sample/beam-advance window. These are a hardware-unvalidated first draft of <code>torch.npu.Event</code> timing modeled on the GPU CUDA-event code, so treat the split as indicative rather than calibrated.</p>'
        '<p><strong>Prefill / Decode device spans</strong>: recorded in a post-canonical diagnostic pass after every official E2E sample has finished. The spans are additive and sum with <strong>Host overhead</strong> back to diagnostic E2E. <strong>Prefill/Decode device wait</strong> is the corresponding span minus NPU compute. <strong>Prefill dispatch wait</strong> is submit to Prefill stage entry, <strong>Prefill / Decode CPU lead</strong> is how far CPU Decode preparation leads NPU Prefill completion, and <strong>Prefill output consumed</strong> marks the later output-consumption boundary.</p>'
        '<p><strong>Beam graph</strong>: the NPU beam decode graph captures fixed (batch, beam) shapes ahead of time; fallbacks count the calls that missed a captured graph and ran eager instead. A healthy run reports 0 fallbacks.</p>'
        '<p><strong>Daily trend</strong>: each calendar day keeps its latest measurement per scenario. The snapshot retains 30 days; older points are dropped automatically when the snapshot is rebuilt.</p>'
    )


def write_dashboard(source: list[Path], output: Path, date_override: str | None) -> None:
    points = collect_raw_v2_points(source, date_override)
    output.mkdir(parents=True, exist_ok=True)
    snapshot = output / "npu-vllm-gr-v2-data.json"

    existing: dict[str, Any] = {"runs": []}
    if snapshot.exists():
        try:
            loaded = json.loads(snapshot.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("runs"), list):
                existing = loaded
        except json.JSONDecodeError as exc:
            print(f"warning: ignoring corrupt snapshot {snapshot}: {exc}")

    today = date.today()
    runs = prune(merge_history(existing, points), today)
    payload = build_payload_v2(runs, today)
    snapshot.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "npu-vllm-gr-v2.md").write_text(dashboard_markdown(bool(runs)), encoding="utf-8")
    print(
        f"wrote {snapshot} with {len(runs)} scenario-day points "
        f"across {len(payload['scenarios'])} scenarios (raw.v2 protocol) "
        f"and the v2 dashboard page"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        nargs="+",
        required=True,
        help="raw.v2 NPU result JSON file(s) or directories to scan recursively",
    )
    parser.add_argument("--output", type=Path, default=Path("docs"), help="dashboard docs directory")
    parser.add_argument("--date", help="force the run date (ISO) for every collected file")
    args = parser.parse_args()
    write_dashboard(args.source, args.output, args.date)


if __name__ == "__main__":
    main()

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[1]
SCRIPT = WORKTREE / "scripts" / "build_npu_v2_dashboard.py"
DASHBOARD_JS = WORKTREE / "docs" / "javascripts" / "npu-v2-dashboard.js"
DASHBOARD_CSS = WORKTREE / "docs" / "stylesheets" / "npu-v2-dashboard.css"

RAW_V2_SCHEMA = "vllm-gr.offline.raw.v2"
V2_DASHBOARD_SCHEMA = "npu-vllm-gr.dashboard.rawv2.v1"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_npu_v2_dashboard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def dist(mean: float, spread: float = 0.1) -> dict:
    """A raw.v2 distribution object with the percentiles the page charts."""
    return {
        "mean": mean,
        "std": mean * spread,
        "p50": mean * 0.98,
        "p90": mean * 1.08,
        "p95": mean * 1.12,
        "p99": mean * 1.2,
        "unit": "ms",
    }


def synthetic_raw_v2() -> dict:
    """An additive NPU raw.v2 result: each state's e2e closes exactly.

    miss: prefill 50 (40 compute + 10 idle) + decode 38 (30 + 8) + host 12 = 100
    hit:  prefill 20 (15 + 5)        + decode 16 (12 + 4) + host  6 =  42
    """
    def cluster(prefix: str, values: dict[str, float]) -> dict:
        return {f"{prefix}{key}": dist(value) for key, value in values.items()}

    diagnostic = {}
    diagnostic.update(cluster("prefill_ms_", {"miss": 50.0, "hit": 20.0}))
    diagnostic.update(cluster("decode_ms_", {"miss": 38.0, "hit": 16.0}))
    diagnostic.update(cluster("prefill_gpu_compute_ms_", {"miss": 40.0, "hit": 15.0}))
    diagnostic.update(cluster("decode_gpu_compute_ms_", {"miss": 30.0, "hit": 12.0}))
    diagnostic.update(cluster("prefill_device_idle_ms_", {"miss": 10.0, "hit": 5.0}))
    diagnostic.update(cluster("decode_device_idle_ms_", {"miss": 8.0, "hit": 4.0}))
    diagnostic.update(cluster("prefill_dispatch_ms_", {"miss": 5.0, "hit": 3.0}))
    diagnostic.update(cluster("prefill_cpu_lead_ms_", {"miss": 7.0, "hit": 4.0}))
    diagnostic.update(cluster("host_overhead_ms_", {"miss": 12.0, "hit": 6.0}))
    diagnostic.update(cluster("prefill_output_consumed_ms_", {"miss": 38.0, "hit": 14.0}))
    # The diagnostic pass re-records E2E as the container that prefill + decode +
    # host overhead closes to, exactly like the GPU diagnostic block.
    diagnostic.update(cluster("e2e_ms_", {"miss": 100.0, "hit": 42.0}))

    return {
        "schema_version": RAW_V2_SCHEMA,
        "device": "npu",
        "beam_width": 8,
        "batch_size": 1,
        "input_length": 256,
        "model": "/models/qwen2.5-7b-instruct",
        "distributions": {
            "e2e_ms_miss": dist(100.0),
            "e2e_ms_hit": dist(42.0),
        },
        "diagnostic": {"distributions": diagnostic},
        "miss": {"e2e_ms": [100.0] * 4},
        "hit": {"e2e_ms": [42.0] * 4},
        "num_prompts": 20,
        "warmup_requests": 4,
        "completed": 20,
        "failed": 0,
        "aggregate_output_tokens": 1234,
        "duration_seconds": 40.0,
        "pair_duration_seconds": 30.0,
        "measurement_mode": "perf_counter_ns+npu.synchronize",
        "pipeline_version": "beam-search-v1-npu",
        "npu_beam_graph": {
            "captured_shapes": [[1, 8]],
            "fallbacks": 0,
            "full_dispatches": 0,
            "beam_graph_batch_size": 1,
            "beam_max_width": 8,
        },
        "engine_config": {
            "catalog_path": "/catalogs/qwen.catalog.json",
            "npu_execution": "graph",
            "constraint_table_backend": "npu",
            "max_model_len": 4096,
            "gpu_memory_utilization": 0.9,
            "single_request": True,
        },
    }


def write_result(source: Path, result: dict) -> None:
    path = source / "run" / "npu-raw-v2.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(result), encoding="utf-8")


@pytest.mark.cpu_test
def test_dashboard_js_parses() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to parse-check the dashboard script")
    result = subprocess.run(
        [node, "--check", str(DASHBOARD_JS)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.cpu_test
def test_empty_source_produces_empty_page_and_snapshot(tmp_path: Path) -> None:
    builder = load_builder()
    source = tmp_path / "runs"
    source.mkdir()
    output = tmp_path / "docs"
    builder.write_dashboard([source], output, None)
    page = (output / "npu-vllm-gr-v2.md").read_text(encoding="utf-8")
    payload = json.loads((output / "npu-vllm-gr-v2-data.json").read_text(encoding="utf-8"))
    assert "No raw.v2 NPU artifacts found." in page
    assert 'id="npu-v2-dashboard"' in page
    # No controls or sections are emitted for an empty snapshot.
    assert 'id="npu-v2-scenario"' not in page
    assert 'id="npu-v2-stage-figure"' not in page
    assert payload["runs"] == []
    assert payload["schema_version"] == V2_DASHBOARD_SCHEMA
    assert payload["device"] == "Ascend 910B1"


@pytest.mark.cpu_test
def test_builder_generates_v2_page_and_payload(tmp_path: Path) -> None:
    builder = load_builder()
    source = tmp_path / "runs"
    write_result(source, synthetic_raw_v2())
    output = tmp_path / "docs"
    today = date.today().isoformat()
    builder.write_dashboard([source], output, today)

    page = (output / "npu-vllm-gr-v2.md").read_text(encoding="utf-8")
    payload = json.loads((output / "npu-vllm-gr-v2-data.json").read_text(encoding="utf-8"))

    assert payload["schema_version"] == V2_DASHBOARD_SCHEMA
    assert payload["device"] == "Ascend 910B1"
    assert len(payload["runs"]) == 1
    run = payload["runs"][0]
    assert run["date"] == today
    assert run["scenario_key"] == "b1-bw8-in256"
    assert run["distributions"]["e2e_ms_miss"]["mean"] == 100.0
    assert run["diagnostic_distributions"]["host_overhead_ms_miss"]["mean"] == 12.0
    assert run["graph"]["fallbacks"] == 0
    assert run["config"]["constraint_table_backend"] == "npu"
    assert "source" in run and run["source"].endswith("npu-raw-v2.json")
    # Canonical E2E and diagnostic e2e stay separate: the trend reads the former,
    # the stage figure reads the latter. The canonical block holds only the
    # miss/hit E2E pair; the prefill/decode cluster lives in the diagnostic block.
    assert set(run["distributions"]) == {"e2e_ms_miss", "e2e_ms_hit"}
    assert run["diagnostic_distributions"]["e2e_ms_miss"]["mean"] == 100.0
    assert run["diagnostic_distributions"]["prefill_ms_miss"]["mean"] == 50.0

    # Metric catalog is the single source of truth in the Python builder.
    assert {item["key"] for item in payload["core_metrics"]} == {
        "e2e_ms", "prefill_ms", "decode_ms",
    }
    # Canonical E2E is split into two single-series cards (miss and hit), and
    # the diagnostic prefill/decode cards are likewise split into miss/hit so
    # every core trend is a single cache-state series.
    assert [item["key"] for item in payload["core_metrics"]] == [
        "e2e_ms", "e2e_ms", "prefill_ms", "prefill_ms", "decode_ms", "decode_ms",
    ]
    assert [item.get("cache_state") for item in payload["core_metrics"]] == [
        "miss", "hit", "miss", "hit", "miss", "hit",
    ]
    assert [item["label"] for item in payload["core_metrics"]] == [
        "E2E miss", "E2E hit",
        "Prefill miss", "Prefill hit",
        "Decode miss", "Decode hit",
    ]
    assert {item["key"] for item in payload["diagnostic_metrics"]} == {
        "prefill_gpu_compute_ms", "decode_gpu_compute_ms",
        "prefill_device_idle_ms", "decode_device_idle_ms",
        "prefill_dispatch_ms", "prefill_cpu_lead_ms", "host_overhead_ms",
    }
    # NPU-compute and device-wait metrics lead the diagnostic grid.
    assert [item["key"] for item in payload["diagnostic_metrics"][:4]] == [
        "prefill_gpu_compute_ms", "decode_gpu_compute_ms",
        "prefill_device_idle_ms", "decode_device_idle_ms",
    ]
    assert payload["percentiles"] == ["mean", "p50", "p90", "p95", "p99"]

    # Page structure mirrors the GPU dashboard's sections.
    for section_id in (
        "npu-v2-dashboard", "npu-v2-latest", "npu-v2-daily-change", "npu-v2-config",
        "npu-v2-miss-hit-breakdown", "npu-v2-core-trend-grid",
        "npu-v2-diagnostic-trend-grid", "npu-v2-latency-grid", "npu-v2-beam-profile",
        "npu-v2-stage-figure", "npu-v2-run-history",
    ):
        assert f'id="{section_id}"' in page, section_id
    assert page.index('id="npu-v2-core-trend-grid"') < page.index(
        'id="npu-v2-diagnostic-trend-grid"'
    )
    assert "Metric definitions" in page
    assert "Run history" in page


@pytest.mark.cpu_test
def test_stage_figure_is_mean_only_and_additive(tmp_path: Path) -> None:
    js = DASHBOARD_JS.read_text(encoding="utf-8")
    # Additivity holds for means only, so the Statistic selector must not reach
    # the figure; the call must not thread `percentile` into it.
    assert "renderStageFigure(stageFigure, selected);" in js
    assert "renderStageFigure(stageFigure, selected, percentile)" not in js
    # The additivity gate must remain (see the suppression message contract).
    assert "prefill + decode + host overhead" in js
    assert "fails the additivity gate" in js
    # The stage palette lives in CSS, never in a `fill=` attribute. The JS names
    # the five fill slots and resolves them through the shared `vgr-stage-fill-*`
    # class template.
    assert "fill=" not in js
    for slot in ('["prefill-gpu"', '["decode-idle"', '["host"'):
        assert slot in js
    assert "vgr-stage-fill-" in js
    css = DASHBOARD_CSS.read_text(encoding="utf-8")
    # Device toggle is self-contained on the v2 page (does not depend on the
    # legacy npu-dashboard.css being loaded).
    assert ".npu-device-btn" in css and ".npu-device-toggle" in css
    # The SVG fill slots resolve against the shared GPU palette; the stage hex
    # values stay in vllm-gr-dashboard.css and are never re-inked into JS.
    shared_css = (WORKTREE / "docs" / "stylesheets" / "vllm-gr-dashboard.css").read_text(encoding="utf-8")
    for value in ("#0f766e", "#99f6e4", "#4338ca", "#c7d2fe", "#b45309"):
        assert value in shared_css


@pytest.mark.cpu_test
def test_mkdocs_registers_v2_assets_with_cache_buster() -> None:
    mkdocs_yml = (WORKTREE / "mkdocs.yml").read_text(encoding="utf-8")
    # Nav keeps the legacy NPU page and adds the v2 page.
    assert "NPU vllm-gr Performance: npu-vllm-gr.md" in mkdocs_yml
    assert "NPU vllm-gr Performance (v2): npu-vllm-gr-v2.md" in mkdocs_yml
    for asset in ("npu-v2-dashboard.js", "npu-v2-dashboard.css"):
        entries = [line for line in mkdocs_yml.splitlines() if asset in line]
        assert len(entries) == 1 and re.search(r"\?v=\d{8}-\d+", entries[0]), entries
    # Returning visitors keep the cached script: a fresh cache-buster ships new
    # renderer code under a new query string. Both the renderer script and the
    # stylesheet carry today's cache-buster; bump them together when either one
    # changes.
    assert "npu-v2-dashboard.js?v=20261126-1" in mkdocs_yml
    assert "npu-v2-dashboard.css?v=20261119-2" in mkdocs_yml


@pytest.mark.cpu_test
def test_v2_builder_leaves_legacy_artifacts_untouched(tmp_path: Path) -> None:
    builder = load_builder()
    source = tmp_path / "runs"
    write_result(source, synthetic_raw_v2())
    output = tmp_path / "docs"
    builder.write_dashboard([source], output, date.today().isoformat())
    # The v2 builder owns only the v2 snapshot and page; it must not emit the
    # legacy snapshot or page.
    assert (output / "npu-vllm-gr-v2-data.json").exists()
    assert (output / "npu-vllm-gr-v2.md").exists()
    assert not (output / "npu-vllm-gr-data.json").exists()
    assert not (output / "npu-vllm-gr.md").exists()
    # The legacy source files themselves remain present in the repo.
    for rel in (
        "docs/npu-vllm-gr.md",
        "docs/javascripts/npu-dashboard.js",
        "tools/daily_benchmark/npu_daily_runner.sh",
    ):
        assert (WORKTREE / rel).exists(), rel
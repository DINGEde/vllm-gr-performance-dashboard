from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[1]
SCRIPT = WORKTREE / "scripts" / "build_vllm_gr_dashboard.py"
SAMPLE = WORKTREE / "runs" / "vllm-gr" / "L20-10018" / "2026-08-28" / "vllm-gr-summary.json"
SCHEMA = WORKTREE / "schemas" / "vllm-gr-daily-summary.schema.json"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_vllm_gr_dashboard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


@pytest.mark.cpu_test
def test_lightweight_timer_aggregates_before_single_flush(tmp_path: Path, monkeypatch) -> None:
    module_path = (
        WORKTREE.parents[1]
        / "tools"
        / "daily_benchmark"
        / "instrumentation"
        / "vllm_gr_lightweight_timing.py"
    )
    spec = importlib.util.spec_from_file_location("vllm_gr_lightweight_timing_test", module_path)
    timing = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(timing)
    monkeypatch.setenv("VLLM_GR_LIGHTWEIGHT_TIMING_DIR", str(tmp_path))
    wrapped = timing._timed("unit_stage", lambda value: value + 1)
    assert [wrapped(value) for value in range(3)] == [1, 2, 3]
    assert timing._STATS["unit_stage"]["count"] == 3
    assert not list(tmp_path.iterdir())
    timing.flush()
    files = list(tmp_path.glob("cpu-timing-*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["hot_path_io"] is False
    assert payload["metrics"]["unit_stage"]["count"] == 3


@pytest.mark.cpu_test
def test_schema_and_sample_are_valid_json() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    sample = load_sample()
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["properties"]["schema_version"]["const"] == "vllm-gr.daily.v1"
    assert sample["schema_version"] == "vllm-gr.daily.v1"


@pytest.mark.cpu_test
def test_legacy_a0_sample_is_valid_but_not_baseline_or_trend_eligible() -> None:
    builder = load_builder()
    sample = load_sample()
    builder.validate_summary(sample)
    assert sample["run"]["status"] == "success"
    assert sample["run"]["baseline_eligible"] is False
    assert sample["run"]["trend_eligible"] is False
    assert sample["dataset"]["kind"] == "synthetic-smoke"
    assert sample["results"]["requests"] == {"completed": 10, "failed": 0}


@pytest.mark.cpu_test
def test_trend_run_requires_real_pinned_dataset_and_warmup() -> None:
    builder = load_builder()
    sample = load_sample()
    invalid = deepcopy(sample)
    invalid["run"]["baseline_eligible"] = True
    invalid["run"]["trend_eligible"] = True
    with pytest.raises(ValueError, match="representative real dataset"):
        builder.validate_summary(invalid)


@pytest.mark.cpu_test
def test_request_counts_must_match_scenario() -> None:
    builder = load_builder()
    invalid = load_sample()
    invalid["results"]["requests"]["failed"] = 1
    with pytest.raises(ValueError, match=r"completed \+ failed"):
        builder.validate_summary(invalid)


@pytest.mark.cpu_test
def test_latency_percentiles_must_be_monotonic() -> None:
    builder = load_builder()
    invalid = load_sample()
    invalid["results"]["latency_ms"]["ttft"]["p95"] = 100
    with pytest.raises(ValueError, match="percentiles must be monotonic"):
        builder.validate_summary(invalid)


@pytest.mark.cpu_test
def test_offline_summary_accepts_issue_aligned_phase_metrics() -> None:
    builder = load_builder()
    offline = load_sample()
    offline["scenario"]["execution_mode"] = "offline"
    base = deepcopy(offline["results"]["latency_ms"]["e2el"])
    offline["results"]["latency_ms"] = {
        key: deepcopy(base)
        for key in (
            "e2el",
            "e2el_hit",
            "prefill_miss",
            "prefill_hit",
            "decode",
            "decode_miss",
            "decode_hit",
            "overhead_miss",
            "overhead_hit",
        )
    }
    count = offline["results"]["requests"]["completed"]
    offline["results"]["samples"] = {
        "e2el_ms": [base["p50"]] * count,
        "e2el_hit_ms": [base["p50"]] * count,
        "input_tokens": [1024] * count,
        "output_tokens": [640] * count,
    }
    offline["results"].pop("cache", None)
    builder.validate_summary(offline)


@pytest.mark.cpu_test
def test_builder_generates_dashboard_page_and_payload(tmp_path: Path) -> None:
    builder = load_builder()
    source = tmp_path / "runs"
    current = load_sample()
    current["run"]["date"] = "2026-08-31"
    current["scenario"]["execution_mode"] = "offline"
    current["scenario"]["benchmark_args"]["phase_definition"] = {
        "version": "vllm-gr-serving-token1-v2"
    }
    base = deepcopy(current["results"]["latency_ms"]["e2el"])
    current["results"]["latency_ms"] = {
        key: deepcopy(base)
        for key in (
            "e2el",
            "e2el_hit",
            "prefill_miss",
            "prefill_hit",
            "decode",
            "decode_miss",
            "decode_hit",
            "overhead_miss",
            "overhead_hit",
        )
    }
    count = current["results"]["requests"]["completed"]
    current["results"]["samples"] = {
        "e2el_ms": [base["p50"]] * count,
        "e2el_hit_ms": [base["p50"]] * count,
        "input_tokens": [1024] * count,
        "output_tokens": [640] * count,
    }
    current["results"].pop("cache", None)
    current_path = source / "L20" / "2026-08-31" / "current" / "vllm-gr-summary.json"
    current_path.parent.mkdir(parents=True)
    current_path.write_text(json.dumps(current), encoding="utf-8")

    legacy_path = source / "legacy" / "2026-08-28" / "vllm-gr-summary.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(json.dumps(load_sample()), encoding="utf-8")

    output = tmp_path / "docs"
    builder.write_dashboard(source, output)
    payload = json.loads((output / "vllm-gr-dashboard-data.json").read_text(encoding="utf-8"))
    page = (output / "vllm-gr.md").read_text(encoding="utf-8")
    assert payload["schema_version"] == "vllm-gr.dashboard.v1"
    assert len(payload["runs"]) == 1
    assert payload["runs"][0]["run"]["date"] == "2026-08-31"
    assert payload["gpu"] == "L20"
    assert "hosts" not in payload
    assert 'id="vgr-dashboard"' in page
    assert 'id="vgr-beam-profile"' in page
    assert 'id="vgr-cpu-pipeline"' in page
    dashboard_js = (WORKTREE / "docs" / "javascripts" / "vllm-gr-dashboard.js").read_text(encoding="utf-8")
    assert "E2E ASYNC DECODE PIPELINE" in dashboard_js
    assert "execute_model parent" in dashboard_js
    assert "attention metadata" in dashboard_js
    assert "AsyncOutput.get_output" in dashboard_js
    assert "optimization focus" in dashboard_js
    assert 'id="vgr-config"' in page
    assert 'id="vgr-trend-grid"' in page
    assert 'id="vgr-metric"' not in page
    assert "Per-request primary E2E" not in page
    assert "Metric definitions" in page
    assert "Average (Mean)" in page
    assert "Qualified trend only" in page
    assert 'id="vgr-host"' not in page
    metric_keys = {item["key"] for item in payload["metrics"]}
    assert metric_keys == {
        "e2el",
        "e2el_hit",
        "prefill_miss",
        "prefill_hit",
        "prefill",
        "decode",
        "sort",
        "total_beam",
    }


@pytest.mark.cpu_test
def test_builder_prefers_v3_phase_runs_over_v2(tmp_path: Path) -> None:
    builder = load_builder()
    source = tmp_path / "runs"
    summaries = []
    for version in ("vllm-gr-serving-token1-v2", "vllm-gr-serving-internal-v3"):
        summary = load_sample()
        summary["run"]["date"] = "2026-09-01"
        summary["scenario"]["execution_mode"] = "offline"
        summary["scenario"]["benchmark_args"]["phase_definition"] = {"version": version}
        base = deepcopy(summary["results"]["latency_ms"]["e2el"])
        summary["results"]["latency_ms"] = {
            key: deepcopy(base)
            for key in ("e2el", "e2el_hit", "prefill_miss", "prefill_hit", "decode", "decode_miss", "decode_hit", "overhead_miss", "overhead_hit")
        }
        count = summary["results"]["requests"]["completed"]
        summary["results"]["samples"] = {
            "e2el_ms": [base["p50"]] * count,
            "e2el_hit_ms": [base["p50"]] * count,
            "input_tokens": [1024] * count,
            "output_tokens": [640] * count,
        }
        summary["results"].pop("cache", None)
        summaries.append(summary)
        path = source / version / "vllm-gr-summary.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(summary), encoding="utf-8")

    runs = builder.discover_runs(source)
    assert len(runs) == 1
    assert runs[0]["phase_version"] == "vllm-gr-serving-internal-v3"

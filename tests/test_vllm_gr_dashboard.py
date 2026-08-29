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
def test_schema_and_sample_are_valid_json() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    sample = load_sample()
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["properties"]["schema_version"]["const"] == "vllm-gr.daily.v1"
    assert sample["schema_version"] == "vllm-gr.daily.v1"


@pytest.mark.cpu_test
def test_a0_sample_is_visible_but_not_baseline_or_trend_eligible() -> None:
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
def test_builder_generates_dashboard_page_and_payload(tmp_path: Path) -> None:
    builder = load_builder()
    output = tmp_path / "docs"
    builder.write_dashboard(WORKTREE / "runs", output)
    payload = json.loads((output / "vllm-gr-dashboard-data.json").read_text(encoding="utf-8"))
    page = (output / "vllm-gr.md").read_text(encoding="utf-8")
    assert payload["schema_version"] == "vllm-gr.dashboard.v1"
    assert len(payload["runs"]) >= 2
    assert payload["trend_runs"] == []
    run_ids = {item["run"]["id"] for item in payload["runs"]}
    assert "p0-a0-20260828T173041-0889ea4" in run_ids
    assert "a1-real-2026-08-29-0889ea4" in run_ids
    assert 'id="vgr-dashboard"' in page
    assert 'id="vgr-beam-profile"' in page
    assert "Qualified trend only" in page
    metric_keys = {item["key"] for item in payload["metrics"]}
    assert {
        "prefill_mean",
        "decode_mean",
        "sort_mean",
        "beam_total_mean",
        "cache_hit",
        "cache_miss",
    } <= metric_keys

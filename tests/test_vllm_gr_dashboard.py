from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[1]
SCRIPT = WORKTREE / "scripts" / "build_vllm_gr_dashboard.py"
SAMPLE = WORKTREE / "runs" / "vllm-gr" / "L20-10018" / "2026-08-28" / "vllm-gr-summary.json"
SCHEMA = WORKTREE / "schemas" / "vllm-gr-daily-summary.schema.json"
DASHBOARD_JS = WORKTREE / "docs" / "javascripts" / "vllm-gr-dashboard.js"
DASHBOARD_CSS = WORKTREE / "docs" / "stylesheets" / "vllm-gr-dashboard.css"
STAGE_HARNESS = WORKTREE / "tests" / "render_stage_figure.mjs"
# The one published run whose diagnostic sample carries the whole v5 stage
# caliber. Without it there is nothing to check the ported geometry against.
STAGE_RUN = (
    WORKTREE / "runs" / "vllm-gr" / "L20" / "2026-09-18"
    / "daily-offline-2026-09-18-f7724d33-bw128-in1024-c1" / "vllm-gr-summary.json"
)
# The prototype's version of the Band A endpoints: it omits the E2E term, so the
# stacked miss row overruns the viewBox and its last segment is clipped away.
PROTOTYPE_DOMAIN = "hi = Math.max(hi, prefill + decode + Math.max(overhead - dispatch, 0));"
FIXED_DOMAIN = "hi = Math.max(hi, prefill + decode + Math.max(overhead - dispatch, 0), e2e);"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_vllm_gr_dashboard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def run_stage_harness(summary: Path, *extra: str) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute the dashboard renderer")
    return subprocess.run(
        [node, str(STAGE_HARNESS), str(summary), *extra],
        capture_output=True,
        text=True,
        cwd=WORKTREE,
        check=False,
    )


def stage_output(result: subprocess.CompletedProcess) -> str:
    return f"{result.stdout}\n{result.stderr}"


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
    current["run"]["date"] = "2026-09-01"
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
    current["results"]["diagnostic"] = {
        "trend_eligible": False,
        "num_prompts": 20,
        "method": "post-canonical internal perf_counter_ns monkeypatches",
        "latency_ms": {
            key: deepcopy(base)
            for key in (
                "prefill_gpu_compute",
                "decode_gpu_compute",
                "prefill_device_idle",
                "decode_device_idle",
                "prefill_output_consumed",
                "prefill_dispatch",
                "prefill_cpu_lead",
                "host_overhead",
            )
        },
        "phase_definition": {"version": "vllm-gr-beam-search-v1-cuda-ready-v4"},
    }
    current["results"].pop("cache", None)
    current_path = source / "L20" / "2026-09-01" / "current" / "vllm-gr-summary.json"
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
    assert payload["runs"][0]["run"]["date"] == "2026-09-01"
    assert payload["gpu"] == "L20"
    assert "hosts" not in payload
    assert 'id="vgr-dashboard"' in page
    assert 'id="vgr-beam-profile"' in page
    # The Async Decode CPU pipeline section was removed: its container must not
    # come back without the renderer that fills it (and vice versa -- a container
    # with no renderer left ``refresh()`` throwing on a null node).
    assert 'id="vgr-cpu-pipeline"' not in page
    dashboard_js = DASHBOARD_JS.read_text(encoding="utf-8")
    assert "renderCpuPipeline" not in dashboard_js
    # The stage figure is the same three-touch shape as the section that was
    # removed above: a container, the lookup, and the call inside refresh(). Half
    # of the pair alone either draws nothing or throws on a null node and takes
    # every later renderer down with it, so all three are asserted together.
    assert 'id="vgr-stage-figure"' in page
    assert 'document.getElementById("vgr-stage-figure")' in dashboard_js
    assert "renderStageFigure(stageFigure, selected);" in dashboard_js
    # Additivity holds for means but not for percentiles, so the Statistic
    # selector must not be able to reach the figure. Threading `percentile` into
    # the call is the one edit that would break that, and it is rejected here.
    assert "renderStageFigure(stageFigure, selected, percentile)" not in dashboard_js
    assert not re.search(r"[一-鿿]", page + dashboard_js)
    # Citation line numbers were stripped from the ported anchor prose: they drift
    # with every benchmark edit, and a stale anchor in a published page is worse
    # than no anchor.
    assert "脚本" not in dashboard_js
    assert "execute:555" not in dashboard_js and "L465" not in dashboard_js
    assert "≈" in dashboard_js
    # Scope: the figure covers the additive decomposition, not the raw-values
    # appendix or the cpu_pipeline_detail dump (which is unvalidated and stays on
    # the data-production side only).
    assert "cpu_pipeline_detail" not in page
    assert "Band A raw values" not in page
    css = DASHBOARD_CSS.read_text(encoding="utf-8")
    # One class per fill slot serves three consumers at once (the SVG mark, the
    # legend swatch, the label ink), so the palette lives in CSS and never in a
    # `fill=` attribute. These five values are the approved pairing.
    for value in ("#0f766e", "#99f6e4", "#4338ca", "#c7d2fe", "#b45309"):
        assert value in css
    assert "fill=" not in dashboard_js
    # Returning visitors keep the cached script, so shipping new JS under the old
    # cache-buster leaves them on a renderer that fills nothing.
    mkdocs_yml = (WORKTREE / "mkdocs.yml").read_text(encoding="utf-8")
    entry = [line for line in mkdocs_yml.splitlines() if "vllm-gr-dashboard.js" in line]
    assert len(entry) == 1 and re.search(r"\?v=\d{8}-\d+", entry[0]), entry
    assert "?v=20260918-1" not in mkdocs_yml
    assert 'id="vgr-config"' in page
    assert 'id="vgr-miss-hit-breakdown"' in page
    assert 'id="vgr-core-trend-grid"' in page
    assert 'id="vgr-diagnostic-trend-grid"' in page
    assert "Prefill GPU compute" in dashboard_js
    assert "prefill_gpu_compute_miss" in dashboard_js
    assert "decode_device_idle_hit" in dashboard_js
    assert '![' + '"canonical", "stage"' + '].includes(measurement)' in dashboard_js
    assert "diagnosticPhaseVersion(run)" in dashboard_js
    assert 'id="vgr-daily-change"' in page
    assert 'id="vgr-metric"' not in page
    assert "Per-request primary E2E" not in page
    assert "Metric definitions" in page
    assert "Average (Mean)" in page
    assert "Qualified trend only" not in page
    assert 'id="vgr-qualified-only"' not in page
    assert 'id="vgr-host"' not in page
    assert "trend_runs" not in payload
    metric_keys = {item["key"] for item in payload["metrics"]}
    assert metric_keys == {
        "e2el",
        "e2el_hit",
        "prefill_miss",
        "prefill_hit",
        "prefill",
        "decode",
        "prefill_gpu_compute",
        "decode_gpu_compute",
        "prefill_device_idle",
        "decode_device_idle",
        "prefill_output_consumed",
        "prefill_dispatch",
        "prefill_cpu_lead",
        "host_overhead",
    }
    assert {item["measurement"] for item in payload["metrics"]} == {
        "canonical", "stage", "gpu-compute", "gpu-wait", "diagnostic"
    }
    assert {item["key"] for item in payload["core_metrics"]} == {
        "e2el", "e2el_hit", "prefill_miss", "prefill_hit",
        "prefill", "decode",
    }
    # Only the diagnostic stages the newest snapshot still emits get a card;
    # retired probes must not leave a dead trend behind.
    assert {item["key"] for item in payload["diagnostic_metrics"]} == {
        "prefill_gpu_compute", "decode_gpu_compute",
        "prefill_device_idle", "decode_device_idle",
        "prefill_output_consumed", "prefill_dispatch", "prefill_cpu_lead",
        "host_overhead",
    }
    assert [item["key"] for item in payload["diagnostic_metrics"][:4]] == [
        "prefill_gpu_compute", "decode_gpu_compute",
        "prefill_device_idle", "decode_device_idle",
    ]
    assert page.index('id="vgr-diagnostic-trend-grid"') < page.index(
        'id="vgr-core-trend-grid"'
    )


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


@pytest.mark.cpu_test
def test_builder_keeps_legacy_and_v1_pipeline_points_for_same_day(tmp_path: Path) -> None:
    builder = load_builder()
    source = tmp_path / "runs"
    for index, (beam_api, pipeline, phase) in enumerate((
        ("beam_search", "legacy-beam-search", "vllm-gr-canonical-e2e-v1"),
        ("beam_search_v1", "beam-search-v1-async-final-output-pr396",
         "vllm-gr-canonical-beam-search-v1-e2e-v1"),
    )):
        summary = load_sample()
        summary["run"]["id"] = f"p0-pipeline-{index}"
        summary["run"]["date"] = "2026-09-17"
        summary["run"]["started_at"] = f"2026-09-17T0{index + 1}:00:00+08:00"
        summary["run"]["finished_at"] = f"2026-09-17T0{index + 1}:10:00+08:00"
        summary["scenario"]["execution_mode"] = "offline"
        summary["scenario"]["key"] = "bw128-in1024"
        summary["scenario"]["beam_api"] = beam_api
        summary["scenario"]["beam_execution_mode"] = "v1" if index else "legacy"
        summary["scenario"]["pipeline_version"] = pipeline
        summary["scenario"]["benchmark_args"]["phase_definition"] = {"version": phase}
        base = deepcopy(summary["results"]["latency_ms"]["e2el"])
        summary["results"]["latency_ms"] = {
            "e2el": deepcopy(base), "e2el_hit": deepcopy(base),
        }
        count = summary["results"]["requests"]["completed"]
        summary["results"]["samples"] = {
            "e2el_ms": [base["p50"]] * count,
            "e2el_hit_ms": [base["p50"]] * count,
            "input_tokens": [1024] * count,
            "output_tokens": [640] * count,
        }
        summary["results"].pop("cache", None)
        path = source / pipeline / "vllm-gr-summary.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(summary), encoding="utf-8")

    runs = builder.discover_runs(source)
    assert len(runs) == 2
    assert {run["summary"]["scenario"]["beam_api"] for run in runs} == {
        "beam_search", "beam_search_v1",
    }


@pytest.mark.cpu_test
def test_canonical_daily_run_keeps_diagnostic_stages_separate(tmp_path: Path) -> None:
    builder = load_builder()
    source = tmp_path / "runs"
    for index, run_date in enumerate(("2026-09-06", "2026-09-07"), start=1):
        summary = load_sample()
        summary["run"]["id"] = f"daily-offline-{run_date}-canonical"
        summary["run"]["date"] = run_date
        summary["run"]["started_at"] = f"{run_date}T02:30:00+08:00"
        summary["run"]["finished_at"] = f"{run_date}T02:40:00+08:00"
        summary["run"]["baseline_eligible"] = True
        summary["run"]["trend_eligible"] = True
        summary["source"]["branch"] = "decode_graph"
        summary["source"]["change_since_previous"] = {
            "previous_git_sha": "4acf9f2e",
            "current_git_sha": "53108242",
            "commit_count": index,
            "commits": [],
            "pull_requests": [{"number": 333, "title": "batch detokenize", "url": "https://github.com/JiusiServe/vllm-gr/pull/333"}],
        }
        summary["dataset"]["kind"] = "real"
        summary["dataset"]["representative"] = True
        summary["dataset"]["sha256"] = "dataset-sha"
        summary["dataset"]["selection"]["sample_ids_sha256"] = "sample-sha"
        summary["scenario"]["execution_mode"] = "offline"
        summary["scenario"]["warmup_requests"] = 4
        summary["scenario"]["benchmark_args"]["phase_definition"] = {
            "version": "vllm-gr-canonical-e2e-v1"
        }
        base = deepcopy(summary["results"]["latency_ms"]["e2el"])
        base["mean"] = float(base["mean"]) - index
        summary["results"]["latency_ms"] = {
            "e2el": deepcopy(base),
            "e2el_hit": deepcopy(base),
        }
        diagnostic_latency = {"entry_preprocess": deepcopy(base), "llm_engine_decode": deepcopy(base)}
        if index == 1:
            # Only the older snapshot carries this probe; the newest one has
            # retired it, so the dashboard must drop the orphan card.
            diagnostic_latency["sort"] = deepcopy(base)
        summary["results"]["diagnostic"] = {
            "trend_eligible": False,
            "num_prompts": 20,
            "method": "post-canonical internal perf_counter_ns monkeypatches",
            "latency_ms": diagnostic_latency,
            "phase_definition": {"version": "vllm-gr-serving-internal-v3-diagnostic"},
        }
        count = summary["results"]["requests"]["completed"]
        summary["results"]["samples"] = {
            "e2el_ms": [base["p50"]] * count,
            "e2el_hit_ms": [base["p50"]] * count,
            "input_tokens": [1024] * count,
            "output_tokens": [640] * count,
        }
        summary["results"].pop("cache", None)
        builder.validate_summary(summary)
        path = source / run_date / "vllm-gr-summary.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(summary), encoding="utf-8")

    runs = builder.discover_runs(source)
    assert len(runs) == 2
    assert all(item["phase_version"] == "vllm-gr-canonical-e2e-v1" for item in runs)
    payload = builder.build_payload(runs)
    canonical = [item for item in payload["metrics"] if item["measurement"] == "canonical"]
    stage = [item for item in payload["metrics"] if item["measurement"] == "stage"]
    diagnostic = [item for item in payload["metrics"] if item["measurement"] == "diagnostic"]
    assert {item["key"] for item in canonical} == {"e2el", "e2el_hit"}
    assert {item["key"] for item in stage} == {
        "prefill", "prefill_miss", "prefill_hit", "decode",
    }
    assert {item["key"] for item in diagnostic} == {"entry_preprocess", "llm_engine_decode"}


@pytest.mark.cpu_test
def test_builder_drops_retired_scenarios(tmp_path: Path) -> None:
    builder = load_builder()
    source = tmp_path / "runs"
    for scenario_key, n in (("bw128-in1024", 128), ("bw512-in1024", 512)):
        summary = load_sample()
        summary["run"]["id"] = f"daily-offline-2026-09-01-4acf9f2e-{scenario_key}-c1"
        summary["run"]["date"] = "2026-09-01"
        summary["scenario"]["execution_mode"] = "offline"
        summary["scenario"]["key"] = scenario_key
        summary["scenario"]["n"] = n
        summary["scenario"]["benchmark_args"]["phase_definition"] = {
            "version": "vllm-gr-canonical-e2e-v1"
        }
        base = deepcopy(summary["results"]["latency_ms"]["e2el"])
        summary["results"]["latency_ms"] = {
            "e2el": deepcopy(base), "e2el_hit": deepcopy(base),
        }
        count = summary["results"]["requests"]["completed"]
        summary["results"]["samples"] = {
            "e2el_ms": [base["p50"]] * count,
            "e2el_hit_ms": [base["p50"]] * count,
            "input_tokens": [1024] * count,
            "output_tokens": [640] * count,
        }
        summary["results"].pop("cache", None)
        path = source / scenario_key / "vllm-gr-summary.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(summary), encoding="utf-8")

    payload = builder.build_payload(builder.discover_runs(source))
    assert {item["key"] for item in payload["scenarios"]} == {"bw128-in1024"}
    assert all(run["scenario"]["key"] == "bw128-in1024" for run in payload["runs"])


@pytest.mark.cpu_test
def test_dashboard_js_parses() -> None:
    # CI only runs ``mkdocs build --strict``, which never parses the dashboard
    # script. A syntax error would therefore ship a page that silently renders
    # nothing at all.
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to parse-check the dashboard script")
    result = subprocess.run(
        [node, "--check", str(DASHBOARD_JS)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.cpu_test
@pytest.mark.skipif(not STAGE_RUN.exists(), reason="the v5 stage fixture run is not in this checkout")
def test_stage_figure_geometry_matches_summary() -> None:
    # The renderer is a port of a standalone prototype, so the property worth
    # testing is not "it draws" but "it still means the same thing". The harness
    # re-derives every rect from the same summary with its own arithmetic and
    # compares against the shipped JavaScript, so a drift in the domain, the
    # endpoint table or the label thresholds fails as a pixel mismatch.
    result = run_stage_harness(STAGE_RUN)
    assert result.returncode == 0, stage_output(result)
    assert "prototype domain line present: true" in result.stdout
    assert "RESULT: all checks passed" in result.stdout
    # The section must not be invisible in production, so the fixture has to keep
    # producing a real figure rather than falling through to a guard.
    assert "vgr-empty" not in result.stdout


@pytest.mark.cpu_test
def test_stage_figure_falls_back_for_legacy_run(tmp_path: Path) -> None:
    # 113 of the 114 published runs cannot be drawn: 49 carry no diagnostic
    # sample at all and 64 carry only the legacy keys. Degrading to prose is the
    # normal path, not an edge case.
    result = run_stage_harness(SAMPLE, "--expect-empty")
    assert result.returncode == 0, stage_output(result)

    legacy = load_sample()
    legacy["results"]["diagnostic"] = {
        "trend_eligible": False,
        "num_prompts": 20,
        "method": "post-canonical internal perf_counter_ns monkeypatches",
        "latency_ms": {
            key: deepcopy(legacy["results"]["latency_ms"]["e2el"])
            for key in ("prefill_miss", "prefill_hit", "decode", "host_overhead")
        },
    }
    partial = tmp_path / "vllm-gr-summary.json"
    partial.write_text(json.dumps(legacy), encoding="utf-8")
    result = run_stage_harness(partial, "--expect-empty")
    assert result.returncode == 0, stage_output(result)
    assert "predates the GPU-compute-v5 stage caliber" in result.stdout


@pytest.mark.cpu_test
@pytest.mark.skipif(not STAGE_RUN.exists(), reason="the v5 stage fixture run is not in this checkout")
def test_stage_figure_additivity_gate_suppresses_drawing(tmp_path: Path) -> None:
    # The prototype asserted the identities before drawing and exited on failure.
    # The JavaScript equivalent is a guard: a run whose stages no longer close to
    # E2E is described, not drawn. Perturbing host_overhead is the smallest edit
    # that breaks exactly the load-bearing identity.
    summary = json.loads(STAGE_RUN.read_text(encoding="utf-8"))
    summary["results"]["diagnostic"]["latency_ms"]["host_overhead_miss"]["mean"] += 0.5
    broken = tmp_path / "vllm-gr-summary.json"
    broken.write_text(json.dumps(summary), encoding="utf-8")
    result = run_stage_harness(broken, "--expect-empty")
    assert result.returncode == 0, stage_output(result)
    assert "fails the v5 additivity gate" in result.stdout
    assert "500.000 us" in result.stdout


@pytest.mark.cpu_test
@pytest.mark.skipif(not STAGE_RUN.exists(), reason="the v5 stage fixture run is not in this checkout")
def test_stage_figure_negative_control_without_domain_fix(tmp_path: Path) -> None:
    # A checking harness that passes on everything is worthless, so this proves
    # the geometry assertions are non-empty: revert the domain to the prototype's
    # version and the same harness must fail. The overflow is deterministic --
    # host_overhead and prefill_dispatch are non-zero on any instrumented run --
    # so this cannot go quietly green.
    source = DASHBOARD_JS.read_text(encoding="utf-8")
    assert source.count(FIXED_DOMAIN) == 1, "the domain line moved; update this control"
    reverted = tmp_path / "vllm-gr-dashboard-prototype.js"
    reverted.write_text(source.replace(FIXED_DOMAIN, PROTOTYPE_DOMAIN), encoding="utf-8")
    result = run_stage_harness(STAGE_RUN, f"--js={reverted}")
    assert result.returncode != 0, stage_output(result)
    output = stage_output(result)
    assert "overflows the viewBox" in output
    assert "band A miss row closes at x(e2e)" in output

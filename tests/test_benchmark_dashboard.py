from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[1]
SCRIPTS = WORKTREE / "scripts"
sys.path.insert(0, str(SCRIPTS))

from benchmark_dashboard_schema import (  # noqa: E402
    CANONICAL_A3_HARDWARE,
    CANONICAL_L20_HARDWARE,
    VLLM_QUEUE_MEAN,
    normalize_experiment,
    normalize_hardware_label,
    normalize_metrics,
    parse_npu_run_dirname,
)


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.cpu_test
def test_normalize_vllm_queue_alias() -> None:
    legacy = "latency_breakdown_seconds.vllm_queue_time.mean"
    assert normalize_metrics({legacy: 3.5, "completed_tasks": 4}) == {
        VLLM_QUEUE_MEAN: 3.5,
        "completed_tasks": 4,
    }
    assert normalize_metrics({VLLM_QUEUE_MEAN: 3.5, legacy: 3.5}) == {VLLM_QUEUE_MEAN: 3.5}


@pytest.mark.cpu_test
def test_normalize_vllm_queue_alias_rejects_conflict() -> None:
    with pytest.raises(ValueError, match="conflicting vLLM queue mean metrics"):
        normalize_metrics(
            {
                VLLM_QUEUE_MEAN: 3.5,
                "latency_breakdown_seconds.vllm_queue_time.mean": 4.0,
            }
        )


@pytest.mark.cpu_test
def test_normalize_new_ci_metric_aliases() -> None:
    assert normalize_metrics(
        {
            "vllm_queue_time_seconds.mean": 14.4,
            "run_wall_time_seconds": 7087.8,
            "prefix_cache_hit_rate": 0,
            "vllm_prefix_cache_hit_rate": 0.698,
        }
    ) == {
        VLLM_QUEUE_MEAN: 14.4,
        "wall_seconds": 7087.8,
        "prefix_cache_hit_rate": 0.698,
        "vllm_prefix_cache_hit_rate": 0.698,
    }


@pytest.mark.cpu_test
def test_adapt_new_ci_dashboard_summary_format() -> None:
    data = {
        "schema_version": "1.0",
        "run": {
            "name": "main-ci-32_16",
            "date": "2026-08-05",
            "host": "L20-10014",
            "commit": "abc123",
        },
        "environment": {"gpus": "2×L20 80GB", "model": "Qwen"},
        "shapes": {"32_16": {"task_num": 32, "max_concurrency": 16}},
        "averages": {
            "all_shapes": {
                "baseline": {
                    "completed_tasks": 30,
                    "vllm_queue_time_seconds.mean": 14.4,
                    "run_wall_time_seconds": 12809.6,
                    "prefix_cache_hit_rate": 0,
                    "vllm_prefix_cache_hit_rate": 0.29,
                },
                "candidate": {
                    "completed_tasks": 29,
                    "vllm_queue_time_seconds.mean": 15.0,
                    "run_wall_time_seconds": 7087.8,
                    "prefix_cache_hit_rate": 0,
                    "vllm_prefix_cache_hit_rate": 0.70,
                },
            }
        },
    }
    normalize_experiment(data)
    dashboard = load_script("build_benchmark_dashboard")
    dashboard.normalize_run_metrics(data)
    assert data["run"]["experiment_kind"] == "daily"
    assert data["run"]["trend_eligible"] is True
    assert data["run"]["expected_shapes"] == ["32/16"]
    assert data["environment"]["hardware"] == CANONICAL_L20_HARDWARE
    assert normalize_hardware_label("2×L20 80GB") == CANONICAL_L20_HARDWARE
    shape = data["shapes"]["32/16"]
    assert shape["baseline"]["completed_tasks"] == 30
    assert shape["router"]["completed_tasks"] == 29
    assert shape["baseline"][VLLM_QUEUE_MEAN] == 14.4
    assert shape["router"]["wall_seconds"] == 7087.8
    assert shape["router"]["prefix_cache_hit_rate"] == 0.70


@pytest.mark.cpu_test
def test_discover_runs_includes_new_ci_summary(tmp_path: Path) -> None:
    dashboard = load_script("build_benchmark_dashboard")
    run_dir = tmp_path / "runs" / "10014" / "2026-08-05"
    run_dir.mkdir(parents=True)
    (run_dir / "dashboard-summary.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "run": {"name": "main-ci-32_16", "date": "2026-08-05", "host": "L20-10014"},
                "environment": {"gpus": "2×L20 80GB"},
                "shapes": {"32_16": {"task_num": 32, "max_concurrency": 16}},
                "averages": {
                    "all_shapes": {
                        "baseline": {"completed_tasks": 30, "vllm_queue_time_seconds.mean": 1.0},
                        "candidate": {"completed_tasks": 29, "vllm_queue_time_seconds.mean": 2.0},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    days = dashboard.daily_points(dashboard.discover_runs(tmp_path / "runs"))
    assert len(days) == 1
    assert days[0]["date"] == "2026-08-05"
    assert days[0]["hardware"] == CANONICAL_L20_HARDWARE
    assert days[0]["runs"][0]["data"]["shapes"]["32/16"]["router"]["completed_tasks"] == 29


@pytest.mark.cpu_test
def test_parse_npu_run_dirname() -> None:
    from datetime import date

    meta = parse_npu_run_dirname(
        "run-Ascend910_9362-local-agentinfer-8-4-0824-132656-a6e0a9",
        reference=date(2026, 8, 25),
    )
    assert meta["hardware"] == CANONICAL_A3_HARDWARE
    assert meta["node"] == "9362"
    assert meta["host"] == ""
    assert meta["shape"] == "8/4"
    assert meta["date"] == "2026-08-24"
    assert normalize_hardware_label("Ascend910") == CANONICAL_A3_HARDWARE


@pytest.mark.cpu_test
def test_adapt_npu_agentinfer_summary() -> None:
    data = {
        "schema_version": "1",
        "run_id": "run-Ascend910_9362-local-agentinfer-8-4-0824-132656-a6e0a9",
        "tasks": {
            "completed": 7,
            "failed": 1,
            "with_patch": 7,
            "duration_seconds": {"mean": 455.0, "p50": 342.0, "p95": 895.0, "p99": 963.0},
        },
        "requests": {
            "latency_seconds": {"mean": 11.9, "p95": 62.1},
            "ttft_seconds": {"mean": 0.9},
        },
        "vllm": {
            "prefix_cache_hit_rate": 0.95,
            "latency_breakdown_seconds": {"queue_time": {"mean": 0.06}},
        },
        "run_wall_time_seconds": 1335.1,
        "request_throughput_per_second": 0.2,
        "cli": {"overrides": {"task_num": 8, "max_concurrency": 4, "model": "glm-5"}},
    }
    normalize_experiment(data)
    assert data["environment"]["hardware"] == CANONICAL_A3_HARDWARE
    assert data["run"]["date"] == "2026-08-24"
    assert data["run"]["host"] == ""
    assert data["run"]["trend_eligible"] is True
    assert data["run"]["expected_shapes"] == ["8/4"]
    shape = data["shapes"]["8/4"]
    assert shape["router"]["completed_tasks"] == 7
    assert shape["baseline"] == {}
    assert shape["router"][VLLM_QUEUE_MEAN] == 0.06
    assert shape["router"]["wall_seconds"] == 1335.1
    assert data["run"]["npu_side"] == "router"
    assert data["run"]["experiment_id"] == "npu-a3-Ascend910_9362-2026-08-24-8_4"


@pytest.mark.cpu_test
def test_adapt_npu_baseline_summary() -> None:
    data = {
        "schema_version": "1",
        "run_id": "run-Ascend910_9362-local-baseline-8-4-0824-132890-a6e0a8",
        "tasks": {"completed": 7, "failed": 1, "with_patch": 7, "duration_seconds": {"mean": 650.0}},
        "requests": {"latency_seconds": {"mean": 12.0, "p95": 44.0}, "ttft_seconds": {"mean": 1.0}},
        "vllm": {
            "prefix_cache_hit_rate": 0.9,
            "latency_breakdown_seconds": {"queue_time": {"mean": 0.1}},
        },
        "run_wall_time_seconds": 2000.0,
        "request_throughput_per_second": 0.15,
    }
    normalize_experiment(
        data,
        source_dir="run-Ascend910_9362-local-baseline-8-4-0824-132890-a6e0a8",
    )
    assert data["run"]["npu_side"] == "baseline"
    assert data["run"]["host"] == ""
    assert data["run"]["experiment_id"] == "npu-a3-Ascend910_9362-2026-08-24-8_4"
    shape = data["shapes"]["8/4"]
    assert shape["baseline"]["completed_tasks"] == 7
    assert shape["router"] == {}


@pytest.mark.cpu_test
def test_discover_runs_includes_npu_summary(tmp_path: Path) -> None:
    dashboard = load_script("build_benchmark_dashboard")
    agent_dir = (
        tmp_path
        / "runs"
        / "npu"
        / "run-Ascend910_9362-local-agentinfer-8-4-0824-132656-a6e0a9"
    )
    base_dir = (
        tmp_path
        / "runs"
        / "npu"
        / "run-Ascend910_9362-local-baseline-8-4-0824-132890-a6e0a8"
    )
    agent_dir.mkdir(parents=True)
    base_dir.mkdir(parents=True)
    (agent_dir / "summary.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-Ascend910_9362-local-agentinfer-8-4-0824-132656-a6e0a9",
                "tasks": {"completed": 7, "failed": 1, "with_patch": 7, "duration_seconds": {"mean": 1.0}},
                "requests": {"latency_seconds": {"mean": 1.0, "p95": 2.0}, "ttft_seconds": {"mean": 0.1}},
                "vllm": {
                    "prefix_cache_hit_rate": 0.9,
                    "latency_breakdown_seconds": {"queue_time": {"mean": 0.05}},
                },
                "run_wall_time_seconds": 10.0,
                "request_throughput_per_second": 0.1,
            }
        ),
        encoding="utf-8",
    )
    (base_dir / "summary1.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "run_id": "run-Ascend910_9362-local-baseline-8-4-0824-132890-a6e0a8",
                "tasks": {"completed": 6, "failed": 2, "with_patch": 6, "duration_seconds": {"mean": 2.0}},
                "requests": {"latency_seconds": {"mean": 3.0, "p95": 4.0}, "ttft_seconds": {"mean": 0.2}},
                "vllm": {
                    "prefix_cache_hit_rate": 0.8,
                    "latency_breakdown_seconds": {"queue_time": {"mean": 0.08}},
                },
                "run_wall_time_seconds": 20.0,
                "request_throughput_per_second": 0.2,
            }
        ),
        encoding="utf-8",
    )
    days = dashboard.daily_points(dashboard.discover_runs(tmp_path / "runs"))
    assert len(days) == 1
    assert days[0]["date"] == "2026-08-24"
    assert days[0]["hardware"] == CANONICAL_A3_HARDWARE
    assert days[0]["host"] == ""
    assert len(days[0]["runs"]) == 2
    assert dashboard.daily_metric(days[0], "router", "8/4", "completed_tasks") == 7
    assert dashboard.daily_metric(days[0], "baseline", "8/4", "completed_tasks") == 6


@pytest.mark.cpu_test
def test_historical_import_and_dashboard_generation(tmp_path: Path) -> None:
    importer = load_script("import_historical_benchmark_reports")
    dashboard = load_script("build_benchmark_dashboard")
    report = tmp_path / "2026-07-09-daily.md"
    report.write_text(
        """# Daily report

| Field | Value |
|---|---|
| Host | `L20-10014-direct` |
| Branch | `release-v0.2` |
| Commit | `da3b89a` |
| Hardware / GPU | `2 x NVIDIA L20, 46068 MiB each` (verified 2026-07-10) |
| NVIDIA driver | `580.159.03` (verified 2026-07-10) |
| CUDA | `13.0` (verified 2026-07-10) |

| Shape | Completed baseline -> router | Queue mean baseline -> router |
|---|---:|---:|
| `4/2` | `4 -> 4` | `0.1 -> 0.1` |
| `8/4` | `8 -> 8` | `0.2 -> 0.2` |
| `16/8` | `16 -> 16` | `0.3 -> 0.3` |
| `32/16` | `18 -> 27` | `8.796 -> 1.386` |
| `64/32` | `0 -> 36` | `104.660 -> 3.924` |
""",
        encoding="utf-8",
    )
    written = importer.import_shape_tables(report, tmp_path)
    assert len(written) == 1
    summary_path = written[0] / "dashboard-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    shape = summary["shapes"]["64/32"]
    assert shape["router"][VLLM_QUEUE_MEAN] == 3.924
    assert "latency_breakdown_seconds.vllm_queue_time.mean" not in shape["router"]
    assert summary["run"]["commit"] == "da3b89a"
    assert summary["provenance"]["environment"]["hardware"] == {
        "source": "current-machine-verification",
        "verified_at": "2026-07-10",
    }
    # Display/filter hardware label is normalized separately in dashboard discovery.

    site = tmp_path / "site"
    figures = site / "figures"
    figures.mkdir(parents=True)
    (figures / "shape-64_32-daily-metrics.svg").write_text("stale", encoding="utf-8")
    (figures / "custom.svg").write_text("custom", encoding="utf-8")
    dashboard.write_dashboard(tmp_path / "runs", site)
    markdown = (site / "README.md").read_text(encoding="utf-8")
    assert markdown == (site / "index.md").read_text(encoding="utf-8")
    payload = json.loads((site / "dashboard-data.json").read_text(encoding="utf-8"))
    assert payload["days"][0]["metrics"]["64/32"]["router"][VLLM_QUEUE_MEAN] == 3.924
    assert "hardware_options" in payload
    assert payload["days"][0]["hardware"]
    assert "id=\"hardware-filter\"" in markdown or "id='hardware-filter'" in markdown
    assert 'data-range="7"' in markdown
    assert 'data-range="30"' in markdown
    assert "Trend charts" in markdown
    assert "Details" in markdown
    assert 'id="details-range-switch"' in markdown
    assert 'id="details-date-from"' in markdown
    assert 'id="details-date-to"' in markdown
    assert "All hardware" not in markdown
    assert "trend-grid" in markdown

    assert "shape-metrics-root" in markdown
    assert "## Qualification and coverage" not in markdown
    assert "## Experiment provenance" not in markdown
    assert "## Scheduler A/B" not in markdown
    assert "## Baseline by concurrency:" not in markdown
    assert "## Router by concurrency:" not in markdown
    provenance = dashboard.provenance_table(
        [
            {"date": "2026-07-08", "host": "10014", "path": Path("older"), "data": {"run": {}}},
            {"date": "2026-07-09", "host": "10014", "path": Path("newer"), "data": {"run": {}}},
        ]
    )
    assert provenance.index("2026-07-09") < provenance.index("2026-07-08")
    assert not (site / "index.html").exists()
    assert {path.name for path in (site / "figures").glob("*.svg")} == {"custom.svg"}
    assert not (site / "figures" / "completed-tasks-by-shape.svg").exists()
    assert not (site / "figures" / "shape-64_32-latency-metrics.svg").exists()


@pytest.mark.cpu_test
def test_historical_commit_mapping_and_ambiguous_day(tmp_path: Path) -> None:
    importer = load_script("import_historical_benchmark_reports")
    report = tmp_path / "2026-07-02-daily.md"
    report.write_text("# historical\n", encoding="utf-8")
    written = importer.import_2026_07_02(report, tmp_path)
    assert written
    for path in written:
        summary = json.loads((path / "dashboard-summary.json").read_text(encoding="utf-8"))
        assert summary["run"]["host"] == "10015"
        assert summary["run"]["commit"] == "from-daily-report"
        assert summary["provenance"]["run"]["commit"]["source"] == "unavailable"


@pytest.mark.cpu_test
def test_report_identity_and_provenance_boundaries(tmp_path: Path) -> None:
    importer = load_script("import_historical_benchmark_reports")
    assert (
        importer.report_commit(
            ["- Latest picked worktree HEAD: `2d75887 fix`"],
            {},
        )
        == "2d75887"
    )
    assert importer.report_commit([], {}) == "from-daily-report"
    with pytest.raises(ValueError, match="does not identify"):
        importer.report_host(["# unknown host"])

    dashboard = load_script("build_benchmark_dashboard")
    run = {
        "data": {
            "run": {},
            "environment": {"agentcache_branch": "release-v0.2"},
            "provenance": {
                "environment": {
                    "agentcache_branch": {"source": "historical-report"},
                }
            },
        }
    }
    assert (
        dashboard.sourced_run_value(
            run,
            ("run", "branch"),
            ("environment", "agentcache_branch"),
        )
        == "release-v0.2 (historical-report)"
    )
    run["data"]["provenance"] = None
    assert dashboard.sourced_run_value(run, ("environment", "agentcache_branch")) == "release-v0.2 (unavailable)"


@pytest.mark.cpu_test
def test_router_delta_arrow_formatting() -> None:
    dashboard = load_script("build_benchmark_dashboard")
    assert dashboard.delta_arrow_pct(10.0, 15.0) == "↑50.0%"
    assert dashboard.delta_arrow_pct(10.0, 5.0) == "↓50.0%"
    assert dashboard.delta_arrow_pct(10.0, 10.0) == "→0.0%"
    assert dashboard.delta_arrow_pct(0.0, 12.0) == ""
    assert dashboard.delta_arrow_pct(None, 12.0) == ""
    assert "delta-up" in dashboard.router_metric_cell(18.0, 27.0)
    assert "↑50.0%" in dashboard.router_metric_cell(18.0, 27.0)
    assert dashboard.router_metric_cell(0.0, 36.0) == "36.00"


@pytest.mark.cpu_test
def test_empty_dashboard_cleans_only_managed_figures(tmp_path: Path) -> None:
    dashboard = load_script("build_benchmark_dashboard")
    figures = tmp_path / "site" / "figures"
    figures.mkdir(parents=True)
    (figures / "shape-64_32-completed-tasks.svg").write_text("managed", encoding="utf-8")
    (figures / "custom.svg").write_text("custom", encoding="utf-8")
    dashboard.write_dashboard(tmp_path / "empty-runs", tmp_path / "site")
    assert not (figures / "shape-64_32-completed-tasks.svg").exists()
    assert (figures / "custom.svg").exists()


@pytest.mark.cpu_test
def test_experiment_compatibility_defaults() -> None:
    full = {
        "run": {"host": "10014", "created_at": "2026-07-01T00:00:00Z", "run_id": "old"},
        "shapes": {shape: {} for shape in ("4/2", "8/4", "16/8", "32/16", "64/32")},
    }
    normalize_experiment(full)
    assert full["run"]["trend_eligible"] is True
    assert full["run"]["experiment_id"] == "legacy-daily-10014-2026-07-01-old"
    partial = {"run": {"host": "10014"}, "shapes": {"32/16": {}}}
    normalize_experiment(partial)
    assert partial["run"]["trend_eligible"] is False


@pytest.mark.cpu_test
def test_import_merges_tables_conflicts_and_idempotence(tmp_path: Path) -> None:
    importer = load_script("import_historical_benchmark_reports")
    report = tmp_path / "2026-07-08-daily.md"
    report.write_text(
        """| Field | Value |
|---|---|
| Host | `L20-10014` |

| Shape | Completed baseline -> router |
|---|---:|
| `32/16` | `10 -> 20` |

| Shape | Request throughput baseline -> router | Latency p95 baseline -> router |
|---|---:|---:|
| `32/16` | `0.2 -> 0.3` | `10s -> 8s` |
""",
        encoding="utf-8",
    )
    written = importer.import_shape_tables(report, tmp_path)
    summary = json.loads((written[0] / "dashboard-summary.json").read_text(encoding="utf-8"))
    assert summary["shapes"]["32/16"]["router"]["request_throughput"] == 0.3
    assert importer.import_shape_tables(report, tmp_path) == written
    conflict = tmp_path / "2026-07-07-daily.md"
    conflict.write_text(
        report.read_text(encoding="utf-8")
        + "\n| Shape | Completed baseline -> router |\n|---|---:|\n| `32/16` | `99 -> 20` |\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="shape 32/16 baseline completed_tasks"):
        importer.import_shape_tables(conflict, tmp_path / "conflict")


@pytest.mark.cpu_test
def test_scheduler_ab_isolation_rendering_and_qualifications(tmp_path: Path) -> None:
    importer = load_script("import_historical_benchmark_reports")
    dashboard = load_script("build_benchmark_dashboard")
    report = Path("D:/agentic_serving/materials/feedback/ci-10014/2026-07-12-scheduler-ab-report.md")
    assert len(importer.import_scheduler_ab(report, tmp_path)) == 4
    runs = dashboard.discover_runs(tmp_path / "runs")
    assert dashboard.daily_points(runs) == []
    dashboard.write_dashboard(tmp_path / "runs", tmp_path / "site")
    markdown = (tmp_path / "site" / "README.md").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "site" / "dashboard-data.json").read_text(encoding="utf-8"))
    assert "## Qualification and coverage" not in markdown
    assert "## Scheduler A/B" not in markdown
    assert payload["days"] == []
    assert payload["counts"]["source_runs"] == 4


@pytest.mark.cpu_test
def test_july_13_daily_import_preserves_cross_midnight_identity(tmp_path: Path) -> None:
    importer = load_script("import_historical_benchmark_reports")
    report = Path("D:/agentic_serving/materials/feedback/ci-10014/2026-07-13-daily.md")
    written = importer.import_shape_tables(report, tmp_path)
    summary = json.loads((written[0] / "dashboard-summary.json").read_text(encoding="utf-8"))
    run = summary["run"]
    assert run["created_at"] == "2026-07-14T01:58:15+08:00"
    assert run["commit"] == "9c05e0a3cd245d4d3cda3bde2bfeb8819b33e388"
    assert run["experiment_id"] == "daily-2026-07-13-10014"
    assert "crossed midnight" in run["qualification"]
    assert "50 router task timeouts" in run["qualification"]
    assert summary["shapes"]["64/32"]["router"]["completed_tasks"] == 14

    dashboard = load_script("build_benchmark_dashboard")
    discovered = dashboard.discover_runs(tmp_path / "runs")
    assert discovered[0]["date"] == "2026-07-13"
    assert dashboard.point_label(dashboard.daily_points(discovered)[0]) == "2026-07-13 · 10014"


@pytest.mark.cpu_test
def test_july_14_daily_import_preserves_explicit_result_run(tmp_path: Path) -> None:
    importer = load_script("import_historical_benchmark_reports")
    dashboard = load_script("build_benchmark_dashboard")
    report = Path("D:/agentic_serving/materials/feedback/ci-10014/2026-07-14-daily.md")
    written = importer.import_shape_tables(report, tmp_path)
    assert len(written) == 1
    summary = json.loads((written[0] / "dashboard-summary.json").read_text(encoding="utf-8"))
    run = summary["run"]
    assert run["created_at"] == "2026-07-14T22:38:25+08:00"
    assert run["commit"] == "075f444a64dc367f91ecee5b124bb22405e6fd4b"
    assert run["experiment_id"] == "daily-2026-07-14-10014"
    assert run["completeness"] == "complete"
    assert run["trend_eligible"] is True
    assert "16/8 router completed two fewer tasks" in run["qualification"]
    assert "52 router task timeouts" in run["qualification"]
    assert "22 topology-fetch failures" in run["qualification"]
    assert set(summary["shapes"]) == {"4/2", "8/4", "16/8", "32/16", "64/32"}
    assert summary["shapes"]["16/8"]["baseline"]["completed_tasks"] == 14
    assert summary["shapes"]["16/8"]["router"]["completed_tasks"] == 12
    assert summary["shapes"]["64/32"]["baseline"]["completed_tasks"] == 0
    assert summary["shapes"]["64/32"]["router"]["completed_tasks"] == 12
    assert summary["shapes"]["64/32"]["router"]["profiling.vllm.latency_breakdown_seconds.decode_time.mean"] == 24.484

    dashboard.write_dashboard(tmp_path / "runs", tmp_path / "site")
    markdown = (tmp_path / "site" / "README.md").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "site" / "dashboard-data.json").read_text(encoding="utf-8"))
    assert "52 router task timeouts" in markdown
    assert payload["daily_experiment_ids"] == ["daily-2026-07-14-10014"]
    assert "scheduler_ab" not in payload


@pytest.mark.cpu_test
def test_july_15_daily_import_preserves_cross_midnight_identity(tmp_path: Path) -> None:
    importer = load_script("import_historical_benchmark_reports")
    dashboard = load_script("build_benchmark_dashboard")
    report = Path("D:/agentic_serving/materials/feedback/ci-10014/2026-07-15-daily.md")
    written = importer.import_shape_tables(report, tmp_path)
    summary = json.loads((written[0] / "dashboard-summary.json").read_text(encoding="utf-8"))
    run = summary["run"]
    assert run["created_at"] == "2026-07-16T02:45:52+08:00"
    assert run["commit"] == "075f444a64dc367f91ecee5b124bb22405e6fd4b"
    assert run["experiment_id"] == "daily-2026-07-15-10014"
    assert "crossed midnight" in run["qualification"]
    assert "53 router task timeouts" in run["qualification"]
    assert "16 topology-fetch failures" in run["qualification"]
    assert summary["shapes"]["64/32"]["router"]["completed_tasks"] == 11

    discovered = dashboard.discover_runs(tmp_path / "runs")
    day = dashboard.daily_points(discovered)[0]
    assert day["date"] == "2026-07-15"
    assert dashboard.point_label(day) == "2026-07-15 · 10014"


@pytest.mark.cpu_test
def test_july_16_daily_import_preserves_identity_and_qualification(tmp_path: Path) -> None:
    importer = load_script("import_historical_benchmark_reports")
    dashboard = load_script("build_benchmark_dashboard")
    report = Path("D:/agentic_serving/materials/feedback/ci-10014/2026-07-16-daily.md")
    written = importer.import_shape_tables(report, tmp_path)
    summary = json.loads((written[0] / "dashboard-summary.json").read_text(encoding="utf-8"))
    run = summary["run"]
    assert run["created_at"] == "2026-07-16T22:12:21+08:00"
    assert run["commit"] == "075f444a64dc367f91ecee5b124bb22405e6fd4b"
    assert run["experiment_id"] == "daily-2026-07-16-10014"
    assert run["completeness"] == "complete"
    assert run["trend_eligible"] is True
    assert "54 router task timeouts" in run["qualification"]
    assert "48 traceback tokens" in run["qualification"]
    assert "24 topology-fetch failures" in run["qualification"]
    assert set(summary["shapes"]) == {"4/2", "8/4", "16/8", "32/16", "64/32"}
    assert summary["shapes"]["32/16"]["baseline"]["completed_tasks"] == 14
    assert summary["shapes"]["32/16"]["router"]["completed_tasks"] == 17
    assert summary["shapes"]["64/32"]["baseline"]["completed_tasks"] == 1
    assert summary["shapes"]["64/32"]["router"]["completed_tasks"] == 9
    assert summary["environment"]["vllm_source_commit"] == ("0decac0d96c42b49572498019f0a0e3600f50398")

    dashboard.write_dashboard(tmp_path / "runs", tmp_path / "site")
    markdown = (tmp_path / "site" / "README.md").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "site" / "dashboard-data.json").read_text(encoding="utf-8"))
    assert "54 router task timeouts" in markdown
    assert payload["daily_experiment_ids"] == ["daily-2026-07-16-10014"]
    assert "scheduler_ab" not in payload
    day = dashboard.daily_points(dashboard.discover_runs(tmp_path / "runs"))[0]
    assert dashboard.point_label(day) == "2026-07-16 · 10014"


@pytest.mark.cpu_test
def test_july_17_daily_import_preserves_cross_midnight_identity(tmp_path: Path) -> None:
    importer = load_script("import_historical_benchmark_reports")
    dashboard = load_script("build_benchmark_dashboard")
    report = Path("D:/agentic_serving/materials/feedback/ci-10014/2026-07-17-daily.md")
    written = importer.import_shape_tables(report, tmp_path)
    summary = json.loads((written[0] / "dashboard-summary.json").read_text(encoding="utf-8"))
    run = summary["run"]
    assert run["created_at"] == "2026-07-18T03:16:13+08:00"
    assert run["commit"] == "075f444a64dc367f91ecee5b124bb22405e6fd4b"
    assert run["experiment_id"] == "daily-2026-07-17-10014"
    assert run["completeness"] == "complete"
    assert run["trend_eligible"] is True
    assert "56 router task timeouts" in run["qualification"]
    assert "18 traceback tokens" in run["qualification"]
    assert "9 topology-fetch failures" in run["qualification"]
    assert set(summary["shapes"]) == {"4/2", "8/4", "16/8", "32/16", "64/32"}
    assert summary["shapes"]["32/16"]["baseline"]["completed_tasks"] == 19
    assert summary["shapes"]["32/16"]["router"]["completed_tasks"] == 22
    assert summary["shapes"]["64/32"]["baseline"]["completed_tasks"] == 0
    assert summary["shapes"]["64/32"]["router"]["completed_tasks"] == 8
    assert summary["environment"]["vllm_source_commit"] == ("0decac0d96c42b49572498019f0a0e3600f50398")

    dashboard.write_dashboard(tmp_path / "runs", tmp_path / "site")
    markdown = (tmp_path / "site" / "README.md").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "site" / "dashboard-data.json").read_text(encoding="utf-8"))
    assert "56 router task timeouts" in markdown
    assert payload["daily_experiment_ids"] == ["daily-2026-07-17-10014"]
    assert "scheduler_ab" not in payload
    day = dashboard.daily_points(dashboard.discover_runs(tmp_path / "runs"))[0]
    assert dashboard.point_label(day) == "2026-07-17 · 10014"


@pytest.mark.cpu_test
def test_july_20_daily_import_preserves_cross_midnight_identity(tmp_path: Path) -> None:
    importer = load_script("import_historical_benchmark_reports")
    dashboard = load_script("build_benchmark_dashboard")
    report = Path("D:/agentic_serving/materials/feedback/ci-10014/2026-07-20-daily.md")
    written = importer.import_shape_tables(report, tmp_path)
    summary = json.loads((written[0] / "dashboard-summary.json").read_text(encoding="utf-8"))
    run = summary["run"]
    assert run["created_at"] == "2026-07-21T04:42:29+08:00"
    assert run["commit"] == "075f444a64dc367f91ecee5b124bb22405e6fd4b"
    assert run["experiment_id"] == "daily-2026-07-20-10014"
    assert run["completeness"] == "complete"
    assert run["trend_eligible"] is True
    assert "53 router task timeouts" in run["qualification"]
    assert "one confirmation hang" in run["qualification"]
    assert "50 traceback tokens" in run["qualification"]
    assert "25 topology-fetch failures" in run["qualification"]
    assert set(summary["shapes"]) == {"4/2", "8/4", "16/8", "32/16", "64/32"}
    assert summary["shapes"]["8/4"]["baseline"]["completed_tasks"] == 8
    assert summary["shapes"]["8/4"]["router"]["completed_tasks"] == 6
    assert summary["shapes"]["64/32"]["baseline"]["completed_tasks"] == 1
    assert summary["shapes"]["64/32"]["router"]["completed_tasks"] == 10

    dashboard.write_dashboard(tmp_path / "runs", tmp_path / "site")
    markdown = (tmp_path / "site" / "README.md").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "site" / "dashboard-data.json").read_text(encoding="utf-8"))
    assert "53 router task timeouts" in markdown
    assert payload["daily_experiment_ids"] == ["daily-2026-07-20-10014"]
    assert "scheduler_ab" not in payload
    day = dashboard.daily_points(dashboard.discover_runs(tmp_path / "runs"))[0]
    assert dashboard.point_label(day) == "2026-07-20 · 10014"


@pytest.mark.cpu_test
def test_july_21_daily_import_preserves_cross_midnight_identity(tmp_path: Path) -> None:
    importer = load_script("import_historical_benchmark_reports")
    dashboard = load_script("build_benchmark_dashboard")
    report = Path("D:/agentic_serving/materials/feedback/ci-10014/2026-07-21-daily.md")
    written = importer.import_shape_tables(report, tmp_path)
    summary = json.loads((written[0] / "dashboard-summary.json").read_text(encoding="utf-8"))
    run = summary["run"]
    assert run["created_at"] == "2026-07-22T06:23:53+08:00"
    assert run["commit"] == "075f444a64dc367f91ecee5b124bb22405e6fd4b"
    assert run["experiment_id"] == "daily-2026-07-21-10014"
    assert run["completeness"] == "complete"
    assert run["trend_eligible"] is True
    assert "58 router task timeouts" in run["qualification"]
    assert "52 traceback tokens" in run["qualification"]
    assert "26 topology-fetch failures" in run["qualification"]
    assert set(summary["shapes"]) == {"4/2", "8/4", "16/8", "32/16", "64/32"}
    assert summary["shapes"]["32/16"]["baseline"]["completed_tasks"] == 18
    assert summary["shapes"]["32/16"]["router"]["completed_tasks"] == 25
    assert summary["shapes"]["64/32"]["baseline"]["completed_tasks"] == 1
    assert summary["shapes"]["64/32"]["router"]["completed_tasks"] == 6

    dashboard.write_dashboard(tmp_path / "runs", tmp_path / "site")
    markdown = (tmp_path / "site" / "README.md").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "site" / "dashboard-data.json").read_text(encoding="utf-8"))
    assert "58 router task timeouts" in markdown
    assert payload["daily_experiment_ids"] == ["daily-2026-07-21-10014"]
    assert "scheduler_ab" not in payload
    day = dashboard.daily_points(dashboard.discover_runs(tmp_path / "runs"))[0]
    assert dashboard.point_label(day) == "2026-07-21 · 10014"


@pytest.mark.cpu_test
def test_july_22_daily_import_preserves_identity_and_qualification(tmp_path: Path) -> None:
    importer = load_script("import_historical_benchmark_reports")
    dashboard = load_script("build_benchmark_dashboard")
    report = Path("D:/agentic_serving/materials/feedback/ci-10014/2026-07-22-daily.md")
    written = importer.import_shape_tables(report, tmp_path)
    summary = json.loads((written[0] / "dashboard-summary.json").read_text(encoding="utf-8"))
    run = summary["run"]
    assert run["created_at"] == "2026-07-22T23:46:32+08:00"
    assert run["commit"] == "075f444a64dc367f91ecee5b124bb22405e6fd4b"
    assert run["experiment_id"] == "daily-2026-07-22-10014"
    assert run["completeness"] == "complete"
    assert run["trend_eligible"] is True
    assert "54 router task timeouts" in run["qualification"]
    assert "24 traceback tokens" in run["qualification"]
    assert "12 topology-fetch failures" in run["qualification"]
    assert set(summary["shapes"]) == {"4/2", "8/4", "16/8", "32/16", "64/32"}
    assert summary["shapes"]["4/2"]["baseline"]["completed_tasks"] == 4
    assert summary["shapes"]["4/2"]["router"]["completed_tasks"] == 3
    assert summary["shapes"]["64/32"]["baseline"]["completed_tasks"] == 2
    assert summary["shapes"]["64/32"]["router"]["completed_tasks"] == 10

    dashboard.write_dashboard(tmp_path / "runs", tmp_path / "site")
    markdown = (tmp_path / "site" / "README.md").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "site" / "dashboard-data.json").read_text(encoding="utf-8"))
    assert "54 router task timeouts" in markdown
    assert payload["daily_experiment_ids"] == ["daily-2026-07-22-10014"]
    assert "scheduler_ab" not in payload
    day = dashboard.daily_points(dashboard.discover_runs(tmp_path / "runs"))[0]
    assert dashboard.point_label(day) == "2026-07-22 · 10014"


@pytest.mark.cpu_test
def test_july_23_daily_import_preserves_identity_and_qualification(tmp_path: Path) -> None:
    importer = load_script("import_historical_benchmark_reports")
    dashboard = load_script("build_benchmark_dashboard")
    report = Path("D:/agentic_serving/materials/feedback/ci-10014/2026-07-23-daily.md")
    written = importer.import_shape_tables(report, tmp_path)
    summary = json.loads((written[0] / "dashboard-summary.json").read_text(encoding="utf-8"))
    run = summary["run"]
    assert run["created_at"] == "2026-07-23T23:39:54+08:00"
    assert run["commit"] == "075f444a64dc367f91ecee5b124bb22405e6fd4b"
    assert run["experiment_id"] == "daily-2026-07-23-10014"
    assert run["completeness"] == "complete"
    assert run["trend_eligible"] is True
    assert "50 router task timeouts" in run["qualification"]
    assert "7 unregistered-task tokens" in run["qualification"]
    assert "9 socket-send exception tokens" in run["qualification"]
    assert set(summary["shapes"]) == {"4/2", "8/4", "16/8", "32/16", "64/32"}
    assert summary["shapes"]["16/8"]["baseline"]["completed_tasks"] == 15
    assert summary["shapes"]["16/8"]["router"]["completed_tasks"] == 13
    assert summary["shapes"]["64/32"]["baseline"]["completed_tasks"] == 0
    assert summary["shapes"]["64/32"]["router"]["completed_tasks"] == 12

    dashboard.write_dashboard(tmp_path / "runs", tmp_path / "site")
    markdown = (tmp_path / "site" / "README.md").read_text(encoding="utf-8")
    payload = json.loads((tmp_path / "site" / "dashboard-data.json").read_text(encoding="utf-8"))
    assert "50 router task timeouts" in markdown
    assert payload["daily_experiment_ids"] == ["daily-2026-07-23-10014"]
    assert "scheduler_ab" not in payload
    day = dashboard.daily_points(dashboard.discover_runs(tmp_path / "runs"))[0]
    assert dashboard.point_label(day) == "2026-07-23 · 10014"


@pytest.mark.cpu_test
def test_line_chart_samples_rotated_labels_without_dropping_points(tmp_path: Path) -> None:
    dashboard = load_script("build_benchmark_dashboard")
    labels = [f"2026-07-{day:02d} · 10014" for day in range(1, 25)]
    chart = tmp_path / "dense.svg"
    dashboard.svg_line_chart(chart, "Dense", {"series": list(zip(labels, range(24), strict=True))})
    svg = chart.read_text(encoding="utf-8")

    assert labels[0] in svg
    assert labels[-1] in svg
    assert sum(label in svg for label in labels) < len(labels)
    assert "rotate(-35" in svg
    polyline = svg.split("<polyline", 1)[1].split("/>", 1)[0]
    assert len(polyline.split("points='", 1)[1].split("'", 1)[0].split()) == len(labels)


@pytest.mark.cpu_test
def test_heatmap_keeps_date_rows_and_shape_columns(tmp_path: Path) -> None:
    dashboard = load_script("build_benchmark_dashboard")
    day = {
        "date": "2026-07-21",
        "host": "10014",
        "runs": [
            {
                "data": {
                    "shapes": {
                        shape: {"baseline": {VLLM_QUEUE_MEAN: 1}, "router": {VLLM_QUEUE_MEAN: 1}}
                        for shape in dashboard.SHAPES
                    }
                }
            }
        ],
    }
    chart = tmp_path / "heatmap.svg"
    dashboard.svg_heatmap(chart, [day], VLLM_QUEUE_MEAN)
    svg = chart.read_text(encoding="utf-8")

    assert "2026-07-21" in svg
    assert all(shape in svg for shape in dashboard.SHAPES)
    assert "rotate(-35" not in svg


@pytest.mark.cpu_test
def test_daily_identity_conflict(tmp_path: Path) -> None:
    dashboard = load_script("build_benchmark_dashboard")
    base = {
        "run": {
            "host": "10014",
            "created_at": "2026-07-12T00:00:00Z",
            "experiment_kind": "daily",
            "experiment_id": "daily-12",
            "expected_shapes": ["32/16"],
            "completeness": "complete",
            "trend_eligible": True,
        },
        "shapes": {"32/16": {"baseline": {"completed_tasks": 1}, "router": {"completed_tasks": 2}}},
    }
    for index, completed in enumerate((2, 3)):
        data = json.loads(json.dumps(base))
        data["shapes"]["32/16"]["router"]["completed_tasks"] = completed
        path = tmp_path / str(index)
        path.mkdir()
        (path / "dashboard-summary.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="daily-12"):
        dashboard.daily_points(dashboard.discover_runs(tmp_path))

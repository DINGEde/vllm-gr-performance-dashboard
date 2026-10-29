"""Build the NPU vllm-gr dashboard data snapshot from raw NPU benchmark JSONs.

The NPU dashboard is independent from the GPU dashboard: GPU code is not
touched. New NPU result files are merged into an accumulating snapshot that
keeps one point per (scenario, day) and prunes anything older than 30 days,
so the page can draw daily latency fluctuation charts.

Run date source (in order): --date override, then the file's last-modified
date. Re-running the script on later days appends new daily points while the
snapshot in docs/ preserves the previous 30 days of history.

Schema routing: files in the legacy ``batch_latency_ms`` format feed the
original snapshot (docs/npu-vllm-gr-data.json, unchanged behavior), while
files produced by tools/daily_benchmark/run_offline_benchmark_npu.py
(``schema_version: vllm-gr.offline.raw.v2``) feed a separate accumulating
snapshot (docs/npu-vllm-gr-v2-data.json) with the same (scenario, day)
merge/retention rules. The two snapshots never mix so the legacy dashboard
keeps a single measurement protocol.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "npu-vllm-gr.dashboard.v1"
V2_SCHEMA_VERSION = "npu-vllm-gr.dashboard.rawv2.v1"
RAW_V2_SCHEMA = "vllm-gr.offline.raw.v2"
NPU_RUN_SUMMARY_SCHEMA = "vllm-gr-npu.daily.v1"
RETENTION_DAYS = 30

LATENCY_STATS = ("mean", "p50", "p95", "min", "max")

# raw.v2 canonical e2e and diagnostic phase distributions the future page will
# chart, copied verbatim from the raw file (values already in ms).
V2_DISTRIBUTIONS = (
    "e2e_ms_miss",
    "e2e_ms_hit",
    "prefill_ms_miss",
    "prefill_ms_hit",
    "decode_ms_miss",
    "decode_ms_hit",
    "prefill_gpu_compute_ms_miss",
    "prefill_gpu_compute_ms_hit",
    "decode_gpu_compute_ms_miss",
    "decode_gpu_compute_ms_hit",
    "host_overhead_ms_miss",
    "host_overhead_ms_hit",
)


def is_npu_result(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("batch_latency_ms"), dict)
        and isinstance(data.get("beam_width"), int)
        and isinstance(data.get("batch_size"), int)
        and isinstance(data.get("input_len"), int)
    )


def load_result(path: Path) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Load one benchmark file.

    Returns ``(result, run_date, source_name)``. For a runs/ archive summary
    (``vllm-gr-npu.daily.v1``, produced by ``build_npu_run_summary.py``) the
    wrapped ``benchmark`` payload is unwrapped, ``run_date`` comes from the
    wrapper's ``run.date``, and ``source_name`` points at the run slug.
    Plain raw result files return the data as-is with ``run_date`` None so the
    caller falls back to the file mtime.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: skip unreadable {path}: {exc}", file=sys.stderr)
        return None, None, None
    if isinstance(data, dict) and data.get("schema_version") == NPU_RUN_SUMMARY_SCHEMA:
        run = data.get("run") if isinstance(data.get("run"), dict) else {}
        inner = data.get("benchmark")
        if not isinstance(inner, dict) or not is_npu_result(inner):
            return None, None, None
        run_id = run.get("id") or path.parent.name
        return inner, run.get("date"), f"{run_id}/{path.name}"
    if not is_npu_result(data):
        return None, None, None
    return data, None, path.name


def scenario_key(result: dict[str, Any]) -> str:
    return f"b{result['batch_size']}-bw{result['beam_width']}-in{result['input_len']}"


def model_short_name(model: Any) -> str:
    return Path(str(model or "unknown")).name


def run_date_for(path: Path, override: str | None) -> str:
    if override:
        return override
    mtime = path.stat().st_mtime
    return datetime.fromtimestamp(mtime).date().isoformat()


def collect_new_points(sources: list[Path], date_override: str | None) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for source in sources:
        files = sorted(source.rglob("*.json")) if source.is_dir() else [source]
        for path in files:
            result, summary_date, source_name = load_result(path)
            if result is None:
                continue
            latency = {
                stat: float(result["batch_latency_ms"][stat])
                for stat in LATENCY_STATS
                if stat in result["batch_latency_ms"]
            }
            repeats = [
                {"run": item.get("run"), "batch_latency_ms": float(item["batch_latency_ms"])}
                for item in result.get("runs", [])
                if isinstance(item, dict) and "batch_latency_ms" in item
            ]
            points.append(
                {
                    "date": date_override or summary_date or run_date_for(path, None),
                    "scenario_key": scenario_key(result),
                    "model": model_short_name(result.get("model")),
                    "latency_ms": latency,
                    "requests_per_second": result.get("requests_per_second"),
                    "returned_beams_per_second": result.get("returned_beams_per_second"),
                    "repeat": result.get("repeat"),
                    "warmup_runs": result.get("warmup_runs"),
                    "repeats": repeats,
                    "graph": result.get("npu_beam_graph") or {},
                    "config": {
                        "model": result.get("model"),
                        "catalog": result.get("catalog"),
                        "prompt": result.get("prompt"),
                        "constraint_backend": result.get("constraint_backend"),
                        "execution": result.get("execution"),
                        "generated_steps": result.get("generated_steps"),
                        "length_penalty": result.get("length_penalty"),
                        "max_model_len": result.get("max_model_len"),
                        "gpu_memory_utilization": result.get("gpu_memory_utilization"),
                        "prefix_caching": result.get("prefix_caching"),
                        "timing_scope": result.get("timing_scope"),
                    },
                    "source": source_name,
                    "_mtime": path.stat().st_mtime,
                }
            )
    return points


def is_raw_v2_result(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and data.get("schema_version") == RAW_V2_SCHEMA
        and data.get("device") == "npu"
        and isinstance(data.get("distributions"), dict)
        and isinstance(data.get("beam_width"), int)
        and isinstance(data.get("batch_size"), int)
        and isinstance(data.get("input_length"), int)
    )


def load_raw_v2_result(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: skip unreadable {path}: {exc}", file=sys.stderr)
        return None
    if not is_raw_v2_result(data):
        return None
    return data


def raw_v2_scenario_key(result: dict[str, Any]) -> str:
    return f"b{result['batch_size']}-bw{result['beam_width']}-in{result['input_length']}"


def _numeric_distribution(dist: Any) -> dict[str, float]:
    if not isinstance(dist, dict):
        return {}
    return {
        str(stat): float(value)
        for stat, value in dist.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def collect_raw_v2_points(
    sources: list[Path], date_override: str | None
) -> list[dict[str, Any]]:
    """Collect canonical miss/hit points from raw.v2 result files.

    One point per file carries the canonical e2e distributions (the values a
    future page charts alongside the GPU dashboard's raw.v2 protocol) plus the
    diagnostic phase distributions, the per-batch sample arrays, a trimmed
    beam-graph health block, and the engine config needed to reproduce the run.
    """
    points: list[dict[str, Any]] = []
    for source in sources:
        files = sorted(source.rglob("*.json")) if source.is_dir() else [source]
        for path in files:
            result = load_raw_v2_result(path)
            if result is None:
                continue
            canonical = result.get("distributions", {})
            diagnostic = (result.get("diagnostic") or {}).get("distributions", {})
            graph = result.get("npu_beam_graph") or {}
            engine = result.get("engine_config") or {}
            points.append(
                {
                    "date": run_date_for(path, date_override),
                    "scenario_key": raw_v2_scenario_key(result),
                    "model": model_short_name(result.get("model")),
                    "distributions": {
                        name: _numeric_distribution(canonical.get(name))
                        for name in V2_DISTRIBUTIONS
                        if name in canonical
                    },
                    "diagnostic_distributions": {
                        name: _numeric_distribution(diagnostic.get(name))
                        for name in V2_DISTRIBUTIONS
                        if name in diagnostic
                    },
                    "miss_samples": [
                        float(value)
                        for value in (result.get("miss") or {}).get("e2e_ms", [])
                    ],
                    "hit_samples": [
                        float(value)
                        for value in (result.get("hit") or {}).get("e2e_ms", [])
                    ],
                    "num_prompts": result.get("num_prompts"),
                    "warmup_requests": result.get("warmup_requests"),
                    "completed": result.get("completed"),
                    "failed": result.get("failed"),
                    "aggregate_output_tokens": result.get("aggregate_output_tokens"),
                    "duration_seconds": result.get("duration_seconds"),
                    "pair_duration_seconds": result.get("pair_duration_seconds"),
                    "measurement_mode": result.get("measurement_mode"),
                    "pipeline_version": result.get("pipeline_version"),
                    "graph": {
                        key: graph[key]
                        for key in (
                            "captured_shapes",
                            "fallbacks",
                            "full_dispatches",
                            "beam_graph_batch_size",
                            "beam_max_width",
                        )
                        if key in graph
                    },
                    "config": {
                        "model": result.get("model"),
                        "catalog": engine.get("catalog_path"),
                        "prompt": result.get("prompt"),
                        "execution": engine.get("npu_execution"),
                        "constraint_table_backend": engine.get("constraint_table_backend"),
                        "max_model_len": result.get("max_model_len", engine.get("max_model_len")),
                        "generated_steps": result.get("generated_steps"),
                        "max_tokens": result.get("max_tokens"),
                        "gpu_memory_utilization": engine.get("gpu_memory_utilization"),
                        "batch_size": result.get("batch_size"),
                        "single_request": engine.get("single_request"),
                        "started_at": result.get("started_at"),
                        "finished_at": result.get("finished_at"),
                    },
                    "source": path.name,
                    "_mtime": path.stat().st_mtime,
                }
            )
    return points


def merge_history(existing: dict[str, Any], points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge new measurements into the snapshot; newest file mtime wins per cell."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for run in existing.get("runs", []):
        key = (run.get("scenario_key"), run.get("date"))
        if isinstance(key[0], str) and isinstance(key[1], str):
            merged[key] = run
    for point in points:
        key = (point["scenario_key"], point["date"])
        current = merged.get(key)
        if current is None or point["_mtime"] >= float(current.get("_mtime", -1)):
            merged[key] = point
    return list(merged.values())


def prune(runs: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    cutoff = (today - timedelta(days=RETENTION_DAYS - 1)).isoformat()
    kept = [run for run in runs if run.get("date", "") >= cutoff]
    kept.sort(key=lambda run: (run["date"], run["scenario_key"]))
    return kept


def build_payload(
    runs: list[dict[str, Any]], today: date, schema_version: str = SCHEMA_VERSION
) -> dict[str, Any]:
    scenarios: dict[str, dict[str, Any]] = {}
    for run in runs:
        key = run["scenario_key"]
        if key not in scenarios:
            batch = int(key.split("-")[0][1:])
            beam = int(key.split("-")[1][2:])
            input_len = int(key.split("-")[2][2:])
            model = run.get("model", "unknown")
            scenarios[key] = {
                "key": key,
                # label mirrors the GPU dashboard naming style (model · batch-N · beam-N · input-N · offline-c1)
                "label": f"{model} · batch-{batch} · beam-{beam} · input-{input_len} · offline-c1",
                "batch_size": batch,
                "beam_width": beam,
                "input_len": input_len,
                "model": model,
            }
    for run in runs:
        run.pop("_mtime", None)
    return {
        "schema_version": schema_version,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "display_start_date": (today - timedelta(days=RETENTION_DAYS - 1)).isoformat(),
        "retention_days": RETENTION_DAYS,
        "device": "Ascend 910B1",
        "scenarios": sorted(
            scenarios.values(),
            key=lambda item: (item["batch_size"], item["beam_width"], item["input_len"]),
        ),
        "runs": runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        nargs="+",
        required=True,
        help="NPU result JSON file(s) or directories to scan recursively",
    )
    parser.add_argument("--output", type=Path, default=Path("docs"), help="dashboard docs directory")
    parser.add_argument("--date", help="force the run date (ISO) for every collected file")
    args = parser.parse_args()

    output = args.output / "npu-vllm-gr-data.json"
    existing: dict[str, Any] = {"runs": []}
    if output.exists():
        try:
            loaded = json.loads(output.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("runs"), list):
                existing = loaded
        except json.JSONDecodeError as exc:
            print(f"warning: ignoring corrupt snapshot {output}: {exc}", file=sys.stderr)

    points = collect_new_points(args.source, args.date)
    today = date.today()
    runs = prune(merge_history(existing, points), today)
    payload = build_payload(runs, today)
    args.output.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output} with {len(runs)} scenario-day points across {len(payload['scenarios'])} scenarios")

    # raw.v2 protocol (run_offline_benchmark_npu.py): same merge/retention
    # rules, separate snapshot, so the legacy dashboard keeps a single
    # measurement protocol. Only touched when raw.v2 files exist or the
    # snapshot already accumulated history.
    v2_output = args.output / "npu-vllm-gr-v2-data.json"
    v2_points = collect_raw_v2_points(args.source, args.date)
    if not v2_points and not v2_output.exists():
        print("note: no raw.v2 result files found; v2 snapshot not created")
        return
    v2_existing: dict[str, Any] = {"runs": []}
    if v2_output.exists():
        try:
            loaded = json.loads(v2_output.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("runs"), list):
                v2_existing = loaded
        except json.JSONDecodeError as exc:
            print(f"warning: ignoring corrupt snapshot {v2_output}: {exc}", file=sys.stderr)
    v2_runs = prune(merge_history(v2_existing, v2_points), today)
    v2_payload = build_payload(v2_runs, today, V2_SCHEMA_VERSION)
    v2_output.write_text(json.dumps(v2_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {v2_output} with {len(v2_runs)} scenario-day points "
        f"across {len(v2_payload['scenarios'])} scenarios (raw.v2 protocol)"
    )


if __name__ == "__main__":
    main()

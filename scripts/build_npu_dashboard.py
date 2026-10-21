"""Build the NPU vllm-gr dashboard data snapshot from raw NPU benchmark JSONs.

The NPU dashboard is independent from the GPU dashboard: GPU code is not
touched. New NPU result files are merged into an accumulating snapshot that
keeps one point per (scenario, day) and prunes anything older than 30 days,
so the page can draw daily latency fluctuation charts.

Run date source (in order): --date override, then the file's last-modified
date. Re-running the script on later days appends new daily points while the
snapshot in docs/ preserves the previous 30 days of history.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "npu-vllm-gr.dashboard.v1"
RETENTION_DAYS = 30

LATENCY_STATS = ("mean", "p50", "p95", "min", "max")


def is_npu_result(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("batch_latency_ms"), dict)
        and isinstance(data.get("beam_width"), int)
        and isinstance(data.get("batch_size"), int)
        and isinstance(data.get("input_len"), int)
    )


def load_result(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: skip unreadable {path}: {exc}", file=sys.stderr)
        return None
    if not is_npu_result(data):
        return None
    return data


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
            result = load_result(path)
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
                    "date": run_date_for(path, date_override),
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


def build_payload(runs: list[dict[str, Any]], today: date) -> dict[str, Any]:
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
        "schema_version": SCHEMA_VERSION,
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


if __name__ == "__main__":
    main()

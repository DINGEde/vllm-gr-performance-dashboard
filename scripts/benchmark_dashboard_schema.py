"""Shared metric names and compatibility normalization for benchmark dashboards."""

from __future__ import annotations

import re
from typing import Any

VLLM_QUEUE_MEAN = "profiling.vllm.latency_breakdown_seconds.queue_time.mean"
VLLM_QUEUE_MEAN_ALIASES = (
    VLLM_QUEUE_MEAN,
    "latency_breakdown_seconds.vllm_queue_time.mean",
    "vllm_queue_time_seconds.mean",
)
WALL_SECONDS_ALIASES = (
    "wall_seconds",
    "run_wall_time_seconds",
)
PREFIX_HIT_ALIASES = (
    "prefix_cache_hit_rate",
    "vllm_prefix_cache_hit_rate",
    "backend_metrics.prefix_cache_hit_rate",
)
STANDARD_SHAPES = ("4/2", "8/4", "16/8", "32/16", "64/32")
EXPERIMENT_KINDS = {"daily", "scheduler_ab"}
COMPLETENESS_VALUES = {"complete", "focused", "partial"}
# Keep historical and new CI GPU labels in one filter bucket.
CANONICAL_L20_HARDWARE = "L20"


def normalize_hardware_label(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    key = (
        text.lower()
        .replace("\u00d7", "x")
        .replace("×", "x")
        .replace(" ", "")
    )
    if "l20" in key:
        return CANONICAL_L20_HARDWARE
    return text


def canonical_shape_key(key: str) -> str:
    text = str(key)
    if "/" in text:
        return text
    match = re.fullmatch(r"(\d+)_(\d+)", text)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return text


def is_new_summary_format(data: dict[str, Any]) -> bool:
    """Detect CI summaries where metrics live under averages and shapes are metadata."""
    shapes = data.get("shapes")
    if isinstance(shapes, dict) and shapes:
        sample = next(iter(shapes.values()))
        if isinstance(sample, dict) and ("baseline" in sample or "router" in sample):
            return False
    averages = data.get("averages")
    all_shapes = averages.get("all_shapes") if isinstance(averages, dict) else None
    if isinstance(all_shapes, dict) and "candidate" in all_shapes:
        return True
    if isinstance(shapes, dict) and shapes:
        sample = next(iter(shapes.values()))
        return isinstance(sample, dict) and "baseline" not in sample and "router" not in sample
    return False


def _side_metrics(*candidates: Any) -> dict[str, Any]:
    for candidate in candidates:
        if isinstance(candidate, dict):
            return dict(candidate)
    return {}


def adapt_summary_format(data: dict[str, Any]) -> None:
    """Convert newer CI dashboard-summary.json into the legacy shapes layout."""
    if not is_new_summary_format(data):
        return

    averages = data.get("averages") if isinstance(data.get("averages"), dict) else {}
    all_shapes = averages.get("all_shapes") if isinstance(averages.get("all_shapes"), dict) else {}
    baseline_all = _side_metrics(all_shapes.get("baseline"))
    router_all = _side_metrics(all_shapes.get("candidate"), all_shapes.get("router"))

    meta_shapes = data.get("shapes") if isinstance(data.get("shapes"), dict) else {}
    adapted: dict[str, Any] = {}
    for raw_key, meta in meta_shapes.items():
        shape = canonical_shape_key(raw_key)
        shape_avg = averages.get(raw_key) if isinstance(averages.get(raw_key), dict) else None
        if shape_avg is None:
            shape_avg = averages.get(shape) if isinstance(averages.get(shape), dict) else None
        baseline = _side_metrics(
            shape_avg.get("baseline") if isinstance(shape_avg, dict) else None,
            baseline_all,
        )
        router = _side_metrics(
            shape_avg.get("candidate") if isinstance(shape_avg, dict) else None,
            shape_avg.get("router") if isinstance(shape_avg, dict) else None,
            router_all,
        )
        payload = {"baseline": baseline, "router": router}
        if isinstance(meta, dict):
            for key, value in meta.items():
                if key not in {"baseline", "router", "available_metrics"}:
                    payload[key] = value
        adapted[shape] = payload

    if not adapted and (baseline_all or router_all):
        run = data.get("run") if isinstance(data.get("run"), dict) else {}
        match = re.search(r"(\d+)[_/](\d+)", str(run.get("name", "")))
        shape = f"{match.group(1)}/{match.group(2)}" if match else "32/16"
        adapted[shape] = {"baseline": baseline_all, "router": router_all}

    data["shapes"] = adapted

    run = data.setdefault("run", {})
    if not isinstance(run, dict):
        return
    date = str(run.get("date") or "")[:10]
    if date and not run.get("created_at"):
        run["created_at"] = f"{date}T00:00:00Z"
    run.setdefault("experiment_kind", "daily")
    if not run.get("experiment_id"):
        host = run.get("host", "unknown-host")
        name = run.get("name", "run")
        stamp = date or str(run.get("created_at", ""))[:10] or "undated"
        run["experiment_id"] = f"ci-{host}-{stamp}-{name}"
    if adapted:
        run.setdefault("expected_shapes", list(adapted.keys()))
    run.setdefault("completeness", "partial" if set(adapted.keys()) != set(STANDARD_SHAPES) else "complete")
    run.setdefault("trend_eligible", True)

    environment = data.get("environment")
    if isinstance(environment, dict):
        if not environment.get("hardware"):
            for key in ("gpus", "gpu", "hardware_label"):
                if environment.get(key):
                    environment["hardware"] = str(environment[key])
                    break
        hardware = normalize_hardware_label(environment.get("hardware"))
        if hardware:
            environment["hardware"] = hardware


def normalize_experiment(data: dict[str, Any]) -> None:
    adapt_summary_format(data)
    run = data.setdefault("run", {})
    shapes = list(data.get("shapes", {}))
    profile = run.get("profile", "")
    kind = run.get("experiment_kind")
    if kind is None:
        kind = "daily"
    if kind not in EXPERIMENT_KINDS:
        raise ValueError(f"unknown experiment kind: {kind!r}")

    expected_shapes = run.get("expected_shapes")
    if expected_shapes is None:
        expected_shapes = list(STANDARD_SHAPES) if kind == "daily" else shapes
    if (
        not isinstance(expected_shapes, list)
        or not expected_shapes
        or any(not isinstance(shape, str) or shape not in STANDARD_SHAPES for shape in expected_shapes)
    ):
        raise ValueError(f"invalid expected shapes: {expected_shapes!r}")
    if len(expected_shapes) != len(set(expected_shapes)):
        raise ValueError(f"duplicate expected shapes: {expected_shapes!r}")

    completeness = run.get("completeness")
    if completeness is None:
        completeness = "complete" if set(shapes) == set(STANDARD_SHAPES) else "partial"
    if completeness not in COMPLETENESS_VALUES:
        raise ValueError(f"unknown completeness: {completeness!r}")
    if kind == "daily" and completeness == "complete" and set(shapes) != set(expected_shapes):
        raise ValueError("complete daily experiment must contain all expected shapes")
    if completeness == "focused" and kind != "scheduler_ab":
        raise ValueError("focused completeness is reserved for scheduler_ab experiments")

    trend_eligible = run.get("trend_eligible")
    if trend_eligible is None:
        trend_eligible = kind == "daily" and (completeness == "complete" or profile == "historical-full")
    if not isinstance(trend_eligible, bool):
        raise ValueError(f"trend_eligible must be boolean: {trend_eligible!r}")
    if kind != "daily" and trend_eligible:
        raise ValueError("only daily experiments may be trend eligible")

    experiment_id = run.get("experiment_id")
    if not experiment_id:
        date = str(run.get("created_at", ""))[:10] or "undated"
        host = run.get("host", "unknown-host")
        experiment_id = f"legacy-{kind}-{host}-{date}-{run.get('run_id', 'run')}"

    run.update(
        {
            "experiment_kind": kind,
            "experiment_id": experiment_id,
            "expected_shapes": expected_shapes,
            "completeness": completeness,
            "trend_eligible": trend_eligible,
        }
    )


def _collapse_aliases(normalized: dict[str, Any], canonical: str, aliases: tuple[str, ...]) -> None:
    present = {key: normalized[key] for key in aliases if key in normalized}
    if not present:
        return
    if canonical in present:
        value = present[canonical]
    else:
        values = list(present.values())
        if any(item != values[0] for item in values[1:]):
            details = ", ".join(f"{key}={item!r}" for key, item in present.items())
            raise ValueError(f"conflicting {canonical} metrics: {details}")
        value = values[0]
    for key in present:
        normalized.pop(key, None)
    normalized[canonical] = value


def normalize_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(metrics)
    present = {key: normalized[key] for key in VLLM_QUEUE_MEAN_ALIASES if key in normalized}
    if present:
        values = list(present.values())
        if any(value != values[0] for value in values[1:]):
            details = ", ".join(f"{key}={value!r}" for key, value in present.items())
            raise ValueError(f"conflicting vLLM queue mean metrics: {details}")
        for key in present:
            normalized.pop(key)
        normalized[VLLM_QUEUE_MEAN] = values[0]
    _collapse_aliases(normalized, "wall_seconds", WALL_SECONDS_ALIASES)
    if "prefix_cache_hit_rate" not in normalized or normalized.get("prefix_cache_hit_rate") in (None, 0, 0.0):
        for key in PREFIX_HIT_ALIASES[1:]:
            if key in normalized and normalized[key] not in (None,):
                normalized["prefix_cache_hit_rate"] = normalized[key]
                break
    return normalized

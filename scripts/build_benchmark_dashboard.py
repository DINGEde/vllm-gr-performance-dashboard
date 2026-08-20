"""Build a static AgentCache benchmark dashboard from compact run artifacts."""

from __future__ import annotations

import argparse
import html
import json
import math
import re
from pathlib import Path
from statistics import mean
from typing import Any

from benchmark_dashboard_schema import (
    VLLM_QUEUE_MEAN,
    normalize_experiment,
    normalize_hardware_label,
    normalize_metrics,
)

SHAPES = ["4/2", "8/4", "16/8", "32/16", "64/32"]
CORE_METRICS = [
    "completed_tasks",
    "failed_tasks",
    "tasks_with_patch",
    "task_duration_seconds.mean",
    "task_duration_seconds.p50",
    "task_duration_seconds.p95",
    "task_duration_seconds.p99",
    "wall_seconds",
    "request_throughput",
    "latency_seconds.mean",
    "latency_seconds.p95",
    "ttft_seconds.mean",
    VLLM_QUEUE_MEAN,
    "prefix_cache_hit_rate",
]
LATENCY_FIGURE_METRICS = [
    (VLLM_QUEUE_MEAN, "queue_mean"),
    ("ttft_seconds.mean", "ttft_mean"),
    ("latency_seconds.mean", "latency_mean"),
]
SHAPE_TREND_METRICS = [
    ("completed_tasks", "completed", "Completed tasks", "completed-tasks-by-shape.svg"),
    ("failed_tasks", "failed", "Failed tasks", "failed-tasks-by-shape.svg"),
    (
        "task_duration_seconds.mean",
        "task_duration_mean",
        "Task duration mean",
        "task-duration-mean-by-shape.svg",
    ),
    (VLLM_QUEUE_MEAN, "queue_mean", "Queue mean", "queue-mean-by-shape.svg"),
    ("ttft_seconds.mean", "ttft_mean", "TTFT mean", "ttft-mean-by-shape.svg"),
    ("latency_seconds.mean", "latency_mean", "Latency mean", "latency-mean-by-shape.svg"),
]
METRIC_LABELS = {
    "completed_tasks": "completed",
    "failed_tasks": "failed",
    "tasks_with_patch": "patches",
    "task_duration_seconds.mean": "task_duration_mean",
    "task_duration_seconds.p50": "task_duration_p50",
    "task_duration_seconds.p95": "task_duration_p95",
    "task_duration_seconds.p99": "task_duration_p99",
    "wall_seconds": "wall_s",
    "request_throughput": "request_throughput",
    "latency_seconds.mean": "latency_mean",
    "latency_seconds.p95": "latency_p95",
    "ttft_seconds.mean": "ttft_mean",
    VLLM_QUEUE_MEAN: "queue_mean",
    "prefix_cache_hit_rate": "prefix_hit",
}
SHAPE_COLORS = {
    "4/2": "#2563eb",
    "8/4": "#0891b2",
    "16/8": "#15803d",
    "32/16": "#b45309",
    "64/32": "#7c3aed",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fmt(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        if abs(value) >= 100:
            return f"{value:.1f}"
        if abs(value) >= 10:
            return f"{value:.2f}"
        return f"{value:.3f}"
    return str(value)


def pct(value: Any) -> str:
    return "N/A" if value is None else f"{value:+.1f}%"


def delta_arrow_pct(baseline: float | None, router: float | None) -> str:
    if baseline is None or router is None or baseline == 0:
        return ""
    delta = ((router - baseline) / baseline) * 100
    if abs(delta) < 0.05:
        return "→0.0%"
    arrow = "↑" if delta > 0 else "↓"
    return f"{arrow}{abs(delta):.1f}%"


def router_metric_cell(baseline: float | None, router: float | None) -> str:
    value = fmt(router)
    arrow = delta_arrow_pct(baseline, router)
    if not arrow:
        return value
    css = "delta-flat" if arrow.startswith("→") else ("delta-up" if arrow.startswith("↑") else "delta-down")
    return f'{html.escape(value)} <span class="{css}">{html.escape(arrow)}</span>'


def numeric(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def average(values: list[float | None]) -> float | None:
    nums = [value for value in values if value is not None]
    return mean(nums) if nums else None


def normalize_run_metrics(data: dict[str, Any]) -> None:
    for shape_data in data.get("shapes", {}).values():
        for side in ("baseline", "router"):
            shape_data[side] = normalize_metrics(shape_data.get(side, {}))
        shape_data["available_metrics"] = sorted(set(shape_data["baseline"]) | set(shape_data["router"]))


def daily_experiment_date(metadata: dict[str, Any], created_at: str) -> str:
    explicit = str(metadata.get("date") or "")[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", explicit):
        return explicit
    experiment_id = str(metadata.get("experiment_id", ""))
    match = re.search(r"(?:^|-)daily-(\d{4}-\d{2}-\d{2})(?:-|$)", experiment_id)
    return match.group(1) if match else created_at[:10]


def discover_runs(source: Path) -> list[dict[str, Any]]:
    runs = []
    for path in sorted(source.rglob("dashboard-summary.json")):
        data = load_json(path)
        try:
            normalize_experiment(data)
            normalize_run_metrics(data)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
        run = data.get("run", {})
        created_at = str(run.get("created_at", ""))
        date = daily_experiment_date(run, created_at) if run.get("experiment_kind") == "daily" else created_at[:10]
        runs.append(
            {
                "path": path,
                "data": data,
                "date": date,
                "host": run.get("host", "") or "N/A",
            }
        )
    return runs


def side_metric(run: dict[str, Any], side: str, shape: str, metric: str) -> float | None:
    return numeric(run["data"].get("shapes", {}).get(shape, {}).get(side, {}).get(metric))


def run_hardware(run: dict[str, Any]) -> str:
    environment = run["data"].get("environment")
    if isinstance(environment, dict):
        for key in ("hardware", "gpus", "gpu", "hardware_label"):
            value = normalize_hardware_label(environment.get(key))
            if value:
                return value
    return "Unknown"


def point_label(day: dict[str, Any]) -> str:
    return f"{day['date']} · {day['host']}"


def run_value(run: dict[str, Any], *paths: tuple[str, ...]) -> str:
    for path in paths:
        value: Any = run["data"]
        for key in path:
            if not isinstance(value, dict) or key not in value:
                break
            value = value[key]
        else:
            if value not in (None, "", "from-daily-report"):
                return str(value)
    return "N/A"


def nested_value(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = data
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def provenance_source(run: dict[str, Any], path: tuple[str, ...]) -> str:
    if len(path) < 2:
        return "unavailable"
    provenance = run["data"].get("provenance")
    if not isinstance(provenance, dict):
        return "unavailable"
    section = provenance.get(path[0])
    if not isinstance(section, dict):
        return "unavailable"
    metadata = section.get(path[1])
    if not isinstance(metadata, dict):
        return "unavailable"
    source = metadata.get("source", "unavailable")
    verified_at = metadata.get("verified_at")
    return f"{source}, verified {verified_at}" if verified_at else str(source)


def sourced_run_value(run: dict[str, Any], *paths: tuple[str, ...]) -> str:
    for path in paths:
        value = nested_value(run["data"], path)
        if value not in (None, "", "from-daily-report"):
            return f"{value} ({provenance_source(run, path)})"
    return "N/A (unavailable)"


def provenance_table(runs: list[dict[str, Any]]) -> str:
    headers = [
        "day",
        "host",
        "profile",
        "hardware / GPU",
        "driver",
        "CUDA",
        "vLLM version / commit",
        "AgentCache branch",
        "AgentCache commit",
    ]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for run in sorted(runs, key=lambda item: (item["date"], item["host"], str(item["path"])), reverse=True):
        row = [
            run["date"] or "N/A",
            run["host"],
            run_value(run, ("run", "profile")),
            sourced_run_value(
                run,
                ("environment", "hardware"),
                ("environment", "gpu"),
                ("run", "hardware"),
            ),
            sourced_run_value(
                run,
                ("environment", "driver"),
                ("environment", "driver_version"),
            ),
            sourced_run_value(
                run,
                ("environment", "cuda"),
                ("environment", "cuda_version"),
                ("run", "cuda"),
            ),
            sourced_run_value(
                run,
                ("environment", "vllm"),
                ("environment", "vllm_version"),
                ("run", "vllm"),
            ),
            sourced_run_value(
                run,
                ("run", "branch"),
                ("environment", "agentcache_branch"),
            ),
            sourced_run_value(
                run,
                ("run", "commit"),
                ("environment", "agentcache_commit"),
            ),
        ]
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |")
    return "\n".join(lines)


def qualification_table(runs: list[dict[str, Any]]) -> str:
    headers = ["experiment", "kind", "coverage", "trend eligible", "qualification"]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for run in runs:
        metadata = run["data"]["run"]
        shapes = ", ".join(run["data"].get("shapes", {})) or "none"
        expected = ", ".join(metadata["expected_shapes"])
        lines.append(
            "| "
            + " | ".join(
                [
                    metadata["experiment_id"],
                    metadata["experiment_kind"],
                    f"{metadata['completeness']}: {shapes} (expected {expected})",
                    str(metadata["trend_eligible"]).lower(),
                    str(metadata.get("qualification", "none")).replace("|", "\\|"),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def scheduler_ab_table(runs: list[dict[str, Any]]) -> str:
    headers = [
        "date",
        "scheduler",
        "shape",
        "baseline completed",
        "router completed",
        "router throughput",
        "latency mean",
        "latency p95",
        "TTFT mean",
        "queue mean",
    ]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for run in sorted(runs, key=lambda item: (item["date"], item["data"]["run"].get("scheduler_mode", ""))):
        metadata = run["data"]["run"]
        if metadata["experiment_kind"] != "scheduler_ab":
            continue
        shape, data = next(iter(run["data"]["shapes"].items()))
        baseline, router = data["baseline"], data["router"]
        row = [run["date"], metadata.get("scheduler_mode", "N/A"), shape]
        row.extend(
            fmt(value)
            for value in (
                baseline.get("completed_tasks"),
                router.get("completed_tasks"),
                router.get("request_throughput"),
                router.get("latency_seconds.mean"),
                router.get("latency_seconds.p95"),
                router.get("ttft_seconds.mean"),
                router.get(VLLM_QUEUE_MEAN),
            )
        )
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def daily_points(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    experiments: dict[str, dict[str, Any]] = {}
    for run in runs:
        metadata = run["data"]["run"]
        if metadata["experiment_kind"] != "daily" or not metadata["trend_eligible"] or not run["date"]:
            continue
        experiment_id = metadata["experiment_id"]
        if experiment_id in experiments:
            other = experiments[experiment_id]
            if other["data"] != run["data"]:
                raise ValueError(f"duplicate daily experiment identity conflict: {experiment_id}")
            continue
        experiments[experiment_id] = run
    return [
        {
            "date": run["date"],
            "host": run["host"],
            "experiment_id": experiment_id,
            "hardware": run_hardware(run),
            "runs": [run],
        }
        for experiment_id, run in sorted(experiments.items(), key=lambda item: (item[1]["date"], item[0]))
    ]


def day_metric_payload(day: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for shape in SHAPES:
        shape_payload: dict[str, dict[str, float | None]] = {"baseline": {}, "router": {}}
        for metric in CORE_METRICS:
            shape_payload["baseline"][metric] = daily_metric(day, "baseline", shape, metric)
            shape_payload["router"][metric] = daily_metric(day, "router", shape, metric)
        payload[shape] = shape_payload
    return payload


def build_dashboard_payload(days: list[dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, Any]:
    hardware_options = sorted({day["hardware"] for day in days if day.get("hardware")})
    return {
        "shapes": SHAPES,
        "shape_colors": SHAPE_COLORS,
        "core_metrics": [{"key": metric, "label": METRIC_LABELS[metric]} for metric in CORE_METRICS],
        "trend_metrics": [
            {"key": metric, "label": short_label, "title": title}
            for metric, short_label, title, _filename in SHAPE_TREND_METRICS
        ],
        "hardware_options": hardware_options,
        "days": [
            {
                "date": day["date"],
                "host": day["host"],
                "experiment_id": day["experiment_id"],
                "hardware": day["hardware"],
                "metrics": day_metric_payload(day),
            }
            for day in days
        ],
        "counts": {"run_groups": len(days), "source_runs": len(runs)},
    }


def daily_metric(day: dict[str, Any], side: str, shape: str, metric: str) -> float | None:
    return average([side_metric(run, side, shape, metric) for run in day["runs"]])


def daily_shape_average(day: dict[str, Any], side: str, metric: str) -> float | None:
    return average([daily_metric(day, side, shape, metric) for shape in SHAPES])


def daily_delta_percent(day: dict[str, Any], shape: str, metric: str) -> float | None:
    baseline = daily_metric(day, "baseline", shape, metric)
    router = daily_metric(day, "router", shape, metric)
    if baseline is None or router is None or baseline == 0:
        return None
    return ((router - baseline) / baseline) * 100


def metric_by_shape_table(days: list[dict[str, Any]], side: str, metric: str) -> str:
    headers = ["day", "host", *SHAPES, "avg"]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for day in days:
        row = [day["date"], day["host"]]
        row.extend(fmt(daily_metric(day, side, shape, metric)) for shape in SHAPES)
        row.append(fmt(daily_shape_average(day, side, metric)))
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def combined_shape_metrics_table(days: list[dict[str, Any]], shape: str) -> str:
    headers = ["day", "host"]
    for metric in CORE_METRICS:
        label = METRIC_LABELS[metric]
        headers.extend([label, f"{label} baseline"])
    parts = [
        "<div class='shape-table-scroll'><table>",
        "<thead><tr>",
        *[f"<th>{html.escape(header)}</th>" for header in headers],
        "</tr></thead>",
        "<tbody>",
    ]
    for day in days:
        parts.append("<tr>")
        parts.append(f"<td>{html.escape(str(day['date']))}</td>")
        parts.append(f"<td>{html.escape(str(day['host']))}</td>")
        for metric in CORE_METRICS:
            baseline = daily_metric(day, "baseline", shape, metric)
            router = daily_metric(day, "router", shape, metric)
            parts.append(f"<td class='metric-router'>{router_metric_cell(baseline, router)}</td>")
            parts.append(f"<td class='metric-baseline'>{html.escape(fmt(baseline))}</td>")
        parts.append("</tr>")
    parts.extend(["</tbody>", "</table></div>"])
    return "\n".join(parts)


def shape_metrics_section(days: list[dict[str, Any]]) -> str:
    default_shape = SHAPES[0]
    buttons = []
    panels = []
    for shape in SHAPES:
        shape_id = shape.replace("/", "_")
        active = " is-active" if shape == default_shape else ""
        selected = "true" if shape == default_shape else "false"
        buttons.append(
            f"<button type='button' class='shape-btn{active}' data-shape='{shape_id}' "
            f"role='tab' aria-selected='{selected}'>{html.escape(shape)}</button>"
        )
        hidden = "" if shape == default_shape else " hidden"
        panels.append(
            f"<div class='shape-panel'{hidden} data-shape='{shape_id}' role='tabpanel'>"
            f"<p class='shape-panel-label'>Shape <strong>{html.escape(shape)}</strong> · "
            "router values show change vs baseline</p>"
            f"{combined_shape_metrics_table(days, shape)}"
            "</div>"
        )
    script = """
<script>
(function () {
  function initShapeMetrics(root) {
    var buttons = Array.prototype.slice.call(root.querySelectorAll('.shape-btn'));
    var panels = Array.prototype.slice.call(root.querySelectorAll('.shape-panel'));
    buttons.forEach(function (button) {
      button.addEventListener('click', function () {
        var shape = button.getAttribute('data-shape');
        buttons.forEach(function (item) {
          var active = item === button;
          item.classList.toggle('is-active', active);
          item.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        panels.forEach(function (panel) {
          panel.hidden = panel.getAttribute('data-shape') !== shape;
        });
      });
    });
  }
  document.querySelectorAll('.shape-metrics').forEach(initShapeMetrics);
})();
</script>
""".strip()
    return "\n".join(
        [
            "<div class='shape-metrics'>",
            "<div class='shape-switch' role='tablist' aria-label='Concurrency shape'>",
            *buttons,
            "</div>",
            *panels,
            script,
            "</div>",
        ]
    )


def sampled_label_indices(total: int, plot_width: int, min_spacing: int = 120) -> list[int]:
    if total <= 2:
        return list(range(total))
    max_labels = max(2, plot_width // min_spacing + 1)
    if total <= max_labels:
        return list(range(total))
    stride = math.ceil((total - 1) / (max_labels - 1))
    indices = list(range(0, total - 1, stride))
    if indices[-1] != total - 1:
        indices.append(total - 1)
    return indices


def svg_line_chart(
    path: Path,
    title: str,
    series: dict[str, list[tuple[str, float | None]]],
    series_styles: dict[str, tuple[str, str, str, str]] | None = None,
) -> None:
    width, height = 1040, 400
    left, right, top, bottom = 72, 24, 44, 116
    values = [value for points in series.values() for _, value in points if value is not None]
    if not values:
        path.write_bytes(
            f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}'><text x='20' y='30'>{html.escape(title)}: no data</text></svg>\n".encode()
        )
        return
    ymin, ymax = min(0, min(values)), max(values)
    if ymin == ymax:
        ymax += 1
    labels = [label for label, _ in next(iter(series.values()))]
    plot_w, plot_h = width - left - right, height - top - bottom

    def x_at(index: int) -> float:
        return left + plot_w * index / max(1, len(labels) - 1)

    def y_at(value: float) -> float:
        return top + plot_h - (value - ymin) / (ymax - ymin) * plot_h

    def marker_svg(shape: str, x: float, y: float, stroke: str, fill: str) -> str:
        if shape == "square":
            return f"<rect x='{x - 4:.1f}' y='{y - 4:.1f}' width='8' height='8' rx='1.2' fill='{fill}' stroke='{stroke}' stroke-width='2'/>"
        if shape == "triangle":
            return f"<polygon points='{x:.1f},{y - 5:.1f} {x - 4.5:.1f},{y + 4:.1f} {x + 4.5:.1f},{y + 4:.1f}' fill='{fill}' stroke='{stroke}' stroke-width='2'/>"
        if shape == "diamond":
            return f"<polygon points='{x:.1f},{y - 5:.1f} {x - 5:.1f},{y:.1f} {x:.1f},{y + 5:.1f} {x + 5:.1f},{y:.1f}' fill='{fill}' stroke='{stroke}' stroke-width='2'/>"
        return f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4' fill='{fill}' stroke='{stroke}' stroke-width='2'/>"

    colors = ["#5f6773", "#2563eb", "#b85d3f", "#15803d", "#7c3aed", "#c2410c"]
    marker_shapes = ["circle", "square", "triangle", "diamond", "circle", "square"]
    parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>",
        "<rect width='100%' height='100%' fill='white'/>",
    ]
    parts.append(f"<text x='{left}' y='26' font-size='18' font-family='sans-serif'>{html.escape(title)}</text>")
    for yv in [ymin, (ymin + ymax) / 2, ymax]:
        y = y_at(yv)
        parts.append(f"<line x1='{left}' y1='{y}' x2='{left + plot_w}' y2='{y}' stroke='#eee'/>")
        parts.append(
            f"<text x='{left - 8}' y='{y + 4}' text-anchor='end' font-size='11' font-family='sans-serif'>{fmt(yv)}</text>"
        )
    parts.append(f"<line x1='{left}' y1='{top + plot_h}' x2='{left + plot_w}' y2='{top + plot_h}' stroke='#999'/>")
    parts.append(f"<line x1='{left}' y1='{top}' x2='{left}' y2='{top + plot_h}' stroke='#999'/>")
    label_y = top + plot_h + 20
    for index in sampled_label_indices(len(labels), plot_w):
        label = labels[index]
        x = x_at(index)
        anchor = "start" if index == 0 else "end"
        parts.append(
            f"<text x='{x}' y='{label_y}' text-anchor='{anchor}' font-size='11' font-family='sans-serif' "
            f"transform='rotate(-35 {x} {label_y})'>{html.escape(label)}</text>"
        )
    for index, (name, points) in enumerate(series.items()):
        if series_styles and name in series_styles:
            color, marker, marker_fill, dash = series_styles[name]
        else:
            color = colors[index % len(colors)]
            marker = marker_shapes[index % len(marker_shapes)]
            marker_fill = "white"
            dash = ""
        coords = [(x_at(i), y_at(value)) for i, (_, value) in enumerate(points) if value is not None]
        if coords:
            dash_attr = f" stroke-dasharray='{dash}'" if dash else ""
            parts.append(
                "<polyline fill='none' stroke='{}' stroke-width='2.8' stroke-linecap='round' stroke-linejoin='round'{} points='{}'/>".format(
                    color, dash_attr, " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
                )
            )
            for x, y in coords:
                parts.append(marker_svg(marker, x, y, color, marker_fill))
        parts.append(
            f"<text x='{left + index * 150}' y='{height - 12}' font-size='12' fill='{color}' font-family='sans-serif'>{html.escape(name)}</text>"
        )
    parts.append("</svg>")
    path.write_bytes(("\n".join(parts) + "\n").encode("utf-8"))


def completed_tasks_trend_svg(days: list[dict[str, Any]]) -> str:
    title = "Completed tasks by shape (hover one series to isolate it and show baseline)"
    width, height = 1100, 440
    left, right, top, bottom = 72, 24, 48, 132
    labels = [point_label(day) for day in days]
    router_series = {
        shape: [daily_metric(day, "router", shape, "completed_tasks") for day in days] for shape in SHAPES
    }
    baseline_series = {
        shape: [daily_metric(day, "baseline", shape, "completed_tasks") for day in days] for shape in SHAPES
    }
    values = [value for series in (*router_series.values(), *baseline_series.values()) for value in series if value is not None]
    if not values:
        return (
            f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}'>"
            f"<text x='20' y='30'>{html.escape(title)}: no data</text></svg>\n"
        )
    ymin, ymax = min(0, min(values)), max(values)
    if ymin == ymax:
        ymax += 1
    plot_w, plot_h = width - left - right, height - top - bottom

    def x_at(index: int) -> float:
        return left + plot_w * index / max(1, len(labels) - 1)

    def y_at(value: float) -> float:
        return top + plot_h - (value - ymin) / (ymax - ymin) * plot_h

    def polyline_points(series: list[float | None]) -> str:
        return " ".join(
            f"{x_at(index):.1f},{y_at(value):.1f}"
            for index, value in enumerate(series)
            if value is not None
        )

    parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}' "
        "class='completed-trend-chart' role='img' "
        f"aria-label='{html.escape(title)}'>",
        "<rect width='100%' height='100%' fill='white'/>",
        """<style>
.completed-trend-chart .router-layer,
.completed-trend-chart .baseline-layer,
.completed-trend-chart .legend-hit { transition: opacity 0.15s ease; }
.completed-trend-chart .baseline-layer { opacity: 0; pointer-events: none; }
.completed-trend-chart .router-hit { fill: none; stroke: transparent; stroke-width: 16; cursor: pointer; }
.completed-trend-chart .legend-hit { cursor: pointer; }
/* Isolate hovered/focused series: hide other router lines, reveal matching baseline. */
.completed-trend-chart:has(.series-group:hover) .series-group:not(:hover) .router-layer,
.completed-trend-chart:has(.series-group:hover) .series-group:not(:hover) .legend-hit,
.completed-trend-chart:has(.series-group:focus-within) .series-group:not(:focus-within) .router-layer,
.completed-trend-chart:has(.series-group:focus-within) .series-group:not(:focus-within) .legend-hit {
  opacity: 0;
}
.completed-trend-chart .series-group:hover .baseline-layer,
.completed-trend-chart .series-group:focus-within .baseline-layer {
  opacity: 1;
}
</style>""",
        f"<text x='{left}' y='28' font-size='18' font-family='sans-serif'>{html.escape(title)}</text>",
    ]
    for yv in [ymin, (ymin + ymax) / 2, ymax]:
        y = y_at(yv)
        parts.append(f"<line x1='{left}' y1='{y}' x2='{left + plot_w}' y2='{y}' stroke='#eee'/>")
        parts.append(
            f"<text x='{left - 8}' y='{y + 4}' text-anchor='end' font-size='11' font-family='sans-serif'>{fmt(yv)}</text>"
        )
    parts.append(f"<line x1='{left}' y1='{top + plot_h}' x2='{left + plot_w}' y2='{top + plot_h}' stroke='#999'/>")
    parts.append(f"<line x1='{left}' y1='{top}' x2='{left}' y2='{top + plot_h}' stroke='#999'/>")
    label_y = top + plot_h + 20
    for index in sampled_label_indices(len(labels), plot_w):
        label = labels[index]
        x = x_at(index)
        anchor = "start" if index == 0 else "end"
        parts.append(
            f"<text x='{x}' y='{label_y}' text-anchor='{anchor}' font-size='11' font-family='sans-serif' "
            f"transform='rotate(-35 {x} {label_y})'>{html.escape(label)}</text>"
        )

    legend_y = height - 28
    legend_width = plot_w / max(1, len(SHAPES))
    for shape_index, shape in enumerate(SHAPES):
        color = SHAPE_COLORS[shape]
        shape_id = shape.replace("/", "_")
        router_points = polyline_points(router_series[shape])
        baseline_points = polyline_points(baseline_series[shape])
        parts.append(f"<g class='series-group' data-shape='{shape_id}' tabindex='0'>")
        parts.append("<g class='baseline-layer'>")
        if baseline_points:
            parts.append(
                "<polyline fill='none' stroke='#111827' stroke-width='2.2' stroke-dasharray='6 5' "
                f"stroke-linecap='round' stroke-linejoin='round' points='{baseline_points}'/>"
            )
            for index, value in enumerate(baseline_series[shape]):
                if value is None:
                    continue
                x, y = x_at(index), y_at(value)
                parts.append(
                    f"<circle cx='{x:.1f}' cy='{y:.1f}' r='3.5' fill='#ffffff' stroke='#111827' stroke-width='2'/>"
                )
        parts.append("</g>")
        parts.append("<g class='router-layer'>")
        if router_points:
            parts.append(f"<polyline class='router-hit' points='{router_points}'/>")
            parts.append(
                f"<polyline fill='none' stroke='{color}' stroke-width='2.8' stroke-linecap='round' "
                f"stroke-linejoin='round' points='{router_points}'/>"
            )
            for index, value in enumerate(router_series[shape]):
                if value is None:
                    continue
                x, y = x_at(index), y_at(value)
                parts.append(
                    f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4' fill='#ecfeff' stroke='{color}' stroke-width='2'/>"
                )
        parts.append("</g>")
        legend_x = left + shape_index * legend_width
        parts.append(
            f"<g class='legend-hit'>"
            f"<line x1='{legend_x:.1f}' y1='{legend_y}' x2='{legend_x + 18:.1f}' y2='{legend_y}' "
            f"stroke='{color}' stroke-width='3'/>"
            f"<text x='{legend_x + 24:.1f}' y='{legend_y + 4:.1f}' font-size='12' fill='{color}' "
            f"font-family='sans-serif'>{html.escape(shape)} router</text>"
            f"</g>"
        )
        parts.append("</g>")
    parts.append(
        f"<text x='{left}' y='{height - 8}' font-size='11' fill='#6b7280' font-family='sans-serif'>"
        "Hover a router line to hide other shapes and show that shape's dashed baseline.</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def write_completed_tasks_trend_chart(path: Path, days: list[dict[str, Any]]) -> None:
    path.write_bytes(completed_tasks_trend_svg(days).encode("utf-8"))


def completed_tasks_trend_embed(figure: str, cache_bust: str = "") -> str:
    # Embed via <object> so SVG CSS :hover works and Markdown cannot split the SVG markup.
    src = html.escape(f"{figure}?v={cache_bust}" if cache_bust else figure)
    return "\n".join(
        [
            "<div class='completed-trend-wrap'>",
            (
                f"<object type='image/svg+xml' data='{src}' width='1100' height='440' "
                "aria-label='Completed tasks by shape'>"
                f"<img src='{src}' alt='Completed tasks by shape' width='1100' height='440'/>"
                "</object>"
            ),
            (
                "<p class='completed-trend-hint'>"
                "Hover a router line (or its legend) to isolate that shape and show its baseline."
                "</p>"
            ),
            "</div>",
        ]
    )


def svg_latency_chart(path: Path, days: list[dict[str, Any]], shape: str) -> None:
    labels = [point_label(day) for day in days]
    series = {}
    styles = {}
    metric_markers = {
        "queue_mean": ("circle", "#eff6ff", "#d9fbff"),
        "ttft_mean": ("square", "#f8fafc", "#c7f9e5"),
        "latency_mean": ("triangle", "#fff7ed", "#ffe7c2"),
    }
    for metric, label in LATENCY_FIGURE_METRICS:
        baseline_name = f"baseline {label}"
        router_name = f"router {label}"
        series[baseline_name] = list(
            zip(labels, [daily_metric(day, "baseline", shape, metric) for day in days], strict=False)
        )
        series[router_name] = list(
            zip(labels, [daily_metric(day, "router", shape, metric) for day in days], strict=False)
        )
        marker, baseline_fill, router_fill = metric_markers[label]
        styles[baseline_name] = ("#374151", marker, baseline_fill, "6 5")
        styles[router_name] = ("#0891b2", marker, router_fill, "")
    svg_line_chart(path, f"{shape} latency metrics, raw daily average", series, styles)


def svg_heatmap(path: Path, days: list[dict[str, Any]], metric: str) -> None:
    cell_w, cell_h = 92, 34
    left, top = 110, 52
    width, height = left + cell_w * len(SHAPES) + 40, top + cell_h * len(days) + 50
    values = [[daily_delta_percent(day, shape, metric) for shape in SHAPES] for day in days]

    def color(value: float | None) -> str:
        if value is None:
            return "#f3f4f6"
        clipped = max(-100, min(100, value))
        if clipped >= 0:
            g = int(180 + 60 * clipped / 100)
            return f"rgb(210,{g},210)"
        r = int(220 + 30 * abs(clipped) / 100)
        return f"rgb({r},210,210)"

    parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>",
        "<rect width='100%' height='100%' fill='white'/>",
    ]
    parts.append(
        "<text x='20' y='28' font-size='18' font-family='sans-serif'>Queue mean Δ% heatmap (router vs baseline)</text>"
    )
    for col, shape in enumerate(SHAPES):
        x = left + col * cell_w + cell_w / 2
        parts.append(
            f"<text x='{x}' y='{top - 12}' text-anchor='middle' font-size='12' font-family='sans-serif'>{shape}</text>"
        )
    for row, day in enumerate(days):
        y = top + row * cell_h
        parts.append(
            f"<text x='{left - 12}' y='{y + 22}' text-anchor='end' font-size='12' font-family='sans-serif'>{day['date']}</text>"
        )
        for col, value in enumerate(values[row]):
            x = left + col * cell_w
            parts.append(
                f"<rect x='{x}' y='{y}' width='{cell_w - 2}' height='{cell_h - 2}' fill='{color(value)}' stroke='white'/>"
            )
            parts.append(
                f"<text x='{x + cell_w / 2}' y='{y + 21}' text-anchor='middle' font-size='11' font-family='sans-serif'>{pct(value)}</text>"
            )
    parts.append("</svg>")
    path.write_bytes(("\n".join(parts) + "\n").encode("utf-8"))


CURRENT_FIGURES: set[str] = set()
LEGACY_FIGURES = {
    *(
        f"{metric}-{shape}.svg"
        for metric in ("completed", "latency_mean", "queue_mean", "ttft_mean")
        for shape in ("4_2", "8_4", "16_8", "32_16", "64_32")
    ),
    "high-load-delta-completed.svg",
    "high-load-delta-latency_mean.svg",
    "high-load-delta-queue_mean.svg",
    "high-load-delta-ttft_mean.svg",
    "shape-32_16-daily-metrics.svg",
    "shape-64_32-daily-metrics.svg",
    "shape-32_16-completed-tasks.svg",
    "shape-64_32-completed-tasks.svg",
    "shape-32_16-latency-metrics.svg",
    "shape-64_32-latency-metrics.svg",
    "daily-average-deltas-by-concurrency.svg",
    "queue-delta-heatmap.svg",
    "completed-tasks-by-shape.svg",
    "queue-mean-by-shape.svg",
    "ttft-mean-by-shape.svg",
    "latency-mean-by-shape.svg",
}


def clean_managed_figures(output: Path) -> None:
    fig_dir = output / "figures"
    for filename in CURRENT_FIGURES | LEGACY_FIGURES:
        (fig_dir / filename).unlink(missing_ok=True)


def dashboard_shell_html(payload: dict[str, Any]) -> str:
    hardware_options = payload.get("hardware_options", [])
    option_html = []
    for index, hardware in enumerate(hardware_options):
        selected = " selected" if index == 0 else ""
        option_html.append(
            f'<option value="{html.escape(hardware)}"{selected}>{html.escape(hardware)}</option>'
        )
    if not option_html:
        option_html.append('<option value="Unknown" selected>Unknown</option>')
    chart_blocks = []
    for item in payload.get("trend_metrics", []):
        metric = html.escape(item["key"])
        title = html.escape(item["title"])
        chart_blocks.extend(
            [
                f'<section class="trend-card" data-metric="{metric}">',
                f'<div class="trend-card-head"><h3>{title}</h3>',
                '<div class="range-switch" role="group" aria-label="Trend window">',
                '<button type="button" class="range-btn is-active" data-range="7">7D</button>',
                '<button type="button" class="range-btn" data-range="30">30D</button>',
                "</div></div>",
                '<div class="trend-chart-mount" aria-live="polite"></div>',
                '<p class="metric-trend-hint">Hover a router line to isolate that shape and show its dashed baseline.</p>',
                "</section>",
            ]
        )
    return "\n".join(
        [
            '<div class="dashboard-root" id="dashboard-root">',
            '<div class="dashboard-controls">',
            '<div class="control-block">',
            '<span class="control-kicker">Filter</span>',
            '<label class="hardware-filter-label" for="hardware-filter">Hardware</label>',
            '<select id="hardware-filter" class="hardware-filter" aria-label="Hardware filter">',
            *option_html,
            "</select>",
            "</div>",
            '<p class="dashboard-counts" id="dashboard-counts"></p>',
            "</div>",
            '<section class="trend-section">',
            '<div class="section-head"><h2>Trend charts</h2>',
            '<p class="section-note">Default window is the past 7 calendar days.</p></div>',
            '<div class="trend-grid">',
            *chart_blocks,
            "</div>",
            "</section>",
            '<section class="metrics-section">',
            '<div class="section-head"><h2>Details</h2>',
            '<div class="details-toolbar">',
            '<div class="range-switch" id="details-range-switch" role="group" aria-label="Details window">',
            '<button type="button" class="range-btn is-active" data-range="7">7D</button>',
            '<button type="button" class="range-btn" data-range="30">30D</button>',
            '<button type="button" class="range-btn" data-range="all">All</button>',
            "</div>",
            '<div class="details-date-range">',
            '<label class="details-date-label" for="details-date-from">From',
            '<input type="date" id="details-date-from" class="details-date-input" aria-label="Details start date"></label>',
            '<label class="details-date-label" for="details-date-to">To',
            '<input type="date" id="details-date-to" class="details-date-input" aria-label="Details end date"></label>',
            "</div>",
            "</div></div>",
            '<p class="section-note">Pick a concurrency shape to compare router vs baseline. Router cells show ↑/↓ vs baseline.</p>',
            '<div class="shape-metrics" id="shape-metrics-root"></div>',
            "</section>",
            "</div>",
        ]
    )


def write_dashboard(source: Path, output: Path) -> None:
    runs = discover_runs(source)
    days = daily_points(runs)
    output.mkdir(parents=True, exist_ok=True)
    clean_managed_figures(output)
    (output / "figures").mkdir(parents=True, exist_ok=True)
    payload = build_dashboard_payload(days, runs)
    (output / "dashboard-data.json").write_bytes((json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    lines = [
        "Compact daily trends from `dashboard-summary.json` artifacts — one logical run per experiment ID.",
        "",
    ]
    if not runs:
        lines.append("No benchmark artifacts found.")
    elif not days:
        lines.append("No trend-eligible daily runs found.")
    else:
        lines.extend([dashboard_shell_html(payload), ""])
    markdown = "\n".join(lines).rstrip() + "\n"
    (output / "README.md").write_bytes(markdown.encode("utf-8"))
    (output / "index.md").write_bytes(markdown.encode("utf-8"))
    (output / "index.html").unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("runs"))
    parser.add_argument("--output", type=Path, default=Path("docs"))
    args = parser.parse_args()
    write_dashboard(args.source, args.output)


if __name__ == "__main__":
    main()

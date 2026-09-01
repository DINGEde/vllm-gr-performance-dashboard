"""Build the vllm-gr daily performance page from compact run summaries."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "vllm-gr.daily.v1"
SUMMARY_NAME = "vllm-gr-summary.json"
DISPLAY_START_DATE = "2026-08-31"
PHASE_VERSION_PREFERENCE = (
    "vllm-gr-serving-internal-v3",
    "vllm-gr-serving-token1-v2",
)
ONLINE_LATENCY_METRICS = ("ttft", "tpot", "itl", "e2el")
OFFLINE_LATENCY_METRICS = (
    "e2el",
    "e2el_hit",
    "prefill_miss",
    "prefill_hit",
    "decode_miss",
    "decode_hit",
    "overhead_miss",
    "overhead_hit",
)
PERCENTILES = ("mean", "p50", "p90", "p95", "p99")


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("top-level JSON value must be an object")
    return data


def require_object(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def require_nonempty_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def validate_iso_date(value: str, key: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an ISO date") from exc


def validate_iso_datetime(value: str, key: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{key} must be an ISO date-time") from exc


def validate_latency_distribution(name: str, values: dict[str, Any]) -> None:
    for percentile in PERCENTILES:
        value = values.get(percentile)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ValueError(f"results.latency_ms.{name}.{percentile} must be non-negative")
    ordered = [float(values[key]) for key in ("p50", "p90", "p95", "p99")]
    if ordered != sorted(ordered):
        raise ValueError(f"results.latency_ms.{name} percentiles must be monotonic")
    if values.get("unit", "ms") != "ms":
        raise ValueError(f"results.latency_ms.{name}.unit must be ms")


def validate_summary(data: dict[str, Any]) -> None:
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION!r}")

    run = require_object(data, "run")
    run_id = require_nonempty_string(run, "id")
    run_date = require_nonempty_string(run, "date")
    validate_iso_date(run_date, "run.date")
    validate_iso_datetime(require_nonempty_string(run, "started_at"), "run.started_at")
    validate_iso_datetime(require_nonempty_string(run, "finished_at"), "run.finished_at")
    if run.get("status") not in {"success", "failed", "invalid"}:
        raise ValueError("run.status must be success, failed, or invalid")
    for flag in ("trend_eligible", "baseline_eligible"):
        if not isinstance(run.get(flag), bool):
            raise ValueError(f"run.{flag} must be boolean")
    reasons = run.get("qualification_reasons")
    if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
        raise ValueError("run.qualification_reasons must be a string array")

    source = require_object(data, "source")
    require_nonempty_string(source, "repository")
    require_nonempty_string(source, "git_sha")
    environment = require_object(data, "environment")
    require_nonempty_string(environment, "host")
    require_nonempty_string(environment, "hardware")
    model = require_object(data, "model")
    require_nonempty_string(model, "id")

    dataset = require_object(data, "dataset")
    kind = dataset.get("kind")
    if kind not in {"real", "synthetic-smoke", "synthetic-control"}:
        raise ValueError("dataset.kind is invalid")
    if not isinstance(dataset.get("representative"), bool):
        raise ValueError("dataset.representative must be boolean")
    selection = require_object(dataset, "selection")
    sample_count = selection.get("sample_count")
    if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count < 1:
        raise ValueError("dataset.selection.sample_count must be a positive integer")

    scenario = require_object(data, "scenario")
    num_prompts = scenario.get("num_prompts")
    if not isinstance(num_prompts, int) or isinstance(num_prompts, bool) or num_prompts < 1:
        raise ValueError("scenario.num_prompts must be a positive integer")
    if sample_count != num_prompts:
        raise ValueError("dataset sample_count must equal scenario num_prompts")
    if "key" in scenario:
        require_nonempty_string(scenario, "key")
    if "input_tokens_target" in scenario:
        value = scenario["input_tokens_target"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("scenario.input_tokens_target must be a positive integer")

    results = require_object(data, "results")
    requests = require_object(results, "requests")
    completed = requests.get("completed")
    failed = requests.get("failed")
    if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in (completed, failed)):
        raise ValueError("request counts must be non-negative integers")
    if completed + failed != num_prompts:
        raise ValueError("completed + failed must equal scenario num_prompts")

    latency = require_object(results, "latency_ms")
    execution_mode = scenario.get("execution_mode", "online")
    latency_metrics = OFFLINE_LATENCY_METRICS if execution_mode == "offline" else ONLINE_LATENCY_METRICS
    for name in latency_metrics:
        validate_latency_distribution(name, require_object(latency, name))
    if execution_mode == "offline" and "decode" in latency:
        validate_latency_distribution("decode", require_object(latency, "decode"))
    if "cache" in results:
        prefix = require_object(require_object(results, "cache"), "prefix")
        hit_rate = prefix.get("hit_rate_percent")
        if not isinstance(hit_rate, (int, float)) or isinstance(hit_rate, bool) or not 0 <= hit_rate <= 100:
            raise ValueError("results.cache.prefix.hit_rate_percent must be between 0 and 100")

    samples = require_object(results, "samples")
    sample_metrics = ("e2el_ms", "e2el_hit_ms", "input_tokens", "output_tokens") if execution_mode == "offline" else ("ttft_ms", "input_tokens", "output_tokens")
    for name in sample_metrics:
        values = samples.get(name)
        if not isinstance(values, list):
            raise ValueError(f"results.samples.{name} must be an array")
        if values and len(values) != completed:
            raise ValueError(f"results.samples.{name} must contain one value per completed request")

    baseline_eligible = run["baseline_eligible"]
    trend_eligible = run["trend_eligible"]
    if baseline_eligible:
        if run["status"] != "success" or failed:
            raise ValueError("baseline-eligible run must succeed with zero failed requests")
        if kind != "real" or dataset.get("representative") is not True:
            raise ValueError("baseline-eligible run must use a representative real dataset")
        if not dataset.get("sha256") or not selection.get("sample_ids_sha256"):
            raise ValueError("baseline-eligible run must pin dataset and sample IDs with SHA-256")
        if scenario.get("warmup_requests", 0) < 1:
            raise ValueError("baseline-eligible run must include warmup requests")
    if trend_eligible and not baseline_eligible:
        raise ValueError("trend-eligible run must also be baseline eligible")

    if not run_id.startswith("p0-") and run_date not in run_id:
        # Daily production IDs should be human-auditable. Calibration IDs are exempt.
        raise ValueError("production run id must include run.date")


def discover_runs(source: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for path in sorted(source.rglob(SUMMARY_NAME)):
        data = load_json(path)
        try:
            validate_summary(data)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc
        benchmark_args = data.get("scenario", {}).get("benchmark_args", {})
        phase_version = benchmark_args.get("phase_definition", {}).get("version")
        if data["run"]["date"] >= DISPLAY_START_DATE and data["scenario"].get("execution_mode") == "offline":
            candidates.append({"path": path.as_posix(), "summary": data, "phase_version": phase_version})
    active_version = next(
        (version for version in PHASE_VERSION_PREFERENCE if any(item["phase_version"] == version for item in candidates)),
        None,
    )
    runs = [item for item in candidates if item["phase_version"] == active_version]
    runs.sort(key=lambda item: (item["summary"]["run"]["date"], item["summary"]["run"]["started_at"]))
    return runs


def build_payload(runs: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = [item["summary"] for item in runs]
    active_version = (
        summaries[0].get("scenario", {}).get("benchmark_args", {}).get("phase_definition", {}).get("version")
        if summaries
        else None
    )
    scenarios = {}
    for item in summaries:
        scenario = item["scenario"]
        key = scenario.get("key", f"beam{scenario.get('n', 'unknown')}-legacy")
        scenarios[key] = {
            "key": key,
            "label": scenario["name"],
            "beam_width": scenario.get("n"),
            "input_tokens": scenario.get("input_tokens_target"),
        }
    return {
        "schema_version": "vllm-gr.dashboard.v1",
        "generated_from": SUMMARY_NAME,
        "runs": summaries,
        "trend_runs": [item for item in summaries if item["run"]["trend_eligible"]],
        "gpu": "L20",
        "phase_version": active_version,
        "scenarios": sorted(scenarios.values(), key=lambda item: (item["beam_width"] or 0, item["input_tokens"] or 0)),
        "metrics": [
            {"key": "e2el", "label": "E2E miss / primary", "unit": "ms"},
            {"key": "e2el_hit", "label": "E2E hit", "unit": "ms"},
            {"key": "prefill_miss", "label": "Prefill miss", "unit": "ms"},
            {"key": "prefill_hit", "label": "Prefill hit", "unit": "ms"},
            {"key": "decode", "label": "Decode (common, token 1+)", "unit": "ms"},
        ],
        "percentiles": list(PERCENTILES),
    }


def dashboard_markdown(has_runs: bool) -> str:
    intro = (
        "Daily offline single-batch performance on GPU `L20`. The dashboard shows only "
        "the newest serving-aligned phase definition captured on or after 2026-08-31."
    )
    if not has_runs:
        return f"# vllm-gr Performance\n\n{intro}\n\nNo vllm-gr artifacts found.\n"
    return "\n".join(
        [
            "# vllm-gr Performance",
            "",
            intro,
            "",
            '<div class="vgr-dashboard" id="vgr-dashboard">',
            '  <div class="vgr-toolbar">',
            '    <div class="vgr-control"><label for="vgr-scenario">Scenario</label><select id="vgr-scenario"></select></div>',
            '    <div class="vgr-control"><label for="vgr-metric">Metric</label><select id="vgr-metric"></select></div>',
            '    <div class="vgr-control"><label for="vgr-percentile">Statistic</label><select id="vgr-percentile"></select></div>',
            '    <label class="vgr-check"><input type="checkbox" id="vgr-qualified-only"> Qualified trend only</label>',
            '    <p class="vgr-count" id="vgr-count"></p>',
            "  </div>",
            '  <div id="vgr-status"></div>',
            '  <section class="vgr-latest" id="vgr-latest"></section>',
            '  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Reproducibility</p><h2>Current configuration</h2></div><p>Exact parameters for the selected run.</p></div><div id="vgr-config"></div></section>',
            '  <section class="vgr-section">',
            '    <div class="vgr-section-head"><div><p class="vgr-kicker">Daily signal</p><h2 id="vgr-trend-title">Trend</h2></div><p id="vgr-trend-caption"></p></div>',
            '    <div class="vgr-chart" id="vgr-trend-chart" aria-live="polite"></div>',
            "  </section>",
            '  <div class="vgr-split">',
            '    <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Distribution</p><h2>Per-request primary E2E</h2></div></div><div class="vgr-chart" id="vgr-primary-chart"></div></section>',
            '    <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Measurement</p><h2>Latency profile</h2></div></div><div id="vgr-latency-grid"></div></section>',
            "  </div>",
            '  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Beam execution</p><h2>Prefill & Decode</h2></div><p>Serving-aligned wall-clock phases for the selected run.</p></div><div id="vgr-beam-profile"></div></section>',
            '  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Methodology</p><h2>Metric definitions</h2></div><p>How to read and compare the values.</p></div><div class="vgr-methodology"><p><strong>Offline E2E miss/hit</strong>: wall-clock time of one direct <code>GRLLM.beam_search()</code> call. Miss resets Prefix Cache first; hit immediately repeats the identical prompt. It excludes HTTP, SSE, serialization, and network round trip.</p><p><strong>Current v3 Prefill miss/hit</strong>: internal beam token-loop start through completion of token 0, aligned with the online serving metric boundary. Entry-side prompt and initial-beam preparation remains part of E2E but is outside Prefill. <strong>Decode common</strong>: token 1 preparation through <code>beam_search</code> return, including later engine steps, beam bookkeeping, sorting, reconstruction and detokenization. Miss/hit Decode samples are pooled into one distribution; they are repeated observations, never additive components.</p><p><strong>Average (Mean)</strong>: phase wall-time sum divided by its request-observation count, equivalent to <code>Δ phase_time_seconds_total ÷ Δ requests_total</code>. With N prompts, Prefill miss and Prefill hit each divide by N; Decode common pools N miss plus N hit observations and divides by 2N. The dashboard also retains P50/P90/P95/P99.</p><p>The dashboard never mixes phase versions. Until the first complete v3 matrix is published it retains the v2 matrix, whose Prefill started at the outer call boundary; the selected run always shows its exact phase version in Current configuration.</p></div></section>',
            '  <section class="vgr-section"><div class="vgr-section-head"><div><p class="vgr-kicker">Evidence</p><h2>Run history</h2></div><p>Select a run to inspect its configuration and qualification.</p></div><div id="vgr-run-history"></div></section>',
            "</div>",
            "",
        ]
    )


def write_dashboard(source: Path, output: Path) -> None:
    runs = discover_runs(source)
    payload = build_payload(runs)
    output.mkdir(parents=True, exist_ok=True)
    (output / "vllm-gr-dashboard-data.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "vllm-gr.md").write_text(dashboard_markdown(bool(runs)), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("runs"))
    parser.add_argument("--output", type=Path, default=Path("docs"))
    args = parser.parse_args()
    write_dashboard(args.source, args.output)


if __name__ == "__main__":
    main()

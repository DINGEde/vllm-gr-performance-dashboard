"""Attach independent Worker measurements without replacing formal metrics."""
import argparse
import json
from pathlib import Path


def attach(summary_path, worker_dir, loader):
    summary_path, worker_dir = Path(summary_path), Path(worker_dir)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    raw = json.loads((worker_dir / "raw-result.json").read_text(encoding="utf-8"))
    scenario = summary["scenario"]
    runner = scenario.get("server_args", {}).get("model_runner", "v1")
    if (raw.get("engine_config", {}).get("model_runner", "v1") != runner
            or raw.get("batch_size", 1) != scenario.get("batch_size", 1)
            or raw["beam_width"] != scenario["n"]
            or raw["input_length"] != scenario["input_tokens_target"]
            or raw["model"] != summary["model"]["id"]
            or raw.get("beam_api", "beam_search") != scenario.get("beam_api", "beam_search")
            or raw["failed"] or raw["completed"] != raw["num_prompts"]):
        raise ValueError("Worker diagnostic scenario mismatch or incomplete run")
    detail = loader(worker_dir / "cpu-timing")
    if not detail or not detail.get("metrics"):
        raise ValueError("Worker timing files contain no measurements")
    if runner == "v2":
        required = ("gr_v2_prefill_logits", "gr_v1_stage_execute", "gr_v1_stage_sample")
        missing = [name for name in required if not detail["metrics"].get(name, {}).get("count")]
        if missing:
            raise ValueError(f"V2 Worker timing anchors did not run: {', '.join(missing)}")
        if not (worker_dir / "summary.json").is_file():
            raise ValueError("V2 Worker summary is required for provenance validation")
    detail["measurement_source"] = {
        "kind": "independent-worker-diagnostic",
        "model_runner": runner,
        "formal_run_id": summary["run"]["id"],
        "git_sha": summary["source"]["git_sha"],
        "num_prompts": raw["num_prompts"],
        "started_at": raw["started_at"],
        "finished_at": raw["finished_at"],
        "scope": "process aggregates; may include initialization and warmup",
        "affects_formal_metrics": False,
    }
    summary["results"]["cpu_pipeline_detail"] = detail
    attached_worker_phases = False
    diagnostic_summary = worker_dir / "summary.json"
    if diagnostic_summary.exists():
        diagnostic_payload = json.loads(diagnostic_summary.read_text(encoding="utf-8"))
        if runner == "v2":
            worker_scenario = diagnostic_payload["scenario"]
            for key in ("pipeline_version", "server_args"):
                expected = scenario[key]
                actual = worker_scenario[key]
                if key == "server_args":
                    keys = ("model_runner_classes", "compilation_config", "max_num_batched_tokens", "max_model_len")
                    expected = {name: expected.get(name) for name in keys}
                    actual = {name: actual.get(name) for name in keys}
                if actual != expected:
                    raise ValueError(f"Worker diagnostic {key} mismatch")
            if diagnostic_payload["source"]["git_sha"] != summary["source"]["git_sha"]:
                raise ValueError("Worker diagnostic source mismatch")
            if not raw.get("instrumentation", {}).get("canonical_contaminated_by_diagnostic"):
                raise ValueError("V2 Worker sample was collected without Worker probes")
            ignored = {
                "prompt_token_ids_sha256", "instrumentation", "diagnostic_prompts",
                "stage_phase_definition", "measurement_mode",
            }
            for key, value in scenario["benchmark_args"].items():
                if key not in ignored and worker_scenario["benchmark_args"].get(key) != value:
                    raise ValueError(f"Worker diagnostic benchmark argument mismatch: {key}")
        diagnostic = diagnostic_payload["results"].get("diagnostic")
        if diagnostic:
            attached_worker_phases = True
            diagnostic["method"] = "independent post-matrix process with mock.patch and Worker probes"
            summary["results"]["diagnostic"] = diagnostic
            # Preserve canonical E2E miss/hit from the formal process while
            # attaching the four established phase trends from the independent
            # diagnostic process under their historical metric keys.
            formal_latency = summary["results"]["latency_ms"]
            phase_latency = diagnostic["latency_ms"]
            for key in (
                "prefill_miss",
                "prefill_hit",
                "prefill",
                "decode",
            ):
                formal_latency[key] = phase_latency[key]
    note = (
        "Worker pipeline and phase metrics come from a separate post-matrix process; "
        "formal E2E miss/hit remain unchanged."
        if attached_worker_phases
        else "Worker pipeline comes from a separate post-matrix process; formal E2E and "
        "post-canonical stage metrics remain unchanged."
    )
    summary["run"].setdefault("notes", []).append(note)
    temporary = summary_path.with_suffix(".worker-tmp")
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(summary_path)


if __name__ == "__main__":
    from generate_summary import load_cpu_timing
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", required=True)
    parser.add_argument("--worker-dir", required=True)
    args = parser.parse_args()
    attach(args.summary, args.worker_dir, load_cpu_timing)

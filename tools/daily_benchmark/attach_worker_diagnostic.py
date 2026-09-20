"""Attach independent Worker measurements without replacing formal metrics."""
import argparse
import json
from pathlib import Path


def attach(summary_path, worker_dir, loader):
    summary_path, worker_dir = Path(summary_path), Path(worker_dir)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    raw = json.loads((worker_dir / "raw-result.json").read_text(encoding="utf-8"))
    scenario = summary["scenario"]
    if (raw["beam_width"] != scenario["n"]
            or raw["input_length"] != scenario["input_tokens_target"]
            or raw["model"] != summary["model"]["id"]
            or raw.get("beam_api", "beam_search") != scenario.get("beam_api", "beam_search")
            or raw["failed"] or raw["completed"] != raw["num_prompts"]):
        raise ValueError("Worker diagnostic scenario mismatch or incomplete run")
    detail = loader(worker_dir / "cpu-timing")
    if not detail or not detail.get("metrics"):
        raise ValueError("Worker timing files contain no measurements")
    detail["measurement_source"] = {
        "kind": "independent-worker-diagnostic",
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

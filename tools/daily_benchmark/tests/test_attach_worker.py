import importlib.util
import json
from pathlib import Path

import pytest


def test_attach_preserves_formal_metrics_and_rejects_mismatch(tmp_path):
    path = Path(__file__).resolve().parents[1] / "attach_worker_diagnostic.py"
    spec = importlib.util.spec_from_file_location("attach", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    summary = {"run": {"id": "formal", "trend_eligible": True},
               "source": {"git_sha": "abc"}, "model": {"id": "model"},
               "scenario": {"n": 128, "input_tokens_target": 1024,
                            "beam_api": "beam_search_v1"},
               "results": {"latency_ms": {"e2el": {"mean": 42}},
                           "diagnostic": {"latency_ms": {"decode": 17}}}}
    raw = dict(beam_width=128, input_length=1024, model="model", failed=0,
               completed=20, num_prompts=20, started_at="start", finished_at="end",
               beam_api="beam_search_v1")
    target = tmp_path / "formal-summary.json"
    target.write_text(json.dumps(summary))
    raw_path = tmp_path / "raw-result.json"
    raw_path.write_text(json.dumps(raw))
    phases = {key: {"mean": value} for value, key in enumerate(
        ("prefill_miss", "prefill_hit", "prefill", "decode",
         "prefill_output_consumed"), start=1)}
    worker_summary = {"results": {"diagnostic": {
        "latency_ms": phases, "method": "worker", "phase_definition": {"version": "v1"}
    }}}
    (tmp_path / "summary.json").write_text(json.dumps(worker_summary))
    module.attach(target, tmp_path, lambda p: {"metrics": {"execute_model": {"count": 10}}})
    actual = json.loads(target.read_text())
    assert actual["results"]["latency_ms"]["e2el"] == {"mean": 42}
    formal_phase_keys = ("prefill_miss", "prefill_hit", "prefill", "decode")
    assert {key: actual["results"]["latency_ms"][key]
            for key in formal_phase_keys} == {
                key: phases[key] for key in formal_phase_keys
            }
    assert "prefill_output_consumed" not in actual["results"]["latency_ms"]
    assert actual["results"]["diagnostic"]["latency_ms"] == phases
    assert actual["run"]["trend_eligible"] is True
    before = target.read_bytes()
    raw["beam_width"] = 64
    raw_path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        module.attach(target, tmp_path, lambda p: {"metrics": {"count": 10}})
    assert target.read_bytes() == before


def test_attach_worker_detail_does_not_replace_formal_stage_metrics(tmp_path):
    path = Path(__file__).resolve().parents[1] / "attach_worker_diagnostic.py"
    spec = importlib.util.spec_from_file_location("attach_no_worker_phases", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    formal_phases = {key: {"mean": value} for value, key in enumerate(
        ("prefill_miss", "prefill_hit", "prefill", "decode"), start=1)}
    summary = {
        "run": {"id": "formal", "trend_eligible": True},
        "source": {"git_sha": "abc"}, "model": {"id": "model"},
        "scenario": {"n": 128, "input_tokens_target": 1024,
                     "beam_api": "beam_search_v1"},
        "results": {
            "latency_ms": {"e2el": {"mean": 42}, **formal_phases},
            "diagnostic": {"latency_ms": formal_phases},
        },
    }
    raw = {
        "beam_width": 128, "input_length": 1024, "model": "model",
        "failed": 0, "completed": 20, "num_prompts": 20,
        "started_at": "start", "finished_at": "end",
        "beam_api": "beam_search_v1",
    }
    target = tmp_path / "formal-summary.json"
    target.write_text(json.dumps(summary))
    (tmp_path / "raw-result.json").write_text(json.dumps(raw))
    (tmp_path / "summary.json").write_text(json.dumps({"results": {"diagnostic": None}}))

    module.attach(target, tmp_path, lambda p: {"metrics": {"execute_model": {"count": 10}}})
    actual = json.loads(target.read_text())
    assert actual["results"]["latency_ms"] == summary["results"]["latency_ms"]
    assert actual["results"]["diagnostic"] == summary["results"]["diagnostic"]
    assert "formal E2E and post-canonical stage metrics remain unchanged" in actual["run"]["notes"][-1]

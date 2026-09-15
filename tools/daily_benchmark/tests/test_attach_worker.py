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
               "scenario": {"n": 128, "input_tokens_target": 1024},
               "results": {"latency_ms": {"e2el": {"mean": 42}},
                           "diagnostic": {"latency_ms": {"decode": 17}}}}
    raw = dict(beam_width=128, input_length=1024, model="model", failed=0,
               completed=20, num_prompts=20, started_at="start", finished_at="end")
    target = tmp_path / "summary.json"
    target.write_text(json.dumps(summary))
    raw_path = tmp_path / "raw-result.json"
    raw_path.write_text(json.dumps(raw))
    module.attach(target, tmp_path, lambda p: {"metrics": {"execute_model": {"count": 10}}})
    actual = json.loads(target.read_text())
    assert actual["results"]["latency_ms"] == summary["results"]["latency_ms"]
    assert actual["results"]["diagnostic"] == summary["results"]["diagnostic"]
    assert actual["run"]["trend_eligible"] is True
    before = target.read_bytes()
    raw["beam_width"] = 64
    raw_path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        module.attach(target, tmp_path, lambda p: {"metrics": {"count": 10}})
    assert target.read_bytes() == before

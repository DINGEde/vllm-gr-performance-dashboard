import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.mark.parametrize("grouped_cuda_modules", [False, True])
def test_independent_worker_installs_required_gpu_anchors(tmp_path, monkeypatch, grouped_cuda_modules):
    path = Path(__file__).resolve().parents[1] / "instrumentation" / "vllm_gr_lightweight_timing.py"
    spec = importlib.util.spec_from_file_location("worker_timing", path)
    timing = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(timing)
    monkeypatch.setenv("VLLM_GR_LIGHTWEIGHT_TIMING_DIR", str(tmp_path))

    class CudaStageHooksV2:
        def prefill_logits(self):
            return "prefill"

    class GPUBeamStageRunner:
        def execute(self):
            return "executed"

        def sample(self):
            return "sampled"

    worker = "vllm_gr.v1.worker"
    cuda = f"{worker}.cuda" if grouped_cuda_modules else worker
    cuda_v2 = f"{cuda}.v2" if grouped_cuda_modules else cuda
    v2_module = ModuleType(f"{cuda_v2}.gpu_beam_stage_runner_v2")
    v2_module.CudaStageHooksV2 = CudaStageHooksV2
    stage_module = ModuleType(f"{cuda}.gpu_beam_stage_runner")
    stage_module.GPUBeamStageRunner = GPUBeamStageRunner
    stage_module.AsyncGPUBeamOutput = type("AsyncGPUBeamOutput", (), {})
    stage_module.AsyncGRRetireOutput = type("AsyncGRRetireOutput", (), {})
    final_output_module = ModuleType(f"{cuda}.beam_final_output")
    final_output_module.GPUBeamFinalOutput = type("GPUBeamFinalOutput", (), {})
    for module in (v2_module, stage_module, final_output_module):
        monkeypatch.setitem(sys.modules, module.__name__, module)

    timing.install()
    assert CudaStageHooksV2().prefill_logits() == "prefill"
    assert GPUBeamStageRunner().execute() == "executed"
    assert GPUBeamStageRunner().sample() == "sampled"
    timing.flush()

    result_file, = tmp_path.glob("cpu-timing-*.json")
    metrics = json.loads(result_file.read_text(encoding="utf-8"))["metrics"]
    assert {name: values["count"] for name, values in metrics.items()} == {
        "gr_v2_prefill_logits": 1,
        "gr_v1_stage_execute": 1,
        "gr_v1_stage_sample": 1,
    }


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


@pytest.mark.parametrize("runner", ["v1", "v2"])
def test_attach_worker_detail_does_not_replace_formal_stage_metrics(tmp_path, runner):
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
    worker_summary = {"results": {"diagnostic": None}}
    metrics = {"execute_model": {"count": 10}}
    if runner == "v2":
        summary["scenario"].update(
            batch_size=4,
            beam_api="beam_search",
            server_args={"model_runner": "v2", "model_runner_classes": ["GPUModelRunner"], "compilation_config": {}},
            pipeline_version="beam-v1",
            benchmark_args={"dynamic_beam_widths": [256, 512, 1024], "measurement_mode": "canonical-with-posthoc-diagnostic"},
        )
        raw.update(
            batch_size=4,
            beam_api="beam_search",
            engine_config={"model_runner": "v2"},
            instrumentation={"canonical_contaminated_by_diagnostic": True},
        )
        worker_summary.update(scenario=summary["scenario"], source=summary["source"])
        metrics.update({name: {"count": 10} for name in (
            "gr_v2_prefill_logits", "gr_v1_stage_execute", "gr_v1_stage_sample",
        )})
    target = tmp_path / "formal-summary.json"
    target.write_text(json.dumps(summary))
    (tmp_path / "raw-result.json").write_text(json.dumps(raw))
    (tmp_path / "summary.json").write_text(json.dumps(worker_summary))

    if runner == "v2":
        with pytest.raises(ValueError, match="anchors did not run"):
            module.attach(target, tmp_path, lambda p: {"metrics": {"execute_model": {"count": 10}}})
        assert json.loads(target.read_text()) == summary
    module.attach(target, tmp_path, lambda p: {"metrics": metrics})
    actual = json.loads(target.read_text())
    assert actual["results"]["latency_ms"] == summary["results"]["latency_ms"]
    assert actual["results"]["diagnostic"] == summary["results"]["diagnostic"]
    assert "formal E2E and post-canonical stage metrics remain unchanged" in actual["run"]["notes"][-1]

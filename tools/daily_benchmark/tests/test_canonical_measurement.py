from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "run_offline_benchmark.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_offline_benchmark", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(synchronize=lambda: None))
    with mock.patch.dict("sys.modules", {"torch": fake_torch}):
        spec.loader.exec_module(module)
    return module


def test_canonical_measurement_calls_beam_search_without_internal_patching() -> None:
    module = load_module()

    class FakeLlm:
        def __init__(self) -> None:
            self.calls = []

        def beam_search(self, prompts, params, concurrency_limit):
            self.calls.append((prompts, params, concurrency_limit))
            return [SimpleNamespace(sequences=[object(), object()])]

    llm = FakeLlm()
    params = SimpleNamespace(max_tokens=5)
    result = module.measure_request_canonical(llm, "prompt", params, "legacy")

    assert len(llm.calls) == 1
    assert llm.calls[0][2] == 1
    assert result["e2e_ms"] >= 0
    assert result["returned_beams"] == 2
    assert result["aggregate_output_tokens"] == 10


def test_canonical_measurement_uses_v1_api_when_selected():
    module = load_module()

    class Llm:
        def __init__(self):
            self.v1_calls = 0

        def beam_search(self, *args, **kwargs):
            raise AssertionError("legacy API must not be used")

        def beam_search_v1(self, *args, **kwargs):
            self.v1_calls += 1
            return [SimpleNamespace(sequences=[SimpleNamespace(token_ids=[1, 2])])]

    llm = Llm()
    result = module.measure_request_canonical(
        llm, [10, 11], SimpleNamespace(max_tokens=2), "v1"
    )
    assert llm.v1_calls == 1
    assert result["returned_beams"] == 1
    assert result["aggregate_output_tokens"] == 2


def test_v1_engine_kwargs_do_not_mix_canonical_and_legacy_config() -> None:
    module = load_module()
    canonical = {"schema_version": 1}
    kwargs = module.build_engine_kwargs(
        model="model",
        beam_width=128,
        max_model_len=40960,
        beam_api="v1",
        catalog=Path("triples.json"),
        additional_config={"vllm_gr_beam_engine_driven": True},
        v1_config=canonical,
    )
    assert kwargs["vllm_gr_config"] is canonical
    assert kwargs["max_num_seqs"] == 1
    for legacy_key in (
        "beam_graph_enabled", "beam_max_width", "attention_config",
        "catalog_path", "constraint_backend", "additional_config",
    ):
        assert legacy_key not in kwargs


@pytest.mark.parametrize("decode_path", ["graph", "eager", "capture"])
def test_v1_stage_measurement_decomposes_e2e_on_the_device_timeline(
    decode_path: str,
) -> None:
    """The device split must hold for every Decode dispatch path.

    ``"graph"`` replays a beam CUDA graph and is attributed through
    ``BeamDecodeGraph.replay``; ``"eager"`` runs the eager forward, which only
    ``GPUBeamStageRunner._forward`` can see; ``"capture"`` is the startup window
    where ``execute`` builds that graph and drives ``_forward`` three times on a
    side stream -- work that must stay out of the Decode cluster.
    """
    module = load_module()
    hooks = {"replay": 0, "forward": 0}

    class BeamSearchClient:
        def submit_once(self):
            return None

        def wait_final(self):
            return None

    class AsyncGPUBeamOutput:
        def __init__(self, kind="prefill", produces_output=True):
            metadata = SimpleNamespace(
                kind=SimpleNamespace(value=kind), produces_output=produces_output
            )
            self.execution = SimpleNamespace(entries=[SimpleNamespace(metadata=metadata)])

        def get_output(self):
            return None

    class GPUBeamStageRunner:
        def __init__(self):
            self.beam = SimpleNamespace(state=SimpleNamespace(device="cuda:0"))
            self.observed_kind = "prefill"
            self.pending = None
            # Mirrors the real runner: ``graph`` being unset while the beam graph
            # is enabled is exactly the startup-capture window the eager-forward
            # hook must not time.
            self.graph = None
            self.worker = worker

        def execute(self, scheduler_output):
            stage = next(iter(scheduler_output.gr_stage_metadata.values()))
            self.observed_kind = stage.kind.value
            entry = SimpleNamespace(
                metadata=SimpleNamespace(
                    kind=stage.kind, produces_output=True
                )
            )
            self.pending = (SimpleNamespace(entries=[entry]), 0)
            worker._current_scheduler_output = scheduler_output
            if self.observed_kind == "prefill":
                worker._model_forward()
            elif decode_path == "graph":
                decode_graph.replay()
            elif decode_path == "capture":
                # ``execute`` builds the beam graph here: two warm-up forwards
                # plus the one inside ``torch.cuda.graph``, all before it assigns
                # ``self.graph``.
                self._forward()
                self._forward()
                self._forward()
            else:
                self._forward()
            return None

        def _forward(self):
            hooks["forward"] += 1
            return None

        def sample(self):
            output = AsyncGPUBeamOutput(kind=self.observed_kind)
            self.pending = None
            return output

    class GPUModelRunner:
        def __init__(self):
            self._current_scheduler_output = None
            # Decode reaches the *timed* ``_forward`` only when the beam graph is
            # off, so only the eager parametrisation must look eager to the gate.
            self.model_config = SimpleNamespace(enforce_eager=decode_path == "eager")

        def _model_forward(self):
            return None

    # The instrumentation refuses to time a Prefill whose anchor does not wrap
    # GR's own prefill-graph interceptor.
    GPUModelRunner._model_forward._vllm_gr_prefill_model_forward_patch = True

    class BeamDecodeGraph:
        def replay(self):
            hooks["replay"] += 1
            return None

    worker = GPUModelRunner()
    decode_graph = BeamDecodeGraph()

    client_module = ModuleType("vllm_gr.entrypoints.beam_search_v1.client")
    client_module.BeamSearchClient = BeamSearchClient
    runner_module = ModuleType("vllm_gr.v1.worker.gpu_beam_stage_runner")
    runner_module.AsyncGPUBeamOutput = AsyncGPUBeamOutput
    runner_module.GPUBeamStageRunner = GPUBeamStageRunner
    model_runner_module = ModuleType("vllm.v1.worker.gpu_model_runner")
    model_runner_module.GPUModelRunner = GPUModelRunner
    decode_graph_module = ModuleType("vllm_gr.v1.worker.beam_decode_graph")
    decode_graph_module.BeamDecodeGraph = BeamDecodeGraph
    prefill_graph_module = ModuleType("vllm_gr.v1.worker.prefill_graph_runner")
    prefill_graph_module.get_prefill_graph_runner = lambda runner: None
    prefill_graph_module.is_prefill_graph_enabled = lambda: False
    # Only the capture parametrisation gets far enough to consult this: it is the
    # one where the beam graph is enabled but has not been built yet.
    common_module = ModuleType("vllm_gr.v1.worker.model_runner_common")
    common_module._get_gr_config = lambda runner: SimpleNamespace(beam_graph_enabled=True)

    class Llm:
        def beam_search_v1(self, *args, **kwargs):
            client = BeamSearchClient()
            runner = GPUBeamStageRunner()
            client.submit_once()
            prefill = SimpleNamespace(kind=SimpleNamespace(value="prefill"))
            runner.execute(SimpleNamespace(gr_stage_metadata={0: prefill}))
            runner.sample()
            decode = SimpleNamespace(kind=SimpleNamespace(value="decode"))
            runner.execute(SimpleNamespace(gr_stage_metadata={0: decode}))
            # V1 look-ahead may start Decode before EngineCore consumes the
            # output-producing Prefill result.
            AsyncGPUBeamOutput(kind="prefill").get_output()
            runner.sample()
            client.wait_final()
            sequence = SimpleNamespace(token_ids=[1, 2])
            return [SimpleNamespace(sequences=[sequence])]

    fake_modules = {
        "vllm_gr.entrypoints.beam_search_v1.client": client_module,
        "vllm_gr.v1.worker.gpu_beam_stage_runner": runner_module,
        "vllm.v1.worker.gpu_model_runner": model_runner_module,
        "vllm_gr.v1.worker.beam_decode_graph": decode_graph_module,
        "vllm_gr.v1.worker.prefill_graph_runner": prefill_graph_module,
        "vllm_gr.v1.worker.model_runner_common": common_module,
    }

    # Stage spans remain 20ms Prefill and 30ms Decode. The nested event pairs
    # isolate 17ms Prefill and 20ms Decode GPU enqueue clusters, leaving 3ms
    # and 10ms of device wait respectively.
    device_clock = [
        100.0,  # Prefill stage start
        102.0,  # Prefill model start
        115.0,  # Prefill execute end
        116.0,  # Prefill sample start
        120.0,  # Prefill sample/stage end
        120.0,  # Decode stage start
    ]
    if decode_path != "capture":
        # Startup capture records no Decode events at all: its forwards run on a
        # side stream and are deliberately left untimed.
        device_clock += [
            125.0,  # Decode replay start
            140.0,  # Decode replay end
        ]
    device_clock += [
        145.0,  # Decode sample start
        150.0,  # Decode sample/stage end
    ]

    class FakeCudaEvent:
        def __init__(self, enable_timing=False):
            assert enable_timing, "phase events must be timing-enabled"
            self.device_time = device_clock.pop(0)

        def record(self, stream):
            return None

        def synchronize(self):
            return None

        def elapsed_time(self, other):
            return other.device_time - self.device_time

    # The engine never switches its compute stream mid-request; the
    # instrumentation asserts every phase event landed on the same one, so the
    # fake has to hand back a stable object rather than a fresh one per call.
    stream = object()

    module.torch.cuda.Event = FakeCudaEvent
    module.torch.cuda.current_stream = lambda *args, **kwargs: stream
    clock = [
        0,  # request_started
        5_000_000,  # post-synchronize submit anchor
        9_000_000,  # PREFILL execute entry
        21_000_000,  # first DECODE execute entry
        23_000_000,  # output-producing Prefill get_output
        50_000_000,  # wait_final return
        51_000_000,  # request_finished
    ]
    with (
        mock.patch.dict(sys.modules, fake_modules),
        mock.patch.object(module.time, "perf_counter_ns", side_effect=clock),
    ):
        result = module._measure_request_diagnostic_v1(
            Llm(), [10, 11], SimpleNamespace(max_tokens=2)
        )

    assert device_clock == [], "unexpected extra CUDA events were recorded"
    # Exactly one Decode hook fired, and it is the one this path reaches.
    if decode_path == "graph":
        assert (hooks["replay"], hooks["forward"]) == (1, 0)
    elif decode_path == "eager":
        assert (hooks["replay"], hooks["forward"]) == (0, 1)
    else:
        # Capture still drives ``_forward``; the point is that none of it is
        # attributed to Decode, so the whole Decode span stays device idle.
        assert (hooks["replay"], hooks["forward"]) == (0, 3)
    assert result["prefill_ms"] == 20.0
    assert result["decode_ms"] == 30.0
    assert result["prefill_gpu_compute_ms"] == 17.0
    # Decode contributes the 15ms replay plus the 5ms sample window on the graph
    # path, and only the 5ms sample window during startup capture -- the forward
    # work the graph later absorbs is not attributed to any measured Decode.
    assert result["decode_gpu_compute_ms"] == (5.0 if decode_path == "capture" else 20.0)
    assert result["prefill_device_idle_ms"] == 3.0
    assert result["decode_device_idle_ms"] == (25.0 if decode_path == "capture" else 10.0)
    assert result["e2e_ms"] == 51.0
    assert result["prefill_dispatch_ms"] == 4.0
    # Prefill device completion at host 29ms vs first Decode entry at host 21ms.
    assert result["prefill_cpu_lead_ms"] == 8.0
    assert result["host_overhead_ms"] == 1.0
    assert result["prefill_output_consumed_ms"] == 18.0
    # The three components must reconstruct the end-to-end wall time.
    assert (
        result["prefill_ms"] + result["decode_ms"] + result["host_overhead_ms"]
        == result["e2e_ms"]
    )

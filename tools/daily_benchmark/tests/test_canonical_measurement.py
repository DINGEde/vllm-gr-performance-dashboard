from __future__ import annotations

import importlib.util
import sys
import tarfile
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


@pytest.mark.parametrize("linkname", ["../vllm_gr/config/default_config.yaml", "../../outside.yaml"])
def test_pinned_source_filter_handles_legacy_tarfile_symlink_check(tmp_path, monkeypatch, linkname):
    source = SCRIPT.with_name("prepare_native_source.py")
    spec = importlib.util.spec_from_file_location(source.stem, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data_filter = tarfile.data_filter

    def legacy_data_filter(member, destination):
        # The older Python 3.10 filter checks relative symlinks against the
        # archive root. Simulate the rejection before invoking its other checks.
        if member.issym() and member.linkname.startswith("../"):
            raise tarfile.LinkOutsideDestinationError(member, str(tmp_path.parent))
        return data_filter(member, destination)

    monkeypatch.setattr(tarfile, "data_filter", legacy_data_filter)
    member = tarfile.TarInfo("configs/default_config.yaml")
    member.type = tarfile.SYMTYPE
    member.linkname = linkname
    if linkname == "../../outside.yaml":
        with pytest.raises(tarfile.LinkOutsideDestinationError):
            module.source_archive_filter(member, tmp_path)
        return

    checked = module.source_archive_filter(member, tmp_path)
    assert checked.name == member.name
    assert checked.issym() and checked.linkname == linkname
    assert checked.uid is None


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
    method, call, output_mode = module.resolve_beam_call(llm, "legacy")
    result = module.measure_request_canonical(call, [[1, 2]], params)

    assert (method, output_mode) == ("beam_search", "full")
    assert len(llm.calls) == 1
    assert llm.calls[0][2] == 1
    assert result["e2e_ms"] >= 0
    assert result["returned_beams"] == 2
    assert result["aggregate_output_tokens"] == 10


def test_runner_identity_rejects_silent_fallback():
    module = load_module()
    v2 = "vllm.v1.worker.gpu.model_runner.GPUModelRunner"
    llm = SimpleNamespace(collective_rpc=lambda fn: [v2])
    assert module.verify_model_runner(llm, "v2") == [v2]
    with pytest.raises(RuntimeError, match="requested Model Runner v1"):
        module.verify_model_runner(llm, "v1")


@pytest.mark.parametrize("method_name", ["beam_search_v1", "beam_search"])
def test_canonical_measurement_resolves_persistent_api_and_times_an_entire_batch(method_name):
    module = load_module()
    observations = []

    def run(prompts, params, *, concurrency_limit, output_mode="full"):
        observations.append("call")
        assert prompts == [{"prompt_token_ids": [10, 11]}, {"prompt_token_ids": [20, 21]}]
        assert (concurrency_limit, output_mode) == (1, "compact")
        return [SimpleNamespace(sequences=[object()]) for _ in prompts]

    def retired(*args, **kwargs):
        raise NotImplementedError("the pre-rename beam_search entrypoint is retired")

    llm = SimpleNamespace(beam_search=retired)
    setattr(llm, method_name, run)
    method, call, output_mode = module.resolve_beam_call(llm, "v1")
    assert (method, output_mode) == (method_name, "compact")
    setattr(llm, method_name, retired)

    ticks = iter([0, 2_000_000])

    def tick():
        observations.append("timer")
        return next(ticks)

    module.torch.cuda.synchronize = lambda: observations.append("sync")
    with mock.patch.object(module.time, "perf_counter_ns", tick):
        result = module.measure_request_canonical(call, [[10, 11], [20, 21]], SimpleNamespace(max_tokens=2))
    assert observations == ["timer", "call", "sync", "timer"]
    assert result["e2e_ms"] == 2.0
    assert result["returned_beams"] == 2
    assert result["aggregate_output_tokens"] == 4


def test_persistent_api_without_compact_preserves_old_sha_output():
    module = load_module()

    def run(prompts, params, *, concurrency_limit):
        assert concurrency_limit == 1
        return []

    method, call, output_mode = module.resolve_beam_call(SimpleNamespace(beam_search_v1=run), "v1")
    assert (method, output_mode) == ("beam_search_v1", "full")
    assert call([], None) == []


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


def test_gr_customer_config_keeps_v2_native_capture_at_16384(monkeypatch):
    module = load_module()
    monkeypatch.setenv("VLLM_GR_PREFILL_GRAPH_MAX_BUCKET", "8192")
    monkeypatch.setenv("VLLM_GR_PREFILL_GRAPH_BUCKET_STEP", "128")
    monkeypatch.setenv("VLLM_GR_PREFILL_GRAPH_KV_BOUND", "4096")
    schedule = module.build_beam_schedule(
        beam_width=1024,
        generated_steps=3,
        dynamic_beam_widths=[256, 512, 1024],
        per_parent_beam_widths=[256, 128, 64],
        result_width=1000,
    )
    old_config = {
        "beam": {
            "max_width": 1024,
            "max_decode_steps": 3,
            "graph_enabled": True,
            "worker_decision": True,
            "enable_short_suffix_attention": True,
        },
        "constraint_table": {
            "path": "/constraint.safetensors",
            "backend": "cuda",
            "format": "constraint_table_v1",
            "max_top_k": 256,
            "artifact_digest": "artifact",
            "tokenizer_digest": "tokenizer",
        },
    }
    gr = module.build_gr_constraint_config(old_config, schedule)
    kwargs = module.build_engine_kwargs(
        model="model", beam_width=1024, max_model_len=40960, beam_api="v1",
        catalog=Path("triples.json"), additional_config={}, v1_config=old_config,
        batch_size=4, candidate_width=schedule["candidate_width"], gr_config=gr,
    )
    graphs = module.configure_prefill_graph(kwargs, model_runner="v2", batch_size=4, input_length=4096)

    assert "vllm_gr_config" not in kwargs
    assert kwargs["attention_config"] == {"backend": "CUSTOM"}
    assert kwargs["max_logprobs"] == gr["constraint_table"]["max_top_k"] == 256
    assert kwargs["max_num_seqs"] == 4
    assert kwargs["enable_chunked_prefill"] is False
    assert gr["beam_width"] == [256, 512, 1024]
    assert gr["candidate_width"] == [256, 128, 64]
    assert gr["result_width"] == 1000
    assert gr["constraint_mode"] == "table"
    assert gr["constraint_table"]["digests"] == {"artifact": "artifact", "tokenizer": "tokenizer"}
    assert gr["prefill_token_buckets"] is None
    assert gr["prefill_kv_bound"] == 4096
    capture_sizes = kwargs["compilation_config"]["cudagraph_capture_sizes"]
    assert capture_sizes == graphs["prefill_token_buckets"] == [1, 2, 4, 8, 16, 32, 64, 128, 16384]
    assert graphs["prefill_graph_max_bucket"] == 16384


def test_gr_v1_prefill_uses_the_daily_shell_grid(monkeypatch):
    module = load_module()
    monkeypatch.setenv("VLLM_GR_PREFILL_GRAPH_MAX_BUCKET", "16384")
    monkeypatch.setenv("VLLM_GR_PREFILL_GRAPH_BUCKET_STEP", "128")
    monkeypatch.setenv("VLLM_GR_PREFILL_GRAPH_KV_BOUND", "4096")
    kwargs = {"gr": {}}

    graphs = module.configure_prefill_graph(kwargs, model_runner="v1", batch_size=4, input_length=4096)

    assert kwargs["gr"]["prefill_token_buckets"] == graphs["prefill_token_buckets"]
    assert graphs["prefill_token_buckets"][:3] == [1, 16, 128]
    assert graphs["prefill_token_buckets"][-1] == 16384
    assert "compilation_config" not in kwargs


@pytest.mark.parametrize(
    ("revision", "use_v2"),
    [("27de4485", False), ("539a30f2", False), ("91f353d2", False), ("91f353d2", True)],
)
@pytest.mark.parametrize("decode_path", ["graph", "eager", "capture"])
def test_v1_stage_measurement_decomposes_e2e_on_the_device_timeline(
    revision: str, use_v2: bool, decode_path: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The device split must hold for every Decode dispatch path.

    ``"graph"`` replays a beam CUDA graph and is attributed through
    ``BeamDecodeGraph.replay``; ``"eager"`` runs the eager forward, which only
    ``GPUBeamStageRunner._forward`` can see; ``"capture"`` is the startup window
    where ``execute`` builds that graph and drives ``_forward_hidden`` three times on a
    side stream -- work that must stay out of the Decode cluster.
    """
    module = load_module()
    monkeypatch.setenv("VLLM_USE_V2_MODEL_RUNNER", "1" if use_v2 else "0")
    hooks = {"replay": 0, "forward": 0, "capture_forward": 0}

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
                if use_v2:
                    worker.cudagraph_manager.run_fullgraph()
                else:
                    worker._model_forward()
            elif decode_path == "graph":
                decode_graph.replay()
            elif decode_path == "capture":
                # ``execute`` builds the beam graph here: two warm-up forwards
                # plus the one inside ``torch.cuda.graph``. The captured body
                # uses the hidden-state entry point.
                self._forward_hidden(False)
                self._forward_hidden(False)
                self._forward_hidden(True)
            else:
                self._forward()
            return None

        def _forward(self):
            hooks["forward"] += 1
            return None

        def _forward_hidden(self, capture):
            hooks["capture_forward"] += 1

        def sample(self):
            output = AsyncGPUBeamOutput(kind=self.observed_kind)
            self.pending = None
            return output

    class GPUModelRunner:
        def __init__(self):
            self._current_scheduler_output = None
            # The eager path also exists when graph mode is requested but the
            # current geometry has fallen back to eager.
            self.model_config = SimpleNamespace(enforce_eager=False)
            self.cudagraph_manager = SimpleNamespace(run_fullgraph=lambda: None)
            self.model = SimpleNamespace(
                register_forward_pre_hook=lambda hook: SimpleNamespace(remove=lambda: None)
            )

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

    api = "beam_search" if revision == "91f353d2" else "beam_search_v1"
    worker_base = "vllm_gr.v1.worker"
    cuda_base = worker_base if revision == "27de4485" else f"{worker_base}.cuda"
    client_module = ModuleType(f"vllm_gr.entrypoints.{api}.client")
    client_module.BeamSearchClient = BeamSearchClient
    runner_module = ModuleType(f"{cuda_base}.gpu_beam_stage_runner")
    runner_module.AsyncGPUBeamOutput = AsyncGPUBeamOutput
    runner_module.GPUBeamStageRunner = GPUBeamStageRunner
    model_runner_module = ModuleType("vllm.v1.worker.gpu_model_runner")
    model_runner_module.GPUModelRunner = GPUModelRunner
    decode_graph_module = ModuleType(f"{cuda_base}.beam_decode_graph")
    decode_graph_module.BeamDecodeGraph = BeamDecodeGraph
    prefill_graph_module = ModuleType("vllm_gr.v1.worker.prefill_graph_runner")
    prefill_graph_module.get_prefill_graph_runner = lambda runner: None
    prefill_graph_module.is_prefill_graph_enabled = lambda: False
    if revision != "27de4485":
        def configured_prefill_graph_enabled(model_runner):
            assert model_runner is worker
            return False

        prefill_graph_module.is_prefill_graph_enabled = configured_prefill_graph_enabled

    class Llm:
        def search(self, prompts, params, *, concurrency_limit, output_mode):
            assert concurrency_limit == 1
            assert output_mode == "compact"
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

    setattr(Llm, api, Llm.search)
    _, beam_call, _ = module.resolve_beam_call(Llm(), "v1")
    fake_modules = {
        fake.__name__: fake
        for fake in (client_module, runner_module, model_runner_module, decode_graph_module, prefill_graph_module)
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
        # The diagnostic takes the batch dimension explicitly; one prompt in,
        # one prompt list here.
        result = module._measure_request_diagnostic_v1(
            beam_call, [[10, 11]], SimpleNamespace(max_tokens=2)
        )

    assert device_clock == [], "unexpected extra CUDA events were recorded"
    # Exactly one Decode hook fired, and it is the one this path reaches.
    if decode_path == "graph":
        assert hooks == {"replay": 1, "forward": 0, "capture_forward": 0}
    elif decode_path == "eager":
        assert hooks == {"replay": 0, "forward": 1, "capture_forward": 0}
    else:
        # Capture forwards stay outside the Decode compute intervals.
        assert hooks == {"replay": 0, "forward": 0, "capture_forward": 3}
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

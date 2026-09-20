#!/usr/bin/env python3
"""Run paired cold/hit OneRec beam-search measurements through GRLLM offline."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import torch
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

# Align with the abba E2E baseline: run the V1 engine single-process (driver +
# EngineCore in one process) to avoid the ZMQ/TCP IPC round-trip per request.
os.environ.setdefault("VLLM_ENABLE_V1_MULTIPROCESSING", "0")


def distribution_ms(values: list[float]) -> dict[str, float | str]:
    """Summarize millisecond samples using the same percentiles as the dashboard."""
    if not values:
        raise ValueError("cannot summarize an empty sample")
    ordered = sorted(float(value) for value in values)

    def percentile(percent: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = (len(ordered) - 1) * percent / 100.0
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    return {
        "mean": statistics.fmean(ordered),
        "std": statistics.pstdev(ordered),
        "p50": percentile(50),
        "p90": percentile(90),
        "p95": percentile(95),
        "p99": percentile(99),
        "unit": "ms",
    }


def _run_beam_call(
    llm: Any,
    prompt_token_ids: list[int],
    params: Any,
    beam_api: str,
) -> list[Any]:
    method = llm.beam_search_v1 if beam_api == "v1" else llm.beam_search
    return method(
        [{"prompt_token_ids": prompt_token_ids}],
        params,
        concurrency_limit=1,
    )


def measure_request_canonical(
    llm: Any,
    prompt_token_ids: list[int],
    params: Any,
    beam_api: str,
) -> dict[str, Any]:
    """Measure the production trend with no internal monkeypatches or profiler.

    Keep the timed region deliberately small: one direct Beam API call followed
    by a device completion fence. Output materialization happens after the
    clock stops.
    Any diagnostic decomposition is collected only after all canonical samples.
    """
    started = time.perf_counter_ns()
    outputs = _run_beam_call(llm, prompt_token_ids, params, beam_api)
    torch.cuda.synchronize()
    finished = time.perf_counter_ns()
    if len(outputs) != 1:
        raise RuntimeError(f"expected one offline output, received {len(outputs)}")
    output = outputs[0]
    return {
        "e2e_ms": (finished - started) / 1_000_000.0,
        "returned_beams": len(output.sequences),
        "aggregate_output_tokens": len(output.sequences) * int(params.max_tokens),
    }


def _measure_request_diagnostic_legacy(
    llm: Any,
    prompt_token_ids: list[int],
    params: Any,
) -> dict[str, Any]:
    """Measure one offline request's prefill/decode boundary with a minimal patch.

    Only two boundary functions are wrapped with a one-line perf_counter
    timestamp: ``_custom_beam_search_batch`` marks the internal beam token-loop
    start (``phase_started``); ``_prepare_beam_step_requests`` marks the first
    decode token (``decode_started`` at token 1). No global hot functions
    (sorted / np.asarray / deepcopy / tokenizer.decode) are patched, so this
    cannot perturb cyclic-GC timing or inflate the decode path the way the old
    20-function instrumentation did.
    """
    import vllm_gr.entrypoints.gr as gr_module

    original_custom = gr_module._custom_beam_search_batch
    original_prepare = gr_module._prepare_beam_step_requests

    phase_started = None
    decode_started = None

    def timed_custom(*a: Any, **k: Any) -> Any:
        nonlocal phase_started
        if phase_started is None:
            phase_started = time.perf_counter()
        return original_custom(*a, **k)

    def timed_prepare(*a: Any, **k: Any) -> Any:
        nonlocal decode_started
        token = int(k.get("token", a[2] if len(a) > 2 else 0))
        if token == 1 and decode_started is None:
            decode_started = time.perf_counter()
        return original_prepare(*a, **k)

    started = time.perf_counter()
    with (
        mock.patch.object(gr_module, "_custom_beam_search_batch", timed_custom),
        mock.patch.object(gr_module, "_prepare_beam_step_requests", timed_prepare),
    ):
        outputs = llm.beam_search([{"prompt_token_ids": prompt_token_ids}], params, concurrency_limit=1)
    finished = time.perf_counter()

    e2e_ms = (finished - started) * 1000.0
    if phase_started is None:
        raise RuntimeError("offline serving-aligned Prefill boundary was not observed")
    boundary = decode_started if decode_started is not None else finished
    prefill_ms = (boundary - phase_started) * 1000.0
    decode_ms = (finished - boundary) * 1000.0

    if len(outputs) != 1:
        raise RuntimeError(f"expected one offline output, received {len(outputs)}")
    output = outputs[0]
    return {
        "e2e_ms": e2e_ms,
        "prefill_ms": prefill_ms,
        "decode_ms": decode_ms,
        "returned_beams": len(output.sequences),
        "aggregate_output_tokens": len(output.sequences) * int(params.max_tokens),
    }


def _measure_request_diagnostic_v1(
    llm: Any,
    prompt_token_ids: list[int],
    params: Any,
) -> dict[str, Any]:
    """Measure V1 Beam phases on the CUDA device timeline.

    V1 uses asynchronous scheduling: while the GPU still executes the full
    Prefill, the CPU has already prepared and enqueued the first Decode stage.
    Host-side boundaries therefore order enqueues rather than device work, so
    each stage is bracketed by a pair of ``enable_timing`` CUDA events recorded
    on the engine's compute stream:

    ``prefill_ms``
        entry to the PREFILL ``GPUBeamStageRunner.execute`` through the
        output-producing PREFILL ``sample`` completion.
    ``decode_ms``
        entry to the first DECODE ``execute`` through the last Decode
        ``sample`` completion.

    Device compute is single-stream FIFO (``execute`` never switches streams),
    so the two intervals cannot overlap and their sum is an additive device
    decomposition. The first Decode start event is stream-ordered behind the
    Prefill kernels, so it fires at Prefill completion and the Decode interval
    excludes Prefill by construction. What remains of ``e2e_ms`` is host
    overhead: frontend dispatch, EngineCore queueing, and terminal collection.

    ``decode_ms`` is a device-timeline span, so it absorbs any device idle
    inside the Decode stage (for example the ``prepare_inputs_event`` wait
    before the first Decode dispatch). That idle is not host overhead.

    Events stay outside the CUDA graph capture regions: both hook points are
    ordinary stream operations wrapping ``graph.replay()``.
    """
    from vllm_gr.entrypoints.beam_search_v1.client import BeamSearchClient
    from vllm_gr.v1.worker.gpu_beam_stage_runner import (
        AsyncGPUBeamOutput,
        GPUBeamStageRunner,
    )
    from vllm_gr.v1.worker.beam_decode_graph import BeamDecodeGraph
    from vllm_gr.v1.worker.prefill_graph_runner import (
        get_prefill_graph_runner,
        is_prefill_graph_enabled,
    )
    from vllm.v1.worker.gpu_model_runner import GPUModelRunner

    original_submit = BeamSearchClient.submit_once
    original_wait = BeamSearchClient.wait_final
    original_get_output = AsyncGPUBeamOutput.get_output
    original_execute = GPUBeamStageRunner.execute
    original_sample = GPUBeamStageRunner.sample
    original_forward = GPUBeamStageRunner._forward
    original_decode_replay = BeamDecodeGraph.replay
    original_model_forward = GPUModelRunner._model_forward
    # The Prefill anchor is only meaningful while it wraps GR's own interceptor:
    # that interceptor is what picks between a Prefill CUDA graph replay and the
    # eager forward, and it is installed on the runner class during engine
    # startup. Keeping the timing wrapper outside it is what makes the anchor
    # fire in graph mode; a reordering that leaves the timing wrapper innermost
    # would silently time the eager fallback only, so fail here instead.
    if not getattr(original_model_forward, "_vllm_gr_prefill_model_forward_patch", False):
        raise RuntimeError(
            "GPUModelRunner._model_forward is not wrapped by the vllm-gr prefill "
            "graph interceptor; the Prefill timing anchor would miss graph replays"
        )
    beam_started: int | None = None
    prefill_stage_start_event: Any | None = None
    prefill_end_event: Any | None = None
    prefill_model_start_event: Any | None = None
    prefill_execute_end_event: Any | None = None
    prefill_sample_pair: tuple[Any, Any] | None = None
    decode_start_event: Any | None = None
    decode_end_event: Any | None = None
    decode_compute_pairs: list[tuple[Any, Any]] = []
    prefill_worker_started: int | None = None
    first_decode_started: int | None = None
    prefill_output_consumed: int | None = None
    beam_finished: int | None = None
    # The additive split only holds while every phase event lands on one
    # compute stream; track them so a future multi-stream change fails loudly
    # instead of silently producing a meaningless decomposition.
    observed_streams: set[Any] = set()

    def record_timing_event(runner: Any | None = None) -> Any:
        """Return a one-shot timing event recorded on the runner's stream.

        ``GPUBeamStageRunner.produced_ready`` slots and ``last_work`` are
        reused by look-ahead stages, so they cannot carry per-stage timestamps.
        """
        device = runner.beam.state.device if runner is not None else None
        stream = torch.cuda.current_stream(device)
        observed_streams.add(stream)
        event = torch.cuda.Event(enable_timing=True)
        event.record(stream)
        return event

    def is_beam_graph_enabled(runner: Any) -> bool:
        """Mirror the gate ``GPUBeamStageRunner.execute`` uses to pick the graph."""
        if runner.worker.model_config.enforce_eager:
            return False
        from vllm_gr.v1.worker.model_runner_common import _get_gr_config

        return bool(_get_gr_config(runner.worker).beam_graph_enabled)

    def timed_submit(self: Any, *a: Any, **k: Any) -> Any:
        nonlocal beam_started
        if beam_started is None:
            # Drain device work left by the previous request so the host anchor
            # and the Prefill dispatch gap both start from an idle engine. Only
            # the post-canonical diagnostic path synchronizes here.
            torch.cuda.synchronize()
            beam_started = time.perf_counter_ns()
        return original_submit(self, *a, **k)

    def timed_get_output(self: Any, *a: Any, **k: Any) -> Any:
        nonlocal prefill_output_consumed
        output = original_get_output(self, *a, **k)
        if prefill_output_consumed is None:
            entries = getattr(getattr(self, "execution", None), "entries", ())
            for entry in entries:
                metadata = getattr(entry, "metadata", None)
                kind = getattr(getattr(metadata, "kind", None), "value", None)
                if kind == "prefill" and bool(getattr(metadata, "produces_output", False)):
                    prefill_output_consumed = time.perf_counter_ns()
                    break
        return output

    def timed_execute(self: Any, scheduler_output: Any, *a: Any, **k: Any) -> Any:
        nonlocal first_decode_started, decode_start_event
        nonlocal prefill_worker_started, prefill_stage_start_event
        nonlocal prefill_execute_end_event
        stages = getattr(scheduler_output, "gr_stage_metadata", {}) or {}
        kinds = {
            getattr(getattr(stage, "kind", None), "value", None)
            for stage in stages.values()
        }
        if "prefill" in kinds and prefill_worker_started is None:
            prefill_worker_started = time.perf_counter_ns()
            prefill_stage_start_event = record_timing_event(self)
        if first_decode_started is None and "decode" in kinds:
            # Recorded before this dispatch's CPU preparation, so the event is
            # stream-ordered behind the Prefill kernels and fires at Prefill
            # GPU completion rather than at this host instant.
            first_decode_started = time.perf_counter_ns()
            decode_start_event = record_timing_event(self)
        result = original_execute(self, scheduler_output, *a, **k)
        if "prefill" in kinds and prefill_execute_end_event is None:
            prefill_execute_end_event = record_timing_event(self)
            # A bucket miss degrades Prefill to eager silently, which would make
            # this sample's Prefill window incomparable with the trend. Capture
            # itself is startup-only (``capture_prefill_graphs`` is guarded by
            # ``_prefill_graphs_captured``), so a capture can never land inside
            # the measured window; ``kv_length_exceeds_bound`` is a legitimate
            # fallback, while a missing captured graph means the bucket grid and
            # the measured prompt lengths disagree.
            if is_prefill_graph_enabled() and getattr(
                get_prefill_graph_runner(self.worker), "last_replay_reason", None
            ) == "graph_not_captured":
                raise RuntimeError(
                    "beam_search_v1 Prefill fell back to eager because no CUDA graph "
                    "was captured for the scheduled bucket; the Prefill window is not "
                    "comparable with the graph-replay trend"
                )
        return result

    def timed_model_forward(self: Any, *a: Any, **k: Any) -> Any:
        nonlocal prefill_model_start_event
        scheduler_output = getattr(self, "_current_scheduler_output", None)
        stages = getattr(scheduler_output, "gr_stage_metadata", {}) or {}
        if prefill_model_start_event is None and any(
            getattr(getattr(stage, "kind", None), "value", None) == "prefill"
            for stage in stages.values()
        ):
            # This excludes dispatch and native input preparation but still
            # covers model forward plus compute_logits until execute returns, so
            # it is an upper bound on Prefill GPU busy time rather than the busy
            # time itself.
            prefill_model_start_event = record_timing_event()
        return original_model_forward(self, *a, **k)

    def timed_decode_replay(self: Any, *a: Any, **k: Any) -> Any:
        start = record_timing_event()
        result = original_decode_replay(self, *a, **k)
        decode_compute_pairs.append((start, record_timing_event()))
        return result

    def timed_forward(self: Any, *a: Any, **k: Any) -> Any:
        # Eager Decode -- beam graph disabled or enforce_eager -- never reaches
        # BeamDecodeGraph.replay, so this is the only place its model forward can
        # be attributed to the Decode phase.
        #
        # ``execute`` also calls ``_forward`` three times while it builds that
        # graph -- twice as warm-up and once inside ``torch.cuda.graph``, all on a
        # side capture stream. Those are startup work rather than Decode work, and
        # an event recorded inside a capture region is re-recorded on every
        # replay, so only the eager path the graph replaced may be timed here.
        if self.graph is None and is_beam_graph_enabled(self):
            return original_forward(self, *a, **k)
        start = record_timing_event(self)
        result = original_forward(self, *a, **k)
        decode_compute_pairs.append((start, record_timing_event(self)))
        return result

    def timed_sample(self: Any, *a: Any, **k: Any) -> Any:
        nonlocal prefill_end_event, decode_end_event, prefill_sample_pair
        pending = getattr(self, "pending", None)
        pending_entries = getattr(pending[0], "entries", ()) if pending else ()
        pending_kinds = {
            getattr(getattr(getattr(entry, "metadata", None), "kind", None), "value", None)
            for entry in pending_entries
        }
        sample_start = (
            record_timing_event(self)
            if "prefill" in pending_kinds or "decode" in pending_kinds
            else None
        )
        output = original_sample(self, *a, **k)
        entries = getattr(getattr(output, "execution", None), "entries", ())
        kinds = {
            getattr(getattr(getattr(entry, "metadata", None), "kind", None), "value", None)
            for entry in entries
        }
        is_output_prefill = any(
            getattr(getattr(getattr(entry, "metadata", None), "kind", None), "value", None)
            == "prefill"
            and bool(getattr(getattr(entry, "metadata", None), "produces_output", False))
            for entry in entries
        )
        if prefill_end_event is None and is_output_prefill:
            prefill_end_event = record_timing_event(self)
            if sample_start is not None:
                prefill_sample_pair = (sample_start, prefill_end_event)
        if "decode" in kinds:
            # Keep only the latest Decode completion: the terminal stage is the
            # one whose result reaches wait_final.
            decode_end_event = record_timing_event(self)
            if sample_start is not None:
                decode_compute_pairs.append((sample_start, decode_end_event))
        return output

    def timed_wait(self: Any, *a: Any, **k: Any) -> Any:
        nonlocal beam_finished
        result = original_wait(self, *a, **k)
        beam_finished = time.perf_counter_ns()
        return result

    request_started = time.perf_counter_ns()
    with (
        mock.patch.object(BeamSearchClient, "submit_once", timed_submit),
        mock.patch.object(BeamSearchClient, "wait_final", timed_wait),
        mock.patch.object(AsyncGPUBeamOutput, "get_output", timed_get_output),
        mock.patch.object(GPUBeamStageRunner, "execute", timed_execute),
        mock.patch.object(GPUBeamStageRunner, "sample", timed_sample),
        mock.patch.object(GPUBeamStageRunner, "_forward", timed_forward),
        mock.patch.object(GPUModelRunner, "_model_forward", timed_model_forward),
        mock.patch.object(BeamDecodeGraph, "replay", timed_decode_replay),
    ):
        outputs = _run_beam_call(llm, prompt_token_ids, params, "v1")
    request_finished = time.perf_counter_ns()

    if beam_started is None:
        raise RuntimeError("beam_search_v1 submit boundary was not observed")
    if first_decode_started is None or decode_start_event is None:
        raise RuntimeError("beam_search_v1 first Decode boundary was not observed")
    if (
        prefill_stage_start_event is None
        or prefill_end_event is None
        or prefill_model_start_event is None
        or prefill_execute_end_event is None
        or prefill_sample_pair is None
        or prefill_worker_started is None
    ):
        raise RuntimeError("beam_search_v1 Prefill GPU boundaries were not observed")
    if decode_end_event is None:
        raise RuntimeError("beam_search_v1 terminal Decode GPU boundary was not observed")
    if prefill_output_consumed is None:
        raise RuntimeError("beam_search_v1 output-producing Prefill consumption was not observed")
    if beam_finished is None:
        raise RuntimeError("beam_search_v1 terminal result boundary was not observed")
    if not beam_started <= first_decode_started <= beam_finished:
        raise RuntimeError("beam_search_v1 Decode timestamps are not monotonic")
    if len(outputs) != 1:
        raise RuntimeError(f"expected one offline output, received {len(outputs)}")
    if len(observed_streams) != 1:
        raise RuntimeError(
            "beam_search_v1 phase events landed on multiple CUDA streams; "
            "the Prefill/Decode device decomposition requires one compute stream"
        )

    prefill_end_event.synchronize()
    decode_end_event.synchronize()
    # The additive split rests on two structural facts: the model-forward window
    # ends before the sample window starts (the stage runner's pending gate
    # forces that CPU order), and both windows sit inside the stage span. Assert
    # them instead of clamping the residual, so a broken assumption fails loudly
    # rather than turning into a plausible-looking device-idle number.
    if float(prefill_execute_end_event.elapsed_time(prefill_sample_pair[0])) < 0:
        raise RuntimeError(
            "beam_search_v1 Prefill model-forward and sample windows overlap; "
            "the pending gate or an event placement changed"
        )
    prefill_ms = float(prefill_stage_start_event.elapsed_time(prefill_end_event))
    decode_ms = float(decode_start_event.elapsed_time(decode_end_event))
    prefill_gpu_compute_ms = float(
        prefill_model_start_event.elapsed_time(prefill_execute_end_event)
        + prefill_sample_pair[0].elapsed_time(prefill_sample_pair[1])
    )
    decode_gpu_compute_ms = float(
        sum(start.elapsed_time(end) for start, end in decode_compute_pairs)
    )
    if prefill_gpu_compute_ms > prefill_ms + 1e-3 or decode_gpu_compute_ms > decode_ms + 1e-3:
        raise RuntimeError(
            "beam_search_v1 stage GPU compute exceeds its device span: "
            f"prefill={prefill_gpu_compute_ms:.3f}/{prefill_ms:.3f}ms "
            f"decode={decode_gpu_compute_ms:.3f}/{decode_ms:.3f}ms"
        )
    prefill_device_idle_ms = prefill_ms - prefill_gpu_compute_ms
    decode_device_idle_ms = decode_ms - decode_gpu_compute_ms
    e2e_ms = (request_finished - request_started) / 1_000_000.0
    host_overhead_ms = e2e_ms - prefill_ms - decode_ms
    if host_overhead_ms < -0.5:
        raise RuntimeError(
            "beam_search_v1 device phases exceed the end-to-end wall time: "
            f"prefill={prefill_ms:.3f}ms decode={decode_ms:.3f}ms e2e={e2e_ms:.3f}ms"
        )
    # Map the Prefill device completion onto the host clock so the CPU look-ahead
    # depth shares a time base with first_decode_started.
    prefill_gpu_ready = prefill_worker_started + round(prefill_ms * 1_000_000.0)
    output = outputs[0]
    return {
        "e2e_ms": e2e_ms,
        "prefill_ms": prefill_ms,
        "decode_ms": decode_ms,
        "prefill_gpu_compute_ms": prefill_gpu_compute_ms,
        "decode_gpu_compute_ms": decode_gpu_compute_ms,
        "prefill_device_idle_ms": prefill_device_idle_ms,
        "decode_device_idle_ms": decode_device_idle_ms,
        "prefill_dispatch_ms": (prefill_worker_started - beam_started) / 1_000_000.0,
        "prefill_cpu_lead_ms": max(0, prefill_gpu_ready - first_decode_started) / 1_000_000.0,
        "host_overhead_ms": host_overhead_ms,
        "prefill_output_consumed_ms": (
            prefill_output_consumed - beam_started
        ) / 1_000_000.0,
        "returned_beams": len(output.sequences),
        "aggregate_output_tokens": len(output.sequences) * int(params.max_tokens),
    }


def measure_request_diagnostic(
    llm: Any,
    prompt_token_ids: list[int],
    params: Any,
    beam_api: str,
) -> dict[str, Any]:
    if beam_api == "v1":
        return _measure_request_diagnostic_v1(llm, prompt_token_ids, params)
    return _measure_request_diagnostic_legacy(llm, prompt_token_ids, params)


def build_v1_constraint_config(
    *,
    catalog: Path,
    tokenizer: Any,
    model_config: Any,
    artifact: Path,
    beam_width: int,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Compile the canonical CUDA constraint resource before timed requests."""
    from vllm_gr.constraints.artifact import save_constraint_table_artifact
    from vllm_gr.constraints.artifact_compiler import ConstraintTableArtifactCompiler
    from vllm_gr.constraints.tokenizer_digest import compute_tokenizer_digest

    document = json.loads(catalog.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("V1 constraint triples document must be a JSON object")
    vocab_size = int(model_config.vocab_size)
    tokenizer_digest = compute_tokenizer_digest(tokenizer)
    table = ConstraintTableArtifactCompiler(
        tokenizer,
        vocab_size,
        tokenizer_digest=tokenizer_digest,
    ).compile(document)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact_digest = save_constraint_table_artifact(table, artifact)
    config = {
        "schema_version": 1,
        "attention_backend": "CUSTOM",
        "beam": {
            "execution_mode": "v1",
            "graph_enabled": True,
            "worker_decision": True,
            "max_width": beam_width,
            "max_decode_steps": 3,
        },
        "constraint_table": {
            "enabled": True,
            "backend": "cuda",
            "max_top_k": beam_width,
            "path": str(artifact.resolve()),
            "format": "constraint_table_v1",
            "artifact_digest": artifact_digest,
            "tokenizer_digest": tokenizer_digest,
        },
    }
    return config, {
        "path": str(artifact.resolve()),
        "artifact_digest": artifact_digest,
        "tokenizer_digest": tokenizer_digest,
    }


def build_engine_kwargs(
    *,
    model: str,
    beam_width: int,
    max_model_len: int,
    beam_api: str,
    catalog: Path,
    additional_config: dict[str, object],
    v1_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build one unambiguous GR configuration source for the selected API."""
    kwargs: dict[str, Any] = {
        "model": model,
        "trust_remote_code": True,
        "max_logprobs": beam_width,
        "max_num_seqs": 1 if beam_api == "v1" else max(128, beam_width),
        "max_num_batched_tokens": 40960,
        "max_model_len": max_model_len,
        "scheduling_policy": "fcfs",
        "async_scheduling": True,
        "enable_prefix_caching": True,
        "enable_chunked_prefill": True,
        "gpu_memory_utilization": 0.5,
    }
    if beam_api == "v1":
        if v1_config is None:
            raise ValueError("beam_search_v1 requires canonical vllm_gr_config")
        # PR #396 rejects canonical vllm_gr_config combined with legacy GR
        # Python arguments or an explicit legacy attention backend.
        kwargs["vllm_gr_config"] = v1_config
    else:
        kwargs.update(
            beam_graph_enabled=True,
            beam_max_width=beam_width,
            attention_config={"backend": "CUSTOM"},
            catalog_path=str(catalog),
            constraint_backend="constraint_table",
            additional_config=additional_config or None,
        )
    return kwargs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("/opt/vllm-gr/data"))
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("/opt/vllm-gr/test_profilling/video_constraint_triples.json"),
        help="Constraint table triples document for constrained beam decoding.",
    )
    parser.add_argument("--model", default="OpenOneRec/OneRec-1.7B")
    parser.add_argument("--task", default="video")
    parser.add_argument("--num-prompts", type=int, default=100)
    parser.add_argument("--warmup-requests", type=int, default=4)
    parser.add_argument(
        "--diagnostic-prompts",
        type=int,
        default=100,
        help="Requests sampled after canonical measurement for instrumented stage timing; 0 disables it.",
    )
    parser.add_argument("--beam-width", type=int, required=True)
    parser.add_argument("--input-length", type=int, required=True)
    parser.add_argument(
        "--beam-api",
        choices=("legacy", "v1"),
        default="v1",
        help="Beam API/pipeline to benchmark. New daily runs use beam_search_v1.",
    )
    parser.add_argument(
        "--beam-engine-driven",
        default=None,
        metavar="{auto,true,false}",
        help="explicitly set beam_engine_driven (default: auto-detect)",
    )
    args = parser.parse_args()
    if args.beam_engine_driven is not None:
        v = args.beam_engine_driven.strip().lower()
        if v == "auto":
            args.beam_engine_driven = None
        elif v in ("1", "true", "yes", "on"):
            args.beam_engine_driven = True
        elif v in ("0", "false", "no", "off"):
            args.beam_engine_driven = False
        else:
            parser.error("--beam-engine-driven must be one of auto/true/false")
    if (
        min(args.num_prompts, args.beam_width) < 1
        or args.input_length < 2
        or args.warmup_requests < 0
    ):
        parser.error(
            "num-prompts and beam-width must be positive; input-length must be at least 2 "
            "(one token is reserved for <|sid_begin|>); warmup must be non-negative"
        )

    from transformers import AutoConfig, AutoTokenizer

    from benchmarks.open_one_rec.open_one_rec_dataset import OneRecDataset
    from vllm_gr.entrypoints.gr import GRLLM
    from vllm_gr.sampling_params import BeamSearchParams

    started_at = datetime.now(timezone.utc)
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    model_config = AutoConfig.from_pretrained(args.model, trust_remote_code=True)
    sample_size = max(args.num_prompts, args.warmup_requests)
    dataset = OneRecDataset(
        dataset_path=str(args.data_dir),
        task_types=[args.task],
        tokenizer=tokenizer,
        custom_input_len=args.input_length - 1,
        sample_size=sample_size,
    )
    requests = dataset.sample(
        num_requests=sample_size,
        tokenizer=tokenizer,
        no_oversample=True,
    )
    if len(requests) < sample_size:
        raise RuntimeError(f"requested {sample_size} samples, dataset returned {len(requests)}")

    # Pre-encode prompts outside the timed region so GRLLM.beam_search's
    # _preprocess_cmpl does not re-encode inside the perf_counter window.
    prompt_token_ids_list = [
        tokenizer.encode(request.prompt, add_special_tokens=False)
        for request in requests
    ]

    max_tokens = int(requests[0].expected_output_len)
    params = BeamSearchParams(beam_width=args.beam_width, max_tokens=max_tokens, temperature=0.0)
    params.begin_token = "<|sid_begin|>"
    params.end_token = "<|sid_end|>"
    # GR beam search prepends begin_token after the dataset has already shaped
    # the prompt to input_length. Reserve both that token and the full requested
    # generation budget at the 8K/10K boundaries. Align with the abba E2E baseline.
    engine_max_model_len = 40960

    miss_samples: list[dict[str, Any]] = []
    hit_samples: list[dict[str, Any]] = []
    diagnostic_miss_samples: list[dict[str, Any]] = []
    diagnostic_hit_samples: list[dict[str, Any]] = []
    additional_config: dict[str, object] = {}
    if args.beam_engine_driven is not None:
        additional_config["vllm_gr_beam_engine_driven"] = args.beam_engine_driven
        if args.beam_engine_driven:
            additional_config["vllm_gr_beam_worker_decision"] = True
    v1_resource: dict[str, str] | None = None
    v1_config: dict[str, Any] | None = None
    if args.beam_api == "v1":
        v1_config, v1_resource = build_v1_constraint_config(
            catalog=args.catalog,
            tokenizer=tokenizer,
            model_config=model_config,
            artifact=args.output.parent / "constraint-table-v1.safetensors",
            beam_width=args.beam_width,
        )

    engine_kwargs = build_engine_kwargs(
        model=args.model,
        beam_width=args.beam_width,
        max_model_len=engine_max_model_len,
        beam_api=args.beam_api,
        catalog=args.catalog,
        additional_config=additional_config,
        v1_config=v1_config,
    )

    with GRLLM(
        **engine_kwargs,
    ) as llm:
        for prompt_token_ids in prompt_token_ids_list[: args.warmup_requests]:
            if not llm.reset_prefix_cache():
                raise RuntimeError("prefix cache reset failed during warmup")
            # Warm both members of the exact protocol used by canonical
            # sampling. Hit-only graph and constraint paths may initialize
            # lazily and must not first appear in a measured request.
            measure_request_canonical(llm, prompt_token_ids, params, args.beam_api)
            measure_request_canonical(llm, prompt_token_ids, params, args.beam_api)

        measured_requests = requests[: args.num_prompts]
        for index, (request, prompt_token_ids) in enumerate(
            zip(measured_requests, prompt_token_ids_list[: args.num_prompts]), start=1
        ):
            if not llm.reset_prefix_cache():
                raise RuntimeError(f"prefix cache reset failed before measured request {index}")
            miss = measure_request_canonical(llm, prompt_token_ids, params, args.beam_api)
            hit = measure_request_canonical(llm, prompt_token_ids, params, args.beam_api)
            miss_samples.append(miss)
            hit_samples.append(hit)

        # Diagnostic requests deliberately run after every canonical sample so
        # their monkeypatches cannot inflate the official daily E2E trend.
        diagnostic_count = min(max(0, args.diagnostic_prompts), len(measured_requests))
        for index, (request, prompt_token_ids) in enumerate(
            zip(measured_requests[:diagnostic_count], prompt_token_ids_list[:diagnostic_count]), start=1
        ):
            if not llm.reset_prefix_cache():
                raise RuntimeError(f"prefix cache reset failed before diagnostic request {index}")
            diagnostic_miss_samples.append(
                measure_request_diagnostic(llm, prompt_token_ids, params, args.beam_api)
            )
            diagnostic_hit_samples.append(
                measure_request_diagnostic(llm, prompt_token_ids, params, args.beam_api)
            )

    finished_at = datetime.now(timezone.utc)
    fields = (
        "e2e_ms",
        "prefill_ms",
        "decode_ms",
    )
    if args.beam_api == "v1":
        fields += (
            "prefill_gpu_compute_ms",
            "decode_gpu_compute_ms",
            "prefill_device_idle_ms",
            "decode_device_idle_ms",
            "prefill_output_consumed_ms",
            "prefill_dispatch_ms",
            "prefill_cpu_lead_ms",
            "host_overhead_ms",
        )
    raw = {
        "schema_version": "vllm-gr.offline.raw.v2",
        "execution_mode": "offline",
        "beam_api": "beam_search_v1" if args.beam_api == "v1" else "beam_search",
        "beam_execution_mode": args.beam_api,
        "pipeline_version": (
            "beam-search-v1-async-final-output-pr396"
            if args.beam_api == "v1"
            else "legacy-beam-search"
        ),
        "measurement_mode": "canonical-with-posthoc-diagnostic",
        "instrumentation": {
            "canonical": "top-level perf_counter_ns plus final CUDA completion fence; no profiler or internal monkeypatch",
            "diagnostic": (
                "post-canonical V1 one-shot CUDA event pairs bracketing the Prefill and Decode stages on the engine compute stream"
                if args.beam_api == "v1"
                else "post-canonical minimal 2-function legacy Prefill/Decode boundary patch"
            ),
            "canonical_contaminated_by_diagnostic": bool(
                os.environ.get("VLLM_GR_LIGHTWEIGHT_TIMING") == "1"
            ),
        },
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "model": args.model,
        "task": args.task,
        "num_prompts": args.num_prompts,
        "warmup_requests": args.warmup_requests,
        "beam_width": args.beam_width,
        "input_length": args.input_length,
        "max_tokens": max_tokens,
        "max_concurrency": 1,
        "completed": len(miss_samples),
        "failed": 0,
        "input_lens": [int(request.prompt_len) for request in measured_requests],
        "output_lens": [int(sample["aggregate_output_tokens"]) for sample in miss_samples],
        "duration_seconds": sum(float(sample["e2e_ms"]) for sample in miss_samples) / 1000.0,
        "pair_duration_seconds": sum(
            float(miss["e2e_ms"]) + float(hit["e2e_ms"])
            for miss, hit in zip(miss_samples, hit_samples, strict=True)
        )
        / 1000.0,
        "miss": {"e2e_ms": [float(sample["e2e_ms"]) for sample in miss_samples]},
        "hit": {"e2e_ms": [float(sample["e2e_ms"]) for sample in hit_samples]},
        "returned_beams": [int(sample["returned_beams"]) for sample in miss_samples],
        "aggregate_output_tokens": sum(int(sample["aggregate_output_tokens"]) for sample in miss_samples),
        "distributions": {
            f"e2e_ms_{cache_state}": distribution_ms(
                [float(sample["e2e_ms"]) for sample in samples]
            )
            for cache_state, samples in (("miss", miss_samples), ("hit", hit_samples))
        },
        "diagnostic": {
            "num_prompts": len(diagnostic_miss_samples),
            "miss": {
                field: [float(sample[field]) for sample in diagnostic_miss_samples]
                for field in fields
            },
            "hit": {
                field: [float(sample[field]) for sample in diagnostic_hit_samples]
                for field in fields
            },
            "distributions": ({
                f"{phase}_{cache_state}": distribution_ms(
                    [float(sample[phase]) for sample in samples]
                )
                for cache_state, samples in (
                    ("miss", diagnostic_miss_samples),
                    ("hit", diagnostic_hit_samples),
                )
                for phase in fields
            } if diagnostic_miss_samples else {}),
            "phase_definition": {
                "version": (
                    "vllm-gr-beam-search-v1-gpu-compute-v5"
                    if args.beam_api == "v1"
                    else "vllm-gr-serving-internal-v4-minimal"
                ),
                "e2e": f"instrumented direct {args.beam_api} call start through return",
                "prefill": (
                    "CUDA event interval on the engine compute stream: entry to the PREFILL GPUBeamStageRunner.execute through output-producing PREFILL sample completion; excludes frontend dispatch"
                    if args.beam_api == "v1"
                    else "internal beam token-loop start through completion of token 0"
                ),
                "decode": (
                    "CUDA event interval on the engine compute stream: entry to the first DECODE GPUBeamStageRunner.execute through the last Decode sample completion; excludes Prefill by stream ordering and includes device idle inside Decode"
                    if args.beam_api == "v1"
                    else "start of token 1 preparation through beam_search return"
                ),
                "prefill_gpu_compute": (
                    "sum of CUDA event intervals from the first Prefill model-forward enqueue through the PREFILL GPUBeamStageRunner.execute return, plus the output-producing sample/beam initialization window; excludes dispatch and the execute-to-sample gap, and is an upper bound on Prefill GPU busy time because the host tail after the last enqueue falls inside it"
                    if args.beam_api == "v1"
                    else None
                ),
                "decode_gpu_compute": (
                    "sum of per-stage CUDA event intervals covering Decode graph replay and sample/beam advance, or the eager Decode forward when the beam graph is disabled; excludes CPU prepare, inter-stage queue gaps, and the per-dispatch model.compute_logits that runs outside the replayed graph and therefore lands in Decode device idle"
                    if args.beam_api == "v1"
                    else None
                ),
                "device_idle": (
                    "device stage span minus the measured GPU compute clusters; a residual rather than an independent measurement, holding CPU launch bubbles, queue waits, and any GPU work outside those windows"
                    if args.beam_api == "v1"
                    else None
                ),
                "prefill_dispatch": (
                    "host wall from the post-synchronize submit anchor through entry to the PREFILL GPUBeamStageRunner.execute; a subset of host_overhead, so it must not be added to prefill_gpu_compute, decode_gpu_compute, and host_overhead"
                    if args.beam_api == "v1"
                    else None
                ),
                "prefill_cpu_lead": (
                    "host look-ahead depth: how far the CPU had advanced into first-Decode preparation before the GPU finished Prefill"
                    if args.beam_api == "v1"
                    else None
                ),
                "host_overhead": (
                    "e2e minus both device intervals: frontend dispatch, EngineCore queueing, and terminal collection; the asynchronous-scheduling signal, and the one host term Prefill dispatch is already inside"
                    if args.beam_api == "v1"
                    else None
                ),
                "prefill_output_consumed": (
                    "diagnostic only: submit boundary through completion of the output-producing PREFILL AsyncGPUBeamOutput; includes async lookahead queue delay"
                    if args.beam_api == "v1"
                    else None
                ),
                "average": "arithmetic mean over post-canonical diagnostic observations",
                "decode_cache_semantics": "common phase; miss/hit are repeated observations, not additive components",
                "device_state_semantics": (
                    "Prefill and Decode are CUDA device-timeline intervals on a single FIFO compute stream; they are additive and sum with host_overhead back to e2e"
                    if args.beam_api == "v1"
                    else "phase boundaries are host-side; the intervals are not device measurements"
                ),
            },
        },
        "phase_definition": {
            "version": (
                "vllm-gr-canonical-beam-search-v1-e2e-v1"
                if args.beam_api == "v1"
                else "vllm-gr-canonical-e2e-v1"
            ),
            "e2e": (
                "unmodified direct GRLLM.beam_search_v1 call start through CUDA-complete return"
                if args.beam_api == "v1"
                else "unmodified direct GRLLM.beam_search call start through CUDA-complete return"
            ),
            "cache_protocol": "paired cold miss followed by identical warm hit",
            "internal_stages": "reported separately from post-canonical diagnostic samples",
        },
        "engine_config": {
            "attention_backend": "CUSTOM",
            "beam_graph_enabled": True,
            "beam_max_width": args.beam_width,
            "max_logprobs": args.beam_width,
            "max_num_seqs": engine_kwargs["max_num_seqs"],
            "max_num_batched_tokens": 40960,
            "max_model_len": engine_max_model_len,
            "scheduling_policy": "fcfs",
            "gpu_memory_utilization": 0.5,
            "catalog_path": str(args.catalog),
            "constraint_backend": "constraint_table",
            "beam_api": "beam_search_v1" if args.beam_api == "v1" else "beam_search",
            "beam_execution_mode": args.beam_api,
            "pipeline_version": (
                "beam-search-v1-async-final-output-pr396"
                if args.beam_api == "v1"
                else "legacy-beam-search"
            ),
            "constraint_resource": v1_resource,
            "single_request": True,
            "beam_engine_driven": args.beam_engine_driven,
            "prefill_graph_kv_bound": os.environ.get("VLLM_GR_PREFILL_GRAPH_KV_BOUND"),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "beam_width": args.beam_width,
                "input_length": args.input_length,
                "e2e_miss_p50_ms": raw["distributions"]["e2e_ms_miss"]["p50"],
                "e2e_hit_p50_ms": raw["distributions"]["e2e_ms_hit"]["p50"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

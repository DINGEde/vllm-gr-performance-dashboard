#!/usr/bin/env python3
"""Run paired cold/hit OneRec beam-search measurements through GRLLM offline."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import random
import statistics
import time
import torch
from datetime import datetime, timezone
from functools import partial
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from contextlib import ExitStack
from unittest import mock

# Align with the abba E2E baseline: run the V1 engine single-process (driver +
# EngineCore in one process) to avoid the ZMQ/TCP IPC round-trip per request.
os.environ.setdefault("VLLM_ENABLE_V1_MULTIPROCESSING", "0")
# Standalone invocations default to V1; the daily matrix selects each runner
# explicitly. V2 collects canonical E2E only, without V1 stage probes.
os.environ.setdefault("VLLM_USE_V2_MODEL_RUNNER", "0")

# Mirrors vllm_gr.config.MAX_CANDIDATE_WIDTH / CONSTRAINT_TABLE_MAX_DECODE_STEPS.
# The RecConstrainedTopK operator caps the per-row candidate count at 256, and a
# constraint table serves at most three generated steps.
MAX_CANDIDATE_WIDTH = 256
MAX_DECODE_STEPS = 3


def runner_identity(worker: Any) -> str:
    """Read the actual worker class before timing; never infer it from the env."""
    cls = type(worker.model_runner)
    return f"{cls.__module__}.{cls.__name__}"


def verify_model_runner(llm: Any, requested: str) -> list[str]:
    classes = llm.collective_rpc(runner_identity)
    expected = (
        "vllm.v1.worker.gpu.model_runner.GPUModelRunner"
        if requested == "v2"
        else "vllm.v1.worker.gpu_model_runner.GPUModelRunner"
    )
    if not classes or any(name != expected for name in classes):
        raise RuntimeError(f"requested Model Runner {requested}, actual workers: {classes}")
    return classes


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


def parse_int_list(name: str, raw: str | None) -> list[int] | None:
    """Parse one comma-separated integer schedule from the command line."""
    if raw is None:
        return None
    parts = [part.strip() for part in raw.split(",")]
    if not parts or any(not part for part in parts):
        raise ValueError(f"{name} must be a comma-separated list of integers")
    try:
        return [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"{name} must be a comma-separated list of integers") from exc


def build_beam_schedule(
    *,
    beam_width: int,
    generated_steps: int,
    dynamic_beam_widths: list[int] | None = None,
    per_parent_beam_widths: list[int] | None = None,
    result_width: int | None = None,
) -> dict[str, Any]:
    """Validate a per-stage beam schedule and derive the engine knobs from it.

    ``beam_width`` is the request's storage capacity; ``dynamic_beam_widths``
    (retained winners per generated token) and ``per_parent_beam_widths``
    (candidates expanded per parent at that token) each carry one entry per
    generated token. The first per-parent width must equal the first retained
    width, and every later stage must be reachable from its predecessor:
    ``retained[i] <= retained[i-1] * per_parent[i]``.

    The per-row candidate count is capped by the operator ceiling
    (``MAX_CANDIDATE_WIDTH``), not by the beam width, which is what lets a beam
    of 1024 run with decoupled per-parent widths. A wider beam is therefore only
    accepted when the schedule states that candidate width explicitly.
    """
    if not 1 <= generated_steps <= MAX_DECODE_STEPS:
        raise ValueError(
            f"max_tokens must be in [1, {MAX_DECODE_STEPS}] generated tokens, "
            f"got {generated_steps}"
        )
    retained = dynamic_beam_widths
    if retained is not None:
        if len(retained) != generated_steps:
            raise ValueError(
                "--dynamic-beam-widths needs one width per generated token "
                f"({generated_steps}), got {len(retained)}"
            )
        if any(width < 1 or width > beam_width for width in retained):
            raise ValueError(
                f"--dynamic-beam-widths values must be in [1, beam-width={beam_width}]"
            )
    per_parent = per_parent_beam_widths
    if per_parent is not None:
        if len(per_parent) != generated_steps:
            raise ValueError(
                "--per-parent-beam-widths needs one width per generated token "
                f"({generated_steps}), got {len(per_parent)}"
            )
        if any(width < 1 or width > MAX_CANDIDATE_WIDTH for width in per_parent):
            raise ValueError(
                "--per-parent-beam-widths values must be in "
                f"[1, {MAX_CANDIDATE_WIDTH}]"
            )
        bootstrap = retained[0] if retained is not None else beam_width
        if per_parent[0] != bootstrap:
            raise ValueError(
                "the first per-parent beam width must equal the first retained "
                f"width ({bootstrap}), got {per_parent[0]}"
            )
        later = retained[1:] if retained is not None else [beam_width] * (generated_steps - 1)
        input_width = bootstrap
        for index, (parents, width) in enumerate(zip(per_parent[1:], later), start=2):
            if width > input_width * parents:
                raise ValueError(
                    f"stage {index} retains {width} beams from {input_width} parents "
                    f"x {parents} candidates; raise --per-parent-beam-widths stage {index}"
                )
            input_width = width
    final_width = retained[-1] if retained is not None else beam_width
    if result_width is not None and not 1 <= result_width <= final_width:
        raise ValueError(
            f"--result-width must be in [1, final retained width {final_width}], "
            f"got {result_width}"
        )
    if per_parent is not None:
        candidate_width = max(per_parent)
    elif retained is not None:
        candidate_width = max(retained)
    else:
        candidate_width = beam_width
    if candidate_width > MAX_CANDIDATE_WIDTH:
        raise ValueError(
            f"per-row candidate width {candidate_width} exceeds the "
            f"{MAX_CANDIDATE_WIDTH} operator ceiling; pass "
            "--per-parent-beam-widths to decouple candidates from the beam width"
        )
    return {
        "generated_steps": generated_steps,
        "dynamic_beam_widths": retained,
        "per_parent_beam_widths": per_parent,
        "result_width": result_width,
        "final_width": final_width,
        "candidate_width": candidate_width,
    }


def resolve_beam_call(
    llm: Any, beam_api: str,
) -> tuple[str, Callable[..., list[Any]], str]:
    """Resolve the public method and its output contract before taking samples."""
    if beam_api == "v1":
        method = getattr(llm, "beam_search_v1", None)
        method_name = "beam_search_v1"
        if not callable(method):
            method = llm.beam_search
            method_name = "beam_search"
        if "output_mode" in inspect.signature(method).parameters:
            return method_name, partial(method, concurrency_limit=1, output_mode="compact"), "compact"
        if method_name == "beam_search":
            raise RuntimeError("the selected beam_search does not support persistent compact output")
    else:
        method = llm.beam_search
        method_name = "beam_search"
    # Older persistent SHAs predate compact output. Keep their original return
    # mode, as with the separate legacy pipeline.
    return method_name, partial(method, concurrency_limit=1), "full"


def _run_beam_call(
    beam_call: Callable[..., list[Any]],
    prompt_token_ids_list: list[list[int]],
    params: Any,
) -> list[Any]:
    """Submit one logical batch through the selected Beam API."""
    return beam_call(
        [{"prompt_token_ids": prompt} for prompt in prompt_token_ids_list],
        params,
    )


def measure_request_canonical(
    beam_call: Callable[..., list[Any]],
    prompt_token_ids_list: list[list[int]],
    params: Any,
) -> dict[str, Any]:
    """Measure the production trend with no internal monkeypatches or profiler.

    Keep the timed region deliberately small: one direct Beam API call followed
    by a device completion fence. The API's output construction is timed.
    Counting the returned sequences happens after the clock stops.
    Any diagnostic decomposition is collected only after all canonical samples.

    The call carries the whole batch, so ``e2e_ms`` is the wall time of the
    batched call rather than of a single request; ``returned_beams`` and
    ``aggregate_output_tokens`` sum over every request in the batch, which
    keeps the throughput numerators correct at any batch size.
    """
    started = time.perf_counter_ns()
    outputs = _run_beam_call(beam_call, prompt_token_ids_list, params)
    torch.cuda.synchronize()
    finished = time.perf_counter_ns()
    if len(outputs) != len(prompt_token_ids_list):
        raise RuntimeError(
            f"expected {len(prompt_token_ids_list)} offline output(s), "
            f"received {len(outputs)}"
        )
    return {
        "e2e_ms": (finished - started) / 1_000_000.0,
        "returned_beams": sum(len(output.sequences) for output in outputs),
        "sequences_per_request": [len(output.sequences) for output in outputs],
        "aggregate_output_tokens": sum(
            len(output.sequences) * int(params.max_tokens) for output in outputs
        ),
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


def _import_beam_diagnostic_module(module: str, previous_module: str) -> Any:
    try:
        return import_module(module)
    except ModuleNotFoundError as exc:
        if not exc.name or (exc.name != module and not module.startswith(f"{exc.name}.")):
            raise
        return import_module(previous_module)


def _measure_request_diagnostic_v1(
    beam_call: Callable[..., list[Any]],
    prompt_token_ids_list: list[list[int]],
    params: Any,
) -> dict[str, Any]:
    """Measure persistent Beam Search phases on the CUDA device timeline.

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

    ``prompt_token_ids_list`` carries one prompt for a single-request scenario
    and the whole logical batch otherwise. Every anchor below is keyed on a
    phase dispatch or a stage completion, and a batched call still issues
    exactly one Prefill and one Decode dispatch serving all of its requests, so
    the same decomposition applies unchanged; only the output-count assertion
    and the aggregation of the returned beams are batch aware.
    """
    from vllm_gr.v1.worker.prefill_graph_runner import (
        get_prefill_graph_runner,
        is_prefill_graph_enabled,
    )
    BeamSearchClient = _import_beam_diagnostic_module(
        "vllm_gr.entrypoints.beam_search.client",
        "vllm_gr.entrypoints.beam_search_v1.client",
    ).BeamSearchClient
    stage_module = _import_beam_diagnostic_module(
        "vllm_gr.v1.worker.cuda.gpu_beam_stage_runner",
        "vllm_gr.v1.worker.gpu_beam_stage_runner",
    )
    AsyncGPUBeamOutput = stage_module.AsyncGPUBeamOutput
    GPUBeamStageRunner = stage_module.GPUBeamStageRunner
    BeamDecodeGraph = _import_beam_diagnostic_module(
        "vllm_gr.v1.worker.cuda.beam_decode_graph",
        "vllm_gr.v1.worker.beam_decode_graph",
    ).BeamDecodeGraph
    # Before unified startup configuration, this gate read a cached environment
    # value. Newer versions need the actual ModelRunner's configuration.
    prefill_graph_enabled = (
        is_prefill_graph_enabled
        if inspect.signature(is_prefill_graph_enabled).parameters
        else lambda _runner: is_prefill_graph_enabled()
    )
    use_v2 = os.environ.get("VLLM_USE_V2_MODEL_RUNNER") == "1"
    if not use_v2:
        from vllm.v1.worker.gpu_model_runner import GPUModelRunner

    original_submit = BeamSearchClient.submit_once
    original_wait = BeamSearchClient.wait_final
    original_get_output = AsyncGPUBeamOutput.get_output
    original_execute = GPUBeamStageRunner.execute
    original_sample = GPUBeamStageRunner.sample
    original_forward = GPUBeamStageRunner._forward
    original_decode_replay = BeamDecodeGraph.replay
    original_model_forward = None if use_v2 else GPUModelRunner._model_forward
    # The Prefill anchor is only meaningful while it wraps GR's own interceptor:
    # that interceptor is what picks between a Prefill CUDA graph replay and the
    # eager forward, and it is installed on the runner class during engine
    # startup. Keeping the timing wrapper outside it is what makes the anchor
    # fire in graph mode; a reordering that leaves the timing wrapper innermost
    # would silently time the eager fallback only, so fail here instead.
    if not use_v2 and not getattr(original_model_forward, "_vllm_gr_prefill_model_forward_patch", False):
        raise RuntimeError(
            "GPUModelRunner._model_forward is not wrapped by the vllm-gr prefill "
            "graph interceptor; the Prefill timing anchor would miss graph replays"
        )
    beam_started: int | None = None
    prefill_stage_start_event: Any | None = None
    prefill_end_event: Any | None = None
    prefill_model_start_event: Any | None = None
    prefill_execute_end_event: Any | None = None
    prefill_compute_pairs: list[tuple[Any, Any]] = []
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
        nonlocal prefill_model_start_event, prefill_execute_end_event
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
        with ExitStack() as hooks:
            if use_v2 and "prefill" in kinds:
                prefill_model_start_event = None
                worker = self.worker
                manager = worker.cudagraph_manager
                if manager is not None:
                    replay = manager.run_fullgraph

                    def timed_prefill_replay(*args: Any, **kwargs: Any) -> Any:
                        mark_prefill_forward()
                        return replay(*args, **kwargs)

                    hooks.enter_context(mock.patch.object(manager, "run_fullgraph", timed_prefill_replay))
                handle = worker.model.register_forward_pre_hook(mark_prefill_forward)
                hooks.callback(handle.remove)
            result = original_execute(self, scheduler_output, *a, **k)
        if "prefill" in kinds and (use_v2 or prefill_execute_end_event is None):
            prefill_execute_end_event = record_timing_event(self)
            if use_v2:
                if prefill_model_start_event is None:
                    raise RuntimeError("Model Runner V2 Prefill forward or graph replay not observed")
                prefill_compute_pairs.append((prefill_model_start_event, prefill_execute_end_event))
            # A bucket miss degrades Prefill to eager silently, which would make
            # this sample's Prefill window incomparable with the trend. Capture
            # itself is startup-only (``capture_prefill_graphs`` is guarded by
            # ``_prefill_graphs_captured``), so a capture can never land inside
            # the measured window; ``kv_length_exceeds_bound`` is a legitimate
            # fallback, while a missing captured graph means the bucket grid and
            # the measured prompt lengths disagree.
            if not use_v2 and prefill_graph_enabled(self.worker) and getattr(
                get_prefill_graph_runner(self.worker), "last_replay_reason", None
            ) == "graph_not_captured":
                raise RuntimeError(
                    "Beam Search Prefill fell back to eager because no CUDA graph "
                    "was captured for the scheduled bucket; the Prefill window is not "
                    "comparable with the graph-replay trend"
                )
        return result

    def mark_prefill_forward(*_args: Any) -> None:
        nonlocal prefill_model_start_event
        if prefill_model_start_event is None:
            prefill_model_start_event = record_timing_event()

    def timed_model_forward(self: Any, *a: Any, **k: Any) -> Any:
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
            mark_prefill_forward()
        return original_model_forward(self, *a, **k)

    def timed_decode_replay(self: Any, *a: Any, **k: Any) -> Any:
        start = record_timing_event()
        result = original_decode_replay(self, *a, **k)
        decode_compute_pairs.append((start, record_timing_event()))
        return result

    def timed_forward(self: Any, *a: Any, **k: Any) -> Any:
        # The stage runner captures with ``_forward_hidden`` and uses
        # ``_forward`` only for eager Decode, including graph fallbacks.
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
        ExitStack() as hooks,
        mock.patch.object(BeamSearchClient, "submit_once", timed_submit),
        mock.patch.object(BeamSearchClient, "wait_final", timed_wait),
        mock.patch.object(AsyncGPUBeamOutput, "get_output", timed_get_output),
        mock.patch.object(GPUBeamStageRunner, "execute", timed_execute),
        mock.patch.object(GPUBeamStageRunner, "sample", timed_sample),
        mock.patch.object(GPUBeamStageRunner, "_forward", timed_forward),
        mock.patch.object(BeamDecodeGraph, "replay", timed_decode_replay),
    ):
        if not use_v2:
            hooks.enter_context(mock.patch.object(GPUModelRunner, "_model_forward", timed_model_forward))
        # _run_beam_call takes the batch dimension explicitly, so the caller
        # passes a list of prompts -- one entry for a single-request scenario --
        # rather than a flat token list, which it would iterate as one prompt
        # per token.
        outputs = _run_beam_call(beam_call, prompt_token_ids_list, params)
    request_finished = time.perf_counter_ns()

    if beam_started is None:
        raise RuntimeError("Beam Search submit boundary was not observed")
    if first_decode_started is None or decode_start_event is None:
        raise RuntimeError("Beam Search first Decode boundary was not observed")
    if (
        prefill_stage_start_event is None
        or prefill_end_event is None
        or prefill_model_start_event is None
        or prefill_execute_end_event is None
        or prefill_sample_pair is None
        or prefill_worker_started is None
    ):
        raise RuntimeError("Beam Search Prefill GPU boundaries were not observed")
    if decode_end_event is None:
        raise RuntimeError("Beam Search terminal Decode GPU boundary was not observed")
    if prefill_output_consumed is None:
        raise RuntimeError("Beam Search output-producing Prefill consumption was not observed")
    if beam_finished is None:
        raise RuntimeError("Beam Search terminal result boundary was not observed")
    if not beam_started <= first_decode_started <= beam_finished:
        raise RuntimeError("Beam Search Decode timestamps are not monotonic")
    if len(outputs) != len(prompt_token_ids_list):
        raise RuntimeError(
            f"expected {len(prompt_token_ids_list)} offline output(s), "
            f"received {len(outputs)}"
        )
    if len(observed_streams) != 1:
        raise RuntimeError(
            "Beam Search phase events landed on multiple CUDA streams; "
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
            "Beam Search Prefill model-forward and sample windows overlap; "
            "the pending gate or an event placement changed"
        )
    prefill_ms = float(prefill_stage_start_event.elapsed_time(prefill_end_event))
    decode_ms = float(decode_start_event.elapsed_time(decode_end_event))
    if not use_v2:
        prefill_compute_pairs.append((prefill_model_start_event, prefill_execute_end_event))
    prefill_gpu_compute_ms = sum(
        float(start.elapsed_time(end)) for start, end in [*prefill_compute_pairs, prefill_sample_pair]
    )
    decode_gpu_compute_ms = float(
        sum(start.elapsed_time(end) for start, end in decode_compute_pairs)
    )
    if prefill_gpu_compute_ms > prefill_ms + 1e-3 or decode_gpu_compute_ms > decode_ms + 1e-3:
        raise RuntimeError(
            "Beam Search stage GPU compute exceeds its device span: "
            f"prefill={prefill_gpu_compute_ms:.3f}/{prefill_ms:.3f}ms "
            f"decode={decode_gpu_compute_ms:.3f}/{decode_ms:.3f}ms"
        )
    prefill_device_idle_ms = prefill_ms - prefill_gpu_compute_ms
    decode_device_idle_ms = decode_ms - decode_gpu_compute_ms
    e2e_ms = (request_finished - request_started) / 1_000_000.0
    host_overhead_ms = e2e_ms - prefill_ms - decode_ms
    if host_overhead_ms < -0.5:
        raise RuntimeError(
            "Beam Search device phases exceed the end-to-end wall time: "
            f"prefill={prefill_ms:.3f}ms decode={decode_ms:.3f}ms e2e={e2e_ms:.3f}ms"
        )
    # Map the Prefill device completion onto the host clock so the CPU look-ahead
    # depth shares a time base with first_decode_started.
    prefill_gpu_ready = prefill_worker_started + round(prefill_ms * 1_000_000.0)
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
        "returned_beams": sum(len(output.sequences) for output in outputs),
        "aggregate_output_tokens": sum(
            len(output.sequences) * int(params.max_tokens) for output in outputs
        ),
    }


def measure_request_diagnostic(
    llm: Any,
    prompt_token_ids_list: list[list[int]],
    params: Any,
    beam_api: str,
    beam_call: Callable[..., list[Any]],
) -> dict[str, Any]:
    if beam_api == "v1":
        return _measure_request_diagnostic_v1(beam_call, prompt_token_ids_list, params)
    if len(prompt_token_ids_list) != 1:
        raise RuntimeError(
            "the legacy beam_search diagnostic measures one request per call"
        )
    return _measure_request_diagnostic_legacy(llm, prompt_token_ids_list[0], params)


def build_v1_constraint_config(
    *,
    catalog: Path,
    tokenizer: Any,
    model_config: Any,
    artifact: Path,
    beam_width: int,
    candidate_width: int,
    max_decode_steps: int,
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
    constraint_table: dict[str, Any] = {
        "enabled": True,
        "backend": "cuda",
        "max_top_k": candidate_width,
        "path": str(artifact.resolve()),
        "format": "constraint_table_v1",
        "artifact_digest": artifact_digest,
        "tokenizer_digest": tokenizer_digest,
    }
    # K follows the beam width when this stays unset, so only a decoupled
    # candidate width is stated explicitly; that keeps every historical row's
    # engine configuration byte-identical.
    if candidate_width != beam_width:
        constraint_table["candidate_width"] = candidate_width
    config = {
        "schema_version": 1,
        "attention_backend": "CUSTOM",
        "beam": {
            "execution_mode": "v1",
            "graph_enabled": True,
            "worker_decision": True,
            "max_width": beam_width,
            "max_decode_steps": max_decode_steps,
            # PR #457: fused short-suffix attention + prefix merge. Enabled on
            # every daily row so the trend measures the current engine path.
            "enable_short_suffix_attention": True,
        },
        "constraint_table": constraint_table,
    }
    return config, {
        "path": str(artifact.resolve()),
        "artifact_digest": artifact_digest,
        "tokenizer_digest": tokenizer_digest,
    }


def build_gr_constraint_config(
    v1_config: dict[str, Any], schedule: dict[str, Any],
) -> dict[str, Any]:
    """Translate the selected CUDA table and per-stage widths to the gr API."""
    beam = v1_config["beam"]
    table = v1_config["constraint_table"]
    steps = schedule["generated_steps"]
    widths = schedule["dynamic_beam_widths"] or [beam["max_width"]] * steps
    candidates = schedule["per_parent_beam_widths"] or widths
    if max(widths) != beam["max_width"]:
        raise ValueError("--beam-width must equal the maximum retained width on this vllm-gr revision")
    # Table startup requires a three-stage schedule. Shorter requests still
    # declare and use only their requested stages in BeamSearchParams.
    padding = MAX_DECODE_STEPS - steps
    return {
        "beam_width": widths + [widths[-1]] * padding,
        "candidate_width": candidates + [candidates[-1]] * padding,
        "result_width": schedule["result_width"],
        "max_decode_steps": MAX_DECODE_STEPS,
        "worker_decision": beam["worker_decision"],
        "beam_graph_enabled": beam["graph_enabled"],
        "enable_short_suffix_attention": beam["enable_short_suffix_attention"],
        "constraint_mode": "table",
        "constraint_table": {
            "backend": table["backend"],
            "format": table["format"],
            "path": table["path"],
            "max_top_k": table["max_top_k"],
            "digests": {
                "artifact": table["artifact_digest"],
                "tokenizer": table["tokenizer_digest"],
            },
        },
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
    batch_size: int = 1,
    candidate_width: int | None = None,
    gr_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one unambiguous GR configuration source for the selected API."""
    # The sampler is asked for the per-row candidate count, which follows the
    # beam width unless a decoupled schedule states it.
    max_logprobs = beam_width if candidate_width is None else candidate_width
    # V1 sizes the scheduler by the logical batch: the prefill CUDA graph grid
    # is filtered by ``max_num_seqs`` (GPU captures the exact request counts in
    # PREFILL_GRAPH_CAPTURE_NUM_REQS, i.e. 1, 2 and 4), and the engine refuses to
    # schedule more requests than this at once. At batch size 1 this must stay
    # exactly 1 so the single-batch trend is unchanged.
    kwargs: dict[str, Any] = {
        "model": model,
        "trust_remote_code": True,
        "max_logprobs": max_logprobs,
        "max_num_seqs": batch_size if beam_api == "v1" else max(128, beam_width),
        "max_num_batched_tokens": 40960,
        "max_model_len": max_model_len,
        "scheduling_policy": "fcfs",
        "async_scheduling": True,
        "enable_prefix_caching": True,
        "enable_chunked_prefill": True,
        "gpu_memory_utilization": 0.5,
    }
    if beam_api == "v1":
        kwargs["enable_chunked_prefill"] = False
        if gr_config is not None:
            kwargs.update(gr=gr_config, attention_config={"backend": "CUSTOM"})
        elif v1_config is not None:
            # PR #396 rejects this schema together with legacy GR arguments.
            kwargs["vllm_gr_config"] = v1_config
        else:
            raise ValueError("persistent Beam Search requires a canonical constraint table config")
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


def configure_prefill_graph(
    engine_kwargs: dict[str, Any],
    *,
    model_runner: str,
    batch_size: int,
    input_length: int,
) -> dict[str, Any]:
    """Apply the daily capture policy and return the resolved graph settings."""
    kv_bound_env = os.environ.get("VLLM_GR_PREFILL_GRAPH_KV_BOUND", "").strip()
    kv_bound = int(kv_bound_env) if kv_bound_env else None
    if kv_bound is not None and kv_bound <= 0:
        raise ValueError("VLLM_GR_PREFILL_GRAPH_KV_BOUND must be a positive integer")
    enabled = os.environ.get("VLLM_ENABLE_PREFILL_CUDAGRAPH", "").strip().lower() not in (
        "0", "false", "no", "off",
    )
    if model_runner == "v2":
        # Pre-gr versions let this environment grid override native V2 sizes.
        os.environ.pop("VLLM_GR_PREFILL_GRAPH_MAX_BUCKET", None)
        os.environ.pop("VLLM_GR_PREFILL_GRAPH_BUCKET_STEP", None)
        # Capture the full batch plus small cached suffixes. A full V1 grid
        # wastes the KV budget when V2 profiles native FULL graphs.
        buckets = sorted({
            1, 2, 4, 8, 16, 32, 64, 128,
            ((batch_size * input_length + 127) // 128) * 128,
        })
        engine_kwargs["compilation_config"] = {"cudagraph_capture_sizes": buckets}
        step = None
    else:
        # The daily shell widens this grid for batches above the usual 8192.
        buckets = parse_int_list(
            "VLLM_GR_PREFILL_GRAPH_BUCKETS", os.environ.get("VLLM_GR_PREFILL_GRAPH_BUCKETS")
        )
        if buckets is None:
            step = int(os.environ.get("VLLM_GR_PREFILL_GRAPH_BUCKET_STEP", "128"))
            limit = int(os.environ.get("VLLM_GR_PREFILL_GRAPH_MAX_BUCKET", "8192"))
            if step <= 0 or limit < step or limit % step:
                raise ValueError("Prefill graph maximum bucket must be a positive multiple of its step")
            buckets = list(range(step, limit + 1, step))
        else:
            if min(buckets) <= 0 or buckets != sorted(set(buckets)):
                raise ValueError("VLLM_GR_PREFILL_GRAPH_BUCKETS must be strictly increasing and positive")
            step = None
        if kv_bound is not None:
            buckets = sorted(set(buckets) | {n for n in (1, 16) if n <= buckets[-1]})

    capture_batch_sizes = parse_int_list(
        "VLLM_GR_PREFILL_GRAPH_CAPTURE_NUM_REQS",
        os.environ.get("VLLM_GR_PREFILL_GRAPH_CAPTURE_NUM_REQS"),
    ) or [1, 2, 4]
    if "gr" in engine_kwargs:
        engine_kwargs["gr"].update(
            prefill_graph_enabled=enabled,
            # A GR list takes precedence over native V2 compilation settings.
            prefill_token_buckets=None if model_runner == "v2" else buckets,
            prefill_kv_bound=kv_bound,
            beam_batch_sizes=capture_batch_sizes,
        )
    return {
        "prefill_graph_enabled": enabled,
        "prefill_token_buckets": buckets,
        "prefill_graph_max_bucket": max(buckets),
        "prefill_graph_bucket_step": step,
        "prefill_graph_kv_bound": kv_bound,
        "prefill_graph_capture_num_reqs": [b for b in capture_batch_sizes if b <= batch_size],
    }


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
        "--batch-size",
        type=int,
        default=1,
        choices=(1, 2, 4),
        help=(
            "Requests per logical beam batch. 1 keeps the original one-prompt-per-call "
            "protocol byte for byte; 2 and 4 both have a captured GPU Prefill CUDA graph "
            "(PREFILL_GRAPH_CAPTURE_NUM_REQS = (1, 2, 4)), so every offered size stays on "
            "the graphed path. No other width is captured, and an uncaptured one falls "
            "back to eager Prefill without a warning."
        ),
    )
    parser.add_argument(
        "--beam-api",
        choices=("legacy", "v1"),
        default="v1",
        help="Beam pipeline to benchmark. v1 selects persistent Beam Search across API revisions.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help=(
            "Generated recommendation tokens per request, i.e. the caller-facing "
            "max_tokens. The begin/end boundary tokens are added on top, so this "
            "stays the number the customer asked for. Defaults to the dataset's "
            "expected_output_len minus those boundary tokens."
        ),
    )
    parser.add_argument(
        "--dynamic-beam-widths",
        metavar="W1[,W2,W3]",
        help=(
            "Retained beam width after each generated token (one value per "
            "generated token). Omit for a fixed beam at --beam-width."
        ),
    )
    parser.add_argument(
        "--per-parent-beam-widths",
        metavar="K1[,K2,K3]",
        help=(
            "Candidates expanded per parent beam at each generated token (one "
            "value per generated token, each <= 256). The first value must equal "
            "the first retained width. This is what decouples the per-row "
            "candidate count from the beam width."
        ),
    )
    parser.add_argument(
        "--result-width",
        type=int,
        default=None,
        help=(
            "Returned sequences per request. Defaults to the final retained "
            "width, or --beam-width when no schedule is given."
        ),
    )
    parser.add_argument(
        "--synthetic-prompts",
        action="store_true",
        help=(
            "Generate deterministic prompts instead of reading the gated OneRec "
            "parquet. Marks the raw result as a synthetic smoke run; pair it with "
            "generate_summary.py --dataset-kind synthetic-smoke."
        ),
    )
    parser.add_argument(
        "--synthetic-prompt-file",
        type=Path,
        default=None,
        help=(
            "Prompt text to derive synthetic token sequences from (cycled to the "
            "requested input length). Without it, ids are drawn deterministically "
            "from the model vocabulary."
        ),
    )
    parser.add_argument(
        "--synthetic-output-len",
        type=int,
        default=5,
        help=(
            "Public max_tokens for synthetic prompts (default 5, i.e. three "
            "generated tokens plus the begin/end boundary positions)."
        ),
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
    try:
        args.dynamic_beam_widths = parse_int_list(
            "--dynamic-beam-widths", args.dynamic_beam_widths
        )
        args.per_parent_beam_widths = parse_int_list(
            "--per-parent-beam-widths", args.per_parent_beam_widths
        )
    except ValueError as exc:
        parser.error(str(exc))
    if (
        min(args.num_prompts, args.beam_width) < 1
        or args.input_length < 2
        or args.warmup_requests < 0
    ):
        parser.error(
            "num-prompts and beam-width must be positive; input-length must be at least 2 "
            "(one token is reserved for <|sid_begin|>); warmup must be non-negative"
        )
    if args.num_prompts % args.batch_size:
        # Keep the request accounting exact: a partial trailing batch would make
        # requests.completed and the throughput numerator disagree.
        parser.error(
            f"--num-prompts ({args.num_prompts}) must be a multiple of "
            f"--batch-size ({args.batch_size})"
        )
    if args.batch_size > 1 and args.beam_api != "v1":
        # The legacy beam_search adapter chunks its prompt list by
        # concurrency_limit, so a multi-prompt call would silently become N
        # serial single-prompt calls and measure nothing useful.
        parser.error("--batch-size above 1 requires --beam-api v1")

    from transformers import AutoConfig, AutoTokenizer

    from benchmarks.open_one_rec.open_one_rec_dataset import OneRecDataset
    from vllm_gr.entrypoints.gr import GRLLM
    from vllm_gr.sampling_params import BeamSearchParams

    uses_gr_config = "gr" in inspect.signature(GRLLM).parameters
    if uses_gr_config and args.beam_api == "legacy":
        parser.error("the legacy Beam Search pipeline is unavailable on this vllm-gr revision")

    started_at = datetime.now(timezone.utc)
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    model_config = AutoConfig.from_pretrained(args.model, trust_remote_code=True)
    sample_size = max(args.num_prompts, args.warmup_requests)

    if args.synthetic_prompts:
        # Smoke path for hosts without the gated OneRec parquet: same engine,
        # same schedule, generated prompts. The summary marks it synthetic and
        # keeps it out of the baseline/trend series.
        seed_ids: list[int] = []
        if args.synthetic_prompt_file is not None:
            seed_ids = tokenizer.encode(
                args.synthetic_prompt_file.read_text(encoding="utf-8"),
                add_special_tokens=False,
            )
        if len(seed_ids) < 32:
            rng = random.Random(20261104)
            vocab_size = int(getattr(model_config, "vocab_size", 0) or 0)
            if vocab_size <= 1024:
                raise RuntimeError("synthetic prompts need a usable model vocab_size")
            seed_ids = [rng.randrange(100, vocab_size) for _ in range(256)]
        target_len = args.input_length - 1
        prompt_token_ids_list = [
            [seed_ids[(start + index) % len(seed_ids)] for index in range(target_len)]
            for start in range(0, sample_size * 7, 7)
        ]
        requests = [
            SimpleNamespace(
                prompt_len=target_len,
                expected_output_len=args.synthetic_output_len,
            )
            for _ in range(sample_size)
        ]
        dataset_kind = "synthetic-smoke"
        dataset_source = (
            f"synthetic:{args.synthetic_prompt_file}"
            if args.synthetic_prompt_file is not None
            else f"synthetic:{args.task}"
        )
    else:
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
            raise RuntimeError(
                f"requested {sample_size} samples, dataset returned {len(requests)}"
            )
        # Pre-encode prompts outside the timed region so GRLLM.beam_search's
        # _preprocess_cmpl does not re-encode inside the perf_counter window.
        prompt_token_ids_list = [
            tokenizer.encode(request.prompt, add_special_tokens=False)
            for request in requests
        ]
        dataset_kind = "real"
        dataset_source = str(args.data_dir)

    begin_token = "<|sid_begin|>"
    end_token = "<|sid_end|>"
    # The caller-facing max_tokens spans both boundary positions; the beam
    # schedules below carry one entry per generated token.
    boundary_tokens = 1 + 1
    dataset_max_tokens = int(requests[0].expected_output_len)
    if args.max_tokens is None:
        # Historical default: the dataset's generation budget is the public
        # max_tokens, which leaves three generated tokens behind the boundaries.
        public_max_tokens = dataset_max_tokens
        generated_steps = public_max_tokens - boundary_tokens
    else:
        generated_steps = args.max_tokens
        public_max_tokens = generated_steps + boundary_tokens
    try:
        schedule = build_beam_schedule(
            beam_width=args.beam_width,
            generated_steps=generated_steps,
            dynamic_beam_widths=args.dynamic_beam_widths,
            per_parent_beam_widths=args.per_parent_beam_widths,
            result_width=args.result_width,
        )
    except ValueError as exc:
        parser.error(str(exc))
    params = BeamSearchParams(
        beam_width=args.beam_width,
        max_tokens=public_max_tokens,
        temperature=0.0,
        dynamic_beam_widths=schedule["dynamic_beam_widths"],
        per_parent_beam_widths=schedule["per_parent_beam_widths"],
        result_width=schedule["result_width"],
        # The boundary tokens are part of the schedule contract: the per-stage
        # list length is validated against max_tokens minus these two positions,
        # so they must be set before __post_init__ runs (i.e. at construction).
        begin_token=begin_token,
        end_token=end_token,
    )
    if args.beam_api == "v1":
        # Preserve the legacy BeamSearchParams default on that pipeline.
        params.ignore_eos = True
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
    gr_config: dict[str, Any] | None = None
    if args.beam_api == "v1":
        v1_config, v1_resource = build_v1_constraint_config(
            catalog=args.catalog,
            tokenizer=tokenizer,
            model_config=model_config,
            artifact=args.output.parent / "constraint-table-v1.safetensors",
            beam_width=args.beam_width,
            candidate_width=schedule["candidate_width"],
            max_decode_steps=schedule["generated_steps"],
        )
        if uses_gr_config:
            try:
                gr_config = build_gr_constraint_config(v1_config, schedule)
            except ValueError as exc:
                parser.error(str(exc))

    engine_kwargs = build_engine_kwargs(
        model=args.model,
        beam_width=args.beam_width,
        max_model_len=engine_max_model_len,
        beam_api=args.beam_api,
        catalog=args.catalog,
        additional_config=additional_config,
        v1_config=v1_config,
        batch_size=args.batch_size,
        candidate_width=schedule["candidate_width"],
        gr_config=gr_config,
    )

    model_runner = "v2" if os.environ.get("VLLM_USE_V2_MODEL_RUNNER") == "1" else "v1"
    graph_config = configure_prefill_graph(
        engine_kwargs,
        model_runner=model_runner,
        batch_size=args.batch_size,
        input_length=args.input_length,
    )

    with GRLLM(
        **engine_kwargs,
    ) as llm:
        runner_classes = verify_model_runner(llm, model_runner)
        print(f"Verified Model Runner {model_runner}: {runner_classes}", flush=True)
        beam_api, beam_call, output_mode = resolve_beam_call(llm, args.beam_api)
        endpoint = f"GRLLM.{beam_api}"
        # One logical batch per measured call. ``--num-prompts`` keeps counting
        # requests, not calls, so throughput and request totals stay comparable
        # with the single-batch scenarios. At --batch-size 1 the grouping is the
        # identity and every call below is the one it always was.
        groups = [
            prompt_token_ids_list[start : start + args.batch_size]
            for start in range(0, args.num_prompts, args.batch_size)
        ]

        for group in groups[: args.warmup_requests]:
            if not llm.reset_prefix_cache():
                raise RuntimeError("prefix cache reset failed during warmup")
            # Warm both members of the exact protocol used by canonical
            # sampling. Hit-only graph and constraint paths may initialize
            # lazily and must not first appear in a measured request.
            measure_request_canonical(beam_call, group, params)
            measure_request_canonical(beam_call, group, params)

        measured_requests = requests[: args.num_prompts]
        for index, group in enumerate(groups, start=1):
            if not llm.reset_prefix_cache():
                raise RuntimeError(f"prefix cache reset failed before measured batch {index}")
            miss_samples.append(
                measure_request_canonical(beam_call, group, params)
            )
            hit_samples.append(
                measure_request_canonical(beam_call, group, params)
            )

        # Stage timing deliberately runs after every canonical sample so its
        # monkeypatches cannot inflate the official daily E2E trend.
        diagnostic_count = min(max(0, args.diagnostic_prompts), len(groups))
        diagnostic_error = None
        try:
            for index, group in enumerate(groups[:diagnostic_count], start=1):
                if not llm.reset_prefix_cache():
                    raise RuntimeError(f"prefix cache reset failed before diagnostic batch {index}")
                diagnostic_miss_samples.append(
                    measure_request_diagnostic(llm, group, params, args.beam_api, beam_call)
                )
                diagnostic_hit_samples.append(
                    measure_request_diagnostic(llm, group, params, args.beam_api, beam_call)
                )
        except Exception as exc:
            diagnostic_error = f"{type(exc).__name__}: {exc}"
            print(f"Stage diagnostic failed: {diagnostic_error}", flush=True)
            diagnostic_miss_samples.clear()
            diagnostic_hit_samples.clear()

        # Read existing counters only after all official samples. This adds no
        # wrappers or events to the measured path and is not stage timing.
        graph_execution = None
        if args.beam_api == "v1":
            try:
                graph_execution = llm.collective_rpc("get_gr_beam_graph_runtime_stats")
            except Exception as exc:
                # Optional provenance must not discard completed E2E samples.
                graph_execution = [{"unavailable": f"{type(exc).__name__}: {exc}"}]

    finished_at = datetime.now(timezone.utc)
    use_v2_runner = model_runner == "v2" and args.beam_api == "v1"
    fields = (
        "e2e_ms",
        "prefill_ms",
        "decode_ms",
    )
    if args.beam_api == "v1":
        # Every V1 diagnostic sample carries the full cluster set, whatever the
        # batch size, so the field list no longer depends on it.
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
        "beam_api": beam_api,
        "endpoint": endpoint,
        "output_mode": output_mode,
        "beam_execution_mode": args.beam_api,
        "pipeline_version": (
            "beam-search-v1-async-final-output-pr396"
            if args.beam_api == "v1"
            else "legacy-beam-search"
        ),
        "measurement_mode": "canonical-with-posthoc-diagnostic" if diagnostic_count else "canonical-only",
        "instrumentation": {
            "canonical": "top-level perf_counter_ns plus final CUDA completion fence; no profiler or internal monkeypatch",
            "diagnostic": (
                "post-canonical Beam Search CUDA event pairs bracketing the Prefill and Decode stages on the engine compute stream"
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
        "dataset_kind": dataset_kind,
        "dataset_source": dataset_source,
        "prompt_token_ids_sha256": hashlib.sha256(
            json.dumps(prompt_token_ids_list, separators=(",", ":")).encode()
        ).hexdigest(),
        "num_prompts": args.num_prompts,
        "warmup_requests": args.warmup_requests,
        "beam_width": args.beam_width,
        "input_length": args.input_length,
        "batch_size": args.batch_size,
        "max_tokens": public_max_tokens,
        # One record per measured call. A logical batch is scheduled atomically,
        # but each request keeps its own returned-beam count, which is what the
        # per-request output-token fan-out in generate_summary.py needs.
        "sequences_per_request": [
            [int(value) for value in sample["sequences_per_request"]]
            for sample in miss_samples
        ],
        "beam_schedule": {
            "generated_steps": schedule["generated_steps"],
            "dynamic_beam_widths": schedule["dynamic_beam_widths"],
            "per_parent_beam_widths": schedule["per_parent_beam_widths"],
            "result_width": schedule["result_width"],
            "final_width": schedule["final_width"],
            "candidate_width": schedule["candidate_width"],
            "public_max_tokens": public_max_tokens,
            "boundary_tokens": boundary_tokens,
        },
        "max_concurrency": 1,
        # A logical beam batch is scheduled atomically: one call carries
        # ``batch_size`` requests, so the per-call sample arrays below hold one
        # entry per batch while the request counts stay per request.
        "num_batches": len(miss_samples),
        "sample_unit": "logical-batch",
        "completed": len(miss_samples) * args.batch_size,
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
            "unavailable_reason": diagnostic_error or (
                "Stage diagnostics disabled for this run" if not diagnostic_count else None
            ),
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
                    "vllm-gr-model-runner-v2-gpu-compute-v1"
                    if use_v2_runner else "vllm-gr-beam-search-v1-gpu-compute-v5"
                    if args.beam_api == "v1"
                    else "vllm-gr-serving-internal-v4-minimal"
                ),
                "e2e": f"instrumented direct {endpoint} call start through return",
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
                    "sum of CUDA event intervals from Prefill model forward or graph replay through Prefill stage execute return, plus the output-producing sample/beam initialization window; excludes dispatch and the execute-to-sample gap, and is an upper bound on Prefill GPU busy time because the host tail after the last enqueue falls inside it"
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
            "e2e": f"unmodified direct {endpoint} call start through CUDA-complete return",
            "cache_protocol": "paired cold miss followed by identical warm hit",
            "internal_stages": "reported separately from post-canonical diagnostic samples",
        },
        "engine_config": {
            "attention_backend": "CUSTOM",
            "beam_graph_enabled": True,
            "beam_max_width": args.beam_width,
            "max_logprobs": engine_kwargs["max_logprobs"],
            "candidate_width": schedule["candidate_width"],
            "max_top_k": schedule["candidate_width"],
            "max_decode_steps": gr_config["max_decode_steps"] if gr_config else schedule["generated_steps"],
            "model_runner": model_runner,
            "model_runner_classes": runner_classes,
            "compilation_config": engine_kwargs.get("compilation_config"),
            "graph_execution": graph_execution,
            **graph_config,
            "max_num_seqs": engine_kwargs["max_num_seqs"],
            "max_num_batched_tokens": 40960,
            "max_model_len": engine_max_model_len,
            "scheduling_policy": "fcfs",
            "gpu_memory_utilization": 0.5,
            "catalog_path": str(args.catalog),
            "constraint_backend": "constraint_table",
            "beam_api": beam_api,
            "endpoint": endpoint,
            "output_mode": output_mode,
            "beam_execution_mode": args.beam_api,
            "pipeline_version": (
                "beam-search-v1-async-final-output-pr396"
                if args.beam_api == "v1"
                else "legacy-beam-search"
            ),
            "constraint_resource": v1_resource,
            "batch_size": args.batch_size,
            "single_request": args.batch_size == 1,
            "beam_engine_driven": args.beam_engine_driven,
            "beam_short_suffix_attention": bool(
                (v1_config or {}).get("beam", {}).get("enable_short_suffix_attention", False)
            ),
        },
    }
    if not diagnostic_count:
        raw["diagnostic"] = {"num_prompts": 0, "miss": {}, "hit": {}, "distributions": {}}
        raw["instrumentation"]["diagnostic"] = "not collected (E2E only)"
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

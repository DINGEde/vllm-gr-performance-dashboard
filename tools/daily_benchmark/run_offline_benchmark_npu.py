#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM-GR project
"""Run paired cold/hit OneRec beam-search measurements on Ascend NPU offline.

This is the NPU counterpart of ``run_offline_benchmark.py`` (the GPU raw.v2
producer). It emits the same ``vllm-gr.offline.raw.v2`` schema with
``device == "npu"`` so ``scripts/build_npu_v2_dashboard.py`` can chart the
GPU-aligned v2 NPU dashboard.

The measured trend is the canonical offline E2E miss/hit pair: a cold prefix
cache miss followed by an identical warm hit, each bracketed by one outer
monotonic clock and a final ``torch.npu.synchronize()`` completion fence. This
is the difference from the legacy NPU ``Batch latency`` metric, which stopped
at output-object return without a device fence.

A post-canonical diagnostic pass then re-measures a subset of the batches with
``torch.npu.Event`` stage timing modeled on the GPU CUDA-event code in
``run_offline_benchmark.py``. The NPU stage spans are a hardware-unvalidated
first draft (this file cannot be run on Ascend 910B1 from this checkout): the
event placement mirrors the GPU FIFO-compute-stream assumption, but the NPU
timeline has not been calibrated on hardware, so treat the device split as
indicative rather than calibrated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

import torch

# Register torch.npu on Ascend; harmless (ImportError swallowed) elsewhere.
try:  # pragma: no cover - exercised only on Ascend hosts
    import torch_npu  # noqa: F401
except ImportError:
    pass

# Align with the GPU benchmark: run the V1 engine single-process (driver +
# EngineCore in one process) to avoid the ZMQ/TCP IPC round-trip per request.
os.environ.setdefault("VLLM_ENABLE_V1_MULTIPROCESSING", "0")

# Mirrors vllm_gr.config.MAX_CANDIDATE_WIDTH / CONSTRAINT_TABLE_MAX_DECODE_STEPS.
MAX_CANDIDATE_WIDTH = 256
MAX_DECODE_STEPS = 3

BEGIN_TOKEN = "<|sid_begin|>"
END_TOKEN = "<|sid_end|>"


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

    Mirrors the GPU benchmark's ``build_beam_schedule`` verbatim so the NPU
    scenario matrix and the beam-width/candidate-width decoupling rules stay
    identical across devices.
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


def runner_identity(worker: Any) -> dict[str, Any]:
    """Read the actual worker class and device before timing; never infer from env."""
    cls = type(worker.model_runner)
    return {
        "runner": f"{cls.__module__}.{cls.__name__}",
        "device": str(getattr(worker, "device", "")),
    }


def verify_npu_runner(llm: Any) -> list[str]:
    """Fail loudly when the engine did not land on an NPU worker."""
    infos = llm.collective_rpc(runner_identity)
    if not infos or any("npu" not in str(info.get("device", "")).lower() for info in infos):
        raise RuntimeError(f"NPU benchmark requires an NPU worker, actual workers: {infos}")
    return [str(info["runner"]) for info in infos]


def load_triples(path: Path) -> list[list[str]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = document.get("triples") if isinstance(document, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path} is not a nonempty Constraint Table triples document")
    triples = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not all(key in row for key in ("a", "b", "c")):
            raise ValueError(f"invalid triples row {index}: {row!r}")
        triples.append([str(row["a"]), str(row["b"]), str(row["c"])])
    return sorted(triples)


def make_prompt(tokenizer: Any, path: Path, input_len: int) -> list[int]:
    """Shape the single prompt text to ``input_len - 1`` tokens.

    ``input_len`` includes the begin token appended by ``beam_search_v1``, so the
    submitted prompt is ``input_len - 1`` tokens (matching the legacy NPU
    ``make_prompt`` contract).
    """
    submitted_len = input_len - 1
    if submitted_len <= 0:
        raise ValueError("--input-length must be at least 2")
    source = tokenizer.encode(path.read_text(encoding="utf-8"), add_special_tokens=False)
    if len(source) >= submitted_len:
        return [int(token) for token in source[-submitted_len:]]
    newline = tokenizer.encode("\n", add_special_tokens=False)
    if len(newline) != 1:
        raise RuntimeError(f"newline must map to exactly one token, got {newline}")
    return [int(newline[0])] * (submitted_len - len(source)) + [int(token) for token in source]


def build_artifact(
    model: Path,
    catalog: Path,
    artifact_path: Path,
) -> tuple[str, str]:
    """Compile the canonical NPU constraint-table artifact before timed requests."""
    from transformers import AutoConfig, AutoTokenizer

    from vllm_gr.constraints.artifact import save_constraint_table_artifact
    from vllm_gr.constraints.artifact_compiler import ConstraintTableArtifactCompiler
    from vllm_gr.constraints.json_compiler import compile_relations_json
    from vllm_gr.constraints.tokenizer_digest import compute_tokenizer_digest

    tokenizer = AutoTokenizer.from_pretrained(model, trust_remote_code=True)
    model_config = AutoConfig.from_pretrained(model, trust_remote_code=True)
    tokenizer_digest = compute_tokenizer_digest(tokenizer)
    table = ConstraintTableArtifactCompiler(
        tokenizer,
        int(model_config.vocab_size),
        tokenizer_digest=tokenizer_digest,
    ).compile(compile_relations_json(load_triples(catalog)))
    artifact_digest = save_constraint_table_artifact(table, artifact_path)
    return artifact_digest, tokenizer_digest


def build_v1_constraint_config(
    *,
    catalog: Path,
    tokenizer: Any,
    model_config: Any,
    artifact: Path,
    beam_width: int,
    candidate_width: int,
    max_decode_steps: int,
    batch_size: int,
    execution: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Compile the canonical NPU constraint resource before timed requests."""
    from vllm_gr.constraints.artifact import save_constraint_table_artifact
    from vllm_gr.constraints.artifact_compiler import ConstraintTableArtifactCompiler
    from vllm_gr.constraints.json_compiler import compile_relations_json
    from vllm_gr.constraints.tokenizer_digest import compute_tokenizer_digest

    vocab_size = int(model_config.vocab_size)
    tokenizer_digest = compute_tokenizer_digest(tokenizer)
    table = ConstraintTableArtifactCompiler(
        tokenizer,
        vocab_size,
        tokenizer_digest=tokenizer_digest,
    ).compile(compile_relations_json(load_triples(catalog)))
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact_digest = save_constraint_table_artifact(table, artifact)
    # Canonical ``gr`` mapping. The legacy ``vllm_gr_config`` schema (flat
    # ``schema_version``/``attention_backend``/``beam``) was removed upstream in
    # favor of the ``gr`` keys read by GRLLM; this mirrors
    # examples/offline_inference/beam_search/offline_beam_search.py so the engine
    # and constraint-table capacity match the BeamSearchParams schedule.
    candidate_widths = [beam_width] + [candidate_width] * (max_decode_steps - 1)
    max_candidates = max(candidate_widths)
    constraint_table: dict[str, Any] = {
        "backend": "npu",
        "max_top_k": max_candidates,
        "path": str(artifact.resolve()),
        "format": "constraint_table_v1",
        "digests": {"artifact": artifact_digest, "tokenizer": tokenizer_digest},
    }
    config: dict[str, Any] = {
        "beam_graph_enabled": execution == "graph",
        "worker_decision": True,
        "beam_width": [beam_width] * max_decode_steps,
        "candidate_width": candidate_widths,
        "max_decode_steps": max_decode_steps,
        "constraint_mode": "table",
        "constraint_table": constraint_table,
        "decode_graph_batch_size": batch_size,
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
    batch_size: int,
    input_length: int,
    max_model_len: int,
    candidate_width: int,
    execution: str,
    v1_config: dict[str, Any],
) -> dict[str, Any]:
    """Build one unambiguous GR configuration source for the NPU V1 engine."""
    kwargs: dict[str, Any] = {
        "model": model,
        "trust_remote_code": True,
        "gr": v1_config,
        "attention_config": {"backend": "CUSTOM"},
        "async_scheduling": True,
        "enable_prefix_caching": True,
        "enable_chunked_prefill": False,
        "max_model_len": max_model_len,
        # The V1 input processor appends the begin token to every prompt, so a
        # B-wide Prefill schedules B * (input_length + 1) tokens.
        "max_num_batched_tokens": max(max_model_len, batch_size * (input_length + 1)),
        "max_num_seqs": max(beam_width, batch_size),
        "max_logprobs": candidate_width,
        "gpu_memory_utilization": 0.5,
        "enforce_eager": execution == "eager",
    }
    # vllm-ascend 0.30 enables the ``norm_quant`` graph fusion by default. Its
    # pattern references ``aclnnAddRmsNormBias``, a custom op whose tiling is
    # only found when ASCEND_CUSTOM_OPP_PATH is set (EZ1008 "tiling function
    # does not exist" otherwise). OneRec is loaded as float, so the quantization
    # fusion has no benefit either way; this env gate lets the runner keep it
    # off until a control run proves ASCEND_CUSTOM_OPP_PATH alone suffices.
    if os.environ.get("NPU_FUSE_NORM_QUANT", "").strip().lower() in (
        "0",
        "false",
        "off",
        "no",
    ):
        kwargs["additional_config"] = {
            "ascend_compilation_config": {"fuse_norm_quant": False}
        }
    return kwargs


def _run_beam_call(llm: Any, prompts: list[dict[str, Any]], params: Any) -> list[Any]:
    """Submit one logical beam batch through ``beam_search_v1``.

    ``concurrency_limit`` stays at 1; the V1 entry point validates the value and
    never splits on it, so a multi-prompt call still reaches the engine as one
    immutable batch.
    """
    return llm.beam_search(prompts, params, concurrency_limit=1, output_mode="full")


def measure_request_canonical(
    llm: Any,
    prompts: list[dict[str, Any]],
    params: Any,
) -> dict[str, Any]:
    """Measure the production trend with no internal monkeypatches or profiler.

    The timed region is one direct Beam API call followed by an NPU completion
    fence; output materialization happens after the clock stops. The call carries
    the whole batch, so ``e2e_ms`` is the wall time of the batched call and the
    throughput numerators below sum over every request in the batch.
    """
    started = time.perf_counter_ns()
    outputs = _run_beam_call(llm, prompts, params)
    torch.npu.synchronize()
    finished = time.perf_counter_ns()
    if len(outputs) != len(prompts):
        raise RuntimeError(
            f"expected {len(prompts)} offline output(s), received {len(outputs)}"
        )
    return {
        "e2e_ms": (finished - started) / 1_000_000.0,
        "returned_beams": sum(len(output.sequences) for output in outputs),
        "sequences_per_request": [len(output.sequences) for output in outputs],
        "aggregate_output_tokens": sum(
            len(output.sequences) * int(params.max_tokens) for output in outputs
        ),
    }


def _npu_event_sanity() -> None:
    """Best-effort check that ``torch.npu.Event.elapsed_time`` is usable here.

    Runs a trivial matmul and compares a host-clock fence against an event pair,
    so a broken device clock or a wrong default stream is visible before the real
    stage probe. Gated by ``NPU_STAGE_DEBUG=1``; meaningful only on an Ascend
    host.
    """
    if os.environ.get("NPU_STAGE_DEBUG") != "1":
        return
    try:
        print(
            "[npustage-debug] device_count=%d current_device=%d"
            % (torch.npu.device_count(), torch.npu.current_device()),
            flush=True,
        )
        current = torch.npu.current_stream()
        print("[npustage-debug] current_stream=%r" % (current,), flush=True)
        default_fn = getattr(torch.npu, "default_stream", None)
        if default_fn is not None:
            default = default_fn()
            print("[npustage-debug] default_stream=%r" % (default,), flush=True)
            print(
                "[npustage-debug] current==default: %s" % (current == default,),
                flush=True,
            )
        else:
            print("[npustage-debug] torch.npu.default_stream not available", flush=True)
        a = torch.randn(2048, 2048, device="npu")
        b = torch.randn(2048, 2048, device="npu")
        c = a @ b  # warmup (graph / compile on first call)
        torch.npu.synchronize()
        ev0 = torch.npu.Event(enable_timing=True)
        ev1 = torch.npu.Event(enable_timing=True)
        t0 = time.perf_counter_ns()
        ev0.record(current)
        for _ in range(32):
            c = a @ b
        ev1.record(current)
        ev1.synchronize()
        t1 = time.perf_counter_ns()
        host_ms = (t1 - t0) / 1_000_000.0
        event_ms = float(ev0.elapsed_time(ev1))
        print(
            "[npustage-debug] matmul host=%.3fms event=%.3fms ratio=%.3f"
            % (host_ms, event_ms, event_ms / host_ms if host_ms else float("nan")),
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001
        print("[npustage-debug] sanity check failed: %r" % (exc,), flush=True)


def _dump_stage_debug(**vals: Any) -> None:
    """Print raw NPU stage-timing telemetry for the additive-overlap diagnosis.

    Called only when ``NPU_STAGE_DEBUG=1`` from inside
    ``measure_request_diagnostic`` after the terminal events have synchronized,
    so every ``elapsed_time`` below is valid. This function never raises.
    """

    def ms(ns: Any) -> float:
        return float(ns) / 1_000_000.0

    def elapsed(e0: Any, e1: Any) -> float:
        try:
            return float(e0.elapsed_time(e1))
        except Exception:  # noqa: BLE001
            return float("nan")

    anchor = vals["beam_started"]
    print("[npustage-debug] ===== host timeline (relative to beam_started) =====", flush=True)
    for label, ts in (
        ("beam_started", vals["beam_started"]),
        ("prefill_worker_started", vals["prefill_worker_started"]),
        ("first_decode_started", vals["first_decode_started"]),
        ("prefill_output_consumed", vals["prefill_output_consumed"]),
        ("beam_finished", vals["beam_finished"]),
        ("request_finished", vals["request_finished"]),
    ):
        print(
            "[npustage-debug]   %-24s +%8.3f ms" % (label, ms(ts - anchor)),
            flush=True,
        )
    print(
        "[npustage-debug]   host e2e (request_started->finished) = %.3f ms"
        % ms(vals["request_finished"] - vals["request_started"]),
        flush=True,
    )

    streams = vals["observed_streams"]
    print("[npustage-debug] observed_streams count=%d" % len(streams), flush=True)
    for stream in streams:
        print("[npustage-debug]   stream=%r" % (stream,), flush=True)

    print("[npustage-debug] ===== device event pairs (elapsed_time ms) =====", flush=True)
    prefill_ms = elapsed(vals["prefill_stage_start_event"], vals["prefill_end_event"])
    decode_ms = elapsed(vals["decode_start_event"], vals["decode_end_event"])
    print(
        "[npustage-debug]   prefill_stage_start -> prefill_end = %.3f ms" % prefill_ms,
        flush=True,
    )
    print(
        "[npustage-debug]   prefill_model_start -> prefill_execute_end = %.3f ms"
        % elapsed(vals["prefill_model_start_event"], vals["prefill_execute_end_event"]),
        flush=True,
    )
    print(
        "[npustage-debug]   prefill_sample(start -> end) = %.3f ms"
        % elapsed(vals["prefill_sample_pair"][0], vals["prefill_sample_pair"][1]),
        flush=True,
    )
    print(
        "[npustage-debug]   decode_start -> decode_end = %.3f ms" % decode_ms,
        flush=True,
    )
    pair_ms = [elapsed(s, e) for s, e in vals["decode_compute_pairs"]]
    print(
        "[npustage-debug]   decode_compute pairs (%d) = %s sum=%.3f ms"
        % (len(pair_ms), [round(v, 3) for v in pair_ms], sum(pair_ms)),
        flush=True,
    )

    print("[npustage-debug] ===== ordering / overlap =====", flush=True)
    # decode_start -> prefill_end = (prefill_end timestamp - decode_start
    # timestamp). Positive => decode_start fires BEFORE prefill_end on the device
    # timeline (real prefill/decode overlap); negative => a gap between them.
    decode_vs_prefill_end = elapsed(vals["decode_start_event"], vals["prefill_end_event"])
    print(
        "[npustage-debug]   decode_start -> prefill_end = %+.3f ms  (>0 = overlap, <0 = gap)"
        % decode_vs_prefill_end,
        flush=True,
    )
    print(
        "[npustage-debug]   prefill_end -> decode_end = %.3f ms"
        % elapsed(vals["prefill_end_event"], vals["decode_end_event"]),
        flush=True,
    )
    print(
        "[npustage-debug]   prefill_stage_start -> decode_end = %.3f ms  (total device span)"
        % elapsed(vals["prefill_stage_start_event"], vals["decode_end_event"]),
        flush=True,
    )
    print(
        "[npustage-debug]   prefill + decode = %.3f ms" % (prefill_ms + decode_ms),
        flush=True,
    )
    print(
        "[npustage-debug]   host e2e = %.3f ms  => overlap = %.3f ms"
        % (
            ms(vals["request_finished"] - vals["request_started"]),
            prefill_ms
            + decode_ms
            - ms(vals["request_finished"] - vals["request_started"]),
        ),
        flush=True,
    )


def measure_request_diagnostic(
    llm: Any,
    prompts: list[dict[str, Any]],
    params: Any,
) -> dict[str, Any]:
    """Measure NPU Beam phases on the NPU device timeline (first draft).

    The NPU stage spans mirror the GPU CUDA-event decomposition, with
    ``torch.npu.Event`` pairs standing in for the GPU's CUDA events on the
    engine's single compute stream. The hooks bracket the persistent NPU stage
    runner instead of the GPU's graph/forward dispatch:

    ``prefill_ms``
        entry to the PREFILL ``NPUEagerBeamStageRunner.execute`` through the
        output-producing PREFILL ``sample`` completion.
    ``decode_ms``
        entry to the first DECODE ``execute`` through the last Decode ``sample``
        completion.

    Device compute is single-stream FIFO, so the two intervals cannot overlap
    and their sum is an additive device decomposition. The first Decode start
    event is stream-ordered behind the Prefill kernels, so it fires at Prefill
    completion and the Decode interval excludes Prefill by construction. What
    remains of ``e2e_ms`` is host overhead: frontend dispatch, EngineCore
    queueing, and terminal collection.

    The NPU compute clusters bracket the model forward inside ``execute`` (the
    NPU runner has no separate graph replay or ``_forward`` to hook) plus the
    ``sample`` window; the residual device-wait is the execute-return-to-sample
    gap plus any inter-stage bubbles.

    This is a hardware-unvalidated first draft: the event placement assumes the
    same FIFO compute-stream ordering the GPU path relies on, but it has not been
    calibrated on Ascend 910B1. Treat the split as indicative.
    """
    from vllm_gr.entrypoints.beam_search.client import BeamSearchClient
    from vllm_gr.v1.worker.npu.npu_beam_stage_runner import NPUEagerBeamStageRunner

    original_submit = BeamSearchClient.submit_once
    original_wait = BeamSearchClient.wait_final
    original_execute = NPUEagerBeamStageRunner.execute
    original_sample = NPUEagerBeamStageRunner.sample

    beam_started: int | None = None
    beam_finished: int | None = None
    prefill_worker_started: int | None = None
    first_decode_started: int | None = None
    prefill_stage_start_event: Any | None = None
    prefill_model_start_event: Any | None = None
    prefill_execute_end_event: Any | None = None
    prefill_end_event: Any | None = None
    prefill_sample_pair: tuple[Any, Any] | None = None
    prefill_output_consumed: int | None = None
    decode_start_event: Any | None = None
    decode_end_event: Any | None = None
    decode_compute_pairs: list[tuple[Any, Any]] = []
    observed_streams: set[Any] = set()

    def record_timing_event(runner: Any | None = None) -> Any:
        """Return a one-shot timing event recorded on the engine compute stream."""
        if runner is not None:
            device = getattr(runner.worker, "device", None)
            stream = torch.npu.current_stream(device) if device is not None else torch.npu.current_stream()
        else:
            stream = torch.npu.current_stream()
        observed_streams.add(stream)
        event = torch.npu.Event(enable_timing=True)
        event.record(stream)
        return event

    def timed_submit(self: Any, *a: Any, **k: Any) -> Any:
        nonlocal beam_started
        if beam_started is None:
            # Drain device work left by the previous request so the host anchor
            # and the Prefill dispatch gap both start from an idle engine. Only
            # the post-canonical diagnostic path synchronizes here.
            torch.npu.synchronize()
            beam_started = time.perf_counter_ns()
        return original_submit(self, *a, **k)

    def timed_wait(self: Any, *a: Any, **k: Any) -> Any:
        nonlocal beam_finished
        result = original_wait(self, *a, **k)
        beam_finished = time.perf_counter_ns()
        return result

    def timed_execute(self: Any, scheduler_output: Any, original: Any, *a: Any, **k: Any) -> Any:
        nonlocal first_decode_started, decode_start_event
        nonlocal prefill_worker_started, prefill_stage_start_event
        nonlocal prefill_model_start_event, prefill_execute_end_event
        stages = getattr(scheduler_output, "gr_stage_metadata", {}) or {}
        kinds = {
            getattr(getattr(stage, "kind", None), "value", None)
            for stage in stages.values()
        }
        decode_forward_start: Any | None = None
        if "prefill" in kinds and prefill_worker_started is None:
            prefill_worker_started = time.perf_counter_ns()
            prefill_stage_start_event = record_timing_event(self)
            # The NPU runner's execute IS the model forward (it calls the
            # intercepted ``original`` directly), so the model-forward window
            # starts at the same boundary as the stage span.
            prefill_model_start_event = record_timing_event(self)
        if first_decode_started is None and "decode" in kinds:
            # Recorded before this dispatch's CPU preparation, so the event is
            # stream-ordered behind the Prefill kernels and fires at Prefill
            # completion rather than at this host instant.
            first_decode_started = time.perf_counter_ns()
            decode_start_event = record_timing_event(self)
        if "decode" in kinds:
            decode_forward_start = record_timing_event(self)
        result = original_execute(self, scheduler_output, original, *a, **k)
        if "prefill" in kinds:
            prefill_execute_end_event = record_timing_event(self)
        if decode_forward_start is not None:
            decode_compute_pairs.append((decode_forward_start, record_timing_event(self)))
        return result

    def timed_sample(self: Any, *a: Any, **k: Any) -> Any:
        nonlocal prefill_end_event, decode_end_event, prefill_sample_pair
        nonlocal prefill_output_consumed
        plan = getattr(self, "pending_plan", None)
        kind = getattr(getattr(plan, "execution", None), "kind", None)
        sample_start = record_timing_event(self) if kind in ("prefill", "decode") else None
        output = original_sample(self, *a, **k)
        if sample_start is None:
            return output
        if kind == "prefill":
            if prefill_end_event is None:
                prefill_end_event = record_timing_event(self)
                prefill_sample_pair = (sample_start, prefill_end_event)
                prefill_output_consumed = time.perf_counter_ns()
        elif kind == "decode":
            # Keep only the latest Decode completion: the terminal stage is the
            # one whose result reaches wait_final.
            decode_end_event = record_timing_event(self)
            decode_compute_pairs.append((sample_start, decode_end_event))
        return output

    request_started = time.perf_counter_ns()
    with (
        mock.patch.object(BeamSearchClient, "submit_once", timed_submit),
        mock.patch.object(BeamSearchClient, "wait_final", timed_wait),
        mock.patch.object(NPUEagerBeamStageRunner, "execute", timed_execute),
        mock.patch.object(NPUEagerBeamStageRunner, "sample", timed_sample),
    ):
        outputs = _run_beam_call(llm, prompts, params)
    request_finished = time.perf_counter_ns()

    if beam_started is None:
        raise RuntimeError("beam_search_v1 submit boundary was not observed")
    if beam_finished is None:
        raise RuntimeError("beam_search_v1 terminal result boundary was not observed")
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
        raise RuntimeError("beam_search_v1 Prefill NPU boundaries were not observed")
    if decode_end_event is None:
        raise RuntimeError("beam_search_v1 terminal Decode NPU boundary was not observed")
    if prefill_output_consumed is None:
        raise RuntimeError("beam_search_v1 output-producing Prefill completion was not observed")
    if not beam_started <= first_decode_started <= beam_finished:
        raise RuntimeError("beam_search_v1 Decode timestamps are not monotonic")
    if len(outputs) != len(prompts):
        raise RuntimeError(
            f"expected {len(prompts)} offline output(s), received {len(outputs)}"
        )
    if len(observed_streams) != 1:
        raise RuntimeError(
            "beam_search_v1 phase events landed on multiple NPU streams; "
            "the Prefill/Decode device decomposition requires one compute stream"
        )

    prefill_end_event.synchronize()
    decode_end_event.synchronize()
    if os.environ.get("NPU_STAGE_DEBUG") == "1":
        _dump_stage_debug(
            beam_started=beam_started,
            beam_finished=beam_finished,
            prefill_worker_started=prefill_worker_started,
            first_decode_started=first_decode_started,
            prefill_output_consumed=prefill_output_consumed,
            request_started=request_started,
            request_finished=request_finished,
            observed_streams=observed_streams,
            prefill_stage_start_event=prefill_stage_start_event,
            prefill_end_event=prefill_end_event,
            prefill_model_start_event=prefill_model_start_event,
            prefill_execute_end_event=prefill_execute_end_event,
            prefill_sample_pair=prefill_sample_pair,
            decode_start_event=decode_start_event,
            decode_end_event=decode_end_event,
            decode_compute_pairs=decode_compute_pairs,
        )
    # The additive split rests on the same structural fact as the GPU path: the
    # forward window ends before the sample window starts (the stage runner's
    # pending gate forces that CPU order). Assert instead of clamping the
    # residual so a broken assumption fails loudly.
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
            "beam_search_v1 stage NPU compute exceeds its device span: "
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="OpenOneRec/OneRec-1.7B")
    parser.add_argument("--task", default="video")
    parser.add_argument(
        "--catalog",
        type=Path,
        required=True,
        help="Constraint table triples document for constrained beam decoding.",
    )
    parser.add_argument("--prompt", type=Path, required=True, help="Prompt text file.")
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
    parser.add_argument("--batch-size", type=int, default=1, choices=(1, 2, 4))
    parser.add_argument("--generated-steps", type=int, default=3, choices=(1, 2, 3))
    parser.add_argument(
        "--execution",
        choices=("eager", "graph"),
        default="graph",
        help="NPU Decode execution mode (beam decode ACL graph or eager fallback).",
    )
    parser.add_argument(
        "--dynamic-beam-widths",
        metavar="W1[,W2,W3]",
        help="Retained beam width after each generated token (one value per generated token).",
    )
    parser.add_argument(
        "--per-parent-beam-widths",
        metavar="K1[,K2,K3]",
        help="Candidates expanded per parent beam at each generated token (one value per generated token).",
    )
    parser.add_argument("--result-width", type=int, default=None)
    parser.add_argument("--max-model-len", type=int, default=16384)
    args = parser.parse_args()

    _npu_event_sanity()

    if args.batch_size > 1 and args.num_prompts % args.batch_size:
        parser.error(
            f"--num-prompts ({args.num_prompts}) must be a multiple of "
            f"--batch-size ({args.batch_size})"
        )
    if args.input_length < 2 or args.warmup_requests < 0 or args.num_prompts < 1:
        parser.error(
            "num-prompts and beam-width must be positive; input-length must be at least 2 "
            "(one token is reserved for <|sid_begin|>); warmup must be non-negative"
        )
    try:
        args.dynamic_beam_widths = parse_int_list(
            "--dynamic-beam-widths", args.dynamic_beam_widths
        )
        args.per_parent_beam_widths = parse_int_list(
            "--per-parent-beam-widths", args.per_parent_beam_widths
        )
    except ValueError as exc:
        parser.error(str(exc))

    from transformers import AutoConfig, AutoTokenizer

    from vllm_gr.entrypoints.gr import GRLLM
    from vllm_gr.sampling_params import BeamSearchParams

    started_at = datetime.now(timezone.utc)
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    model_config = AutoConfig.from_pretrained(args.model, trust_remote_code=True)

    generated_steps = args.generated_steps
    public_max_tokens = generated_steps + 2  # begin + end boundary positions
    boundary_tokens = 2
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
    candidate_width = schedule["candidate_width"]

    params = BeamSearchParams(
        beam_width=args.beam_width,
        max_tokens=public_max_tokens,
        temperature=0.0,
        dynamic_beam_widths=schedule["dynamic_beam_widths"],
        per_parent_beam_widths=schedule["per_parent_beam_widths"],
        result_width=schedule["result_width"],
        begin_token=BEGIN_TOKEN,
        end_token=END_TOKEN,
    )
    params.ignore_eos = True

    artifact = args.output.parent / "constraint-table-v1.safetensors"
    v1_config, v1_resource = build_v1_constraint_config(
        catalog=args.catalog,
        tokenizer=tokenizer,
        model_config=model_config,
        artifact=artifact,
        beam_width=args.beam_width,
        candidate_width=candidate_width,
        max_decode_steps=schedule["generated_steps"],
        batch_size=args.batch_size,
        execution=args.execution,
    )
    engine_kwargs = build_engine_kwargs(
        model=args.model,
        beam_width=args.beam_width,
        batch_size=args.batch_size,
        input_length=args.input_length,
        max_model_len=args.max_model_len,
        candidate_width=candidate_width,
        execution=args.execution,
        v1_config=v1_config,
    )

    with GRLLM(**engine_kwargs) as llm:
        runner_classes = verify_npu_runner(llm)
        print(f"Verified NPU Model Runner: {runner_classes}", flush=True)

        tokenizer = llm.get_tokenizer()
        prompt_token_ids = make_prompt(tokenizer, args.prompt.resolve(), args.input_length)
        # Keep prefix caching enabled, but give each request a unique salt so
        # duplicate batch rows never share the same prompt block before their
        # atomic Prefill completes (mirrors the legacy NPU benchmark).
        request_prompts = [
            {
                "prompt_token_ids": list(prompt_token_ids),
                "cache_salt": f"offline-npu-v2-row-{request_index}",
            }
            for request_index in range(args.num_prompts)
        ]
        groups = [
            request_prompts[start : start + args.batch_size]
            for start in range(0, args.num_prompts, args.batch_size)
        ]

        for group in groups[: args.warmup_requests]:
            if not llm.reset_prefix_cache():
                raise RuntimeError("prefix cache reset failed during warmup")
            measure_request_canonical(llm, group, params)
            measure_request_canonical(llm, group, params)

        miss_samples: list[dict[str, Any]] = []
        hit_samples: list[dict[str, Any]] = []
        for index, group in enumerate(groups, start=1):
            if not llm.reset_prefix_cache():
                raise RuntimeError(f"prefix cache reset failed before measured batch {index}")
            miss_samples.append(measure_request_canonical(llm, group, params))
            hit_samples.append(measure_request_canonical(llm, group, params))

        # Stage timing deliberately runs after every canonical sample so its
        # monkeypatches cannot inflate the official daily E2E trend.
        diagnostic_count = min(max(0, args.diagnostic_prompts), len(groups))
        diagnostic_miss_samples: list[dict[str, Any]] = []
        diagnostic_hit_samples: list[dict[str, Any]] = []
        diagnostic_error: str | None = None
        for index, group in enumerate(groups[:diagnostic_count], start=1):
            if not llm.reset_prefix_cache():
                raise RuntimeError(f"prefix cache reset failed before diagnostic batch {index}")
            try:
                diagnostic_miss_samples.append(measure_request_diagnostic(llm, group, params))
                diagnostic_hit_samples.append(measure_request_diagnostic(llm, group, params))
            except Exception as exc:  # noqa: BLE001 - stage timing is best-effort
                # The NPU stage timing is a hardware-unvalidated first draft. A
                # failure here must not discard the already-measured canonical
                # E2E trend, so the diagnostic is demoted to "unavailable".
                diagnostic_error = f"{type(exc).__name__}: {exc}"
                diagnostic_miss_samples = []
                diagnostic_hit_samples = []
                print(
                    "NPU stage diagnostic failed; canonical E2E data is kept, "
                    f"stage timing is marked unavailable: {diagnostic_error}",
                    flush=True,
                )
                break

        graph_execution = None
        try:
            from vllm_gr.v1.worker.model_runner_common import get_beam_graph_runtime_stats

            graph_execution = llm.collective_rpc("get_gr_beam_graph_runtime_stats")
        except Exception as exc:
            graph_execution = [{"unavailable": f"{type(exc).__name__}: {exc}"}]

    finished_at = datetime.now(timezone.utc)
    fields = (
        "e2e_ms",
        "prefill_ms",
        "decode_ms",
        "prefill_gpu_compute_ms",
        "decode_gpu_compute_ms",
        "prefill_device_idle_ms",
        "decode_device_idle_ms",
        "prefill_output_consumed_ms",
        "prefill_dispatch_ms",
        "prefill_cpu_lead_ms",
        "host_overhead_ms",
    )
    npu_beam_graph = (graph_execution or [{}])[0] if isinstance(graph_execution, list) else {}

    raw = {
        "schema_version": "vllm-gr.offline.raw.v2",
        "device": "npu",
        "execution_mode": "offline",
        "beam_api": "beam_search_v1",
        "beam_execution_mode": "v1",
        "pipeline_version": "beam-search-v1-npu-async-final-output",
        "measurement_mode": (
            "canonical-with-posthoc-diagnostic"
            if diagnostic_count and diagnostic_error is None
            else "canonical-only"
        ),
        "instrumentation": {
            "canonical": "top-level perf_counter_ns plus final torch.npu.synchronize() completion fence; no profiler or internal monkeypatch",
            "diagnostic": (
                (
                    "post-canonical one-shot torch.npu.Event pairs bracketing the Prefill and "
                    "Decode NPUEagerBeamStageRunner stages on the engine compute stream; "
                    "hardware-unvalidated first draft modeled on the GPU CUDA-event code"
                )
                if diagnostic_count and diagnostic_error is None
                else (
                    f"not collected: stage timing failed ({diagnostic_error})"
                    if diagnostic_error is not None
                    else "not collected (E2E only)"
                )
            ),
            "canonical_contaminated_by_diagnostic": False,
        },
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "model": args.model,
        "task": args.task,
        "dataset_kind": "synthetic-smoke",
        "dataset_source": f"synthetic:{args.prompt}",
        "prompt": str(args.prompt.resolve()),
        "prompt_token_ids_sha256": hashlib.sha256(
            json.dumps(prompt_token_ids, separators=(",", ":")).encode()
        ).hexdigest(),
        "num_prompts": args.num_prompts,
        "warmup_requests": args.warmup_requests,
        "beam_width": args.beam_width,
        "input_length": args.input_length,
        "batch_size": args.batch_size,
        "max_tokens": public_max_tokens,
        "generated_steps": generated_steps,
        "max_model_len": args.max_model_len,
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
        "num_batches": len(miss_samples),
        "sample_unit": "logical-batch",
        "completed": len(miss_samples) * args.batch_size,
        "failed": 0,
        "input_lens": [len(prompt_token_ids)] * args.num_prompts,
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
        "aggregate_output_tokens": sum(
            int(sample["aggregate_output_tokens"]) for sample in miss_samples
        ),
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
                "version": "vllm-gr-beam-search-v1-npu-events-v1-draft",
                "e2e": "instrumented direct beam_search_v1 call start through return",
                "prefill": (
                    "torch.npu.Event interval on the engine compute stream: entry to the "
                    "PREFILL NPUEagerBeamStageRunner.execute through output-producing "
                    "PREFILL sample completion; excludes frontend dispatch"
                ),
                "decode": (
                    "torch.npu.Event interval on the engine compute stream: entry to the "
                    "first DECODE NPUEagerBeamStageRunner.execute through the last Decode "
                    "sample completion; excludes Prefill by stream ordering and includes "
                    "device idle inside Decode"
                ),
                "prefill_gpu_compute": (
                    "sum of the torch.npu.Event interval bracketing the PREFILL execute "
                    "(model forward plus compute_logits) and the output-producing sample "
                    "window; excludes dispatch and the execute-to-sample gap"
                ),
                "decode_gpu_compute": (
                    "sum of per-stage torch.npu.Event intervals covering each Decode "
                    "execute and the sample/beam-advance window; excludes CPU prepare and "
                    "inter-stage queue gaps"
                ),
                "device_idle": (
                    "device stage span minus the measured NPU compute clusters; a residual "
                    "rather than an independent measurement, holding CPU launch bubbles, "
                    "queue waits, and any NPU work outside those windows"
                ),
                "prefill_dispatch": (
                    "host wall from the post-synchronize submit anchor through entry to the "
                    "PREFILL NPUEagerBeamStageRunner.execute; a subset of host_overhead"
                ),
                "prefill_cpu_lead": (
                    "host look-ahead depth: how far the CPU had advanced into first-Decode "
                    "preparation before the NPU finished Prefill"
                ),
                "host_overhead": (
                    "e2e minus both device intervals: frontend dispatch, EngineCore "
                    "queueing, and terminal collection"
                ),
                "prefill_output_consumed": (
                    "diagnostic only: submit boundary through completion of the "
                    "output-producing PREFILL sample; the NPU runner has no separate async "
                    "output queue, so this marks the same boundary as Prefill sample completion"
                ),
                "average": "arithmetic mean over post-canonical diagnostic observations",
                "decode_cache_semantics": "common phase; miss/hit are repeated observations, not additive components",
                "device_state_semantics": (
                    "Prefill and Decode are NPU device-timeline intervals on a single FIFO "
                    "compute stream; they are additive and sum with host_overhead back to e2e. "
                    "Hardware-unvalidated first draft."
                ),
            },
        },
        "phase_definition": {
            "version": "vllm-gr-canonical-beam-search-v1-npu-e2e-v1",
            "e2e": "unmodified direct GRLLM.beam_search_v1 call start through NPU-complete return",
            "cache_protocol": "paired cold miss followed by identical warm hit",
            "internal_stages": "reported separately from post-canonical diagnostic samples",
        },
        "engine_config": {
            "attention_backend": "CUSTOM",
            "beam_graph_enabled": args.execution == "graph",
            "beam_max_width": args.beam_width,
            "beam_graph_batch_size": args.batch_size,
            "max_logprobs": candidate_width,
            "candidate_width": schedule["candidate_width"],
            "max_decode_steps": schedule["generated_steps"],
            "constraint_backend": "npu",
            "constraint_table_backend": "npu",
            "npu_execution": args.execution,
            "catalog_path": str(args.catalog.resolve()),
            "constraint_resource": v1_resource,
            "batch_size": args.batch_size,
            "single_request": args.batch_size == 1,
            "max_num_seqs": max(args.beam_width, args.batch_size),
            "max_model_len": args.max_model_len,
            "gpu_memory_utilization": 0.5,
            "scheduling_policy": "fcfs",
            "beam_api": "beam_search_v1",
            "beam_execution_mode": "v1",
            "pipeline_version": "beam-search-v1-npu-async-final-output",
            "graph_execution": graph_execution,
        },
        "npu_beam_graph": npu_beam_graph,
    }
    if not diagnostic_count or diagnostic_error is not None:
        demoted = {"num_prompts": 0, "miss": {}, "hit": {}, "distributions": {}}
        if diagnostic_error is not None:
            demoted["unavailable"] = diagnostic_error
        raw["diagnostic"] = demoted
        raw["instrumentation"]["diagnostic"] = (
            f"not collected: stage timing failed ({diagnostic_error})"
            if diagnostic_error is not None
            else "not collected (E2E only)"
        )
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

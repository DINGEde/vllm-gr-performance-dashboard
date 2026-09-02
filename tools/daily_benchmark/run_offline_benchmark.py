#!/usr/bin/env python3
"""Run paired cold/hit OneRec beam-search measurements through GRLLM offline."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock


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


def measure_request(llm: Any, prompt: str, params: Any) -> dict[str, Any]:
    """Measure one offline request using the serving-engine phase boundary.

    vllm-gr's serving implementation treats token 0 as Prefill and moves the
    phase boundary immediately before token 1 request preparation. Decode is
    therefore wall time from token 1 through sorting/reconstruction and return.
    Engine-only timings are retained as diagnostics.
    """
    import vllm_gr.entrypoints.gr as gr_module
    import vllm_gr.entrypoints.openai.serving_engine as serving_engine_module

    original_custom = gr_module._custom_beam_search_batch
    original_step = gr_module._step_engine_and_collect_outputs
    original_prepare = gr_module._prepare_beam_step_requests
    original_parse = gr_module._parse_step_logprobs
    original_eos = gr_module.complete_eos_candidates
    original_topk = gr_module.select_top_indices
    original_materialize = gr_module.materialize_selected_beams
    original_worker_from_result = serving_engine_module._worker_decision_from_result
    original_worker_flatten = serving_engine_module._worker_decision_flatten
    original_sorted = sorted
    engine_prefill_ms = 0.0
    engine_decode_ms = 0.0
    sort_ms = 0.0
    decode_steps = 0
    # Per-decode-step CPU stage timings (token >= 1 only, mirroring engine_decode_ms).
    cpu_prepare_ms = 0.0
    cpu_decision_ms = 0.0
    cpu_eos_ms = 0.0
    cpu_topk_ms = 0.0
    cpu_materialize_ms = 0.0
    # Token index of the engine step currently being processed; Step 3 output
    # handling runs against this token's outputs.
    current_token = -1
    phase_started: float | None = None
    decode_started: float | None = None

    def timed_custom(*args: Any, **kwargs: Any) -> Any:
        nonlocal phase_started
        if phase_started is None:
            phase_started = time.perf_counter()
        return original_custom(*args, **kwargs)

    def timed_prepare(*args: Any, **kwargs: Any) -> Any:
        nonlocal decode_started, cpu_prepare_ms
        token = int(kwargs.get("token", args[2] if len(args) > 2 else 0))
        started = time.perf_counter()
        if token == 1 and decode_started is None:
            decode_started = started
        result = original_prepare(*args, **kwargs)
        if token >= 1:
            cpu_prepare_ms += (time.perf_counter() - started) * 1000.0
        return result

    def timed_step(*args: Any, **kwargs: Any) -> Any:
        nonlocal engine_prefill_ms, engine_decode_ms, decode_steps, current_token
        token = int(kwargs.get("token", args[2] if len(args) > 2 else 0))
        current_token = token
        started = time.perf_counter()
        result = original_step(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if token == 0:
            engine_prefill_ms += elapsed_ms
        else:
            engine_decode_ms += elapsed_ms
            decode_steps += 1
        return result

    def timed_parse(*args: Any, **kwargs: Any) -> Any:
        """Time legacy flat-logprobs parsing (skipped on worker-decision path)."""
        nonlocal cpu_decision_ms
        started = time.perf_counter()
        result = original_parse(*args, **kwargs)
        if current_token >= 1:
            cpu_decision_ms += (time.perf_counter() - started) * 1000.0
        return result

    def timed_worker_from_result(*args: Any, **kwargs: Any) -> Any:
        """Time worker-decision extraction from the engine result."""
        nonlocal cpu_decision_ms
        started = time.perf_counter()
        result = original_worker_from_result(*args, **kwargs)
        if current_token >= 1:
            cpu_decision_ms += (time.perf_counter() - started) * 1000.0
        return result

    def timed_worker_flatten(*args: Any, **kwargs: Any) -> Any:
        """Time worker-decision flattening into candidate arrays."""
        nonlocal cpu_decision_ms
        started = time.perf_counter()
        result = original_worker_flatten(*args, **kwargs)
        if current_token >= 1:
            cpu_decision_ms += (time.perf_counter() - started) * 1000.0
        return result

    def timed_eos(*args: Any, **kwargs: Any) -> Any:
        nonlocal cpu_eos_ms
        started = time.perf_counter()
        result = original_eos(*args, **kwargs)
        if current_token >= 1:
            cpu_eos_ms += (time.perf_counter() - started) * 1000.0
        return result

    def timed_topk(*args: Any, **kwargs: Any) -> Any:
        nonlocal cpu_topk_ms
        started = time.perf_counter()
        result = original_topk(*args, **kwargs)
        if current_token >= 1:
            cpu_topk_ms += (time.perf_counter() - started) * 1000.0
        return result

    def timed_materialize(*args: Any, **kwargs: Any) -> Any:
        nonlocal cpu_materialize_ms
        started = time.perf_counter()
        result = original_materialize(*args, **kwargs)
        if current_token >= 1:
            cpu_materialize_ms += (time.perf_counter() - started) * 1000.0
        return result

    def timed_sorted(*args: Any, **kwargs: Any) -> Any:
        """Time GRLLM's single final completed-beam sort."""
        nonlocal sort_ms
        started = time.perf_counter()
        result = original_sorted(*args, **kwargs)
        sort_ms += (time.perf_counter() - started) * 1000.0
        return result

    started = time.perf_counter()
    with (
        mock.patch.object(gr_module, "_custom_beam_search_batch", timed_custom),
        mock.patch.object(gr_module, "_prepare_beam_step_requests", timed_prepare),
        mock.patch.object(gr_module, "_step_engine_and_collect_outputs", timed_step),
        mock.patch.object(gr_module, "_parse_step_logprobs", timed_parse),
        mock.patch.object(gr_module, "complete_eos_candidates", timed_eos),
        mock.patch.object(gr_module, "select_top_indices", timed_topk),
        mock.patch.object(gr_module, "materialize_selected_beams", timed_materialize),
        mock.patch.object(
            serving_engine_module, "_worker_decision_from_result", timed_worker_from_result
        ),
        mock.patch.object(
            serving_engine_module, "_worker_decision_flatten", timed_worker_flatten
        ),
        mock.patch.object(gr_module, "sorted", timed_sorted, create=True),
    ):
        outputs = llm.beam_search([{"prompt": prompt}], params, concurrency_limit=1)
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
    aggregate_output_tokens = len(output.sequences) * int(params.max_tokens)
    return {
        "e2e_ms": e2e_ms,
        "prefill_ms": prefill_ms,
        "decode_ms": decode_ms,
        "sort_ms": sort_ms,
        # Compatibility aggregate used by the online Prometheus collector.
        # Decode already includes Sort, so this is not a non-overlapping wall time.
        "total_beam_ms": prefill_ms + decode_ms + sort_ms,
        "beam_entry_overhead_ms": (phase_started - started) * 1000.0,
        "engine_prefill_ms": engine_prefill_ms,
        "engine_decode_ms": engine_decode_ms,
        "decode_overhead_ms": max(0.0, decode_ms - engine_decode_ms),
        "cpu_prepare_ms": cpu_prepare_ms,
        "cpu_decision_ms": cpu_decision_ms,
        "cpu_eos_ms": cpu_eos_ms,
        "cpu_topk_ms": cpu_topk_ms,
        "cpu_materialize_ms": cpu_materialize_ms,
        "decode_steps": decode_steps,
        "returned_beams": len(output.sequences),
        "aggregate_output_tokens": aggregate_output_tokens,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("/opt/vllm-gr/data"))
    parser.add_argument("--model", default="OpenOneRec/OneRec-1.7B")
    parser.add_argument("--task", default="video")
    parser.add_argument("--num-prompts", type=int, default=100)
    parser.add_argument("--warmup-requests", type=int, default=4)
    parser.add_argument("--beam-width", type=int, required=True)
    parser.add_argument("--input-length", type=int, required=True)
    args = parser.parse_args()
    if min(args.num_prompts, args.beam_width, args.input_length) < 1 or args.warmup_requests < 0:
        parser.error("num-prompts, beam-width, and input-length must be positive; warmup must be non-negative")

    from transformers import AutoTokenizer

    from benchmarks.open_one_rec.open_one_rec_dataset import OneRecDataset
    from vllm_gr.entrypoints.gr import GRLLM
    from vllm_gr.sampling_params import BeamSearchParams

    started_at = datetime.now(timezone.utc)
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    sample_size = max(args.num_prompts, args.warmup_requests)
    dataset = OneRecDataset(
        dataset_path=str(args.data_dir),
        task_types=[args.task],
        tokenizer=tokenizer,
        custom_input_len=args.input_length,
        sample_size=sample_size,
    )
    requests = dataset.sample(
        num_requests=sample_size,
        tokenizer=tokenizer,
        no_oversample=True,
    )
    if len(requests) < sample_size:
        raise RuntimeError(f"requested {sample_size} samples, dataset returned {len(requests)}")

    max_tokens = int(requests[0].expected_output_len)
    params = BeamSearchParams(beam_width=args.beam_width, max_tokens=max_tokens, temperature=0.0)
    params.begin_token = "<|sid_begin|>"
    params.end_token = "<|sid_end|>"

    miss_samples: list[dict[str, Any]] = []
    hit_samples: list[dict[str, Any]] = []
    with GRLLM(
        model=args.model,
        trust_remote_code=True,
        max_logprobs=1024,
        beam_graph_enabled=True,
        beam_max_width=args.beam_width,
        attention_config={"backend": "CUSTOM"},
        max_num_seqs=1024,
        max_num_batched_tokens=16384,
        scheduling_policy="priority",
    ) as llm:
        for request in requests[: args.warmup_requests]:
            llm.reset_prefix_cache()
            measure_request(llm, request.prompt, params)

        measured_requests = requests[: args.num_prompts]
        for index, request in enumerate(measured_requests, start=1):
            if not llm.reset_prefix_cache():
                raise RuntimeError(f"prefix cache reset failed before measured request {index}")
            miss_samples.append(measure_request(llm, request.prompt, params))
            hit_samples.append(measure_request(llm, request.prompt, params))

    finished_at = datetime.now(timezone.utc)
    fields = (
        "e2e_ms",
        "prefill_ms",
        "decode_ms",
        "sort_ms",
        "total_beam_ms",
        "engine_prefill_ms",
        "engine_decode_ms",
        "decode_overhead_ms",
        "beam_entry_overhead_ms",
        "cpu_prepare_ms",
        "cpu_decision_ms",
        "cpu_eos_ms",
        "cpu_topk_ms",
        "cpu_materialize_ms",
    )
    raw = {
        "schema_version": "vllm-gr.offline.raw.v1",
        "execution_mode": "offline",
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
        "miss": {field: [float(sample[field]) for sample in miss_samples] for field in fields},
        "hit": {field: [float(sample[field]) for sample in hit_samples] for field in fields},
        "decode_steps": [int(sample["decode_steps"]) for sample in miss_samples],
        "returned_beams": [int(sample["returned_beams"]) for sample in miss_samples],
        "aggregate_output_tokens": sum(int(sample["aggregate_output_tokens"]) for sample in miss_samples),
        "distributions": {
            f"{phase}_{cache_state}": distribution_ms([float(sample[phase]) for sample in samples])
            for cache_state, samples in (("miss", miss_samples), ("hit", hit_samples))
            for phase in fields
        },
        "engine_config": {
            "attention_backend": "CUSTOM",
            "beam_graph_enabled": True,
            "beam_max_width": args.beam_width,
            "max_logprobs": 1024,
            "max_num_seqs": 1024,
            "max_num_batched_tokens": 16384,
            "scheduling_policy": "priority",
            "single_request": True,
        },
        "phase_definition": {
            "version": "vllm-gr-serving-internal-v3",
            "e2e": "direct call start through beam_search return",
            "prefill": "internal beam token-loop start through completion of token 0",
            "decode": "start of token 1 preparation through beam_search return",
            "sort": "final sorted(completed, key=cum_logprob, reverse=True) call only",
            "total_beam": "compatibility aggregate: prefill + decode + sort; sort is already contained in decode",
            "entry_overhead": "direct call start through internal beam token-loop start",
            "average": "arithmetic mean: sum of observed phase wall times divided by request-observation count",
            "decode_cache_semantics": "common phase; miss/hit are repeated observations, not additive components",
            "cpu_prepare": "per-decode-token time building EngineCoreRequest / BeamRequestStepUpdate",
            "cpu_decision": "per-decode-token time parsing flat logprobs (legacy) or extracting+flattening worker decision (worker path)",
            "cpu_eos": "per-decode-token time materializing EOS candidates",
            "cpu_topk": "per-decode-token time selecting top-k indices (legacy path only; 0 on worker path)",
            "cpu_materialize": "per-decode-token time materializing selected beams + fork mapping",
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

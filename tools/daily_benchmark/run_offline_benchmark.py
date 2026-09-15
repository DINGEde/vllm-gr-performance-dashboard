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


def measure_e2e(llm: Any, prompt_token_ids: list[int], params: Any) -> dict[str, Any]:
    """Measure the production trend with no internal monkeypatches or profiler.

    Keep the timed region deliberately small: one direct ``beam_search`` call.
    Any diagnostic decomposition is collected only after all canonical samples.
    """
    started = time.perf_counter_ns()
    outputs = llm.beam_search([{"prompt_token_ids": prompt_token_ids}], params, concurrency_limit=1)
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


def measure_request_diagnostic(llm: Any, prompt_token_ids: list[int], params: Any) -> dict[str, Any]:
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
        "total_beam_ms": prefill_ms + decode_ms,
        "returned_beams": len(output.sequences),
        "aggregate_output_tokens": len(output.sequences) * int(params.max_tokens),
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
        # Align with abba make_prompt: submit INPUT_LEN-1 tokens; GRLLM appends
        # <|sid_begin|> to reach exactly INPUT_LEN model tokens.
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

    # Pre-encode prompts outside the timed region (abba script parity): the
    # tokenizer.encode otherwise runs inside GRLLM.beam_search's
    # _preprocess_cmpl, inside the perf_counter window, inflating miss/hit E2E.
    prompt_token_ids_list = [
        tokenizer.encode(request.prompt, add_special_tokens=False)
        for request in requests
    ]

    max_tokens = int(requests[0].expected_output_len)
    params = BeamSearchParams(beam_width=args.beam_width, max_tokens=max_tokens, temperature=0.0)
    params.begin_token = "<|sid_begin|>"
    params.end_token = "<|sid_end|>"
    # GR beam search prepends begin_token after the dataset has already shaped
    # the prompt to input_length. Align max_model_len with the abba E2E baseline.
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
    with GRLLM(
        model=args.model,
        trust_remote_code=True,
        max_logprobs=128,
        catalog_path=str(args.catalog),
        constraint_backend="constraint_table",
        beam_graph_enabled=True,
        beam_max_width=args.beam_width,
        attention_config={"backend": "CUSTOM"},
        max_num_seqs=128,
        max_num_batched_tokens=40960,
        max_model_len=engine_max_model_len,
        scheduling_policy="fcfs",
        async_scheduling=True,
        gpu_memory_utilization=0.5,
        additional_config=additional_config or None,
    ) as llm:
        for prompt_token_ids in prompt_token_ids_list[: args.warmup_requests]:
            llm.reset_prefix_cache()
            measure_e2e(llm, prompt_token_ids, params)

        measured_requests = requests[: args.num_prompts]
        for index, (request, prompt_token_ids) in enumerate(
            zip(measured_requests, prompt_token_ids_list[: args.num_prompts]), start=1
        ):
            if not llm.reset_prefix_cache():
                raise RuntimeError(f"prefix cache reset failed before measured request {index}")
            miss_samples.append(measure_e2e(llm, prompt_token_ids, params))
            hit_samples.append(measure_e2e(llm, prompt_token_ids, params))

        # Diagnostic requests deliberately run after every canonical sample so
        # their monkeypatches cannot inflate the official daily E2E trend.
        diagnostic_count = min(max(0, args.diagnostic_prompts), len(measured_requests))
        for index, (request, prompt_token_ids) in enumerate(
            zip(measured_requests[:diagnostic_count], prompt_token_ids_list[:diagnostic_count]), start=1
        ):
            if not llm.reset_prefix_cache():
                raise RuntimeError(f"prefix cache reset failed before diagnostic request {index}")
            diagnostic_miss_samples.append(
                measure_request_diagnostic(llm, prompt_token_ids, params)
            )
            diagnostic_hit_samples.append(
                measure_request_diagnostic(llm, prompt_token_ids, params)
            )

    finished_at = datetime.now(timezone.utc)
    fields = (
        "e2e_ms",
        "prefill_ms",
        "decode_ms",
        "total_beam_ms",
    )
    raw = {
        "schema_version": "vllm-gr.offline.raw.v2",
        "execution_mode": "offline",
        "measurement_mode": "canonical-with-posthoc-diagnostic",
        "instrumentation": {
            "canonical": "top-level perf_counter_ns only; no profiler or internal monkeypatch",
            "diagnostic": "post-canonical minimal 2-function perf_counter monkeypatch (prefill/decode boundary only)",
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
                "version": "vllm-gr-serving-internal-v4-minimal",
                "e2e": "instrumented direct call start through beam_search return",
                "prefill": "internal beam token-loop start through completion of token 0",
                "decode": "start of token 1 preparation through beam_search return",
                "total_beam": "prefill + decode (internal beam token-loop total)",
                "average": "arithmetic mean over post-canonical diagnostic observations",
                "decode_cache_semantics": "common phase; miss/hit are repeated observations, not additive components",
            },
        },
        "phase_definition": {
            "version": "vllm-gr-canonical-e2e-v1",
            "e2e": "unmodified direct GRLLM.beam_search call start through return",
            "cache_protocol": "paired cold miss followed by identical warm hit",
            "internal_stages": "reported separately from post-canonical diagnostic samples",
        },
        "engine_config": {
            "attention_backend": "CUSTOM",
            "beam_graph_enabled": True,
            "beam_max_width": args.beam_width,
            "max_logprobs": 128,
            "max_num_seqs": 128,
            "max_num_batched_tokens": 40960,
            "max_model_len": engine_max_model_len,
            "scheduling_policy": "fcfs",
            "gpu_memory_utilization": 0.5,
            "catalog_path": str(args.catalog),
            "constraint_backend": "constraint_table",
            "single_request": True,
            "beam_engine_driven": args.beam_engine_driven,
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

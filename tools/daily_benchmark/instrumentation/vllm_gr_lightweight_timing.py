"""Low-overhead, aggregate-only CPU timing for vLLM EngineCore workers.

This is deliberately not a profiler.  Each wrapped call reads two monotonic
clocks and updates in-memory aggregates.  No logging or file I/O occurs in the
hot path; one JSON file per process is written during GPUModelRunner shutdown
or normal interpreter exit.
"""

from __future__ import annotations

import atexit
import functools
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable


_LOCK = threading.Lock()
_STATS: dict[str, dict[str, Any]] = {}
_INSTALLED = False
_FLUSHED = False
_TLS = threading.local()
_CAPABILITIES: dict[str, Any] = {}


def _record(name: str, wall_ns: int, cpu_ns: int) -> None:
    with _LOCK:
        row = _STATS.setdefault(
            name,
            {
                "count": 0,
                "wall_ns_total": 0,
                "wall_ns_min": wall_ns,
                "wall_ns_max": wall_ns,
                "thread_cpu_ns_total": 0,
                "wall_ns_samples": [],
                "thread_cpu_ns_samples": [],
            },
        )
        row["count"] += 1
        row["wall_ns_total"] += wall_ns
        row["wall_ns_min"] = min(row["wall_ns_min"], wall_ns)
        row["wall_ns_max"] = max(row["wall_ns_max"], wall_ns)
        row["thread_cpu_ns_total"] += cpu_ns
        row["wall_ns_samples"].append(wall_ns)
        row["thread_cpu_ns_samples"].append(cpu_ns)


def _scope_depth(name: str) -> int:
    return int(getattr(_TLS, f"scope_{name}", 0))


def _set_scope_depth(name: str, value: int) -> None:
    setattr(_TLS, f"scope_{name}", value)


def _beam_decode_scheduler_output(value: Any) -> bool:
    beam_data = getattr(value, "beam_data", None) or {}
    if any(bool(metadata.get("is_beam_decode")) for metadata in beam_data.values()):
        return True
    # Beam Search V1 attaches one semantic stage record per session instead of
    # legacy beam_data/is_beam_decode flags. This includes output-producing and
    # chunked Prefill dispatches as well as Decode dispatches.
    return bool(getattr(value, "gr_stage_metadata", None))


def _execute_is_beam_decode(args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
    scheduler_output = kwargs.get("scheduler_output", args[1] if len(args) > 1 else None)
    return _beam_decode_scheduler_output(scheduler_output)


def _sample_is_beam_decode(args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
    runner = args[0] if args else None
    state = getattr(runner, "execute_model_state", None)
    scheduler_output = state[0] if state else None
    return _beam_decode_scheduler_output(scheduler_output)


def _scheduler_has_beam_decode(args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
    owner = args[0] if args else None
    scheduler = getattr(owner, "scheduler", owner)
    requests = getattr(scheduler, "requests", {}) or {}
    return bool(getattr(scheduler, "_gr_v1_sessions", None)) or any(
        bool(getattr(request, "is_beam_decode", False)) for request in requests.values()
    )


def _executor_sample_is_beam_decode(args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
    executor = args[0] if args else None
    wrapper = getattr(executor, "driver_worker", None)
    worker = getattr(wrapper, "worker", None)
    runner = getattr(worker, "model_runner", None)
    state = getattr(runner, "execute_model_state", None)
    return _beam_decode_scheduler_output(state[0] if state else None)


def _timed(
    name: str,
    original: Callable[..., Any],
    *,
    skip_dummy: bool = False,
    predicate: Callable[[tuple[Any, ...], dict[str, Any]], bool] | None = None,
    require_scope: str | None = None,
    activate_scope: str | None = None,
):
    @functools.wraps(original)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if require_scope is not None and _scope_depth(require_scope) < 1:
            return original(*args, **kwargs)
        if predicate is not None and not predicate(args, kwargs):
            return original(*args, **kwargs)
        if skip_dummy:
            dummy_run = bool(kwargs.get("dummy_run", args[3] if len(args) > 3 else False))
            if dummy_run:
                return original(*args, **kwargs)
        wall_start = time.perf_counter_ns()
        cpu_start = time.thread_time_ns()
        if activate_scope is not None:
            _set_scope_depth(activate_scope, _scope_depth(activate_scope) + 1)
        try:
            return original(*args, **kwargs)
        finally:
            if activate_scope is not None:
                _set_scope_depth(activate_scope, _scope_depth(activate_scope) - 1)
            _record(
                name,
                time.perf_counter_ns() - wall_start,
                time.thread_time_ns() - cpu_start,
            )

    setattr(wrapped, "__vllm_gr_lightweight_timing__", True)
    return wrapped


def _patch_method(
    cls: type,
    method_name: str,
    metric_name: str,
    *,
    skip_dummy: bool = False,
    predicate: Callable[[tuple[Any, ...], dict[str, Any]], bool] | None = None,
    require_scope: str | None = None,
    activate_scope: str | None = None,
) -> None:
    original = getattr(cls, method_name, None)
    if original is None or getattr(original, "__vllm_gr_lightweight_timing__", False):
        return
    setattr(
        cls,
        method_name,
        _timed(
            metric_name,
            original,
            skip_dummy=skip_dummy,
            predicate=predicate,
            require_scope=require_scope,
            activate_scope=activate_scope,
        ),
    )


def flush() -> None:
    global _FLUSHED
    output_dir = os.environ.get("VLLM_GR_LIGHTWEIGHT_TIMING_DIR")
    if _FLUSHED or not output_dir or not _STATS:
        return
    _FLUSHED = True
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        snapshot = {name: dict(values) for name, values in _STATS.items()}
    payload = {
        "schema_version": "vllm-gr.cpu-timing.v1",
        "pid": os.getpid(),
        "clock": {
            "wall": "time.perf_counter_ns",
            "cpu": "time.thread_time_ns",
        },
        "hot_path_io": False,
        "metrics": snapshot,
        "capabilities": dict(_CAPABILITIES),
    }
    target = path / f"cpu-timing-{os.getpid()}.json"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # Record the tested revision's capabilities separately from runtime path
    # counts. The dashboard follows origin/decode_graph, so older commits must
    # remain measurable and visibly report that a newer optimization is absent.
    try:
        from vllm_gr.v1.worker import prefill_graph_common

        buckets = list(prefill_graph_common.get_default_buckets())
        _CAPABILITIES.update(
            prefill_graph_buckets=buckets,
            small_prefill_buckets_available=bool(1 in buckets and 16 in buckets),
            configured_prefill_graph_kv_bound=os.environ.get(
                "VLLM_GR_PREFILL_GRAPH_KV_BOUND"
            ),
            kv_bound_override_supported=hasattr(
                prefill_graph_common, "get_kv_capture_bound"
            ),
        )
    except (ImportError, AttributeError, ValueError) as exc:
        _CAPABILITIES["prefill_graph_capability_error"] = repr(exc)

    # Count constrained prelaunch stages directly. Refresh aliases imported by
    # gpu_model_runner_patch so wrapping the defining module cannot be bypassed.
    try:
        import vllm_gr.v1.worker.constraint_hook as constraint_hook
        import vllm_gr.v1.worker.gpu_model_runner_patch as gpu_patch

        for function_name, metric_name in (
            ("prepare_cuda_prefill_constraint", "constraint_prepare"),
            ("launch_prepared_cuda_prefill_constraint", "constraint_prelaunch"),
            ("consume_prelaunched_cuda_prefill", "constraint_consume_prelaunched"),
            ("try_sample_with_constraint_backend", "constraint_try_sample"),
        ):
            original = getattr(constraint_hook, function_name, None)
            if original is None or getattr(original, "__vllm_gr_lightweight_timing__", False):
                continue
            wrapped = _timed(metric_name, original)
            setattr(constraint_hook, function_name, wrapped)
            if getattr(gpu_patch, function_name, None) is original:
                setattr(gpu_patch, function_name, wrapped)
    except ImportError:
        _CAPABILITIES["constrained_prelaunch_available"] = False
    else:
        _CAPABILITIES["constrained_prelaunch_available"] = all(
            hasattr(constraint_hook, name)
            for name in (
                "prepare_cuda_prefill_constraint",
                "launch_prepared_cuda_prefill_constraint",
                "consume_prelaunched_cuda_prefill",
            )
        )

    # A call to execute_model_prefill is an attempted custom prefill replay.
    # Its return value tells us whether the model graph replayed or fell back.
    try:
        from vllm_gr.v1.worker.gpu_prefill_graph_runner import GPUPrefillGraphRunner

        original_prefill = GPUPrefillGraphRunner.execute_model_prefill
        if not getattr(original_prefill, "__vllm_gr_lightweight_timing__", False):
            @functools.wraps(original_prefill)
            def timed_prefill_graph(self: Any, bucket: int, *args: Any, **kwargs: Any) -> Any:
                wall_start = time.perf_counter_ns()
                cpu_start = time.thread_time_ns()
                result = original_prefill(self, bucket, *args, **kwargs)
                outcome = "replay" if result is not None else "fallback"
                _record(
                    f"prefill_graph_{outcome}_bucket_{int(bucket)}",
                    time.perf_counter_ns() - wall_start,
                    time.thread_time_ns() - cpu_start,
                )
                return result

            setattr(timed_prefill_graph, "__vllm_gr_lightweight_timing__", True)
            GPUPrefillGraphRunner.execute_model_prefill = timed_prefill_graph
    except (ImportError, AttributeError):
        _CAPABILITIES["gpu_prefill_graph_runner_available"] = False
    else:
        _CAPABILITIES["gpu_prefill_graph_runner_available"] = True

    # vLLM 0.22 ships both the established GPUModelRunner and ModelRunner V2.
    # OneRec currently selects the established runner; instrument both so a
    # future runtime switch does not silently produce an empty diagram.
    runner_classes: list[type] = []
    try:
        from vllm.v1.worker.gpu_model_runner import (
            AsyncGPUModelRunnerOutput,
            GPUModelRunner as LegacyGPUModelRunner,
        )

        runner_classes.append(LegacyGPUModelRunner)
        for method_name, metric_name in (
            ("_update_states", "update_states"),
            ("_prepare_inputs", "prepare_inputs"),
            ("_determine_batch_execution_and_padding", "determine_batch"),
            ("_get_slot_mappings", "prepare_attn_buffers"),
            ("_build_attention_metadata", "prepare_attn_metadata"),
            ("_preprocess", "preprocess_model_inputs"),
            ("_model_forward", "run_model_forward"),
            ("_sample", "sample"),
            ("_update_states_after_model_execute", "update_states_after_execute"),
            ("_bookkeeping_sync", "bookkeeping_sync"),
        ):
            scope = "beam_sample" if method_name in {
                "_sample", "_update_states_after_model_execute", "_bookkeeping_sync"
            } else "beam_execute"
            _patch_method(
                LegacyGPUModelRunner,
                method_name,
                metric_name,
                require_scope=scope,
            )

        original_async_init = AsyncGPUModelRunnerOutput.__init__
        if not getattr(original_async_init, "__vllm_gr_lightweight_timing__", False):
            @functools.wraps(original_async_init)
            def timed_async_init(self: Any, *args: Any, **kwargs: Any) -> None:
                selected = _scope_depth("beam_sample") > 0
                if not selected:
                    original_async_init(self, *args, **kwargs)
                    return
                wall_start = time.perf_counter_ns()
                cpu_start = time.thread_time_ns()
                try:
                    original_async_init(self, *args, **kwargs)
                    self._vllm_gr_timed_beam_decode = True
                finally:
                    _record(
                        "async_output_create",
                        time.perf_counter_ns() - wall_start,
                        time.thread_time_ns() - cpu_start,
                    )

            setattr(timed_async_init, "__vllm_gr_lightweight_timing__", True)
            AsyncGPUModelRunnerOutput.__init__ = timed_async_init
        _patch_method(
            AsyncGPUModelRunnerOutput,
            "get_output",
            "async_output_get_output",
            predicate=lambda args, kwargs: bool(
                args and getattr(args[0], "_vllm_gr_timed_beam_decode", False)
            ),
        )
    except (ImportError, AttributeError):
        pass

    try:
        from vllm.v1.worker.gpu.cudagraph_utils import ModelCudaGraphManager
        from vllm.v1.worker.gpu.model_runner import GPUModelRunner as V2GPUModelRunner

        runner_classes.append(V2GPUModelRunner)
        _patch_method(V2GPUModelRunner, "prepare_inputs", "prepare_inputs", require_scope="beam_execute")
        _patch_method(V2GPUModelRunner, "prepare_attn", "prepare_attn_buffers", require_scope="beam_execute")
        _patch_method(V2GPUModelRunner, "sample", "sample", require_scope="beam_sample")
        _patch_method(V2GPUModelRunner, "postprocess", "postprocess", require_scope="beam_sample")
        _patch_method(ModelCudaGraphManager, "run_fullgraph", "run_fullgraph", require_scope="beam_execute")
    except (ImportError, AttributeError):
        pass

    for runner_cls in runner_classes:
        _patch_method(
            runner_cls,
            "execute_model",
            "execute_model",
            skip_dummy=True,
            predicate=_execute_is_beam_decode,
            activate_scope="beam_execute",
        )
        _patch_method(
            runner_cls,
            "sample_tokens",
            "sample_tokens",
            predicate=_sample_is_beam_decode,
            activate_scope="beam_sample",
        )

    # TP=1 uses UniProcExecutor: the readiness wait runs on the EngineCore
    # thread inside AsyncOutputFuture.result(), not on a WorkerAsyncOutputCopy
    # thread as in the TP=2 reference report.
    try:
        from vllm.v1.executor.uniproc_executor import AsyncOutputFuture, UniProcExecutor

        _patch_method(
            UniProcExecutor,
            "execute_model",
            "engine_submit_execute_model",
            predicate=_execute_is_beam_decode,
        )
        _patch_method(
            UniProcExecutor,
            "sample_tokens",
            "engine_submit_sample_tokens",
            predicate=_executor_sample_is_beam_decode,
        )
        _patch_method(
            AsyncOutputFuture,
            "result",
            "engine_wait_output_future",
            predicate=lambda args, kwargs: bool(
                args
                and getattr(
                    getattr(args[0], "async_output", None),
                    "_vllm_gr_timed_beam_decode",
                    False,
                )
            ),
        )
    except (ImportError, AttributeError):
        pass

    try:
        from vllm.v1.core.sched.scheduler import Scheduler
        from vllm.v1.engine.core import EngineCore

        _patch_method(
            Scheduler,
            "schedule",
            "scheduler_schedule",
            predicate=_scheduler_has_beam_decode,
        )
        _patch_method(
            Scheduler,
            "update_from_output",
            "scheduler_update",
            predicate=lambda args, kwargs: _beam_decode_scheduler_output(
                kwargs.get("scheduler_output", args[1] if len(args) > 1 else None)
            ),
        )
        _patch_method(
            EngineCore,
            "step_with_batch_queue",
            "engine_async_step",
            predicate=_scheduler_has_beam_decode,
        )
    except (ImportError, AttributeError):
        pass

    # Beam Search V1 owns sampling, terminal compact D2H, and retirement in
    # vllm-gr classes outside the upstream GPUModelRunner wrappers. Keep these
    # measurements diagnostic-only and avoid synchronization beyond waits the
    # production path already performs.
    try:
        from vllm_gr.v1.worker.beam_final_output import GPUBeamFinalOutput
        from vllm_gr.v1.worker.gpu_beam_stage_runner import (
            AsyncGPUBeamOutput,
            AsyncGRRetireOutput,
            GPUBeamStageRunner,
        )

        _patch_method(GPUBeamStageRunner, "execute", "gr_v1_stage_execute")
        _patch_method(GPUBeamStageRunner, "sample", "gr_v1_stage_sample")
        _patch_method(GPUBeamStageRunner, "request_release", "gr_v1_request_release")
        _patch_method(AsyncGPUBeamOutput, "get_output", "gr_v1_async_output_get_output")
        _patch_method(AsyncGRRetireOutput, "get_output", "gr_v1_retire_get_output")
        _patch_method(GPUBeamFinalOutput, "_enqueue", "gr_v1_final_output_enqueue")
        _patch_method(
            GPUBeamFinalOutput,
            "wait_embedded_control",
            "gr_v1_final_output_wait_control",
        )
        _patch_method(GPUBeamFinalOutput, "consume", "gr_v1_final_output_consume")
        _CAPABILITIES["beam_search_v1_timing"] = True
    except (ImportError, AttributeError):
        _CAPABILITIES["beam_search_v1_timing"] = False

    # vLLM-gr worker decision is outside upstream _sample's original body.
    # Patch the semantic function and refresh the copied module alias when the
    # patch module has already been imported.
    try:
        import vllm_gr.v1.beam.beam_decision as beam_decision

        original_decision = beam_decision.maybe_run_worker_beam_decision
        wrapped_decision = _timed(
            "beam_worker_decision", original_decision, require_scope="beam_sample"
        )
        beam_decision.maybe_run_worker_beam_decision = wrapped_decision
        try:
            import vllm_gr.v1.worker.gpu_model_runner_patch as gpu_patch

            gpu_patch.maybe_run_worker_beam_decision = wrapped_decision
        except ImportError:
            pass
    except (ImportError, AttributeError):
        pass

    # OneRec uses DefaultModelState today.  Patch all shipped model-state
    # variants so the probe remains valid if the selected model changes.
    for module_name, class_name in (
        ("vllm.v1.worker.gpu.model_states.default", "DefaultModelState"),
        ("vllm.v1.worker.gpu.model_states.whisper", "WhisperModelState"),
        ("vllm.v1.worker.gpu.model_states.mamba_hybrid", "MambaHybridModelState"),
    ):
        try:
            module = __import__(module_name, fromlist=[class_name])
            cls = getattr(module, class_name)
        except (ImportError, AttributeError):
            continue
        _patch_method(
            cls,
            "prepare_inputs",
            "model_state_prepare_inputs",
            require_scope="beam_execute",
        )
        _patch_method(
            cls,
            "prepare_attn",
            "prepare_attn_metadata",
            require_scope="beam_execute",
        )

    for runner_cls in runner_classes:
        original_shutdown = runner_cls.shutdown
        if getattr(original_shutdown, "__vllm_gr_lightweight_timing__", False):
            continue

        @functools.wraps(original_shutdown)
        def shutdown_and_flush(self: Any, *args: Any, __original=original_shutdown, **kwargs: Any) -> Any:
            try:
                return __original(self, *args, **kwargs)
            finally:
                flush()

        setattr(shutdown_and_flush, "__vllm_gr_lightweight_timing__", True)
        runner_cls.shutdown = shutdown_and_flush

    atexit.register(flush)

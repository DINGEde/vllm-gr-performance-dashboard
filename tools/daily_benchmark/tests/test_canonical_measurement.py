from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "run_offline_benchmark.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_offline_benchmark", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
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
    result = module.measure_request_canonical(llm, "prompt", params)

    assert len(llm.calls) == 1
    assert llm.calls[0][2] == 1
    assert result["e2e_ms"] >= 0
    assert result["returned_beams"] == 2
    assert result["aggregate_output_tokens"] == 10


def test_native_phases_are_additive_without_sort_double_count():
    module = load_module()
    class Llm:
        def beam_search(self, *args, **kwargs):
            self._benchmark_phase_marks = [1000000, 4000000, 9000000]
            return [SimpleNamespace(sequences=[object()])]
    result = module.measure_request_canonical(Llm(), "prompt", SimpleNamespace(max_tokens=5))
    assert result["prefill_ms"] == 3
    assert result["decode_ms"] == 5
    assert result["total_beam_ms"] == 8


def test_cache_pair_is_primed_hit_then_reset_miss() -> None:
    module = load_module()
    events = []

    class Llm:
        def reset_prefix_cache(self):
            events.append("reset")
            return True

        def beam_search(self, *args, **kwargs):
            events.append("prime")
            return [SimpleNamespace(sequences=[object()])]

    def measured(*args, **kwargs):
        events.append("measured")
        return {"sequence": len(events)}

    hit, miss = module.measure_hit_then_miss(
        Llm(), "prompt", SimpleNamespace(max_tokens=5), measured, "test request"
    )

    assert events == ["reset", "prime", "measured", "reset", "measured"]
    assert hit["sequence"] < miss["sequence"]

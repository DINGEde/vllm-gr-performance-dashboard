from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("torch", reason="torch is required to import the NPU benchmark module")

WORKTREE = Path(__file__).resolve().parents[1]
BENCHMARK = WORKTREE / "tools" / "daily_benchmark" / "run_offline_benchmark_npu.py"
NPU_BUILDER = WORKTREE / "scripts" / "build_npu_dashboard.py"

RAW_V2_SCHEMA = "vllm-gr.offline.raw.v2"


def load_benchmark():
    spec = importlib.util.spec_from_file_location("run_offline_benchmark_npu", BENCHMARK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_npu_builder():
    spec = importlib.util.spec_from_file_location("build_npu_dashboard", NPU_BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def benchmark():
    return load_benchmark()


@pytest.mark.cpu_test
def test_distribution_ms_percentiles_and_unit(benchmark) -> None:
    d = benchmark.distribution_ms([1.0, 2.0, 3.0, 4.0, 5.0])
    assert d["unit"] == "ms"
    assert d["mean"] == pytest.approx(3.0)
    assert d["p50"] == pytest.approx(3.0)
    # p90, p95, p99 are monotonic across the sample.
    assert d["p90"] <= d["p95"] <= d["p99"]
    assert set(d) == {"mean", "std", "p50", "p90", "p95", "p99", "unit"}


@pytest.mark.cpu_test
def test_distribution_ms_single_value_is_stable(benchmark) -> None:
    d = benchmark.distribution_ms([7.5])
    assert d["mean"] == pytest.approx(7.5)
    assert d["p50"] == pytest.approx(7.5)
    assert d["p99"] == pytest.approx(7.5)


@pytest.mark.cpu_test
def test_distribution_ms_rejects_empty_sample(benchmark) -> None:
    with pytest.raises(ValueError, match="empty"):
        benchmark.distribution_ms([])


@pytest.mark.cpu_test
def test_parse_int_list(benchmark) -> None:
    assert benchmark.parse_int_list("widths", "1,2,3") == [1, 2, 3]
    assert benchmark.parse_int_list("widths", " 64 , 128 ") == [64, 128]
    assert benchmark.parse_int_list("widths", None) is None
    with pytest.raises(ValueError):
        benchmark.parse_int_list("widths", "1,,3")
    with pytest.raises(ValueError):
        benchmark.parse_int_list("widths", "1,abc")


@pytest.mark.cpu_test
def test_build_beam_schedule_defaults(benchmark) -> None:
    s = benchmark.build_beam_schedule(beam_width=128, generated_steps=3)
    assert s["generated_steps"] == 3
    assert s["dynamic_beam_widths"] is None
    assert s["per_parent_beam_widths"] is None
    assert s["candidate_width"] == 128
    assert s["final_width"] == 128


@pytest.mark.cpu_test
def test_build_beam_schedule_decoupled_candidates(benchmark) -> None:
    s = benchmark.build_beam_schedule(
        beam_width=256,
        generated_steps=3,
        dynamic_beam_widths=[64, 128, 256],
        per_parent_beam_widths=[64, 128, 64],
    )
    assert s["candidate_width"] == 128
    assert s["final_width"] == 256


@pytest.mark.cpu_test
def test_build_beam_schedule_rejects_mismatched_width_list(benchmark) -> None:
    with pytest.raises(ValueError, match="one width per generated token"):
        benchmark.build_beam_schedule(
            beam_width=128, generated_steps=3, dynamic_beam_widths=[64, 128]
        )


@pytest.mark.cpu_test
def test_build_beam_schedule_rejects_oversized_beam_without_decoupling(benchmark) -> None:
    # beam_width 512 needs a per-parent candidate schedule to stay under the
    # 256-candidate operator ceiling; the bare schedule must fail loudly.
    with pytest.raises(ValueError, match="256 operator ceiling"):
        benchmark.build_beam_schedule(beam_width=512, generated_steps=3)


@pytest.mark.cpu_test
def test_build_beam_schedule_rejects_out_of_range_generated_steps(benchmark) -> None:
    with pytest.raises(ValueError, match="max_tokens"):
        benchmark.build_beam_schedule(beam_width=128, generated_steps=4)


@pytest.mark.cpu_test
def test_make_prompt_pads_to_input_len_minus_one(benchmark, tmp_path: Path) -> None:
    class FakeTokenizer:
        def encode(self, text, add_special_tokens=False):
            if text == "\n":
                return [99]
            return [10, 20, 30]

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("abc", encoding="utf-8")
    tokens = benchmark.make_prompt(FakeTokenizer(), prompt, input_len=5)
    # input_len includes the begin token appended by beam_search_v1, so the
    # submitted prompt is input_len - 1 tokens, left-padded with newlines.
    assert tokens == [99, 10, 20, 30]


@pytest.mark.cpu_test
def test_make_prompt_truncates_oversized_source(benchmark, tmp_path: Path) -> None:
    class FakeTokenizer:
        def encode(self, text, add_special_tokens=False):
            if text == "\n":
                return [99]
            return [10, 20, 30, 40, 50]

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("abcde", encoding="utf-8")
    assert benchmark.make_prompt(FakeTokenizer(), prompt, input_len=3) == [40, 50]


@pytest.mark.cpu_test
def test_make_prompt_rejects_input_len_below_two(benchmark, tmp_path: Path) -> None:
    class FakeTokenizer:
        def encode(self, text, add_special_tokens=False):
            return [10]

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("a", encoding="utf-8")
    with pytest.raises(ValueError, match="at least 2"):
        benchmark.make_prompt(FakeTokenizer(), prompt, input_len=1)


@pytest.mark.cpu_test
def test_benchmark_distributions_feed_the_v2_collector(benchmark, tmp_path: Path) -> None:
    builder = load_npu_builder()
    e2e_miss = benchmark.distribution_ms([100.0, 110.0, 120.0])
    e2e_hit = benchmark.distribution_ms([40.0, 44.0, 48.0])
    raw = {
        "schema_version": RAW_V2_SCHEMA,
        "device": "npu",
        "beam_width": 8,
        "batch_size": 1,
        "input_length": 256,
        "model": "/models/qwen2.5-7b-instruct",
        "distributions": {"e2e_ms_miss": e2e_miss, "e2e_ms_hit": e2e_hit},
        "miss": {"e2e_ms": [100.0, 110.0, 120.0]},
        "hit": {"e2e_ms": [40.0, 44.0, 48.0]},
        "num_prompts": 3,
    }
    path = tmp_path / "npu-raw-v2.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    points = builder.collect_raw_v2_points([tmp_path], None)
    assert len(points) == 1
    point = points[0]
    assert point["scenario_key"] == "b1-bw8-in256"
    assert point["distributions"]["e2e_ms_miss"]["mean"] == pytest.approx(110.0)
    assert point["distributions"]["e2e_ms_hit"]["p50"] == pytest.approx(44.0)


@pytest.mark.cpu_test
def test_raw_v2_document_contract_is_present(benchmark) -> None:
    # The raw document is assembled inline in main(), so its shape is pinned
    # against the exact collector/UI contract rather than executed (there is no
    # Ascend device here to run main() on).
    source = BENCHMARK.read_text(encoding="utf-8")
    assert '"schema_version": "vllm-gr.offline.raw.v2"' in source
    assert '"device": "npu"' in source
    # Canonical miss/hit E2E pair the collector reads for the daily trend.
    assert 'f"e2e_ms_{cache_state}"' in source
    # Diagnostic distributions carry every stage the v2 page charts.
    for field in (
        "e2e_ms",
        "prefill_ms",
        "decode_ms",
        "prefill_gpu_compute_ms",
        "decode_gpu_compute_ms",
        "prefill_device_idle_ms",
        "decode_device_idle_ms",
        "prefill_dispatch_ms",
        "prefill_cpu_lead_ms",
        "host_overhead_ms",
        "prefill_output_consumed_ms",
    ):
        assert f'"{field}"' in source, field
    assert '"npu_beam_graph"' in source
    assert '"engine_config"' in source
    assert '"constraint_table_backend": "npu"' in source
    assert '"npu_execution": args.execution' in source

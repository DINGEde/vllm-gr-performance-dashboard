# vllm-gr daily result format

The dashboard consumes one compact `vllm-gr-summary.json` per benchmark lifecycle. Raw responses, Prometheus snapshots, GPU telemetry, and service logs remain on the runner host.

The canonical JSON Schema is stored at `schemas/vllm-gr-daily-summary.schema.json`. Run artifacts use this layout:

```text
runs/vllm-gr/<host>/<YYYY-MM-DD>/vllm-gr-summary.json
```

## Qualification rules

A run may be shown without being eligible for the production trend. The two booleans are intentionally separate:

- `baseline_eligible`: the environment, real dataset, warmup, request success, and metric semantics are suitable for establishing a baseline.
- `trend_eligible`: the run belongs in the default daily trend series. Normally this becomes true only after the baseline protocol is frozen.

Synthetic smoke/control runs must set `dataset.representative=false`. A real daily run should pin:

- repository SHA, image digest, model revision, and runtime versions;
- dataset revision and Parquet SHA-256;
- fixed sample IDs and their SHA-256;
- server and benchmark arguments;
- output-token semantics (`single-sequence` or `beam-aggregate`);
- all required latency distributions in milliseconds.

## Metric contract

- `TTFT`: request-side time until the first streamed token-bearing event.
- `TPOT`: end-to-end duration after TTFT divided by generated output-token count; interpret with `results.tokens.output_semantics`.
- `ITL`: request-side interval between streamed token-bearing events.
- `E2EL`: request dispatch until the terminal response event.
- Throughput values are measured over the benchmark wall-clock duration.

For beam search, output-token throughput, TPOT, and ITL are not comparable with a single-sequence run unless their token and event semantics are identical.

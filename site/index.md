# AgentCache Benchmark Dashboard

Generated from compact `dashboard-summary.json` benchmark artifacts.

Runs indexed: **1**

## Baseline daily averages

| run | host | commit | status | completed | failed | patches | wall_s | latency_mean | ttft_mean | queue_mean | prefix_hit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10015/2026-07-08/historical-dryrun | zhike | not-a-g | historical-dryrun | 8.600 | 16.20 | 7.200 | 3728.0 | 44.26 | 23.09 | — | 0.724 |

## Router daily averages

| run | host | commit | status | completed | failed | patches | wall_s | latency_mean | ttft_mean | queue_mean | prefix_hit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10015/2026-07-08/historical-dryrun | zhike | not-a-g | historical-dryrun | 18.20 | 6.600 | 17.60 | 3690.8 | 13.11 | 4.040 | — | 0.915 |

## Baseline vs router average comparison

| run | commit | completed Δ% | failed Δ% | patches Δ% | wall_s Δ% | latency_mean Δ% | ttft_mean Δ% | queue_mean Δ% | prefix_hit Δ% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10015/2026-07-08/historical-dryrun | not-a-g | +111.6% | -59.3% | +144.4% | -1.0% | -70.4% | -82.5% | — | +26.3% |

## High-load comparison only: 32/16 + 64/32

| run | commit | completed Δ% | failed Δ% | patches Δ% | wall_s Δ% | latency_mean Δ% | ttft_mean Δ% | queue_mean Δ% | prefix_hit Δ% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10015/2026-07-08/historical-dryrun | not-a-g | +215.0% | -56.6% | +335.7% | +6.9% | -78.5% | -86.4% | — | +111.4% |


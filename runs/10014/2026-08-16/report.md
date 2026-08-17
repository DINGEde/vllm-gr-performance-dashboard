# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-16
**Host**: L20-10014 (2×L20, 80GB each)
**Commit**: `37e3adc353afebcb1b6be92cc94dd8b28ccedebf`
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2 (4-arm run)

## Summary

All four runner manifests completed at timestamp `20260816_203245`; the standard daily run had no watchdog. AgentCache improved performance in both agent families while preserving the CC completion count (28/32 for both arms). JiuwenSwarm candidate completed 24 tasks versus 11 baseline.

### Claude Code

- **Completed / failed**: unchanged at 28 / 4
- **Tasks with patch**: 30 → 28
- **Wall time**: −41.6% (11448s → 6686s)
- **Mean task duration**: −45.0% (5368s → 2950s)
- **Input token throughput**: +72.3% (5781/s → 9958/s)
- **vLLM prefix cache hit rate**: +48.0 pp (22.8% → 70.8%)
- **TTFT P50**: −76.6% (11.13s → 2.61s)

### JiuwenSwarm

- **Completed / failed**: 11 / 21 → 24 / 8
- **Tasks with patch**: unchanged at 32
- **Wall time**: −45.1% (3188s → 1749s)
- **Mean task duration**: −47.9% (1501s → 782s)
- **Input token throughput**: +232.1% (4146/s → 13770/s)
- **vLLM prefix cache hit rate**: +51.4 pp (37.6% → 89.0%)
- **TTFT P50**: −97.9% (53.11s → 1.11s)

> JiuwenSwarm comparison reports cannot confirm cold start for either arm. This does not alter the within-run baseline/candidate metric comparison; retain the warning when comparing this series across cold-start-sensitive runs.

## Metrics Comparison

| Metric | CC baseline | CC candidate | JiuwenSwarm baseline | JiuwenSwarm candidate |
|---|---:|---:|---:|---:|
| Completed / failed tasks | 28 / 4 | 28 / 4 | 11 / 21 | 24 / 8 |
| Tasks with patch | 30 | 28 | 32 | 32 |
| Run wall time | 11448s | 6686s | 3188s | 1749s |
| Mean task duration | 5368s | 2950s | 1501s | 782s |
| Input throughput | 5781/s | 9958/s | 4146/s | 13770/s |
| Prefix cache hit rate | 22.8% | 70.8% | 37.6% | 89.0% |
| TTFT P50 | 11.13s | 2.61s | 53.11s | 1.11s |

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `launcher.sh`, `generate-dashboard-summary.py`

`dashboard-summary.json` records the exact remote result directories and comparison-report paths used for this report.

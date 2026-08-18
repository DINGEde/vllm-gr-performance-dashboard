# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-17
**Host**: L20-10014 (2×L20, 80GB each)
**Commit**: `37e3adc353afebcb1b6be92cc94dd8b28ccedebf`
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2

## Summary

All four manifests completed at `20260817_093745` without a watchdog. Candidate improved task-level latency and cache metrics in both agent families. CC candidate completed 31 tasks versus baseline 27, so its longer wall time reflects a materially larger workload.

| Metric | CC baseline | CC candidate | Jiuwen baseline | Jiuwen candidate |
|---|---:|---:|---:|---:|
| Completed / failed | 27 / 5 | 31 / 1 | 10 / 22 | 28 / 4 |
| Tasks with patch | 27 | 31 | 32 | 32 |
| Wall time | 10910s | 14724s | 2951s | 1807s |
| Mean task duration | 5156s | 3241s | 1320s | 786s |
| Input throughput | 5822/s | 8580/s | 5077/s | 14264/s |
| Prefix cache hit rate | 26.5% | 75.3% | 54.9% | 88.4% |
| TTFT P50 | 10.85s | 1.99s | 31.45s | 1.23s |

CC candidate wall time rose 35.0% while it completed four additional tasks and issued 2586 requests versus baseline 2056. Its mean task duration fell 37.1%, input throughput rose 47.4%, prefix-cache hit rate rose 48.7 pp, and TTFT P50 fell 81.7%.

JiuwenSwarm candidate completed 18 more tasks, reduced wall time 38.8%, increased input throughput 181.0%, raised prefix-cache hit rate 33.5 pp, and reduced TTFT P50 96.1%.

> The JiuwenSwarm compare report cannot confirm cold start for either arm. Retain this warning for cross-run interpretation.

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `launcher.sh`, `generate-dashboard-summary.py`

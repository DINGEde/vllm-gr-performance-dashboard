# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-27 (intended CI label)
**Host**: L20-10014 (2× L20, 80GB each)
**Commit**: `3d4cb9cafb9356591ad7050650de9558d516b2b7`
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2
**Formal runtime timestamp**: `20260826_223448`

> The host clock was 2026-08-26 22:34 CST at formal launch. Artifact paths retain that timestamp; the Kanban folder uses the intended 2026-08-27 CI label. An incomplete, baseline-only preflight artifact at `20260826_214646` is explicitly excluded.

## Experimental policy

Candidate uses the AgentCache scheduler, identity/lifecycle middlewares, and progress-TTL controller. As in 0826, this run fixes both supported post-#79 force-resume bounds to 1800 seconds:

```json
"force_resume_timeout_min_seconds": 1800,
"force_resume_timeout_max_seconds": 1800
```

All four formal manifests completed, and every formal result directory contains `sessions.csv` and `task_index.json`. Candidate logs contain `AgentCacheAsyncSchedulerBridge` (2), both middlewares, `build_progress_ttl_controller`, `progress_ttl`, and both fixed timeout keys. Treat this as the second fixed-force-resume sample, not as policy-identical to adaptive-range 0819–0825.

## Summary

| Metric | CC baseline | CC candidate | Jiuwen baseline | Jiuwen candidate |
|---|---:|---:|---:|---:|
| Completed / failed | 27 / 5 | 32 / 0 | 32 / 0 | 31 / 1 |
| Tasks with patch | 28 | 30 | 32 | 32 |
| Wall time | 9752s | 6707s | 5578s | 1896s |
| Mean task duration | 4494s | 2884s | 2639s | 779s |
| Requests | 2234 | 2180 | 535 | 542 |
| Input tokens | 67.9M | 70.6M | 25.4M | 26.6M |
| vLLM prefix-cache hit | 34.4% | 73.3% | 14.7% | 81.1% |
| vLLM prompt-token hit | 52.9% | 73.3% | 37.1% | 89.8% |
| Latency mean | 63.7s | 56.6s | 151.1s | 38.6s |
| TTFT mean | 15.9s | 9.3s | 68.0s | 18.8s |

## Claude Code

Candidate completed five more tasks (32 / 0 vs 27 / 5), produced two more patches, and reduced wall time by 31.2% (9752s → 6707s), despite 3.9% more input tokens. Mean latency fell 11.1% and mean TTFT 41.7%; prefill, decode, and inference time improved. Queue mean increased 129.1% (10.8s → 24.9s), and candidate had more failed requests (32 vs 10), so tail behavior remains a concern: latency P99 and TTFT P99 worsened.

Candidate vLLM prefix-cache hit rate rose 38.9 percentage points (34.4% → 73.3%) and prompt-token cache rate rose 20.4 points (52.9% → 73.3%). Candidate prefix queries/input tokens are approximately 1.0 versus approximately 2.9 for baseline. This counter shape requires accounting/replay investigation before claiming a pure reuse gain.

## JiuwenSwarm

Candidate reduced wall time by 66.0% (5578s → 1896s), mean latency by 74.4%, and mean TTFT by 72.4%, at nearly matched request/token volume. It completed one fewer task (31 / 1 vs 32 / 0), though both arms produced 32 patches. Candidate had zero failed requests versus seven baseline failures.

Candidate vLLM prefix-cache hit rate rose 66.3 percentage points (14.7% → 81.1%) and prompt-token rate rose 52.6 points (37.1% → 89.8%). Candidate prefix queries/input tokens are approximately 2.1 versus approximately 7.0 for baseline. This repeats the fixed-1800 counter shape from 0826 and is evidence for further replay/accounting review, not standalone proof of a cache-reuse gain.

> The JiuwenSwarm compare report cannot confirm cold start for either arm. Retain this warning for cross-run interpretation.

## Interpretation boundary

The two fixed-1800 runs (0826 and 0827) both show high candidate cache metrics and strong candidate latency improvement. Their changed policy prevents direct aggregation with adaptive-range 0819–0825. A matched replay or more controlled fixed-1800 samples are needed to distinguish policy effect, metric accounting, and cache reuse.

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `generate-dashboard-summary.py`, `launcher.sh`
- Remote formal task-session artifacts: `sessions.csv` and `task_index.json` in every formal result directory

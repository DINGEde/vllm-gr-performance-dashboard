# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-26 (intended CI label)
**Host**: L20-10014 (2× L20, 80GB each)
**Commit**: `0b5f9b509561e129b779ad4795fd43fe34198942`
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2
**Runtime timestamp**: `20260825_195231`

> The host clock was 2026-08-25 19:52 CST at launch. Artifact paths retain that timestamp; the Kanban folder uses the intended 2026-08-26 CI label.

## Experimental policy

Candidate uses the standard AgentCache scheduler, identity/lifecycle middlewares, and progress-TTL controller. Unlike 0819–0825, this run fixes both supported post-#79 force-resume bounds to 1800 seconds:

```json
"force_resume_timeout_min_seconds": 1800,
"force_resume_timeout_max_seconds": 1800
```

Treat this as a fixed-force-resume policy experiment, not as a policy-identical daily sample. All four manifests completed; each result directory contains `sessions.csv` and `task_index.json`. Candidate service logs contain `AgentCacheAsyncSchedulerBridge` (2), both middlewares, `build_progress_ttl_controller`, `progress_ttl`, and both 1800-second keys.

## Summary

| Metric | CC baseline | CC candidate | Jiuwen baseline | Jiuwen candidate |
|---|---:|---:|---:|---:|
| Completed / failed | 28 / 4 | 30 / 2 | 32 / 0 | 30 / 2 |
| Tasks with patch | 30 | 32 | 32 | 31 |
| Wall time | 16529s | 6627s | 6942s | 2514s |
| Mean task duration | 5022s | 2883s | 2469s | 832s |
| Requests | 2405 | 2226 | 524 | 664 |
| Input tokens | 111.0M | 68.1M | 23.7M | 32.3M |
| vLLM prefix-cache hit | 34.1% | 70.8% | 20.9% | 91.6% |
| vLLM prompt-token hit | 60.8% | 70.9% | 38.5% | 92.2% |
| Latency mean | 66.4s | 49.2s | 143.0s | 34.5s |
| TTFT mean | 20.8s | 10.1s | 56.3s | 15.4s |
| TTFT P50 | 6.68s | 2.75s | 52.36s | 1.17s |

## Claude Code

Candidate completed two more tasks (30 / 2 vs 28 / 4), produced two more patches (32 vs 30), and reduced wall time by 59.9% (16529s → 6627s). It also issued 7.4% fewer requests and 38.6% fewer input tokens, so wall-time improvement is partly confounded by a shorter agent trajectory. Per-request latency still improved independently: mean latency −25.9%, mean TTFT −51.3%, prefill −56.3%, decode −42.8%, and inference −44.4%. Queue mean increased 29.9% (14.0s → 18.2s).

The vLLM prefix-cache rate rose 36.7 percentage points (34.1% → 70.8%) and prompt-token cache rate rose 10.1 points (60.8% → 70.9%). Candidate `prefix_cache_queries_total / input_tokens` is approximately 1.0 while baseline is approximately 2.1. That counter-shape departure from 0819–0825 should be investigated before interpreting the high prefix-cache percentage as an isolated reuse improvement.

## JiuwenSwarm

Candidate reduced wall time by 63.8% (6942s → 2514s), mean latency by 75.9%, and mean TTFT by 72.7%, despite issuing 26.7% more requests and 36.0% more input tokens. However, it completed fewer tasks (30 / 2 vs 32 / 0) and produced one fewer patch. This run therefore demonstrates large serving-side latency gains but not an end-to-end task-outcome improvement.

vLLM prefix-cache rate rose 70.7 percentage points (20.9% → 91.6%) and prompt-token cache rate rose 53.7 points (38.5% → 92.2%). Candidate `prefix_cache_queries_total / input_tokens` is again approximately 1.0, compared with approximately 7.1 for baseline. The high ratio requires replay/accounting review before cross-run conclusions.

> The JiuwenSwarm compare report cannot confirm cold start for either arm. Retain this warning for cross-run interpretation.

## Interpretation boundary

The result is favorable on per-request latency in both arms and on Claude Code completion, but it changed candidate force-resume policy. Do not merge it into the adaptive-range 0819–0825 trend without a matched replay or additional fixed-1800 samples. The high candidate prefix-cache measurements are evidence to investigate, not proof of a real cache-reuse gain.

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `generate-dashboard-summary.py`, `launcher.sh`
- Remote task-session artifacts: `sessions.csv` and `task_index.json` in every result directory

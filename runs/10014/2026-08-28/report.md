# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-28 (intended CI label)
**Host**: L20-10014 (2× L20, 80GB each)
**Commit**: `4eb38ce36247d0659a5d579a8dd7bc1c7a4e7677`
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2
**Formal runtime timestamp**: `20260827_111641`

> The host clock was 2026-08-27 11:16 CST at formal launch. Artifact paths retain that timestamp; the Kanban folder uses the intended 2026-08-28 CI label. The incomplete baseline-only preflight artifact at `20260826_214646` remains excluded.

## Experimental policy

Candidate uses the AgentCache scheduler, identity/lifecycle middlewares, and progress-TTL controller. This third fixed-policy sample sets both supported post-#79 force-resume bounds to 1800 seconds:

```json
"force_resume_timeout_min_seconds": 1800,
"force_resume_timeout_max_seconds": 1800
```

All four formal manifests completed; each formal result directory contains `sessions.csv` and `task_index.json`. Candidate logs contain `AgentCacheAsyncSchedulerBridge` (2), both middlewares, `build_progress_ttl_controller`, `progress_ttl`, and both fixed timeout keys. Compare it with the fixed-1800 0826–0827 samples, not adaptive-range 0819–0825.

## Summary

| Metric | CC baseline | CC candidate | Jiuwen baseline | Jiuwen candidate |
|---|---:|---:|---:|---:|
| Completed / failed | 32 / 0 | 30 / 2 | 32 / 0 | 32 / 0 |
| Tasks with patch | 27 | 32 | 32 | 32 |
| Wall time | 10209s | 10253s | 6057s | 2328s |
| Mean task duration | 4672s | 3207s | 2651s | 807s |
| Requests | 2173 | 2954 | 558 | 593 |
| Input tokens | 66.1M | 173.0M | 27.0M | 28.3M |
| vLLM prefix-cache hit | 33.5% | 88.2% | 19.3% | 93.4% |
| vLLM prompt-token hit | 49.7% | 88.6% | 40.7% | 92.0% |
| Latency mean | 68.2s | 43.7s | 145.6s | 36.2s |
| TTFT mean | 18.8s | 8.1s | 64.9s | 12.2s |

## Claude Code

Candidate produced five more patches (32 vs 27) and improved request-level mean latency (−35.8%), TTFT (−57.0%), prefill (−55.5%), decode (−56.4%), and inference time (−56.3%). But candidate completed fewer tasks (30 / 2 vs 32 / 0), generated 36.0% more requests and 161.7% more input tokens, and had 43 failed requests versus nine. Wall time was essentially flat (+0.4%) despite substantially more work, so this demonstrates throughput/latency improvement, not an end-to-end task-outcome gain. Candidate P99 latency and queue mean were worse.

Candidate vLLM prefix-cache hit rate rose 54.7 percentage points (33.5% → 88.2%) and prompt-token rate rose 38.8 points (49.7% → 88.6%). Candidate prefix queries/input tokens are approximately 1.0, versus approximately 3.3 for baseline. This is the third repeated fixed-1800 high-counter shape and should remain an accounting/replay investigation signal rather than standalone proof of a reuse gain.

## JiuwenSwarm

Candidate matched completion and patches (32 / 0 and 32 each), while reducing wall time by 61.6% (6057s → 2328s), mean latency by 75.2%, and mean TTFT by 81.2%, with only 6.3% more requests and 5.0% more input tokens. Candidate also reduced failed requests (two vs 22). This is a strong per-request and wall-time result under comparable workload volume.

Candidate vLLM prefix-cache hit rate rose 74.0 percentage points (19.3% → 93.4%) and prompt-token rate rose 51.3 points (40.7% → 92.0%). Candidate prefix queries/input tokens are approximately 1.4 versus approximately 6.6 for baseline. The repeated fixed-1800 counter shape needs replay/accounting review before causal cache conclusions.

> The JiuwenSwarm compare report cannot confirm cold start for either arm. Retain this warning for cross-run interpretation.

## Interpretation boundary

Across fixed-1800 samples 0826–0828, candidate per-request latency is consistently favorable and candidate cache counters are repeatedly high, but end-to-end Claude Code completion/workload volume remains variable. Keep fixed-1800 results separate from the adaptive-range series until controlled replay can distinguish scheduler policy, trajectory variation, counter accounting, and actual reuse.

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `generate-dashboard-summary.py`, `launcher.sh`
- Remote formal task-session artifacts: `sessions.csv` and `task_index.json` in every formal result directory

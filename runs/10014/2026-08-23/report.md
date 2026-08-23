# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-23 (intended CI label)
**Host**: L20-10014 (2×L20, 80GB each)
**Commit**: `0b5f9b509561e129b779ad4795fd43fe34198942`
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2
**Runtime timestamp**: `20260822_095918`

> The host clock was Aug 22 09:59 CST at launch, so all runtime artifact paths carry the timestamp `20260822_095918`. The Kanban folder is labeled `2026-08-23` to match the launcher session name (`main-ci-32x16-20260823`) and the intended CI date. Actual artifact paths are not relabeled. Commit `0b5f9b5` is unchanged from 0822 (#93 agentbench router-integration refactor).

## Summary

All four manifests completed without a watchdog. Candidate scheduler evidence is present in the candidate service log (`AgentCacheAsyncSchedulerBridge` plus both AgentCache middlewares), and all four result directories include the #87 task-session artifacts (`sessions.csv`, `task_index.json`). JiuwenSwarm candidate improved strongly at matched 32/0 completion. Claude Code candidate showed better per-request latency and cache reuse but worse aggregate wall time, driven by the candidate generating substantially more requests and input tokens.

| Metric | CC baseline | CC candidate | Jiuwen baseline | Jiuwen candidate |
|---|---:|---:|---:|---:|
| Completed / failed | 31 / 1 | 30 / 2 | 32 / 0 | 32 / 0 |
| Tasks with patch | 27 | 30 | 32 | 32 |
| Wall time | 12115s | 15603s | 5055s | 3298s |
| Mean task duration | 5701s | 4735s | 2439s | 1529s |
| Input throughput | 5281/s | 6411/s | 4899/s | 8769/s |
| vLLM prefix-cache hit | 20.7% | 35.9% | 15.7% | 43.3% |
| vLLM prompt-token hit | 36.9% | 62.3% | 41.1% | 74.2% |
| TTFT P50 | 21.14s | 7.01s | 54.84s | 1.81s |
| Requests | 1974 | 2368 | 543 | 579 |
| Input tokens | 64.0M | 100.0M | 24.8M | 28.9M |

## Claude Code

Candidate completed one fewer task (30 / 2 vs 31 / 1) but produced more patches (30 vs 27). Per-request efficiency improved: mean task duration −16.9% (5701s → 4735s), input throughput +21.4% (5281/s → 6411/s), vLLM prefix-cache hit rate +15.2 pp (20.7% → 35.9%), prompt-token hit rate +25.4 pp (36.9% → 62.3%), TTFT P50 −66.8% (21.14s → 7.01s), queue mean −21.2% (20.60s → 16.23s).

However, aggregate wall time worsened +28.8% (12115s → 15603s). The cause is visible in the workload, not the serving signals: candidate generated +20.0% more requests (2368 vs 1974) and +56.3% more input tokens (100.0M vs 64.0M). With per-request latency improved but the request/token volume substantially larger, total elapsed time grew. The higher request count and longer overall run are consistent with candidate trajectory divergence (more exploration/turns per task), not with a candidate-service degradation — the cache-hit and TTFT metrics moved in the favorable direction.

## JiuwenSwarm

Completion matched (32 / 0, 32 patches for both arms). Candidate improved across the board: wall time −34.8% (5055s → 3298s), mean task duration −37.3% (2439s → 1529s), input throughput +79.0% (4899/s → 8769/s), vLLM prefix-cache hit rate +27.6 pp (15.7% → 43.3%), prompt-token hit rate +33.0 pp (41.1% → 74.2%), TTFT P50 −96.7% (54.84s → 1.81s), queue mean −29.4% (48.01s → 33.89s). Request volume was near-matched (579 vs 543, +6.6%), so the wall-time gain is not an artifact of doing less work.

> The JiuwenSwarm compare report cannot confirm cold start for either arm. Retain this warning for cross-run interpretation.

## Cross-run note (0820 / 0821 / 0822 / 0823 CC)

The CC candidate has now shown four different direction/parity combinations across four runs, all under an unchanged candidate scheduler/factory/middleware (de56079–0b5f9b5; #93 touched benchmark tooling only):

- 0820: candidate worse at matched 31/1 (wall +14.6%, prefix hit −11.5 pp).
- 0821: candidate better, more completed (30/2 vs 28/4) (wall −13.8%, prefix hit +1.5 pp).
- 0822: candidate better per-request, fewer completed (28/4 vs 30/2) (wall −12.9%, prefix hit +15.1 pp).
- 0823: candidate better per-request, one fewer completed (30/2 vs 31/1) (wall +28.8%, prefix hit +15.2 pp).

The per-request cache-hit and TTFT signals have favored the candidate in three of the four runs (0821–0823); the single unfavorable run (0820) remains the outlier. Aggregate wall time is the noisiest metric because it is dominated by agent trajectory length (requests/tokens generated), which is non-deterministic. 0823 is the clearest illustration: per-request latency and cache reuse improved sharply, yet wall time worsened because candidate generated +56% more input tokens. No causal root cause has been proven for the run-to-run completion/token-volume differences; per-task `sessions.csv` / `task_index.json` analysis remains pending.

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `launcher.sh`, `generate-dashboard-summary.py`
- Task-session artifacts: `sessions.csv` + `task_index.json` present in every remote result directory (not mirrored here)

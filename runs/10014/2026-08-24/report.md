# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-24 (intended CI label)
**Host**: L20-10014 (2×L20, 80GB each)
**Commit**: `0b5f9b509561e129b779ad4795fd43fe34198942`
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2
**Runtime timestamp**: `20260823_220620`

> The host clock was Aug 23 22:06 CST at launch, so all runtime artifact paths carry the timestamp `20260823_220620`. The Kanban folder is labeled `2026-08-24` to match the launcher session name (`main-ci-32x16-20260824`) and the intended CI date. Actual artifact paths are not relabeled. Commit `0b5f9b5` is unchanged from 0823 (#93 agentbench router-integration refactor).

## Summary

All four manifests completed without a watchdog. Candidate scheduler evidence is present in the candidate service log (`AgentCacheAsyncSchedulerBridge` plus both AgentCache middlewares), and all four result directories include the #87 task-session artifacts (`sessions.csv`, `task_index.json`). This is the first run since 0820 in which the Claude Code candidate regressed rather than improved: it completed one fewer task (30 / 2 vs 31 / 1) and was worse on every per-request latency and cache-reuse signal, with only marginally more request/token volume (+4.3% requests, +7.5% input tokens) — too small to explain the degradation. JiuwenSwarm per-request metrics improved at matched 32 / 0, but aggregate wall time worsened because the candidate generated +16.1% more requests.

| Metric | CC baseline | CC candidate | Jiuwen baseline | Jiuwen candidate |
|---|---:|---:|---:|---:|
| Completed / failed | 31 / 1 | 30 / 2 | 32 / 0 | 32 / 0 |
| Tasks with patch | 26 | 29 | 32 | 32 |
| Wall time | 8294s | 9273s | 5372s | 6543s |
| Mean task duration | 3724s | 4249s | 2489s | 2152s |
| Requests | 2093 | 2182 | 510 | 592 |
| Input tokens | 62.3M | 66.9M | 24.7M | 27.4M |
| vLLM prefix-cache hit | 42.4% | 29.2% | 24.1% | 28.6% |
| vLLM prompt-token hit | 57.0% | 55.6% | 40.0% | 58.9% |
| Latency mean | 56.4s | 61.8s | 147.5s | 110.1s |
| TTFT mean | 14.5s | 18.6s | 71.3s | 44.9s |
| TTFT P50 | 5.54s | 8.33s | 64.83s | 10.34s |

## Claude Code

Candidate completed one fewer task (30 / 2 vs 31 / 1) and produced more patches (29 vs 26). Per-request efficiency regressed across the board: mean task duration +14.1% (3724s → 4249s), latency mean +9.7% (56.4s → 61.8s), latency P50 +28.0% (33.6s → 43.0s), TTFT mean +27.7% (14.5s → 18.6s), TTFT P50 +50.3% (5.54s → 8.33s), queue mean +38.2% (9.90s → 13.68s). vLLM prefix-cache hit rate dropped −13.2 pp (42.4% → 29.2%) and prompt-token hit rate −1.4 pp (57.0% → 55.6%) — the cache-reuse signal moved against the candidate, the opposite direction of 0821–0823.

Aggregate wall time worsened +11.8% (8294s → 9273s). Unlike 0823, where the wall regression was clearly explained by candidate generating +56% more input tokens, here the workload divergence is modest: +4.3% requests (2093 → 2182) and +7.5% input tokens (62.3M → 66.9M). That small an increase cannot account for the per-request latency and cache-hit degradation, so 0824 looks more like a genuine serving-side regression than a trajectory-volume artifact — though run-to-run nondeterminism in which tasks the candidate takes longer on still cannot be ruled out without per-task session analysis.

## JiuwenSwarm

Completion matched (32 / 0, 32 patches for both arms). Per-request metrics improved: mean task duration −13.6% (2489s → 2152s), latency mean −25.3% (147.5s → 110.1s), latency P50 −61.2% (126.2s → 49.0s), TTFT mean −37.0% (71.3s → 44.9s), TTFT P50 −84.0% (64.83s → 10.34s), queue mean −33.4% (57.34s → 38.20s). vLLM prefix-cache hit rate +4.5 pp (24.1% → 28.6%) and prompt-token hit rate +18.8 pp (40.0% → 58.9%); cached input tokens rose +60.9% (10.0M → 16.1M).

However, aggregate wall time worsened +21.8% (5372s → 6543s) despite per-request latency improving. The cause is workload volume: candidate generated +16.1% more requests (510 → 592) and +11.0% more input tokens (24.7M → 27.4M). With per-request latency down but request count up, total elapsed time grew — the same pattern as the 0823 CC candidate (per-request better, aggregate worse due to more turns), not a serving degradation.

> The JiuwenSwarm compare report cannot confirm cold start for either arm. Retain this warning for cross-run interpretation.

## Cross-run note (0820 / 0821 / 0822 / 0823 / 0824 CC)

The CC candidate has now shown five runs under an unchanged candidate scheduler/factory/middleware (de56079–0b5f9b5; #93 touched benchmark tooling only), with 0824 breaking the 0821–0823 favorable streak:

- 0820: candidate worse at matched 31/1 (wall +14.6%, prefix hit −11.5 pp).
- 0821: candidate better, more completed (30/2 vs 28/4) (wall −13.8%, prefix hit +1.5 pp).
- 0822: candidate better per-request, fewer completed (28/4 vs 30/2) (wall −12.9%, prefix hit +15.1 pp).
- 0823: candidate better per-request, one fewer completed (30/2 vs 31/1) (wall +28.8%, prefix hit +15.2 pp).
- 0824: candidate worse per-request, one fewer completed (30/2 vs 31/1) (wall +11.8%, prefix hit −13.2 pp).

The per-request cache-hit and TTFT signals had favored the candidate in three consecutive runs (0821–0823); 0824 is the second unfavorable run and the first since 0820 to show the cache-hit signal moving against the candidate. Aggregate wall time remains the noisiest metric because it is dominated by agent trajectory length (requests/tokens generated), which is non-deterministic — but 0824 is notable because the per-request regression is not masked by a large workload increase (only +4.3% requests / +7.5% tokens), making it the run most suggestive of a genuine serving-side effect rather than a trajectory-volume artifact. No causal root cause has been proven for the run-to-run completion/token-volume/per-request differences; per-task `sessions.csv` / `task_index.json` analysis remains pending.

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `launcher.sh`, `generate-dashboard-summary.py`
- Task-session artifacts: `sessions.csv` + `task_index.json` present in every remote result directory (not mirrored here)

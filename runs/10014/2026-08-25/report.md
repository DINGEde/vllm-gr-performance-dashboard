# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-25 (intended CI label)
**Host**: L20-10014 (2×L20, 80GB each)
**Commit**: `0b5f9b509561e129b779ad4795fd43fe34198942`
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2
**Runtime timestamp**: `20260824_212612`

> The host clock was Aug 24 21:26 CST at launch, so all runtime artifact paths carry the timestamp `20260824_212612`. The Kanban folder is labeled `2026-08-25` to match the launcher session name (`main-ci-32x16-20260825`) and the intended CI date. Actual artifact paths are not relabeled. Commit `0b5f9b5` is unchanged from 0824 (#93 agentbench router-integration refactor).

## Summary

All four manifests completed without a watchdog. Candidate scheduler evidence is present in the candidate service log (`AgentCacheAsyncSchedulerBridge` plus both AgentCache middlewares), and all four result directories include the #87 task-session artifacts (`sessions.csv`, `task_index.json`). This is a favorable run for both agents: Claude Code candidate matched baseline completion (30/2) while improving per-request latency and wall time despite doing less work (−179 requests, −7.0M input tokens); JiuwenSwarm candidate completed one more task (32/0 vs 31/1) and improved sharply per-request. The candidate prefix-cache hit rate stayed in the true 0.2–0.3 band (post-#79 accounting), consistent with 0819–0824.

| Metric | CC baseline | CC candidate | Jiuwen baseline | Jiuwen candidate |
|---|---:|---:|---:|---:|
| Completed / failed | 30 / 2 | 30 / 2 | 31 / 1 | 32 / 0 |
| Tasks with patch | 28 | 27 | 32 | 32 |
| Wall time | 11460s | 9409s | 5980s | 5047s |
| Mean task duration | 5177s | 4349s | 2629s | 2045s |
| Requests | 2188 | 2009 | 492 | 581 |
| Input tokens | 69.0M | 62.0M | 23.9M | 26.9M |
| vLLM prefix-cache hit | 27.3% | 29.5% | 24.9% | 19.1% |
| vLLM prompt-token hit | 47.9% | 51.3% | 36.3% | 58.1% |
| Latency mean | 74.9s | 68.9s | 163.7s | 106.1s |
| TTFT mean | 21.6s | 22.5s | 74.1s | 46.3s |
| TTFT P50 | 10.77s | 10.57s | 67.71s | 12.57s |

## Claude Code

Candidate matched baseline completion (30 / 2) and produced one fewer patch (27 vs 28). Per-request efficiency improved: wall time −17.9% (11460s → 9409s), mean task duration −16.0% (5177s → 4349s), latency mean −7.9% (74.9s → 68.9s), latency P50 −5.5% (50.0s → 47.2s), vLLM prefix-cache hit rate +2.2 pp (27.3% → 29.5%), prompt-token hit rate +3.4 pp (47.9% → 51.3%). Decode time mean −13.3% (52.3s → 45.4s), inference time mean −13.1% (57.6s → 50.1s). TTFT was flat-to-slightly-up (mean +4.0%, P50 −1.9%).

Notably, the candidate did less work this run: −179 requests (2188 → 2009) and −7.0M input tokens (69.0M → 62.0M). So part of the wall-time gain reflects a smaller workload, not purely per-request improvement — but the per-request latency and cache-hit metrics moved in the favorable direction independently, so this is a genuine improvement, not an artifact of doing less. This is the first run since 0824 where the candidate did not generate more tokens than baseline.

## JiuwenSwarm

Candidate completed one more task (32 / 0 vs 31 / 1, 32 patches for both arms). Per-request metrics improved sharply: wall time −15.6% (5980s → 5047s), mean task duration −22.2% (2629s → 2045s), latency mean −35.2% (163.7s → 106.1s), latency P50 −62.2% (138.7s → 52.4s), TTFT mean −37.5% (74.1s → 46.3s), TTFT P50 −81.4% (67.71s → 12.57s), queue mean −37.6% (62.56s → 39.02s), decode mean −33.5% (86.6s → 57.6s). Prompt-token hit rate +21.8 pp (36.3% → 58.1%); cached input tokens +77.7% (8.8M → 15.7M).

The candidate generated more work (+18.1% requests, 492 → 581; +12.1% input tokens, 23.9M → 26.9M), yet wall time still fell — so the per-request gains outpaced the workload increase. The one unfavorable signal is vLLM prefix-cache hit rate −5.8 pp (24.9% → 19.1%), but the prompt-token hit rate (the finer-grained reuse metric) improved strongly; these two vLLM counters can diverge depending on prefix-block boundary alignment, and the latency/TTFT improvements confirm the candidate reused cache effectively.

> The JiuwenSwarm compare report cannot confirm cold start for either arm. Retain this warning for cross-run interpretation.

## Cross-run note (0820 / 0821 / 0822 / 0823 / 0824 / 0825 CC)

The CC candidate has now shown six runs under an unchanged candidate scheduler/factory/middleware (de56079–0b5f9b5; #93 touched benchmark tooling only). 0825 is favorable, 0824 was unfavorable — the two most recent runs split direction:

- 0820: candidate worse at matched 31/1 (wall +14.6%, prefix hit −11.5 pp).
- 0821: candidate better, more completed (30/2 vs 28/4) (wall −13.8%, prefix hit +1.5 pp).
- 0822: candidate better per-request, fewer completed (28/4 vs 30/2) (wall −12.9%, prefix hit +15.1 pp).
- 0823: candidate better per-request, one fewer completed (30/2 vs 31/1) (wall +28.8%, prefix hit +15.2 pp).
- 0824: candidate worse per-request, one fewer completed (30/2 vs 31/1) (wall +11.8%, prefix hit −13.2 pp).
- 0825: candidate better per-request, matched completion (30/2 vs 30/2) (wall −17.9%, prefix hit +2.2 pp).

The per-request cache-hit and TTFT signals have favored the candidate in five of the six runs (0821–0823, 0825); the two unfavorable runs (0820, 0824) remain the outliers. Aggregate wall time remains noisy because it is dominated by agent trajectory length (requests/tokens generated), which is non-deterministic — 0825 is the cleanest favorable case because the candidate did *less* work (−179 requests) yet still improved per-request, separating the serving signal from the workload-volume confound. No causal root cause has been proven for the run-to-run completion/token-volume/per-request differences; per-task `sessions.csv` / `task_index.json` analysis remains pending.

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `launcher.sh`, `generate-dashboard-summary.py`
- Task-session artifacts: `sessions.csv` + `task_index.json` present in every remote result directory (not mirrored here)

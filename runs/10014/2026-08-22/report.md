# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-22 (intended CI label)
**Host**: L20-10014 (2×L20, 80GB each)
**Commit**: `0b5f9b509561e129b779ad4795fd43fe34198942`
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2
**Runtime timestamp**: `20260821_105812`

> The host clock was Aug 21 10:58 CST at launch, so all runtime artifact paths carry the timestamp `20260821_105812`. The Kanban folder is labeled `2026-08-22` to match the launcher session name (`main-ci-32x16-20260822`) and the intended CI date. Actual artifact paths are not relabeled. This is the first run at commit `0b5f9b5`, which contains the #93 agentbench router-integration refactor (removed benchkit-side router config/collectors; added `effective_metrics_url`; retained the `router` summary field as a hardcoded `False` for compare compatibility; `session_analysis` #87 artifacts unchanged).

## Summary

All four manifests completed without a watchdog. Candidate scheduler evidence is present in the candidate service log (`AgentCacheAsyncSchedulerBridge` plus both AgentCache middlewares), and all four result directories include the #87 task-session artifacts (`sessions.csv`, `task_index.json`). Both arms show candidate improvements; CC candidate improved strongly on per-request efficiency despite completing two fewer tasks.

| Metric | CC baseline | CC candidate | Jiuwen baseline | Jiuwen candidate |
|---|---:|---:|---:|---:|
| Completed / failed | 30 / 2 | 28 / 4 | 32 / 0 | 31 / 1 |
| Tasks with patch | 25 | 30 | 32 | 32 |
| Wall time | 10739s | 9353s | 5346s | 4191s |
| Mean task duration | 5073s | 4299s | 2568s | 2000s |
| Input throughput | 5857/s | 7583/s | 4432/s | 6786/s |
| vLLM prefix-cache hit | 20.2% | 35.3% | 13.9% | 30.3% |
| vLLM prompt-token hit | 41.9% | 58.1% | 34.4% | 62.9% |
| TTFT P50 | 9.57s | 6.89s | 63.31s | 6.48s |

## Claude Code

Candidate completed two fewer tasks (28 / 4 vs 30 / 2), so this is not a matched-completion comparison; the aggregate wall-time and duration gains partly reflect the smaller finished-task set. However, candidate produced more patches (30 vs 25) and improved on every per-request efficiency metric: wall time −12.9% (10739s → 9353s), mean task duration −15.3% (5073s → 4299s), input throughput +29.5% (5857/s → 7583/s), vLLM prefix-cache hit rate +15.1 pp (20.2% → 35.3%), prompt-token hit rate +16.2 pp (41.9% → 58.1%), TTFT P50 −28.0% (9.57s → 6.89s), and queue mean −12.5% (18.06s → 15.81s). The cache-hit and TTFT improvements are per-request and therefore not artifacts of the completion-count difference.

## JiuwenSwarm

Candidate completed one fewer task (31 / 1 vs 32 / 0) but improved across the board on efficiency: wall time −21.6% (5346s → 4191s), mean task duration −22.1% (2568s → 2000s), input throughput +53.1% (4432/s → 6786/s), vLLM prefix-cache hit rate +16.4 pp (13.9% → 30.3%), prompt-token hit rate +28.5 pp (34.4% → 62.9%), TTFT P50 −89.8% (63.31s → 6.48s), and queue mean −29.7% (60.77s → 42.72s). Patch count matched (32 vs 32).

> The JiuwenSwarm compare report cannot confirm cold start for either arm. Retain this warning for cross-run interpretation.

## Cross-run note (0820 / 0821 / 0822 CC)

Across the three most recent CC runs the candidate direction has alternated and is not consistently correlated with completion parity:
- 0820: candidate worse at matched 31/1 (wall +14.6%, prefix hit −11.5 pp).
- 0821: candidate better, more completed (30/2 vs 28/4) (wall −13.8%, prefix hit +1.5 pp).
- 0822: candidate better on per-request metrics, fewer completed (28/4 vs 30/2) (wall −12.9%, prefix hit +15.1 pp).

The candidate-service configuration was unchanged across scheduler/middleware from de56079 through 9aec995; 0822 introduced the #93 agentbench refactor, which touched benchmark tooling (runner/finalizer/config) but not the candidate scheduler/factory/middleware. The per-request cache-hit and latency improvements in 0822 are consistent with the candidate service working as intended; the completion-count variance (−2) is attributable to agent trajectory nondeterminism. No causal root cause has been proven for the run-to-run completion differences; per-task `sessions.csv` / `task_index.json` analysis remains pending.

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `launcher.sh`, `generate-dashboard-summary.py`
- Task-session artifacts: `sessions.csv` + `task_index.json` present in every remote result directory (not mirrored here)

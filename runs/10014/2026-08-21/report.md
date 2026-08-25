# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-21 (intended CI label)
**Host**: L20-10014 (2×L20, 80GB each)
**Commit**: `9aec9958aebf82ebce19d5fb626a4df00ab445d6`
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2
**Runtime timestamp**: `20260820_203350`

> The host clock was still Aug 20 20:33 CST at launch, so all runtime artifact paths carry the timestamp `20260820_203350`. The Kanban folder is labeled `2026-08-21` to match the launcher session name (`main-ci-32x16-20260821`) and the intended CI date. Actual artifact paths are not relabeled.

## Summary

All four manifests completed without a watchdog. Candidate scheduler evidence is present in the candidate service log (`AgentCacheAsyncSchedulerBridge` plus both AgentCache middlewares), and all four result directories include the #87 task-session artifacts (`sessions.csv`, `task_index.json`). This run reversed the 0820 Claude Code pattern: CC candidate improved over baseline here, while in 0820 it had regressed.

| Metric | CC baseline | CC candidate | Jiuwen baseline | Jiuwen candidate |
|---|---:|---:|---:|---:|
| Completed / failed | 28 / 4 | 30 / 2 | 31 / 1 | 30 / 2 |
| Tasks with patch | 26 | 31 | 32 | 31 |
| Wall time | 10341s | 8915s | 5730s | 6034s |
| Mean task duration | 4852s | 4181s | 2413s | 2025s |
| Input throughput | 6378/s | 7378/s | 3925/s | 3637/s |
| vLLM prefix-cache hit | 26.9% | 28.5% | 16.1% | 22.3% |
| vLLM prompt-token hit | 47.0% | 56.7% | 34.1% | 48.8% |
| TTFT P50 | 7.88s | 7.57s | 61.76s | 38.16s |

## Claude Code

Candidate completed more tasks (30 / 2 vs 28 / 4) and produced more patches (31 vs 26), so this is not a strictly matched comparison; two baseline-failed tasks succeeded under the candidate. On the tasks that did complete, candidate improved across the board: wall time −13.8% (10341s → 8915s), mean task duration −13.8% (4852s → 4181s), input throughput +15.7% (6378/s → 7378/s), vLLM prefix-cache hit rate +1.5 pp (26.9% → 28.5%), prompt-token hit rate +9.7 pp (47.0% → 56.7%), TTFT P50 −4.0% (7.88s → 7.57s), and queue mean −12.3% (16.08s → 14.10s). This is the opposite direction from 0820, where CC candidate regressed at matched 31/1 completion.

## JiuwenSwarm

Completion is not matched: candidate completed one fewer task (30 / 2 vs 31 / 1) and produced one fewer patch (31 vs 32). Candidate improved on per-request efficiency: mean task duration −16.1% (2413s → 2025s), vLLM prefix-cache hit rate +6.1 pp (16.1% → 22.3%), prompt-token hit rate +14.7 pp (34.1% → 48.8%), TTFT P50 −38.2% (61.76s → 38.16s), and queue mean −21.7% (50.56s → 39.61s). Wall time worsened +5.3% (5730s → 6034s) and input throughput worsened −7.3% (3925/s → 3637/s), which is consistent with the one fewer completed task producing less aggregate throughput despite better per-request latency.

> The JiuwenSwarm compare report cannot confirm cold start for either arm. Retain this warning for cross-run interpretation.

## Cross-run note (0820 vs 0821)

The CC direction reversed between runs: 0820 had CC candidate worse at matched completion (wall +14.6%, prefix hit −11.5 pp, TTFT P50 +46.8%), while 0821 has CC candidate better (wall −13.8%, prefix hit +1.5 pp, TTFT P50 −4.0%). The candidate service configuration did not change between the two runs (same commit advanced de56079 → 9aec995; scheduler/factory/middleware did not change). This run-to-run reversal supports the earlier reading that the 0820 CC regression was not a deterministic candidate-service defect but rather trajectory variance interacting with cache/scheduling conditions. No causal root cause has been proven; per-task `sessions.csv` / `task_index.json` analysis remains pending.

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `launcher.sh`, `generate-dashboard-summary.py`
- Task-session artifacts: `sessions.csv` + `task_index.json` present in every remote result directory (not mirrored here)

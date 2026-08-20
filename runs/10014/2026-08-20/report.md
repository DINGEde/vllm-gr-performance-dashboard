# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-20
**Host**: L20-10014 (2×L20, 80GB each)
**Commit**: `de560797672e813037f4e1f884b9d580f02ed440`
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2
**Timestamp**: `20260820_083729`

## Summary

All four manifests completed without a watchdog. Candidate scheduler evidence is present in the candidate service log (`AgentCacheAsyncSchedulerBridge` plus both AgentCache middlewares), and all four result directories include the #87 task-session artifacts (`sessions.csv`, `task_index.json`).

| Metric | CC baseline | CC candidate | Jiuwen baseline | Jiuwen candidate |
|---|---:|---:|---:|---:|
| Completed / failed | 31 / 1 | 31 / 1 | 32 / 0 | 32 / 0 |
| Tasks with patch | 27 | 25 | 32 | 32 |
| Wall time | 8821s | 10112s | 7419s | 4402s |
| Mean task duration | 4085s | 4708s | 2968s | 2030s |
| Input throughput | 6925/s | 6548/s | 3475/s | 5476/s |
| vLLM prefix-cache hit | 34.6% | 23.0% | 11.9% | 21.3% |
| vLLM prompt-token hit | 52.3% | 50.4% | 32.2% | 51.8% |
| TTFT P50 | 6.41s | 9.40s | 85.37s | 31.52s |

## Claude Code

Completion is matched (31 / 1 for both arms), so this is a direct comparison. Candidate regressed: wall time +14.6% (8821s → 10112s), mean task duration +15.3% (4085s → 4708s), input throughput −5.4% (6925/s → 6548/s), vLLM prefix-cache hit rate −11.5 pp (34.6% → 23.0%), prompt-token hit rate −2.0 pp (52.3% → 50.4%), and TTFT P50 +46.8% (6.41s → 9.40s). Candidate also produced two fewer patches (25 vs 27).

## JiuwenSwarm

Completion and patch count are matched (32 / 0 and 32 patches for both arms). Candidate improved: wall time −40.7% (7419s → 4402s), mean task duration −31.6% (2968s → 2030s), input throughput +57.6% (3475/s → 5476/s), vLLM prefix-cache hit rate +9.4 pp (11.9% → 21.3%), prompt-token hit rate +19.5 pp (32.2% → 51.8%), and TTFT P50 −63.1% (85.37s → 31.52s).

> The JiuwenSwarm compare report cannot confirm cold start for either arm. Retain this warning for cross-run interpretation.

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `launcher.sh`, `generate-dashboard-summary.py`
- Task-session artifacts: `sessions.csv` + `task_index.json` present in every remote result directory (not mirrored here)

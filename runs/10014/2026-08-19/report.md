# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-19
**Host**: L20-10014 (2×L20, 80GB each)
**Commit**: `de560797672e813037f4e1f884b9d580f02ed440`
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2
**Timestamp**: `20260819_090146`

## Summary

All four manifests completed without a watchdog. This run advances upstream from `2f3da5e` to `de56079` and carries the progress-TTL fix for `#79` (`fd9fcc5`): the candidate `progress_ttl` config drops the four removed keys (`ttl_prefill_seconds_per_1k_uncached_tokens`, `target_min_segment_rounds`, `pause_capacity_lookahead_rounds`, `privileged_lookahead_rounds`), keeping the five still-configurable ones. The candidate vLLM service started cleanly and the candidate log confirms `AgentCacheAsyncSchedulerBridge` activity — the 0818 startup failure is resolved. This is also the first run to include `#87` task-session index artifacts (`sessions.csv`, `task_index.json`) in all four result dirs.

| Metric | CC baseline | CC candidate | Jiuwen baseline | Jiuwen candidate |
|---|---:|---:|---:|---:|
| Completed / failed | 32 / 0 | 31 / 1 | 31 / 1 | 32 / 0 |
| Tasks with patch | 27 | 26 | 32 | 32 |
| Wall time | 10090s | 8723s | 6529s | 5288s |
| Mean task duration | 4686s | 3997s | 2122s | 2037s |
| Input throughput | 6288/s | 6583/s | 3731/s | 5068/s |
| vLLM prefix-cache hit | 23.1% | 25.6% | 16.4% | 30.1% |
| vLLM prompt-token hit | 47.5% | 52.8% | 51.6% | 59.3% |
| TTFT P50 | 7.13s | 8.24s | 20.68s | 14.86s |

## Claude Code

Candidate completed **one fewer** task (31 vs 32) and issued **fewer** requests (1859 vs 2110), so the wall-time reduction (−13.6%, 10090s → 8723s) partly reflects a smaller workload, not a pure speed gain. On matched-load signals: mean task duration −14.7% (4686s → 3997s), input throughput +4.7%, vLLM prompt-token hit rate +5.3 pp (47.5% → 52.8%). TTFT P50 rose slightly (7.13s → 8.24s). Patch rate dropped one (27 → 26 with_patch).

## JiuwenSwarm

Candidate completed one more task (32 vs 31), reduced wall time 19.0% (6529s → 5288s), raised input throughput 35.8% (3731/s → 5068/s), improved vLLM prefix-cache hit rate 13.7 pp (16.4% → 30.1%) and prompt-token hit rate 7.7 pp (51.6% → 59.3%), and cut TTFT P50 28.2% (20.68s → 14.86s).

> The JiuwenSwarm compare report cannot confirm cold start for either arm. Retain this warning for cross-run interpretation.

## Candidate scheduler evidence

The candidate log records `scheduler_cls: agentinfer.agentcache.core.scheduler.AgentCacheAsyncSchedulerBridge` and both middlewares active, with the trimmed `progress_ttl` config (only `target_max_segment_rounds`, `resume_capacity_ratio`, `pause_capacity_ratio`, `privileged_max_context_tokens`, `paused_program_ttl_seconds`). No `progress_ttl settings were removed` error.

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `launcher.sh`, `generate-dashboard-summary.py`
- Task-session index artifacts (#87): present in each remote result dir as `sessions.csv` + `task_index.json` (not mirrored here; available on host)

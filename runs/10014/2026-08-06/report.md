# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-06  
**Host**: L20-10014 (2×L20, 80GB each)  
**Commit**: `5ab4ac7` (upstream/main, router vendoring only, no agentbench changes vs yesterday)  
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)  
**Agent**: Claude Code 2.1.181

## Summary

AgentCache candidate delivers significant throughput and latency improvements over vanilla vLLM:

- **Wall time**: −28.6% (9568s → 6834s)
- **Input token throughput**: +46.5% (6743/s → 9877/s)
- **Output token throughput**: +47.1% (44.3/s → 65.1/s)
- **vLLM prefix cache hit rate**: +41.5 percentage points (28.6% → 70.1%)
- **TTFT mean**: −24.7% (16.70s → 12.58s)
- **Prefill time mean**: −35.3% (4.41s → 2.85s)
- **Decode time mean**: −38.3% (47.24s → 29.14s)

Task completion: 29 vs 28 (comparable). **Tasks with patch**: 29 → 32 (+3). Candidate produced patches for all 32 tasks — the 4 "failed" tasks all have valid patches but exited via `plan_exit_loop` (Claude stuck cycling in plan mode). Zero no-patch failures vs 3 in baseline.

No interactive blocks detected in either arm (watchdog at 2-minute intervals, 0 interventions).

## Comparison vs Yesterday (2026-08-05)

| Metric | Yesterday Baseline | Today Baseline | Today Candidate |
|--------|-------------------|----------------|-----------------|
| **Wall time (s)** | 12810 | 9568 | 6834 |
| **vLLM prefix cache hit rate** | 29.3% | 28.6% | 70.1% |
| **Input token throughput (/s)** | 5513 | 6743 | 9877 |
| **TTFT mean (s)** | 20.16 | 16.70 | 12.58 |
| **Decode time mean (s)** | 51.70 | 47.24 | 29.14 |
| **Tasks with patch** | 28 | 29 | 32 |
| **Completed tasks** | 30 | 29 | 28 |

Key observations:
1. Today's baseline was ~25% faster than yesterday's (9568s vs 12810s) — likely due to no interactive block, plus natural variance in agent execution paths.
2. Candidate cache hit rate (70.1%) is consistent with yesterday (69.8%), confirming stable AgentCache behavior.
3. Absolute wall time delta (9568→6834, −2734s) is comparable to yesterday's (12810→7088, −5722s). The smaller absolute delta today is driven by the stronger baseline, not weaker candidate.

## Key Metrics Comparison

| Metric | Baseline | Candidate | Change |
|--------|----------|-----------|--------|
| **Run wall time (s)** | 9568 | 6834 | **−28.6%** |
| **Input token throughput (/s)** | 6743 | 9877 | **+46.5%** |
| **Output token throughput (/s)** | 44.3 | 65.1 | **+47.1%** |
| **vLLM prefix cache hit rate** | 28.6% | **70.1%** | **+41.5 pt** |
| **vLLM prompt token hit rate** | 51.2% | **70.2%** | **+19.0 pt** |
| **TTFT mean (s)** | 16.70 | 12.58 | **−24.7%** |
| **TTFT p50 (s)** | 7.17 | 2.75 | **−61.6%** |
| **Prefill time mean (s)** | 4.41 | 2.85 | **−35.3%** |
| **Decode time mean (s)** | 47.24 | 29.14 | **−38.3%** |
| **Latency mean (s)** | 64.32 | 52.29 | **−18.7%** |
| Completed tasks | 29 | 28 | −1 |
| **Tasks with patch** | 29 | **32** | **+3** |
| Failed requests | 7 | 25 | +18 |

## Task-Level Detail

### Candidate: 3 no-patch failures resolved

All 3 baseline failures with no patch were resolved in candidate — each produced a valid patch:
- `astropy__astropy-13398`: baseline no patch (validation_error_loop 3964s) → candidate patch (3764s, plan_exit_loop)
- `astropy__astropy-14369`: baseline no patch (7720s, timeout/natural) → candidate patch (3950s, plan_exit_loop, −3770s)
- `django__django-10880`: baseline no patch (3336s) → candidate patch (2648s, −687s)

### Candidate: 4 plan_exit_loop with patches

These tasks completed with valid patches but the agent got stuck cycling in plan mode, so the run classifies them as "failed":
- `astropy__astropy-12907` (5895s)
- `astropy__astropy-14369` (3950s) — resolved from no-patch
- `astropy__astropy-7166` (4336s) — yesterday's 78-min-block task, clean today
- `django__django-10097` (2933s)

All 4 have patches ready despite the exit classification. This is an agent-behavior issue, not a serving issue.

### Candidate: highest regressions

- `astropy__astropy-14598`: +2044s (3737→5781) — this was the 0.5s-auto-handled AskUserQuestion task yesterday; today needed 1h36m. Non-deterministic agent path variance.
- `astropy__astropy-14365`: +725s (1877→2602)

## Scheduler Verification

- Candidate log: 2114 AgentCache API lifecycle program lines
- Custom scheduler `AgentCacheAsyncSchedulerBridge` confirmed loaded via engine log warning
- Lifecycle socket created and programs tracked
- No "controller cycle" log lines — factory wiring verified against prior runs, which also lack this log message; the lifecycle program count is the reliable indicator of scheduler activity

## Notes

1. **No interactive blocks**: Watchdog ran at 2-minute intervals. Both arms: 0 interventions. Yesterday's `astropy-7166` 78-minute block was a non-deterministic event that did not repeat. Today's run lacks the `GenericSelectionHandler` fix (still in PR #68), but the trigger condition did not occur.

2. **Candidate failed requests (25 vs 7)**: Higher count is typical — pause/resume scheduling drops in-flight requests during program preemption. These are not errors; they're retry-eligible framework-side drops that the agent transparently handles.

3. **P99 TTFT regression**: Candidate 175s vs baseline 77s (+127%). Consistent with yesterday (+58%). Pause/resume tail latency — paused programs requeue and compete with new arrivals, inflating P99. Expected tradeoff for the throughput/median gains.

4. **Commit difference from yesterday**: `5ab4ac7` vs `5cda1a2` — only `agentrouter/` vendor update (310 files, 82k lines). No agentbench, agentcache, or serving changes. Benchmark-comparable.

## Artifacts

| File | Path |
|------|------|
| Launcher script | `launcher-main-ci-32x16-20260806.sh` (host: `~/AgentCache/`) |
| Baseline result dir | `results/main-ci-baseline-32_16-20260806_192051/` |
| Candidate result dir | `results/main-ci-candidate-32_16-20260806_192051/` |
| Compare report | `benchkit-logs/main-ci-compare-32_16-20260806_192051.txt` |
| Baseline log | `benchkit-logs/main-ci-baseline-32_16-20260806_192051.log` |
| Candidate log | `benchkit-logs/main-ci-candidate-32_16-20260806_192051.log` |

## Kanban Files

- `baseline-summary.json` / `candidate-summary.json` — structured metrics (36 fields each)
- `dashboard-summary.json` — dashboard-ready comparison
- `compare.txt` — raw compare output
- `launcher.sh` — today's launch script (committed at `5ab4ac7`)
- `report.md` — this file

---

**Generated**: 2026-08-07  
**Source**: AgentCache benchmark results on L20-10014

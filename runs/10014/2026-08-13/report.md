# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-13  
**Host**: L20-10014 (2×L20, 80GB each)  
**Commit**: `a7473e3` (upstream/main; unchanged)  
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)  
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2 (4-arm run)

## Summary

All four arms completed. AgentCache improved CC wall time, cache reuse, throughput, and latency. The intervention pattern inverted from 0812: baseline CC received three watchdog Enter interventions while candidate CC received none. Baseline CC completed 31 tasks vs candidate's 30, but under intervention; candidate's per-request and duration metrics remain ahead.

### Claude Code

- **Wall time**: −24.5% (9535s → 7202s)
- **Completed tasks**: 31 → 30
- **Tasks with patch**: 28 → 29
- **Input token throughput**: +44.7% (6788/s → 9826/s)
- **vLLM prefix cache hit rate**: +44.6 pp (25.3% → 69.8%)
- **TTFT P50**: −55.5% (6.53s → 2.90s)
- **Mean task duration**: −29.1% (4481s → 3176s)

### JiuwenSwarm

- **Wall time**: −37.4% (2916s → 1826s)
- **Completed tasks**: 14 → 28
- **Tasks with patch**: 32 → 32
- **Input token throughput**: +126.1% (6184/s → 13974/s)
- **vLLM prefix cache hit rate**: +39.6 pp (49.8% → 89.4%)
- **TTFT P50**: −94.1% (23.46s → 1.37s)

> JiuwenSwarm reports cannot confirm cold start for either arm. Baseline retains the recurring interruption pattern (18 failed vs 4 candidate failed), so completion deltas are not an isolated scheduler measurement.

## Metrics Comparison

| Metric | CC baseline | CC candidate | JiuwenSwarm baseline | JiuwenSwarm candidate |
|---|---:|---:|---:|---:|
| Completed / failed tasks | 31 / 1 | 30 / 2 | 14 / 18 | 28 / 4 |
| Tasks with patch | 28 | 29 | 32 | 32 |
| Run wall time | 9535s | 7202s | 2916s | 1826s |
| Mean task duration | 4481s | 3176s | 1315s | 780s |
| Input throughput | 6788/s | 9826/s | 6184/s | 13974/s |
| Prefix cache hit rate | 25.3% | 69.8% | 49.8% | 89.4% |
| TTFT P50 | 6.53s | 2.90s | 23.46s | 1.37s |

## Watchdog Evidence

The CC-only watchdog intervened three times in baseline CC and zero times in candidate CC (inverse of 0812). Each baseline event accepted the highlighted default and is recorded in `interactive-block-events.log` under the baseline CC result:

1. `astropy__astropy-14369` — 2026-08-13 10:29:32 — Issue Clarification menu → Enter
2. `astropy__astropy-8872` — 2026-08-13 11:50:22 — Fix Approach menu → Enter
3. `django__django-10097` — 2026-08-13 14:56:15 — URLValidator clarification menu → Enter

This makes 0813's baseline CC outcome (31 completed, fewer failures) subject to a three-intervention caveat; candidate CC was intervention-free but completed one fewer task.

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `launcher.sh`

All source paths and exact timestamp are captured in `dashboard-summary.json` provenance.

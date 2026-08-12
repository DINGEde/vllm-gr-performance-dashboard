# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-11  
**Host**: L20-10014 (2×L20, 80GB each)  
**Commit**: `a7473e3` (upstream/main; unchanged)  
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)  
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2 (4-arm run)

## Summary

All four arms completed. AgentCache improved CC wall time, task completion, cache reuse, throughput, and latency. The baseline CC arm received three watchdog Enter interventions; candidate CC received none, so wall-time and task-outcome comparisons should be read with that condition noted.

### Claude Code

- **Wall time**: −35.8% (11592s → 7439s)
- **Completed tasks**: 27 → 30
- **Tasks with patch**: 25 → 32
- **Input token throughput**: +69.0% (5576/s → 9422/s)
- **vLLM prefix cache hit rate**: +53.8 pp (15.8% → 69.6%)
- **TTFT P50**: −74.5% (12.15s → 3.10s)
- **Mean task duration**: −38.3% (5432s → 3354s)

### JiuwenSwarm

- **Wall time**: −43.0% (3094s → 1762s)
- **Completed tasks**: 13 → 27
- **Tasks with patch**: 32 → 32
- **Input token throughput**: +182.1% (4716/s → 13307/s)
- **vLLM prefix cache hit rate**: +41.7 pp (47.7% → 89.5%)
- **TTFT P50**: −96.6% (35.63s → 1.20s)

> JiuwenSwarm reports cannot confirm cold start for either arm. Baseline also retains the recurring interruption pattern (19 failed tasks vs 5 candidate failures), so completion deltas are not an isolated scheduler measurement.

## Metrics Comparison

| Metric | CC baseline | CC candidate | JiuwenSwarm baseline | JiuwenSwarm candidate |
|---|---:|---:|---:|---:|
| Completed / failed tasks | 27 / 5 | 30 / 2 | 13 / 19 | 27 / 5 |
| Tasks with patch | 25 | 32 | 32 | 32 |
| Run wall time | 11592s | 7439s | 3094s | 1762s |
| Mean task duration | 5432s | 3354s | 1409s | 725s |
| Input throughput | 5576/s | 9422/s | 4716/s | 13307/s |
| Prefix cache hit rate | 15.8% | 69.6% | 47.7% | 89.5% |
| TTFT P50 | 12.15s | 3.10s | 35.63s | 1.20s |

## Watchdog Evidence

The CC-only watchdog detected and accepted the highlighted default in three baseline sessions:

- `astropy__astropy-12907` at 10:01:20
- `astropy__astropy-13033` at 13:46:36
- `django__django-10914` at 14:40:15

Each event is preserved in the baseline CC result's `interactive-block-events.log`. No candidate CC intervention was recorded.

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `launcher.sh`

All source paths and exact timestamp are captured in `dashboard-summary.json` provenance.

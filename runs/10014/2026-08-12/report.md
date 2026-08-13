# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-12  
**Host**: L20-10014 (2×L20, 80GB each)  
**Commit**: `a7473e3` (upstream/main; unchanged)  
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)  
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2 (4-arm run)

## Summary

All four arms completed. AgentCache improved CC wall time, task completion, cache reuse, throughput, and latency. Baseline CC had zero watchdog interventions; candidate CC had one (a memory-file write confirmation prompt), so the candidate's outcome and wall-time should be read with that one intervention noted.

### Claude Code

- **Wall time**: −34.1% (10778s → 7105s)
- **Completed tasks**: 28 → 30
- **Tasks with patch**: 26 → 32
- **Input token throughput**: +53.4% (6373/s → 9774/s)
- **vLLM prefix cache hit rate**: +33.6 pp (35.3% → 68.8%)
- **TTFT P50**: −64.3% (9.26s → 3.31s)
- **Mean task duration**: −33.7% (4856s → 3222s)

### JiuwenSwarm

- **Wall time**: −37.7% (3079s → 1918s)
- **Completed tasks**: 11 → 26
- **Tasks with patch**: 32 → 32
- **Input token throughput**: +185.5% (4868/s → 13890/s)
- **vLLM prefix cache hit rate**: +33.4 pp (54.7% → 88.1%)
- **TTFT P50**: −95.3% (28.71s → 1.33s)

> JiuwenSwarm reports cannot confirm cold start for either arm. Baseline retains the recurring interruption pattern (21 failed vs 6 candidate failed), so completion deltas are not an isolated scheduler measurement.

## Metrics Comparison

| Metric | CC baseline | CC candidate | JiuwenSwarm baseline | JiuwenSwarm candidate |
|---|---:|---:|---:|---:|
| Completed / failed tasks | 28 / 4 | 30 / 2 | 11 / 21 | 26 / 6 |
| Tasks with patch | 26 | 32 | 32 | 32 |
| Run wall time | 10778s | 7105s | 3079s | 1918s |
| Mean task duration | 4856s | 3222s | 1388s | 871s |
| Input throughput | 6373/s | 9774/s | 4868/s | 13890/s |
| Prefix cache hit rate | 35.3% | 68.8% | 54.7% | 88.1% |
| TTFT P50 | 9.26s | 3.31s | 28.71s | 1.33s |

## Watchdog Evidence

The CC-only watchdog intervened zero times in baseline CC and once in candidate CC. The candidate event accepted the highlighted default for a memory-file write confirmation and is recorded in `interactive-block-events.log` under the candidate CC result:

- `astropy__astropy-13977` — 2026-08-12 14:45:46 — "Do you want to create final-validation.md?" → Enter accepted default

This makes 0812's candidate CC outcome subject to a one-intervention caveat; baseline CC was intervention-free.

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `launcher.sh`

All source paths and exact timestamp are captured in `dashboard-summary.json` provenance.

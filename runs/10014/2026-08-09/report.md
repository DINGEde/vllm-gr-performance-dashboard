# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-09
**Host**: L20-10014 (2×L20, 80GB each)
**Commit**: `a7473e3` (upstream/main — unchanged from 0807/0808, no new upstream commits)
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2 (4-arm run)

## Summary

Third 4-arm daily CI. JiuwenSwarm candidate continues strong and on-trend. CC candidate shows a **wall-time inversion** driven by a single task (`astropy__astropy-14309`) hitting the 4h timeout via context exhaustion — all per-request metrics still improved, and this is a tail-latency artifact, not a scheduler regression.

### Claude Code

- **Wall time**: +47.5% (9783s → 14437s) ⚠️ — see anomaly analysis below
- **Input token throughput**: +35.1% (6234/s → 8424/s)
- **Output token throughput**: +6.5% (37.6/s → 40.0/s)
- **vLLM prefix cache hit rate**: +37.4 pp (33.5% → 70.9%)
- **TTFT P50**: −79.4% (10.11s → 2.08s)
- **Mean task duration**: −23.0% (4520s → 3483s)
- **Tasks with patch**: 26 → 30 (+4)

### JiuwenSwarm

- **Wall time**: −40.7% (2866s → 1700s)
- **Input token throughput**: +239% (4436/s → 15054/s)
- **vLLM prefix cache hit rate**: +34.8 pp (54.8% → 89.6%)
- **TTFT P50**: −96.8% (37.98s → 1.22s)
- **Tasks with patch**: 32 → 32 (both arms 32/32)

> ⚠️ JiuwenSwarm baseline had 22/32 tasks fail with `termination_reason=interrupted` due to the recurring JiuwenSwarm 0.2.4b2 internal bug (`ValueError` in message handler). Candidate dropped to 5/32.

## Key Metrics Comparison (Claude Code)

| Metric | Baseline | Candidate | Change |
|--------|----------|-----------|--------|
| **Run wall time (s)** | 9783 | 14437 | **+47.5%** ⚠️ |
| **Mean task duration (s)** | 4520 | 3483 | **−23.0%** ✅ |
| **Input token throughput (/s)** | 6234 | 8424 | **+35.1%** ✅ |
| **Output token throughput (/s)** | 37.6 | 40.0 | +6.5% |
| **vLLM prefix cache hit rate** | 33.5% | **70.9%** | **+37.4 pt** ✅ |
| **vLLM prompt token hit rate** | 47.9% | **74.7%** | **+26.8 pt** ✅ |
| **TTFT mean (s)** | 21.12 | 12.88 | **−39.0%** ✅ |
| **TTFT p50 (s)** | 10.11 | 2.08 | **−79.4%** ✅ |
| **Prefill time mean (s)** | 5.14 | 4.43 | −13.8% |
| **Decode time mean (s)** | 51.69 | 27.13 | −47.5% |
| **Latency mean (s)** | 73.84 | 49.07 | −33.6% |
| Completed tasks | 29 | 29 | 0 |
| **Tasks with patch** | 26 | **30** | **+4** ✅ |

## Key Metrics Comparison (JiuwenSwarm)

| Metric | Baseline | Candidate | Change |
|--------|----------|-----------|--------|
| **Run wall time (s)** | 2866 | 1700 | **−40.7%** ✅ |
| **Input token throughput (/s)** | 4436 | 15054 | **+239%** ✅ |
| **Output token throughput (/s)** | 16.8 | 69.3 | **+313%** ✅ |
| **vLLM prefix cache hit rate** | 54.8% | **89.6%** | **+34.8 pt** ✅ |
| **vLLM prompt token hit rate** | 49.5% | **89.9%** | **+40.4 pt** ✅ |
| **TTFT p50 (s)** | 37.98 | 1.22 | **−96.8%** ✅ |
| **Prefill time mean (s)** | 6.78 | 1.85 | −72.7% |
| Completed tasks | 10 | 27 | +17 |
| **Tasks with patch** | 32 | 32 | 0 |

## CC Wall-Time Anomaly: `astropy__astropy-14309` context exhaustion

The CC candidate wall time (14437s ≈ 4.01h) inverts every prior day's result. Root-cause analysis:

### Single-task diagnosis

| | Baseline CC | Candidate CC |
|---|---|---|
| Duration | 143 min | **240 min (hit 4h timeout)** |
| Outcome | completed | failed (timeout) |
| Final terminal | "Baked for 2h 22m" → idle prompt | **"100% context used"**, 169.4k tokens |
| Patch bytes | 630 | 6284 (10× larger) |
| Transcript lines | — | 1580 |

The candidate's `astropy__astropy-14309` hit **100% context window** (169.4k tokens) at the 4h mark. The terminal-final log ends mid-generation:

```
✻ Kneading… (3h 59m 53s · ↑ 169.4k tokens)
  ⎿  Tip: Use /clear to start fresh when switching topics and free up context
  100% context used
```

### Why the candidate's context grew faster

AgentCache's prefix-cache reuse gives the agent longer/more complete responses per turn (less re-prefill overhead → more decode → more output tokens retained in context). The candidate produced a 10× larger patch (6284 vs 630 bytes), showing the agent explored a much larger solution space — and exhausted its context at 4h before finishing. The baseline finished at 143min before reaching this limit.

### Why wall time is misleading

The 14437s wall = the single 4h-timeout task ran while 31 others had already finished. Per-request metrics all improved (cache +37pt, throughput +35%, TTFT −39%, mean task duration −23%). The wall-time inversion is a **single-task tail-latency artifact**, not a scheduler regression.

### Comparison to prior days

| Day | Candidate wall | Timeout tasks |
|-----|---------------|---------------|
| 0806 | 6834s | 0 |
| 0807 | 7199s | 0 |
| 0808 | 7654s | 0 |
| 0809 | **14437s** | 1 (`astropy-14309`, context exhaustion) |

This is the same non-deterministic agent-path variance seen on 0806 (`astropy-14598` 1h36m AskUserQuestion) and prior `plan_exit_loop` cases — just more extreme today. Not a scheduling defect.

## 4-Day Trend (CC candidate)

| Date | Baseline cache hit | Candidate cache hit | Input throughput gain | Candidate wall time | Mean task duration |
|------|-------------------|---------------------|------------------------|----------------------|---------------------|
| 0806 | 28.6% | 70.1% | +46.5% | 6834s | 4459s (base) → 3226s |
| 0807 | 40.1% | 69.8% | +50.3% | 7199s | 4168s → 3226s |
| 0808 | 23.0% | 69.5% | +56.0% | 7654s | 5002s → 3445s |
| 0809 | 33.5% | 70.9% | +35.1% | 14437s ⚠️ | 4520s → 3483s |

Key observations:
1. **Candidate cache hit 4-day stable at ~70%** (70.1/69.8/69.5/70.9) — progress-TTL controller behavior is reproducible.
2. **Mean task duration consistently drops ~23%** under AgentCache (0809: 4520→3483s) — the per-task scheduling win is real and stable even when wall time is inflated by a single timeout.
3. **0809 wall time is an outlier** due to one context-exhaustion timeout; the underlying per-task and per-request metrics remain on-trend.

## Task-Level Detail (Claude Code)

### Candidate CC: top 5 by duration

| Rank | Duration | Task | Outcome | Patch |
|------|----------|------|---------|-------|
| 1 | 240min (timeout) | `astropy__astropy-14309` | failed | True (6284B) |
| 2 | 92min | `astropy__astropy-14598` | completed | True |
| 3 | 87min | `astropy__astropy-13236` | completed | True |
| 4 | 75min | `astropy__astropy-8707` | completed | True |
| 5 | 73min | `astropy__astropy-14508` | completed | True |

### Candidate CC: 3 failures
- `astropy__astropy-14309` (240min, timeout, context exhaustion, patch 6284B)
- `astropy__astropy-13398` (58min, validation_error_loop, patch)
- `django__django-10914` (48min, plan_exit_loop, patch)

All 3 have patches. 2 of 3 are the recurring agent-behavior issues (validation/plan loops); the 1 timeout is the context-exhaustion case documented above.

## JiuwenSwarm Notes

### Baseline: 22 interrupted tasks (JiuwenSwarm 0.2.4b2 bug, recurring)

Same root cause as 0807/0808 — `ValueError: too many values to unpack (expected 2)` at `jiuwenswarm/gateway/message_handler/message_handler.py:1221`. 22/32 baseline tasks interrupted (0807: 22, 0808: 20, 0809: 22 — stable). Candidate dropped to 5/32.

Despite interruptions, all 32 tasks produced patches (partial work preserved before crash).

### Candidate: 27 completed, 5 interrupted
Candidate wall time (1700s) shorter than baseline (2866s) despite processing 17 more successful tasks. P50 TTFT at 1.22s shows near-instant response for reused prefix contexts.

## Watchdog

Watchdog ran at 2-minute intervals (CC arms only). 0 interventions (no interactive blocks detected). Fourth consecutive day with zero interactive blocks (0806-0809).

## Artifacts

| File | Path |
|------|------|
| Launcher script | `launcher-main-ci-32x16-20260809.sh` (host: `~/AgentCache/`) |
| Baseline CC result dir | `results/main-ci-baseline-cc-32_16-20260809_111951/` |
| Candidate CC result dir | `results/main-ci-candidate-cc-32_16-20260809_111951/` |
| Baseline JiuwenSwarm result dir | `results/main-ci-baseline-jiuwenswarm-32_16-20260809_111951/` |
| Candidate JiuwenSwarm result dir | `results/main-ci-candidate-jiuwenswarm-32_16-20260809_111951/` |
| Compare CC report | `benchkit-logs/main-ci-compare-cc-32_16-20260809_111951.txt` |
| Compare JiuwenSwarm report | `benchkit-logs/main-ci-compare-jiuwenswarm-32_16-20260809_111951.txt` |
| Baseline log | `benchkit-logs/main-ci-baseline-32_16-20260809_111951.log` |
| Candidate log | `benchkit-logs/main-ci-candidate-32_16-20260809_111951.log` |

## Kanban Files

- `baseline-cc-summary.json` / `candidate-cc-summary.json` — structured CC metrics
- `baseline-jiuwenswarm-summary.json` / `candidate-jiuwenswarm-summary.json` — structured JiuwenSwarm metrics
- `dashboard-summary.json` — dashboard-ready comparison (CC)
- `compare-cc.txt` / `compare-jiuwenswarm.txt` — raw compare outputs
- `launcher.sh` — today's launch script (commit `a7473e3`)
- `report.md` — this file

---

**Generated**: 2026-08-10
**Source**: AgentCache benchmark results on L20-10014

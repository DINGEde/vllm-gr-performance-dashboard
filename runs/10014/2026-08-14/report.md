# AgentCache Main CI Report: 32 Tasks / 16 Concurrency

**Date**: 2026-08-14  
**Host**: L20-10014 (2×L20, 80GB each)  
**Commit**: `37e3adc` (upstream/main; **advanced from `a7473e3`** — first change since 0807)  
**Model**: Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 (TP=2)  
**Agents**: Claude Code 2.1.181 + JiuwenSwarm 0.2.4b2 (4-arm run)

## Summary

All four arms completed. This is the first run on the new upstream commit `37e3adc`, which includes PR #68 (AskUserQuestion blocking fix) adding a `GenericSelectionHandler`. Per-request metrics improved strongly; CC candidate wall time inverts slightly (+5.8%) driven by task-completion mix (baseline 32 vs candidate 30 completed).

### Claude Code

- **Wall time**: +5.8% (9946s → 10527s) — see anomaly note
- **Completed tasks**: 32 → 30
- **Tasks with patch**: 28 → 32
- **Input token throughput**: +119.3% (6387/s → 14012/s)
- **vLLM prefix cache hit rate**: +55.4 pp (30.7% → 86.1%)
- **TTFT P50**: −76.6% (7.93s → 1.85s)
- **Mean task duration**: −27.7% (4646s → 3360s)

### JiuwenSwarm

- **Wall time**: −16.4% (2433s → 2033s)
- **Completed tasks**: 12 → 26
- **Tasks with patch**: 32 → 32
- **Input token throughput**: +183.2% (4738/s → 13421/s)
- **vLLM prefix cache hit rate**: +34.9 pp (53.2% → 88.1%)
- **TTFT P50**: −94.5% (24.14s → 1.32s)

> JiuwenSwarm reports cannot confirm cold start for either arm. Baseline retains the recurring interruption pattern (20 failed vs 6 candidate failed).

## Metrics Comparison

| Metric | CC baseline | CC candidate | JiuwenSwarm baseline | JiuwenSwarm candidate |
|---|---:|---:|---:|---:|
| Completed / failed tasks | 32 / 0 | 30 / 2 | 12 / 20 | 26 / 6 |
| Tasks with patch | 28 | 32 | 32 | 32 |
| Run wall time | 9946s | 10527s | 2433s | 2033s |
| Mean task duration | 4646s | 3360s | 1155s | 879s |
| Input throughput | 6387/s | 14012/s | 4738/s | 13421/s |
| Prefix cache hit rate | 30.7% | 86.1% | 53.2% | 88.1% |
| TTFT P50 | 7.93s | 1.85s | 24.14s | 1.32s |

## Watchdog Evidence — False-Positive Intervention Analysis

The CC-only watchdog recorded baseline 1 + candidate 5 = 6 interventions on this run. **All 6 are false positives** — they are the workspace trust prompt ("Is this a project you trust?"), which the runner's `StartupDialogHandler` already dismisses on its own.

Evidence:
1. The trust prompt appears **only in `terminal-0`** (startup first frame); `terminal-1` already shows `Welcome back!` (normal prompt) on both arms — confirming the runner dismissed it.
2. Watchdog timestamps coincide with the ~1.3-second trust window at startup: baseline `django-11099` event at 13:26:05 vs `terminal-1` at 13:26:06.87; candidate 5 events at 14:39:15–18 vs `terminal-1` at 14:39:17–18.
3. Candidate's 5 vs baseline's 1 is a **sampling-phase artifact**: candidate's 16 concurrent workspaces start simultaneously (14:39:15–18), clustering their trust windows so one 2-minute watchdog poll catches 5; baseline's tasks start staggered (arm ran ~2h), so only one task's ~1.3s window happened to align with a poll.

This does **not** indicate an AgentCache regression or a per-arm behavioral difference — both arms use identical `--agent-profile plan-subagent`, `--permission-mode bypassPermissions`, and `--disallowedTools`. The trust prompt occurrence and dismissal rate is symmetric.

### #68 (GenericSelectionHandler) — Confirmed Effective

The old AskUserQuestion clarification menus ("❯ 1. Confirm Root Cause / 2. Provide Test Details") that required watchdog rescue on 0807–0813 are **absent on 0814 candidate (0 occurrences)**. The new `GenericSelectionHandler` in `interaction.py` now auto-selects the highlighted option, eliminating that blocking surface.

### TeamCreate/TeamDelete Permission Warnings — Symmetric, Non-Blocking

Both arms print on startup:
```
Permission deny rule "TeamCreate" matches no known tool — check for typos.
Permission deny rule "TeamDelete" matches no known tool — check for typos.
```
Counts: baseline 128 ≈ candidate 130 occurrences. These are **warnings, not blocks** — `--disallowedTools` is validated against known tool names at startup regardless of `--permission-mode bypassPermissions`. `TeamCreate`/`TeamDelete`/`SendMessage` in `profiles.py` are not recognized tool names in Claude Code 2.1.181, causing the warning. Non-fatal config drift; both arms affected equally.

## Next: No-Watchdog Verification Run

Because the watchdog introduced false-positive interventions (recording trust prompts the runner already handled), a follow-up CC-only two-arm run **without any watchdog** will be launched on the same commit `37e3adc` to confirm the runner independently handles all prompts and no task stalls or hits an abnormal duration.

## Artifacts

- `baseline-cc-summary.json`, `candidate-cc-summary.json`
- `baseline-jiuwenswarm-summary.json`, `candidate-jiuwenswarm-summary.json`
- `compare-cc.txt`, `compare-jiuwenswarm.txt`
- `dashboard-summary.json`, `launcher.sh`

All source paths and exact timestamp are captured in `dashboard-summary.json` provenance.

# 10015 historical packaging dry-run

- Profile: `historical-full`
- Host: `zhike`
- Branch: `not-a-git-checkout`
- Commit: `not-a-git-checkout`

## Shapes

| Shape | Baseline dir | Candidate dir | Log |
| --- | --- | --- | --- |
| `4/2` | `agentcache/benchmarks/results/swebench_verified-vllm/20260707_204000` | `agentcache/benchmarks/results/swebench_verified-vllm-agentcache/20260707_205325` | `/home/zhike/dxw/AgentCache/benchkit-logs/e2e-compare_20260707_203742.log` |
| `8/4` | `agentcache/benchmarks/results/swebench_verified-vllm/20260707_210549` | `agentcache/benchmarks/results/swebench_verified-vllm-agentcache/20260707_212729` | `/home/zhike/dxw/AgentCache/benchkit-logs/e2e-compare_20260707_210332.log` |
| `16/8` | `agentcache/benchmarks/results/swebench_verified-vllm/20260707_214913` | `agentcache/benchmarks/results/swebench_verified-vllm-agentcache/20260707_225214` | `/home/zhike/dxw/AgentCache/benchkit-logs/e2e-compare_20260707_214654.log` |
| `32/16` | `agentcache/benchmarks/results/swebench_verified-vllm/20260707_233758` | `agentcache/benchmarks/results/swebench_verified-vllm-agentcache/20260708_011906` | `/home/zhike/dxw/AgentCache/benchkit-logs/e2e-compare_20260707_233540.log` |
| `64/32` | `agentcache/benchmarks/results/swebench_verified-vllm/20260708_031642` | `agentcache/benchmarks/results/swebench_verified-vllm-agentcache/20260708_052252` | `/home/zhike/dxw/AgentCache/benchkit-logs/e2e-compare_20260708_031423.log` |

## Omitted artifact categories

- agentcache/benchmarks/results/** full directories
- benchkit-logs/** wholesale log directory
- requests.jsonl
- tasks/*/result.json
- full vLLM/router service logs
- router event JSONL
- figures generated on the CI host

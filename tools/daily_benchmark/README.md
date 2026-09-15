# vLLM-gr 每日性能看板工具包

本目录包含从同步 `decode_graph`、运行 OneRec 离线单 batch 性能矩阵、生成统一 JSON，到推送 Dashboard 的完整链路。配置与代码分离，同一份脚本可复制给不同服务器使用。

## 默认测试矩阵

- Input 1024：Beam 64、128、256
- Beam 128：Input 512、2048、4096、8192、10240
- 每个样本按 `reset → 不计时 prime → 计时 hit → reset → 计时 miss` 的方式测量
- `MAX_CONCURRENCY=1`
- 正式 E2E 先运行，只在 `GRLLM.beam_search()` 外读取一对时钟，不启用 profiler 或内部 monkeypatch
- 正式样本结束后再运行少量 diagnostic 样本，用于解释各阶段的每日变化

runner 固定使用并在每次运行前 fast-forward 到 `origin/decode_graph`。如果 worktree 处于其他分支或存在 tracked 修改，会拒绝运行。

## 包含内容

- `benchmark.env.example`：可复制的服务器配置模板
- `preflight.sh`：不启动模型的环境、分支、数据集和 GPU 预检
- `daily_runner.sh`：正式矩阵、汇总及 Dashboard 发布入口
- `install_cron.sh`：幂等安装或移除定时任务
- `run_offline_benchmark.py`：离线 `GRLLM.beam_search()` 测量
- `collect_daily_changes.py`：记录相对上一个自然日新增的 commits 和合入 PR
- `generate_summary.py`：生成 `vllm-gr.daily.v1` 结果
- `instrumentation/`：可选的无热路径 I/O 轻量 CPU 函数计时

## 前置条件

宿主机需要 `bash`、`git`、`docker`、`nvidia-smi`、`flock` 和 `timeout`。需要准备：

1. 一个只检出 `decode_graph` 的 benchmark 专用 worktree。
2. 一个绑定目标 GPU、源码和数据目录的常驻容器；源码在容器内默认挂载为 `/opt/vllm-gr`。
3. 已获得 `OpenOneRec/OpenOneRec-RecIF` 与 `OpenOneRec/OneRec-1.7B` 访问权限的 Hugging Face cache/token。
4. 如需发布，一个 `DINGEde/vllm-gr-performance-dashboard` clone，且当前用户拥有 `git pull/push` 权限。

凭据不属于共享包。不要复制 SSH 私钥、Hugging Face token、`~/.cache` 或已经填写的 `benchmark.env`。

## 配置

```bash
cd /path/to/vllm-gr/tools/daily_benchmark
cp benchmark.env.example benchmark.env
chmod 600 benchmark.env
vi benchmark.env
```

至少修改：`PROJECT_DIR`、`CONTAINER_NAME`、`GPU_INDEX`、`DASHBOARD_DIR` 和 `CRON_LOG`。`benchmark.env` 已被 `.gitignore` 排除。

容器的 bind mount 必须满足：

```text
${PROJECT_DIR} -> ${CONTAINER_PROJECT_DIR:-/opt/vllm-gr}
```

## 首次验证

```bash
./preflight.sh
```

成功输出应包含：

```text
preflight OK: ... branch=decode_graph sha=... scenarios=8
```

## 手动运行

仅保存在服务器：

```bash
BENCHMARK_CONFIG=$PWD/benchmark.env PUSH_DASHBOARD=0 ./daily_runner.sh
```

运行成功后推送 Dashboard：

```bash
BENCHMARK_CONFIG=$PWD/benchmark.env PUSH_DASHBOARD=1 ./daily_runner.sh
```

运行指定场景：

```bash
SCENARIO_SPECS=128:1024 RUN_TAG=manual-test PUSH_DASHBOARD=0 ./daily_runner.sh
```

Beam 只允许 64、128、256。

正式默认值为 `LIGHTWEIGHT_TIMING=0`、`DIAGNOSTIC_PROMPTS=20`。不要在正式趋势任务中打开 Worker lightweight timing；若手动打开，该次结果会被标记为不具备趋势资格。Diagnostic 阶段始终在所有正式 E2E 样本之后运行，因此它自身的计时开销不会污染正式趋势。

看板按“自然日代码快照”比较：每个场景与上一个自然日同场景、同测量版本的成功结果比较，并列出两个 SHA 之间合入 `decode_graph` 的 PR 集合。它不逐 PR 重跑，也不会把多个 PR 的共同变化归因给单个 PR。

## 安装定时任务

```bash
./install_cron.sh install
crontab -l
```

重复执行不会创建重复任务。移除：

```bash
./install_cron.sh remove
```

## GPU 自动恢复

runner 会同时检查容器内 `nvidia-smi` 和 PyTorch CUDA。专用容器空闲但 NVML/CUDA 失效时，会自动重启一次并复检；检测到已有服务或离线 benchmark 时不会重启。可在配置中设置 `AUTO_RESTART_CONTAINER=0` 禁止自动恢复。

## 分享

运行打包脚本：

```bash
./tools/daily_benchmark/build_package.sh
```

它只收集离线看板所需文件，明确排除 `benchmark.env`、凭据、缓存、结果、日志和旧在线脚本，同时生成 SHA-256 校验文件。接收方解压到其 vLLM-gr 仓库根目录，创建自己的 `benchmark.env`，完成 `preflight.sh` 后再安装 cron。

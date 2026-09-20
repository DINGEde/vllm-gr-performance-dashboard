# vLLM-gr 每日性能看板工具包

本目录包含从同步 `decode_graph`、运行 OneRec 离线单 batch 性能矩阵、生成统一 JSON，到推送 Dashboard 的完整链路。配置与代码分离，同一份脚本可复制给不同服务器使用。

看板本身（MkDocs 站点、图表的渲染脚本、GitHub Pages 发布流程）在仓库其余部分，部署方法见仓库根目录的 `README.md`。**本文件只讲数据是怎么产生出来的那一半。**

> **这些脚本必须放在 `$PROJECT_DIR/tools/daily_benchmark/`。** `daily_runner.sh` 在容器内调用 Python 脚本时是按 `$container_project_dir/tools/daily_benchmark/<script>.py` 硬拼路径的（见 `daily_runner.sh` 里六处 `docker exec`），不做任何解析。所以这份目录是**部署目标**，不是源码：仓库里的这份是唯一权威副本，服务器上的那份由 `sync_scripts.sh` 从仓库单向同步下来。

## 默认测试矩阵

6 个场景：

- Input 1024：Beam 64、128、256
- Beam 128：Input 512、2048、4096

数组定义在 `daily_runner.sh:56-57`；`SCENARIO_SPECS` 可以整体覆盖（Beam 只允许 64、128、256）。

- 每个样本按 `reset → 不计时 prime → 计时 hit → reset → 计时 miss` 的方式测量
- `MAX_CONCURRENCY=1`
- 正式 E2E 先运行，只在 `GRLLM.beam_search()` 外读取一对时钟，不启用 profiler 或内部 monkeypatch
- 正式样本结束后再运行少量 diagnostic 样本，用于解释各阶段的每日变化

runner 不切换分支、不读工作树里的代码：它 `git fetch origin decode_graph` 后对 `refs/remotes/origin/decode_graph` 那个 commit 做归档并测量。所以工作树停在别的分支上没关系，**但**约束表（见下）是从工作树读的，这一点是个例外。

## 包含内容

**入口**

- `preflight.sh`：不启动模型的环境、配置、数据集和 GPU 预检。**先跑这个。**
- `sync_scripts.sh`：cron 的入口。先从看板仓库 `pull --ff-only`，再 rsync 覆盖本目录，最后 `exec` `daily_runner.sh`。
- `daily_runner.sh`：正式矩阵、汇总及 Dashboard 发布入口。
- `install_cron.sh`：幂等安装或移除定时任务。

**测量与汇总**

- `run_offline_benchmark.py`：离线 `GRLLM.beam_search()` 测量。
- `run_benchmark.sh`、`start_service.sh`：更早的在线服务路径脚本，不在每日链路里。
- `generate_summary.py`：生成 `vllm-gr.daily.v1` 结果。
- `attach_worker_diagnostic.py`：把 worker 侧诊断的 CPU 计时挂进 summary。
- `instrumentation/`：可选的无热路径 I/O 轻量 CPU 函数计时（`sitecustomize.py` 负责注入）。
- `validate_lightweight_timing.py`、`print_matrix_summary.py`：诊断辅助。

**取数与溯源**

- `download_onerec_dataset.py`：拉取并校验 OneRec 数据集。
- `collect_daily_changes.py`：记录相对上一个自然日新增的 commits 和合入 PR。
- `collect_service_metrics.py`：在线路径的服务指标采集，不在每日链路里。
- `prepare_native_source.py`：为每个 matrix 打一份源码快照。
- `backfill_engine_fields.py`：一次性历史回填工具，不被任何脚本调用。

**A/B 对照（独立入口，不参与每日链路）**

- `ab_runner.sh`、`abs_runner.sh`、`abs_matrix.py`、`compare_ab.py`。

**其它**

- `build_package.sh`：打成可分发的 tar 包（默认输出到 `/tmp`，不落在仓库里）。
- `tests/`：测量契约的自动化检查。在 Linux 上跑：`python -m pytest tools/daily_benchmark/tests/`
- `benchmark.env.example`：可复制的服务器配置模板。
- `vllm-gr-daily.cron`：早期 cron 模板，`install_cron.sh` 已不读它。

## 前置条件

### 宿主机

需要 `bash`、`git`、`docker`、`nvidia-smi`、`flock`、`timeout`、`rsync`。

宿主机只需要 Python 3 跑两个 stdlib-only 的脚本（`collect_daily_changes.py`、`prepare_native_source.py`）。**不要在宿主机上装 pandas / torch / vllm**——它们只在容器里用，装了反而会和容器的版本混淆。

### 容器镜像

镜像**不在任何 registry 上**，是本地构建的，`vllm-gr:dev`。构建文件在 10018 上是 `/home/d00991341/docker-build/Dockerfile`：

```dockerfile
FROM vllm/vllm-openai:v0.22.1
RUN python3 -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
WORKDIR /opt/vllm-gr
COPY . /opt/vllm-gr
RUN python3 -m pip install --no-cache-dir -e ".[dev]"
ENV HF_ENDPOINT=https://hf-mirror.com
ENV HF_HOME=/root/.cache/huggingface
```

关键点：`pip install -e .` 把 vllm-gr 装成指向 `/opt/vllm-gr` 的开发模式安装，**所以 bind mount 必须落在正好这个路径上**，否则容器里 import 到的是构建时 `COPY` 进去的那份旧代码，而不是被测量的源码。

镜像名只在一处默认值里出现：`generate_summary.py:290` 的 `--container-image` 默认 `vllm-gr:dev`，`daily_runner.sh` 不传这个参数。用别的名字就要改那里。

### 容器实例

10018 上跑着的那个等价于：

```bash
docker run -d --name vllm-gr-benchmark-gpu1 \
  --gpus '"device=1"' \
  --ipc=host \
  --ulimit memlock=-1 --ulimit stack=67108864 \
  --security-opt label=disable \
  -v "$PROJECT_DIR":/opt/vllm-gr \
  -v /path/to/onerec-data:/opt/vllm-gr/data \
  -v "$HOME/.cache/huggingface":/root/.cache/huggingface \
  vllm-gr:dev sleep infinity
```

几条容易踩的：

- `--gpus device=N` 必须和 `benchmark.env` 里的 `GPU_INDEX` 一致。`daily_runner.sh:94-98` 会检查 `DeviceRequests` 里有没有这个索引，对不上直接失败。
- `--ipc=host`（或足够大的 `--shm-size`）和 `--ulimit memlock=-1` 是 NCCL 需要的。
- 容器默认 `RestartPolicy=no`：**宿主机重启后容器不会自己起来，之后每一晚 cron 都会静默失败**（`MAILTO=""`，只在 cron.log 里留一行）。要么给它 `--restart unless-stopped`，要么重启宿主机后记得 `docker start`。
- 容器**没有装 git**，所有 git 操作都在宿主机的 bind mount 上做。

### 数据与凭据

1. **模型**：`OpenOneRec/OneRec-1.7B`（默认值在 `benchmark.env.example`）。
2. **数据集**：`OpenOneRec/OpenOneRec-RecIF`，revision 钉在 `download_onerec_dataset.py` 的 `DEFAULT_REVISION`，文件 `benchmark_data/video/video_test.parquet`。两个仓库都需要 HF 授权。
3. **约束表**：`run_offline_benchmark.py:610-613` 把 `--catalog` 默认成 `/opt/vllm-gr/test_profilling/video_constraint_triples.json`（宿主即 `$PROJECT_DIR/test_profilling/video_constraint_triples.json`），而且 runner **从不显式传 `--catalog`**。这个文件约 40 MB，且 `test_profilling/` 在 vllm-gr 仓库里是未跟踪的。缺了它每个场景都会在引擎初始化时挂掉。它和仓库里那份是逐字节相同的，直接复制即可：

   ```bash
   sha256sum tools/AB_Tests/constraints/video_constraint_triples.json   # d3fb0051...
   mkdir -p test_profilling
   cp tools/AB_Tests/constraints/video_constraint_triples.json test_profilling/video_constraint_triples.json
   ```

4. **HF 凭据**：cache 目录由容器内的 root 创建，在宿主机上是 `drwx------ root root`——**宿主机用户读不了也写不了**。所以登录要在容器里做：

   ```bash
   docker exec -it vllm-gr-benchmark-gpu1 hf auth login
   ```

   注意这个 cache 里存着 token 本身，属于凭据，不要打包、不要复制。

凭据不属于共享包。不要复制 SSH 私钥、Hugging Face token、`~/.cache` 或已经填好的 `benchmark.env`。

## 配置

```bash
cd $PROJECT_DIR/tools/daily_benchmark
cp benchmark.env.example benchmark.env
chmod 600 benchmark.env
vi benchmark.env
```

`benchmark.env` 已被 `.gitignore` 排除，且被同步流程排除——它是本机专有状态，仓库永远不该有它。

逐项说明：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PROJECT_DIR` | 无 | **必填**。vllm-gr 检出目录的绝对路径。见下面的警告。 |
| `CONTAINER_NAME` | `vllm-gr-benchmark` | 容器名，要和实际起的一致。 |
| `HOST_NAME` | `$(hostname -s)` | 只作为元数据写进结果。 |
| `GPU_INDEX` | `0` | 必须与容器的 `--gpus device=N` 一致。 |
| `CONTAINER_PROJECT_DIR` | `/opt/vllm-gr` | 宿主 `PROJECT_DIR` 在容器内的挂载点。 |
| `CONTAINER_DATA_DIR` | `$CONTAINER_PROJECT_DIR/data` | 数据集在容器内的位置。 |
| `MODEL_ID` | `OpenOneRec/OneRec-1.7B` | |
| `HF_ENDPOINT` | `https://hf-mirror.com` | 境外网络要换成 `https://huggingface.co`。 |
| `DASHBOARD_DIR` | 无 | 看板仓库的 clone 路径，只在 `PUSH_DASHBOARD=1` 时读。 |
| `PUSH_DASHBOARD` | `0` | 见下面的「发布」一节。 |
| `NUM_PROMPTS` | `100` | 每场景正式样本数。 |
| `WARMUP_REQUESTS` | `4` | |
| `MAX_CONCURRENCY` | `1` | 必须为 1，否则 runner 直接拒绝。 |
| `GPU_IDLE_LIMIT_MIB` | `1024` | 显存占用超过它就认为卡被占用，让出本次运行。 |
| `LIGHTWEIGHT_TIMING` | `0` | 正式趋势必须保持 0。 |
| `DIAGNOSTIC_PROMPTS` | `20` | |
| `WORKER_DIAGNOSTIC_PROMPTS` | `20` | 每场景 worker 诊断的样本数；实际取 `max(本值, DIAGNOSTIC_PROMPTS)`。必须是正整数。 |
| `VLLM_GR_PREFILL_GRAPH_KV_BOUND` | `4096` | 引擎侧 prefill graph 的 bound。 |
| `AUTO_RESTART_CONTAINER` | `1` | 见「GPU 自动恢复」。 |
| `CRON_SCHEDULE` | `30 2 * * *` | |
| `CRON_LOG` | 无 | cron 输出重定向到这个文件。 |

**`PROJECT_DIR` 现在是必填项，这一点变了。** `daily_runner.sh:11-12` 在它未设时会退回 `$script_dir/../..`。这些脚本以前就住在 vllm-gr 检出里，这个退路是对的；现在它们住在看板仓库里，同一个退路会指向**看板仓库**，于是 runner 会拿看板仓库去 `fetch origin decode_graph`（失败），并往它里面建 `results/daily/`（让发布步骤从此一直拒绝运行）。`preflight.sh` 会显式拦下这种情况。

### 换机器时必须改的地方（都写死在代码里，没有 env 开关）

| 位置 | 值 | 说明 |
|---|---|---|
| `daily_runner.sh:13` | `source_branch="decode_graph"` | 硬编码，且在第 8 行 source 配置**之后**赋值，所以 `benchmark.env` 里写 `SOURCE_BRANCH=` 会被静默覆盖。换分支要改代码。 |
| `daily_runner.sh` 五处 `LD_LIBRARY_PATH` | `/usr/local/cuda-13.0/compat:/usr/local/nvidia/lib64:/usr/local/cuda/lib64` | CUDA 版本不同要改。 |
| `generate_summary.py:482` | `"hardware": "L20"` | 写进 payload，非 L20 机器会一直标成 L20。 |
| `generate_summary.py:290` | `--container-image` 默认 `vllm-gr:dev` | 镜像换名要改。 |
| `generate_summary.py:275` | `--repo-dir` 默认 `/opt/vllm-gr` | |
| `run_offline_benchmark.py:610` | `--catalog` 默认 `/opt/vllm-gr/test_profilling/...` | |

### 定时任务的时区

`run_date` 用显式 `TZ=Asia/Shanghai` 计算（`daily_runner.sh:63`），summary 里的日界也是上海时区，所以**分桶与宿主机时区无关**。但 `CRON_SCHEDULE` 用的是宿主机本地时区。时区不是 CST 的机器要挑一个落在 ~03:30 北京时间的时间点，否则这一列的采样时刻会和历史序列错开。

## 从零到出数据

```bash
# 1. 准备检出与容器
git clone <vllm-gr> "$PROJECT_DIR" && cd "$PROJECT_DIR"
docker run -d ... # 见上面「容器实例」

# 2. 约束表
mkdir -p test_profilling
cp tools/AB_Tests/constraints/video_constraint_triples.json test_profilling/video_constraint_triples.json

# 3. 配置
cd tools/daily_benchmark
cp benchmark.env.example benchmark.env && chmod 600 benchmark.env && vi benchmark.env

# 4. 预检：只验环境、分支、数据集、GPU，不跑模型
./preflight.sh
#   成功输出应包含： preflight OK: ... branch=decode_graph sha=... scenarios=6

# 5. 手动跑一轮，只落盘不发布
BENCHMARK_CONFIG=$PWD/benchmark.env PUSH_DASHBOARD=0 ./daily_runner.sh

# 6. 装定时任务
./install_cron.sh install && crontab -l
```

单场景快速试跑：

```bash
SCENARIO_SPECS=128:1024 RUN_TAG=manual-test PUSH_DASHBOARD=0 ./daily_runner.sh
```

正式默认值为 `LIGHTWEIGHT_TIMING=0`、`DIAGNOSTIC_PROMPTS=20`。不要在正式趋势任务中打开 Worker lightweight timing；若手动打开，该次结果会被标记为不具备趋势资格。Diagnostic 阶段始终在所有正式 E2E 样本之后运行，因此它自身的计时开销不会污染正式趋势。

## 脚本同步与定时任务

cron 跑的是 `sync_scripts.sh`，不是 `daily_runner.sh` 本身。它先 `git -C "$DASHBOARD_DIR" pull --ff-only`，再把仓库里的 `tools/daily_benchmark/` rsync 覆盖到 `$PROJECT_DIR/tools/daily_benchmark/`，最后 `exec` runner。所以**仓库是唯一权威副本，服务器上的改动下次运行会被冲掉**。

同步刻意不碰本机状态：`benchmark.env`、`__pycache__/`、`backups/`、`*.bak*`、`*.before-*` 都在排除列表里。

同步失败不会让这一晚的 benchmark 停摆：拉取失败、仓库不在 `main` 分支、`DASHBOARD_DIR` 未设，都只警告一句然后用手头已有的脚本继续跑。想完全关掉同步，在 `benchmark.env` 里设 `SYNC_DASHBOARD=0`。

想先看看会改什么：

```bash
BENCHMARK_CONFIG=$PWD/benchmark.env SYNC_DRY_RUN=1 ./sync_scripts.sh
```

安装/移除定时任务：

```bash
./install_cron.sh install
./install_cron.sh remove
```

重复执行不会创建重复任务。

## 发布到看板

`PUSH_DASHBOARD=0`（默认）只落盘，结果在 `$PROJECT_DIR/results/daily/<matrix_id>/`。

`PUSH_DASHBOARD=1` 会在矩阵跑完之后，把结果提交进 `DASHBOARD_DIR` 指向的那个 clone 并重新生成站点数据。

两个前提：

1. **那个 clone 必须是你有 push 权限的仓库**——通常是你自己的 fork，不是 `DINGEde/vllm-gr-performance-dashboard`。
2. **那个 clone 的工作树必须是干净的**。`daily_runner.sh:398` 只要看到 `git status --porcelain` 非空就拒绝发布。往里面手写文件（包括 `build_package.sh` 的产物）会一直卡住发布。

> **同一套看板不要有两个发布者。** 看板的选点键是 `(日期, 场景, pipeline)`，不含主机名（`scripts/build_vllm_gr_dashboard.py:305`），所以第二台机器发同一天的同一个场景会**顶掉**第一台的点。要让同事也有一份看板，让他发到自己的 fork。

`PUSH_DASHBOARD=1` 而 `DASHBOARD_DIR` 没设或不是 git 仓库时，runner 会在跑完整套矩阵**之后**才 `exit 2` 什么都不发。所以第一次配的时候先保持 0，确认数据落盘正常，再开。

## GPU 自动恢复

runner 会同时检查容器内 `nvidia-smi` 和 PyTorch CUDA。专用容器空闲但 NVML/CUDA 失效时，会自动重启一次并复检；检测到已有服务或离线 benchmark 时不会重启。可在配置中设置 `AUTO_RESTART_CONTAINER=0` 禁止自动恢复。

## 分享给别人的另一种方式

如果对方拿不到仓库，可以打一个 tar 包：

```bash
./build_package.sh [输出路径]
```

它只收集离线看板所需的文件（含 `tests/` 与 `sync_scripts.sh`），排除 `benchmark.env`、凭据、缓存、结果和旧在线脚本，同时生成 SHA-256 校验文件。默认输出到 `/tmp`，**不要**让它落在看板仓库里——那会让发布一直失败（见上）。

接收方解压到自己的 vllm-gr 检出的仓库根目录，创建 `benchmark.env`，跑通 `preflight.sh` 之后再装 cron。

## 已知缺陷

- 正式 `summary` 里没有记录工具包自己的版本，只有被测量源码的 `source.git_sha`。所以「这次跑的是哪版 `generate_summary.py`」在产物里查不到。
- `generate_summary.py:317-322` 固定查 GPU 0，不管 `GPU_INDEX` 是多少（payload 里同时也硬编码了 `"hardware": "L20"` 和 `count: 1`）。
- `generate_summary.py:289` 的 `tracked_clean` 默认 `True` 而 runner 从不传，所以这个已发布字段是没有验证过的。
- 结果目录没有保留策略：一个 matrix 约 105 MB（大头是 `prepare_native_source.py` 每轮写一份的源码快照），每天一份。

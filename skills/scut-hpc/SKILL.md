---
name: scut-hpc
description: 在华南理工 hpckapok1/hpckapok2 集群（Slurm）提交、监控、排查训练、评测和 CPU 作业，核对 GPU/CPU 资源配比，并处理集群存储、容器与环境问题。
---

# 华南理工 HPC（Slurm）作业 skill

面向 hpckapok1 与 hpckapok2 集群。官方文档：https://hpc.scut.edu.cn/docs/guide.html 。
下面每条规则都带"为什么"，模板在 `templates/`：`gpu_job.sbatch`（GPU 作业骨架）、`cpu_run.sbatch`（任意命令的 CPU 包装器）。

## 一、何时用

- 用户要把任何"重"的事（训练、推理、评测、解析大文件、图像/视频转码、`du` 全盘）放到集群上跑。
- 作业排不上、跑挂了、退出码奇怪、日志停了、`Connection reset`。
- 家目录写满、要清 checkpoint / 缓存、要迁移大目录。
- 要在计算节点上下载模型/数据、装依赖、跑 Apptainer 容器。

## 二、连接与节点规则

### 2.1 集群与登录节点（官方）

| 集群 | 登录节点 | 认证 | 分区（节点数 / 单节点配置） |
|---|---|---|---|
| hpckapok1 | 202.38.252.202–205（域名 hpckapok1.scut.edu.cn） | 账号密码 | cpuXeon6458 (320) / gpuA800 (32, 4×A800) / gpuV100 (9) / cpuFatSR950 (32, 6 TB 内存) |
| **hpckapok2** | **202.38.252.210（主）/ 202.38.252.211（备）** | 门户下载的 SSH 密钥 | cpuXeon6458 (195, 64 核 256 GB) / cpuHygon7380 (16, 64 核 512 GB) / **gpuA800 (20, 8×A800 80 GB, 64 核 512 GB)** / gpuMi210 (3) / gpuHygonZ100 (5) |

hpckapok2：CentOS 7.9/8.4，Slurm 20.11，GPU 驱动 545.23.06，CUDA 12.3，软件目录 `/public/software`，门户 https://hpckapok2.scut.edu.cn （生成 `ssh-keygen` 公钥后在门户导入）。两台登录节点共享家目录和 Slurm 队列。密码问题写校内邮箱到 hpc@scut.edu.cn。

### 2.2 SSH 一律带重试、主备切换

**为什么**：主登录节点常被别人的重计算占满（负载 600+/64 核），SSH 会 `Connection reset by peer` 或超时；备用节点看到的是同一套家目录和队列。

```bash
# 建议写进 ~/.ssh/config：Host hpc / hpc2，都指向同一 user 和密钥
hpc_ssh() {                       # 用法：hpc_ssh 'squeue -u $USER'
  local i host
  for i in 1 2 3 4; do
    for host in 202.38.252.210 202.38.252.211; do
      ssh -o ConnectTimeout=20 -o BatchMode=yes -i ~/.ssh/<hpc_key> <user>@$host "$@" && return 0
    done
    sleep 15
  done
  return 1
}
```

### 2.3 登录节点只做轻量操作

**为什么**：登录节点多用户共用，官方 FAQ 明确"登录节点用于文件编辑、作业提交、小型编译、下载等轻量级工作"，重进程会被管理员杀掉，还会把别人的 SSH 也拖垮。

- 允许：`ls/grep/sbatch/squeue/sacct/sinfo/scancel/scp/rsync`，小脚本语法检查。
- 禁止：解析大文件、图像/视频转码、训练/推理、全盘 `du`——都用 `templates/cpu_run.sbatch` 包成作业。
- `rsync` 加 `nice -n 19` 和 `--bwlimit=50m`；本地 → 集群大文件先 `scp` 到家目录再由作业处理。
- 登录节点系统 python 很旧（不认 `from __future__ import annotations`、`match`），`python -m py_compile` 要用自己 venv 的解释器。
- `ulimit -u 1000`（每用户进程/线程上限）在登录节点生效，**sbatch 默认会把它传给作业**，见 §4.2。
- 普通用户没有 `sudo`。`.bashrc` 改坏了先 `export PATH=/bin:/usr/local/sbin:/usr/local/bin:/sbin:/usr/sbin:/usr/bin` 救回来。

## 三、提交作业

### 3.1 骨架规则

```bash
#!/bin/bash
#SBATCH -J <name>              # 作业名，日志用 %x
#SBATCH -p gpuA800             # 或 cpuXeon6458 / cpuHygon7380
#SBATCH --gres=gpu:1           # 只申请需要的卡数
#SBATCH --cpus-per-task=8      # 集群 2 单卡；集群 1 A800 单卡最多按 9 核预算
#SBATCH --time=24:00:00
#SBATCH --propagate=NONE       # 见 §4.2
#SBATCH -o %x_%j.out
#SBATCH -e %x_%j.out
set -u                          # ← 从这里开始才允许出现命令
```

- **所有 `#SBATCH` 必须在第一条非注释命令之前**。**为什么**：Slurm 只解析脚本开头连续的注释块，中间插一句 `set -u` 或 `source` 后，后面的 `#SBATCH` 全部失效——作业静默拿到默认 1 核、默认时限，日志落到 `slurm-<id>.out`，很难察觉。改完脚本核对一次：

```bash
awk '/^#SBATCH/{last=NR} !/^#/ && !/^$/ && !/^#!/ && !first{first=NR} END{print "last SBATCH:",last," first cmd:",first, (last<first?"OK":"BROKEN")}' job.sbatch
```

- 日志用 `%x_%j.out`，脚本末尾 `exit $RC`。**为什么**：Slurm 以脚本最后一条命令的退出码为准；崩溃后若最后一条是 `echo`，状态就是 COMPLETED，`afterok` 拦不住失败。评测/训练 python 脚本本身也要 `sys.exit(rc)`。
- 提交并拿作业号：`JID=$(sbatch --parsable job.sbatch)`。
- 查看：`squeue -u $USER -o '%i %j %T %M %R'`、`sacct -j $JID --format=JobID,State,ExitCode,Elapsed,MaxRSS`、`scontrol show job $JID`。集群配了 `PrivateData=jobs`，`squeue` 看不到别人的作业，排队原因要看 `sinfo`（§4.1）。
- 交互式调试：集群 2 可用 `srun -p gpuA800 --gres=gpu:1 --cpus-per-task=8 --time=2:00:00 --pty bash`；进自己的作业看进程：`srun --jobid=$JID --overlap bash -c 'ps -u $USER -o nlwp,comm --sort=-nlwp | head'`。

### 3.2 通用 CPU 包装器（`templates/cpu_run.sbatch`）

```bash
sbatch --parsable -J convert --export=ALL,CMD='python tools/convert.py --in raw --out jpg' templates/cpu_run.sbatch
```

脚本内 `eval "$CMD"; RC=$?; exit $RC`。**为什么**：一个模板覆盖所有"跑个命令"的需求，不用为每个小任务写 sbatch；`eval` + 透传退出码让它能挂进依赖链。
限制：`--export` 用逗号分隔，**CMD 里不能含逗号**；多值/复杂参数用 `env FOO=a,b sbatch --export=ALL ...` 或把参数写进文件让命令读。

### 3.3 依赖链而不是轮询

```bash
J1=$(sbatch --parsable -J prep   --export=ALL,CMD='python prep.py'    templates/cpu_run.sbatch)
J2=$(sbatch --parsable -J train  --dependency=afterok:$J1              templates/gpu_job.sbatch)
J3=$(sbatch --parsable -J eval   --dependency=afterok:$J2              eval.sbatch)
J4=$(sbatch --parsable -J report --dependency=afterany:$J3 --export=ALL,CMD='python report.py' templates/cpu_run.sbatch)
sbatch -J train --dependency=singleton train.sbatch   # 同名作业串行，天然的"上一轮跑完再跑"
```

**为什么**：`sleep` 轮询要占着一个 SSH/登录节点进程，SSH 一断就丢；依赖链由 Slurm 维护，`afterok` 失败即整条停下（前提是退出码正确透传），`afterany` 用于收尾/清理。

### 3.4 传参

- `--export=ALL,VAR=value`：简单标量。
- `env VAR='a,b,c' sbatch --export=ALL job.sbatch`：含逗号的值。
- 写 `run_<id>.env` 文件，作业里 `source`：多个复杂参数、要留档的配置。
- `#SBATCH --array=0-4` + `$SLURM_ARRAY_TASK_ID`：同一脚本扫参数。

## 四、资源与线程预算

### 4.1 分区选择与节点状态

```bash
sinfo -p gpuA800 -N -o '%N %t %G %C'           # 每节点状态：idle/mix/alloc/drain
sinfo -p gpuA800 -N --states=idle,mix           # 能立刻拿到卡的节点
sinfo -p cpuXeon6458 -o '%P %a %D %t'
```

- `gpuA800`：标称 20 节点，**实际常只有少数节点可用，其余 drain**；排队时先看 `sinfo`，别盲等。
- `cpuXeon6458`：195 节点，默认 CPU 分区。
- `cpuHygon7380`：AMD 兼容架构，**某些 x86 扩展指令不支持**（出现 SIGILL / `rc=132` 就换 Xeon）。
- 集群 1 的 `gpuA800` 按每卡 **9 核**、集群 2 的 `gpuA800` / `gpuMi210` / `gpuHygonZ100` 按每卡 **8 核**作为节点布局预算上限，实际资源提交前用 `scontrol show node` 核对。GPU 作业按程序所需申请 CPU，默认不超过 `GPU 数 × 对应核数`；集群 2 的 1/2/4 卡对应上限为 8/16/32 核。轻量评测若实测 4 核足够，就申请 4 核，不必凑满预算。提交前核对 `--gres`、`--ntasks`、`--cpus-per-task` 和同一节点的空闲资源；多申请 CPU 会挤占同节点剩余 GPU 的调度空间。确需超过每卡预算时，先确认目标节点余量并记录理由，不把超额申请作为模板默认值。
- 评测/推理通常申请 1 卡，微调按实际吞吐和排队情况选择卡数；内存按需申请，不占整节点。纯 CPU 预处理、统计、校验和转码使用 CPU 分区，**不要申请 GPU 或占用 GPU 分区**。
- `--time` 参考同类作业的实际耗时并留启动余量，不依赖运行后再延长：集群 2 普通用户增加运行中作业时限曾返回 `Access/permission denied`。排队中作业可尝试修改时限，但须核对命令返回值和 `scontrol show job`。长评测可按互不重复的 seed 段运行，失败后只补该段。
- 驱动不一致的节点用 `--exclude=gpuXX,gpuYY` 排掉。**为什么**：个别节点驱动版本与容器/venv 内 CUDA 库不匹配，`torch.cuda` 直接失败；模板里先 `nvidia-smi --query-gpu=driver_version` 校验，不匹配 `exit 75`，提交方加 `--exclude` 重提。

### 4.2 `--propagate=NONE`（必加）

**为什么**：登录节点 `ulimit -u 1000`，sbatch 默认把提交环境的 ulimit 传给作业；同一计算节点上自己的多个作业线程总数一旦超过 1000，`pthread_create: Resource temporarily unavailable` → 进程 abort。Isaac Sim、JAX、PyTorch DataLoader 这类多线程程序在 1–2 分钟内必死，且 Slurm 状态往往还是 COMPLETED。
作业里可以每 2 分钟记录一次线程总数（模板已内置）：`ps -u $USER -o nlwp= | awk '{s+=$1} END{print s}'`。

### 4.3 线程数按申请核数派生

```bash
NT=${SLURM_CPUS_PER_TASK:-${SLURM_CPUS_ON_NODE:-8}}
OMP=$(( NT/2 > 4 ? NT/2 : 4 ))
export OMP_NUM_THREADS=$OMP MKL_NUM_THREADS=$OMP OPENBLAS_NUM_THREADS=$OMP NUMEXPR_NUM_THREADS=$OMP
KIT_THREADS=$(( NT*2/3 > 8 ? NT*2/3 : 8 ))
```

**为什么**：节点有 64 核但作业可能只申请了 4 或 8 核；库若默认按全节点 64 核开线程，会触发 §4.2 的进程上限并互相争抢。

- Isaac Sim / Kit：`--kit_args="--/plugins/carb.tasking.plugin/threadCount=$KIT_THREADS"`。**注意等号写法**，写成 `--kit_args "--/..."` 会被 argparse 把 `--/` 当独立选项；线程数低于 8 会卡死。
- JAX：**不要**加 `--xla_cpu_multi_thread_eigen=false`，编译会慢 10 倍。
- LeRobot 自带的视频编码在计算节点多进程下报 `Errno 11`，改用 `imageio-ffmpeg` 自带的静态 ffmpeg 串行编码。

### 4.4 看门狗与超时

- 长作业里起后台循环，日志 15 分钟无更新就 `scancel $SLURM_JOB_ID`（模板已内置）。**为什么**：仿真器/分布式初始化挂死时进程不退出，卡会被空占到时限用完。
- 预热、拉模型、建连接这类步骤套 `timeout 600 <cmd> || exit 1`。
- 排查时先看日志末尾的 `RC=` 行，再看 `sacct` 的 State/ExitCode。

## 五、存储与清理

### 5.1 配额与查看

```bash
df -h $HOME          # 家目录配额视图（4 TB）
df -h /public        # 整个 Lustre，不是你的配额
lfs quota -u $USER /public 2>/dev/null   # 有的节点可用
```

**为什么关心**：写满后连 2 KB 的 `scp` 都失败，训练写 checkpoint 直接 `RESOURCE_EXHAUSTED`，且平台会定期清理"僵尸"数据，重要结果要及时同步走。

### 5.2 大头在哪

| 类型 | 典型规模 | 处理 |
|---|---|---|
| 训练 checkpoint | 每步目录几十 GB，默认每 5000 步留一个，40k 步 = 300 GB+ | 用训练框架的 `--keep-period`/`save_total_limit` 只留最后 1 个；跑完把最终权重挪到 `$HOME/<project>/ckpt_final/` 再删中间步 |
| HF datasets 缓存 | `HF_DATASETS_CACHE` 下 arrow 副本，几百 GB | **训练进行中不能删，它就是 DataLoader 在读的东西**；训练结束后删 |
| 原始 PNG 录制 | 巨大 | JPEG q95 重编码后约 1/3，mp4 后约 1/20，转码放 CPU 作业 |
| pip/conda/apptainer 缓存 | 几十 GB | `pip cache purge`、`conda clean -a`、清 `APPTAINER_CACHEDIR` |

### 5.3 安全删除流程

```bash
# 1. 全盘 du 放 CPU 作业（要几分钟）
sbatch -J du --export=ALL,CMD='du -sh $HOME/*/ 2>/dev/null | sort -h > $HOME/du_$(date +%F).txt' templates/cpu_run.sbatch
# 2. dry-run 列清单，人工看一眼
find $HOME/<project>/ckpt -maxdepth 1 -type d -name 'step_*' | sort -V | head -n -1
# 3. 删除脚本加保护：相关作业还在跑就退出
squeue -u $USER -h -o '%j' | grep -q '^train' && { echo "train running, abort"; exit 1; }
# 4. Lustre 上删几十万小文件很慢，放后台，避免 SSH 断开中断
nohup rm -rf $HOME/<project>/raw_png > $HOME/rm_$(date +%F).log 2>&1 &
```

**为什么**：`rm -rf` 是不可逆的，且 Lustre 上删小文件是分钟到小时级；同盘 `mv` 是瞬间的，所以大改动用"先生成副本 → 校验 → `mv` 替换"的模式，中途失败不会破坏原目录。

## 六、容器 / 网络 / 依赖

### 6.1 计算节点上网（官方 FAQ + 实测）

```bash
export http_proxy=http://login5:3128 https_proxy=http://login5:3128   # 或 192.168.5.249:3128；hpckapok1 用 login04:3128
export no_proxy=localhost,127.0.0.1
export HF_ENDPOINT=https://hf-mirror.com     # huggingface.co 直连被墙
export HF_HUB_DISABLE_XET=1                  # huggingface_hub 1.x 默认 Xet 后端经镜像会 401
hf download <org>/<model> --local-dir $HOME/models/<model>   # 新版 CLI 是 hf，不是 huggingface-cli
```

- 登录节点不需要代理。
- GitHub release 资产经代理可能得到 **0 字节文件**，下载后 `ls -l` 核对大小；不行就本地下载再 `scp`。
- pip/conda 也走同一代理；`pip download` 在本地然后 `scp` wheel 是最稳的兜底。

### 6.2 module / conda / spack（官方）

```bash
module avail                                   # 查
module load cuda/12.3.0                        # hpckapok2 还有 11.8.0 / 11.6 / 10.2.0
module load /public/software/modules/apps/anaconda3/3-2023.09
module load apps/apptainer/1.4.4               # 或 apps/singularity/3.9.9
```

- 第一次用 conda 必须先写 `~/.condarc`（官方要求，否则建不了个人环境）：

```bash
cat > ~/.condarc <<EOC   # 不加引号，让 $HOME 展开
auto_activate_base: false
pkgs_dirs:
  - $HOME/.conda/pkgs
envs_dirs:
  - $HOME/.conda/envs
EOC
```

- 多个 conda/venv 各自独立放在 `$HOME/<project>/envs/` 下，用一个 `$HOME/<project>/env.sh` 统一 `source`（模板里已引用）。**为什么**：模板和依赖链只需要知道一个入口，换环境不用改 sbatch。
- 集群 **glibc 2.28**（CentOS 8）：预编译的 flash-attn wheel（要求 ≥ 2.32）装不上，用 SDPA 回退或源码编译；官方提供 spack 的 `glibc230` 环境（`spack env activate glibc230`）可作兜底，但对 CUDA 扩展慎用。
- 自定义 modulefile：`mkdir $HOME/mymodulefiles && export MODULEPATH=$HOME/mymodulefiles:$MODULEPATH`。

### 6.3 Apptainer（Isaac Sim 等）

```bash
module load apps/apptainer/1.4.4
export APPTAINER_CACHEDIR=$HOME/.apptainer/cache APPTAINER_TMPDIR=$HOME/.apptainer/tmp   # 默认在 /tmp，会满
apptainer pull isaac.sif docker://nvcr.io/nvidia/isaac-sim:<tag>        # 需要 §6.1 代理
apptainer exec --nv \
  --bind $HOME/<project>:/workspace --bind $HOME/.cache:/root/.cache \
  isaac.sif bash -lc 'source /workspace/env.sh && python /workspace/run.py'
```

- `--nv` 给 GPU，`--bind` 挂宿主目录（家目录和当前目录官方默认自动挂）。
- 容器内的 venv 装包用 `pip install --no-deps <pkg>`。**为什么**：Isaac Sim 之类镜像的依赖是钉死的，让 pip 解析依赖会升级 numpy/torch 把镜像搞坏。
- 驱动版本检查见 §4.1：容器带的 CUDA 用户态库要和节点驱动（545.23.06）兼容。

## 七、排障速查表

| 症状 | 原因 | 处理 |
|---|---|---|
| `ssh: Connection reset` / 超时 | 主登录节点被占满 | 切 `.211`，重试循环（§2.2） |
| `pthread_create: Resource temporarily unavailable`，程序 1–2 分钟内 abort | 作业继承登录节点 `ulimit -u 1000` | `#SBATCH --propagate=NONE`；线程预算按 §4.3 |
| 作业只拿到 1 核、日志叫 `slurm-<id>.out`、时限不对 | `#SBATCH` 后面有命令插队，后续 `#SBATCH` 失效 | 用 §3.1 的 awk 核对；把命令挪到全部 `#SBATCH` 之后 |
| `sacct` 显示 COMPLETED 但结果没产出 | 脚本最后一条命令是 `echo`，退出码被吞 | 看日志 `RC=`；脚本末尾 `exit $RC` |
| `afterok` 链没有停在失败处 | 同上 | 同上 |
| `SIGILL` / `rc=132` | cpuHygon7380 不支持某些 x86 扩展指令 | 换 `-p cpuXeon6458` |
| `torch.cuda` 不可用 / CUDA driver mismatch | 该节点驱动版本不同 | `nvidia-smi --query-gpu=driver_version`；`--exclude` 该节点重提 |
| 一直 PD，`squeue` 里只看到自己的作业 | `PrivateData=jobs`；gpuA800 多数节点 drain | `sinfo -p gpuA800 -N -o '%N %t %G'` 看 alloc/mix/drain；减卡数/换分区 |
| `RESOURCE_EXHAUSTED` / `No space left`，`scp` 小文件也失败 | 家目录 4 TB 配额满 | `df -h $HOME`；按 §5 清 checkpoint/缓存 |
| 日志几十分钟不动、GPU 利用率 0 | 仿真器/分布式初始化挂死 | 看门狗自动 `scancel`；预热步骤加 `timeout` |
| `hf download` 401 / 连不上 huggingface.co | 直连被墙；Xet 后端不走镜像 | `HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1` |
| 下到的 GitHub release 文件 0 字节 | 代理对大文件重定向处理有问题 | 本地下载 `scp` 上去 |
| `flash_attn` 安装失败 GLIBC_2.32 not found | 集群 glibc 2.28 | SDPA 回退 / 源码编译 / spack `glibc230` |
| `py_compile` 报语法错但本地正常 | 登录节点系统 python 太旧 | 用 venv 的 python |
| LeRobot 视频编码 `Errno 11` | 多进程 + ffmpeg 在计算节点受限 | imageio-ffmpeg 静态 ffmpeg，串行编码 |
| Isaac Sim `--/plugins/...` 被当成未知参数 | `--kit_args` 没用等号 | `--kit_args="--/plugins/carb.tasking.plugin/threadCount=N"`，N ≥ 8 |
| JAX 编译极慢 | 加了 `--xla_cpu_multi_thread_eigen=false` | 去掉 |
| `rm -rf` 大目录跑一半 SSH 断了 | Lustre 删小文件慢 | `nohup ... &`；或 `mv` 到 `$HOME/.trash/` 后台慢慢删 |
| conda 建环境报权限错 | 没写 `~/.condarc` | §6.2 |
| 登录后命令全找不到 | `.bashrc` 改坏 | `export PATH=/bin:/usr/local/sbin:/usr/local/bin:/sbin:/usr/sbin:/usr/bin` |

## 八、提交前检查清单

1. `#SBATCH` 全部在第一条命令之前（跑一遍 §3.1 的 awk）。
2. 有 `--propagate=NONE`；`-o %x_%j.out`；脚本末尾 `exit $RC`。
3. 卡数/核数/内存/时限按需，不是整机；`--exclude` 里有已知坏节点。
4. 线程数从 `SLURM_CPUS_PER_TASK` 派生，没有硬编码 64。
5. 计算节点要联网的步骤设了代理和 `HF_ENDPOINT`/`HF_HUB_DISABLE_XET`，预热类步骤有 `timeout`。
6. 长作业有看门狗；输出目录存在且 `df -h $HOME` 还有余量（checkpoint 只留最后一个）。
7. 依赖链用 `--dependency`，作业号来自 `sbatch --parsable`，CMD 里没有逗号。
8. 语法检查用 venv 的 python，不用登录节点系统 python。
9. 提交后 `squeue -u $USER` 确认状态，起跑 2 分钟后看一眼日志有 `[driver]`/`[threads]` 行且 RC 未提前出现。
10. 删除类操作先 dry-run 列清单，脚本里有"相关作业在跑就退出"的保护。

## 九、官方文档索引（读到的部分）

登录 `docs/login.html` · 文件传输 `docs/file_transfer.html` · 硬件 `docs/resources/hardware.html` · 软件 `docs/resources/software.html` · Slurm `docs/job/Slurm.html` · 作业示例 `docs/job/job_example.html` · 资源选择 `docs/job/gres_select.html` · Conda/Module/Singularity/Spack `docs/software/basic/*.html` · CUDA `docs/software/list/tools/cuda.html` · FAQ `docs/faq.html`（均在 https://hpc.scut.edu.cn/ 下）。门户可视化平台（Jupyter/VSCode/远程桌面）见 `docs/platform/`，本 skill 不覆盖。

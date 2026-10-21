# 华南理工 HPC 命令行操作手册

本文面向华南理工 `hpckapok1` 和 `hpckapok2`（Slurm）的人类操作员，整理从登录、资源查看、提交作业到排障和清理的常用命令。两个集群的 `sbatch`、`srun`、`squeue`、`sacct` 等基本用法相同；分区、登录地址、认证方式和 GPU 配额不同。登录节点只做轻量操作；训练、推理、转码、大文件解析和全盘 `du` 必须提交到计算节点。

## 1. 登录与安全

### 集群选择

| 集群 | 登录节点 | 认证 | 常用分区 | GPU 配额提示 |
|---|---|---|---|---|
| `hpckapok1` | `202.38.252.202`–`205`，域名 `hpckapok1.scut.edu.cn` | 账号密码 | `cpuXeon6458`、`gpuA800`、`gpuV100`、`cpuFatSR950` | `gpuA800` 通常按每卡 9 个 CPU 核申请 |
| `hpckapok2` | `202.38.252.210`（主）、`211`（备） | 门户导入的 SSH 密钥 | `cpuXeon6458`、`cpuHygon7380`、`gpuA800`、`gpuMi210`、`gpuHygonZ100` | GPU 分区通常按每卡 8 个 CPU 核申请 |

集群一登录示例：

```bash
ssh <user>@hpckapok1.scut.edu.cn
# 域名不可用时，可选择 202.38.252.202、203、204 或 205
ssh <user>@202.38.252.202
```

集群二的主、备登录节点是 `202.38.252.210` 和 `202.38.252.211`。建议在本机 `~/.ssh/config` 中配置一个别名（用户名和密钥路径由操作员自行填写，不要写入脚本或日志）：

```sshconfig
Host scut-hpc
    HostName 202.38.252.210
    User <学号或用户名>
    IdentityFile ~/.ssh/<hpc-key>
    ConnectTimeout 20
```

主节点连接失败时切换备用节点：

```bash
ssh -o ConnectTimeout=20 scut-hpc
ssh -o ConnectTimeout=20 <user>@202.38.252.211
```

也可以分别配置两个别名：

```sshconfig
Host scut-hpc1
    HostName hpckapok1.scut.edu.cn
    User <user>
Host scut-hpc2
    HostName 202.38.252.210
    User <user>
    IdentityFile ~/.ssh/<hpc-key>
```

登录节点上允许编辑、传输、查询和提交作业；不要直接运行训练、推理、转码或大规模扫描。

### 命令应该在哪里运行

| 命令或操作 | 执行位置 | 说明 |
|---|---|---|
| `ssh`、`scp`、`rsync` | 本地电脑 | 连接集群或传输文件；目标写成 `scut-hpc:<path>`。 |
| `sinfo`、`squeue`、`sacct`、`scontrol`、`sbatch`、`scancel` | 集群登录节点 | 先 `ssh scut-hpc`（或 `ssh scut-hpc1`）再运行；这些命令访问集群的 Slurm 控制器。 |
| `srun --pty bash` | 从登录节点提交，程序在计算节点运行 | 适合短时交互调试。 |
| `python train.py`、训练、推理、转码、`du` | 计算节点 | 写入 `sbatch` 脚本后提交，不能直接在本地机或登录节点运行。 |
| `slurmbox start/status/exec` | 通常在本地电脑 | SlurmBox 根据配置通过 SSH 操作集群；`slurmbox exec` 的用户命令最终在独立的 `srun` 步骤中运行。 |

典型流程是：

```text
本地电脑 --ssh--> 集群登录节点 --sbatch/srun--> 计算节点
```

例如，在本地电脑上传脚本并登录：

```bash
scp job.sbatch scut-hpc:"$HOME/project/"
ssh scut-hpc
cd "$HOME/project"
sbatch job.sbatch
```

如果已经在集群登录节点上，后续 `sinfo`、`sbatch` 和 `squeue` 直接运行即可；如果断开 SSH，已提交的作业仍会继续运行。

### 完整示例：提交一个 GPU 计算任务

下面假设本地项目目录为 `~/project`，其中已有 `train.py` 和 `configs/dev.yaml`，结果写入 `results/`。示例以集群二的 `gpuA800` 为例；提交到集群一时，将 SSH 别名改为 `scut-hpc1`，并按前文规则把每卡 CPU 核数调整为 9。

1. 在本地电脑准备作业脚本 `job.sbatch`：

```bash
#!/bin/bash
#SBATCH -J demo-train
#SBATCH -p gpuA800
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --propagate=NONE
#SBATCH --chdir=$HOME/project
#SBATCH -o %x_%j.out
#SBATCH -e %x_%j.out
set -u
set -o pipefail

mkdir -p results
NT=${SLURM_CPUS_PER_TASK:-8}
export OMP_NUM_THREADS="$NT"
export MKL_NUM_THREADS="$NT"

echo "JOB=$SLURM_JOB_ID HOST=$(hostname) START=$(date)"
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader

python train.py --config configs/dev.yaml --output-dir results
rc=$?
echo "RC=$rc END=$(date)"
exit "$rc"
```

2. 从本地电脑上传代码、配置和作业脚本：

```bash
scp -r ~/project scut-hpc:"$HOME/"
scp job.sbatch scut-hpc:"$HOME/project/"
```

如果项目很大，使用限速的增量同步：

```bash
rsync -az --bwlimit=50m ~/project/ scut-hpc:"$HOME/project/"
```

3. 登录集群，确认脚本和配置存在，再提交：

```bash
ssh scut-hpc
cd "$HOME/project"
test -f train.py && test -f configs/dev.yaml && test -f job.sbatch
job_id=$(sbatch --parsable job.sbatch)
echo "submitted: $job_id"
```

4. 在登录节点查看排队、运行状态和原因：

```bash
squeue -j "$job_id" -o '%i %j %T %M %R'
scontrol show job "$job_id"
```

`PD` 表示排队，`R` 表示运行，`CG` 表示收尾，`CD` 表示完成，`F`/`TO` 表示失败或超时。作业运行后查看日志：

```bash
tail -f "demo-train_${job_id}.out"
```

5. 作业结束后检查退出码、时长和内存：

```bash
sacct -j "$job_id" --format=JobID,JobName,State,ExitCode,Elapsed,MaxRSS
ls -lh results/
```

只有看到日志中的 `RC=0` 且 `sacct` 状态为 `COMPLETED`，才视为成功。若失败，先保留日志和作业号，再根据 `ExitCode` 和日志末尾排查；不要用 `echo` 覆盖训练程序的退出码。

6. 回到本地电脑下载结果：

```bash
scp -r scut-hpc:"$HOME/project/results" ./results
# 将 <job-id> 替换为提交时输出的实际作业号，例如 123456
scp scut-hpc:"$HOME/project/demo-train_<job-id>.out" ./
```

任务完成后，如确认不再需要中间文件，可在集群登录节点删除；删除前先确认没有相关作业运行：

```bash
squeue -u "$USER" -o '%i %j %T'
```

### 原生 Slurm 持久交互式资源：`salloc`

不使用 SlurmBox 时，可以直接用 Slurm 的 `salloc` 申请一段较长时间的交互式资源。allocation 会一直保留到时限结束或主动退出；每个 `srun` 是其中的一次独立任务步骤：

```bash
# 在集群登录节点申请 8 小时、1 张 GPU
salloc -p gpuA800 --gres=gpu:1 --cpus-per-task=8 --mem=64G --time=08:00:00

# 进入已分配的计算节点 shell
srun --pty bash

# 在计算节点中运行第一个项目
cd "$HOME/project-a"
python train.py --config configs/a.yaml

# 代码结束后，继续运行第二个项目；资源仍属于同一个 allocation
cd "$HOME/project-b"
python run.py --config configs/b.yaml

# 退出计算节点 shell，再退出 salloc shell，释放 allocation
exit
exit
```

也可以不进入持久 shell，而是在 allocation 中逐个提交步骤：

```bash
salloc -p gpuA800 --gres=gpu:1 --cpus-per-task=8 --time=08:00:00
srun bash -lc 'cd "$HOME/project-a" && python train.py --config configs/a.yaml'
srun bash -lc 'cd "$HOME/project-b" && python run.py --config configs/b.yaml'
exit
```

运行期间可以在另一个 SSH 会话中用 `squeue -u "$USER"` 查看 allocation。若 SSH 断开，交互 shell 通常会结束；需要长时间保持交互环境时，可在登录节点的 `tmux` 中运行 `salloc`，但仍应遵守登录节点不运行重计算的规则。集群一使用 `-p gpuA800` 时，通常将 `--cpus-per-task` 改为 9，并先用 `sinfo` 确认分区和资源配额。

## 2. 只读查看集群和自己的作业

```bash
# 分区、可用性、节点数量和状态
sinfo -o '%P %a %D %t %G'
sinfo -p gpuA800 -N -o '%N %t %G %C'
sinfo -p cpuXeon6458 -N -o '%N %t %C'

# 查看自己的活动作业；PrivateData=jobs 时通常看不到他人作业
squeue -u "$USER" -o '%i %j %T %M %R'

# 查看作业详情、分配节点和预计开始时间
scontrol show job <job-id>

# 查看已结束作业的状态、退出码、时长和最大内存
sacct -j <job-id> --format=JobID,JobName,State,ExitCode,Elapsed,MaxRSS
sacct -u "$USER" --starttime=2026-01-01 --format=JobID,JobName,State,Elapsed
```

切换集群后先运行不带 `-p` 的 `sinfo`，确认目标集群实际存在的分区；例如集群一可查询 `gpuV100`、`cpuFatSR950`，集群二可查询 `gpuMi210`、`gpuHygonZ100`。

GPU 作业提交前先看 `idle`/`mix` 节点，并排除 `drain`/`down`。`%G` 是节点总 GPU 数，不等于当前可申请的空卡数；还要核对 CPU 是否足够。

## 3. 编写并提交批处理作业

所有 `#SBATCH` 行必须位于第一条命令之前，并建议使用 `--propagate=NONE`、作业名和带作业号的日志：

```bash
#!/bin/bash
#SBATCH -J demo
#SBATCH -p gpuA800
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8       # hpckapok2；hpckapok1 gpuA800 通常填 9
#SBATCH --time=02:00:00
#SBATCH --propagate=NONE
#SBATCH -o %x_%j.out
#SBATCH -e %x_%j.out
set -u

set -o pipefail
# 默认在 sbatch 提交时的目录运行；也可以在这里显式固定目录
# cd "$HOME/project"
python train.py --config configs/dev.yaml
rc=$?
echo "RC=$rc"
exit "$rc"
```

上例中的 `python train.py --config configs/dev.yaml` 默认从执行 `sbatch job.sbatch` 时的当前目录解析：如果当时位于 `$HOME/project`，实际读取的是 `$HOME/project/train.py` 和 `$HOME/project/configs/dev.yaml`。为避免从其他目录提交时路径失效，可以在脚本中显式写入：

```bash
#SBATCH --chdir=$HOME/project
python train.py --config configs/dev.yaml
```

或者使用绝对路径：

```bash
python "$HOME/project/train.py" --config "$HOME/project/configs/dev.yaml"
```

提交前可在脚本中临时加入 `pwd` 和 `ls -la` 检查工作目录与文件是否存在；排查完成后可删除这两行。

提交并记录作业号：

```bash
job_id=$(sbatch --parsable job.sbatch)
printf 'submitted job %s\n' "$job_id"
squeue -j "$job_id"
```

提交后两分钟内检查状态和日志：

```bash
squeue -j "$job_id" -o '%i %j %T %M %R'
tail -n 50 "demo_${job_id}.out"
sacct -j "$job_id" --format=JobID,State,ExitCode,Elapsed,MaxRSS
```

检查 `#SBATCH` 是否被命令提前截断：

```bash
awk '/^#SBATCH/{last=NR} !/^#/ && !/^$/ && !/^#!/ && !first{first=NR} END{print "last SBATCH:",last," first cmd:",first,(last<first?"OK":"BROKEN")}' job.sbatch
```

### `#SBATCH` 参数含义

`#SBATCH` 是写在作业脚本开头的 Slurm 指令。它们只影响当前作业的资源、调度和输出设置，不是 shell 命令；必须放在第一条实际命令之前。

| 参数 | 含义 | 示例 |
|---|---|---|
| `-J` / `--job-name` | 作业名称，便于在 `squeue`、日志和 `sacct` 中识别 | `#SBATCH -J train` |
| `-p` / `--partition` | 选择分区；按任务选择 CPU 或 GPU 分区 | `#SBATCH -p gpuA800` |
| `--gres=gpu:N` | 申请 N 张 GPU；作业通常在单个节点上获得这些卡 | `#SBATCH --gres=gpu:1` |
| `-c` / `--cpus-per-task` | 每个任务申请的 CPU 核数；单进程多线程程序常用此项 | `#SBATCH --cpus-per-task=8` |
| `-n` / `--ntasks` | 申请任务（进程）数量；MPI 或多进程程序使用 | `#SBATCH --ntasks=4` |
| `--ntasks-per-node` | 每个节点放置的任务数 | `#SBATCH --ntasks-per-node=4` |
| `--mem` | 每个节点申请的总内存 | `#SBATCH --mem=64G` |
| `--mem-per-cpu` | 每个 CPU 核申请的内存；不要与 `--mem` 同时使用 | `#SBATCH --mem-per-cpu=4G` |
| `-N` / `--nodes` | 节点数量；不写时由 Slurm 根据任务自动分配 | `#SBATCH --nodes=1` |
| `--time` | 作业最长运行时间，格式为 `日-时:分:秒` 或 `时:分:秒` | `#SBATCH --time=02:00:00` |
| `--gres` | 通用资源申请接口，GPU 是最常见用法 | `#SBATCH --gres=gpu:1` |
| `--exclude` | 排除指定节点；用于已知驱动或硬件异常节点 | `#SBATCH --exclude=gpuXX` |
| `--constraint` | 按节点特征筛选资源，具体特征以集群配置为准 | `#SBATCH --constraint=<feature>` |
| `--array` | 创建作业数组；每个子作业通过 `$SLURM_ARRAY_TASK_ID` 区分 | `#SBATCH --array=0-4` |
| `--dependency` | 设置前置作业条件；常用 `afterok:<job-id>` | `#SBATCH --dependency=afterok:12345` |
| `--propagate=NONE` | 不继承登录节点的资源限制；本集群建议所有作业都加 | `#SBATCH --propagate=NONE` |
| `-o` / `--output` | 标准输出文件；`%x` 是作业名，`%j` 是作业号 | `#SBATCH -o %x_%j.out` |
| `-e` / `--error` | 标准错误文件；可与标准输出写入同一文件 | `#SBATCH -e %x_%j.out` |
| `--chdir` | 作业启动时切换到指定工作目录 | `#SBATCH --chdir=$HOME/project` |
| `--export` | 向作业传递环境变量；也可在提交命令中设置 | `sbatch --export=ALL,DATA_DIR=/path job.sbatch` |
| `--mail-type` | 作业状态邮件通知类型，需集群配置邮件服务 | `#SBATCH --mail-type=FAIL,END` |
| `--mail-user` | 接收通知的邮箱地址；不要在公开脚本中提交私人信息 | `#SBATCH --mail-user=<email>` |

资源参数应与程序实际需求匹配。GPU 作业还要按集群规定的每卡 CPU 配额申请核数；`--time` 应接近同类任务实际耗时，过长会降低排队机会。`--gres=gpu:N`、`--cpus-per-task`、`--mem` 和 `--nodes` 之间必须能在同一节点上同时满足，不能把多台机器的空闲 GPU 拼成一个单节点作业。

## 4. 交互式调试和依赖链

申请短时交互资源进行调试；退出 shell 即结束该交互作业：

```bash
srun -p gpuA800 --gres=gpu:1 --cpus-per-task=8 --time=01:00:00 --pty bash
```

用 Slurm 依赖连接阶段，不要在登录节点用 `sleep` 轮询：

```bash
prep=$(sbatch --parsable -J prep prep.sbatch)
train=$(sbatch --parsable -J train --dependency=afterok:"$prep" train.sbatch)
eval=$(sbatch --parsable -J eval --dependency=afterok:"$train" eval.sbatch)
sbatch --parsable -J report --dependency=afterany:"$eval" report.sbatch
```

`afterok` 只在前置作业成功时运行；`afterany` 用于无论成败都要执行的收尾步骤。脚本必须透传真实退出码，否则依赖判断会失真。

## 5. 传参、数组和资源预算

```bash
# 简单变量
sbatch --export=ALL,DATA_DIR="$HOME/data" job.sbatch

# 参数扫描
#SBATCH --array=0-4
echo "task index: $SLURM_ARRAY_TASK_ID"
```

GPU 卡、CPU 核和时限按实际需要申请；不要为程序不使用的 GPU 或整节点内存付出排队成本。程序内线程数应来自申请的核数：

```bash
NT=${SLURM_CPUS_PER_TASK:-8}
export OMP_NUM_THREADS="$NT" MKL_NUM_THREADS="$NT" OPENBLAS_NUM_THREADS="$NT"
```

## 6. 模块、Conda、容器和计算节点网络

```bash
module avail
module load cuda/12.3.0
module load apps/apptainer/1.4.4

# 首次使用 Conda 前，将个人包和环境放到家目录
cat > ~/.condarc <<EOC
auto_activate_base: false
pkgs_dirs:
  - $HOME/.conda/pkgs
envs_dirs:
  - $HOME/.conda/envs
EOC
```

计算节点需要访问 Hugging Face 时，在作业脚本中设置代理和镜像；不要把带凭据的 URL 写入日志：

```bash
export http_proxy=http://login5:3128
export https_proxy=http://login5:3128
export no_proxy=localhost,127.0.0.1
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1
hf download <org>/<model> --local-dir "$HOME/models/<model>"
```

使用 GPU 容器：

```bash
export APPTAINER_CACHEDIR="$HOME/.apptainer/cache"
export APPTAINER_TMPDIR="$HOME/.apptainer/tmp"
apptainer exec --nv \
  --bind "$HOME/<project>:/workspace" \
  image.sif bash -lc 'source /workspace/env.sh && python /workspace/run.py'
```

## 7. 文件传输、配额和清理

```bash
scp -r ./results scut-hpc:"$HOME/project/"
rsync -az --bwlimit=50m ./results/ scut-hpc:"$HOME/project/results/"

df -h "$HOME"
lfs quota -u "$USER" /public 2>/dev/null
```

大目录统计应放进 CPU 作业：

```bash
sbatch -J disk-usage --export=ALL,CMD='du -sh "$HOME"/* 2>/dev/null | sort -h > "$HOME/du.txt"' templates/cpu_run.sbatch
```

删除前先列清单并确认相关作业已停止；`rm -rf` 不可恢复。Lustre 上大量小文件删除可能很慢，可把删除命令放入作业或使用 `nohup`，不要占用登录节点。

## 8. 停止、修改和排障

```bash
# 取消自己的作业；先确认作业号
scancel <job-id>

# 修改排队作业的时限
scontrol update JobId=<job-id> TimeLimit=01:30:00

# 读取作业日志末尾和完整记账信息
tail -n 100 <job-name>_<job-id>.out
sacct -j <job-id> --format=JobID,State,Reason,ExitCode,Elapsed,MaxRSS
```

常见现象：

| 现象 | 首要检查 |
|---|---|
| SSH 超时或 `Connection reset` | 切换 `202.38.252.211`，并重试 |
| 一直 `PD` | `squeue` 看 Reason，`sinfo` 检查节点是否 `drain`，减少卡数或缩短 `--time` |
| `pthread_create` / 线程资源不足 | 加 `--propagate=NONE`，按 `SLURM_CPUS_PER_TASK` 限制线程 |
| `COMPLETED` 但没有结果 | 查看日志中的 `RC=`，确认脚本末尾 `exit $rc` |
| `SIGILL` 或退出码 132 | 改用 `cpuXeon6458`，避免不兼容的 `cpuHygon7380` |
| `No space left` | `df -h $HOME`，清理可再生缓存和旧 checkpoint |
| `CUDA driver mismatch` | 在作业中检查 `nvidia-smi`，必要时 `--exclude=<node>` |

## 9. 提交前检查

1. `#SBATCH` 全部位于第一条命令之前，日志使用 `%x_%j.out`。
2. 有 `--propagate=NONE`，并且脚本最后透传真实退出码。
3. `sinfo` 已确认分区、节点状态、GPU 和 CPU 资源；时限接近同类作业的实际耗时。
4. 训练、推理、转码和大规模扫描都通过 `sbatch`/`srun` 在计算节点执行。
5. 提交后查看 `squeue`、日志和 `sacct`；删除操作先 dry-run 并确认没有相关作业运行。

官方文档入口：<https://hpc.scut.edu.cn/docs/guide.html>。

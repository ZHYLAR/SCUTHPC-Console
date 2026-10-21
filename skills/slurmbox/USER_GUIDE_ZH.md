# SlurmBox 中文使用文档

SlurmBox 是一个面向 Slurm 集群的命令行控制工具。它先申请一个较长时间的
CPU/GPU allocation，再把每条用户命令作为独立的 `srun` step 运行。

这样可以在同一份资源上连续执行编译、测试、训练和调试命令；某条命令失败时，
只会结束该命令对应的 step，不会自动取消整个 allocation。

## 1. 工作方式

```text
本地 SlurmBox
    |
    +-- SSH（可选）--> 集群登录节点
                         |
                         +-- sbatch  提交 keep-alive allocation
                         +-- srun    执行独立用户 step
```

SlurmBox 本身通常安装在本地电脑或集群登录节点上。集群不需要安装 SlurmBox
Python 包，但必须提供 Slurm 命令和你的项目运行环境。

长期 allocation 中运行的是一个只负责保持资源占用并输出心跳的脚本，不会执行
用户代码。用户代码通过以下形式进入 allocation：

```bash
srun --jobid=<job-id> --overlap ...
```

## 2. 前置条件

本地需要：

- Python 3.11 或更高版本；
- `setuptools >= 68`（安装项目时使用）；
- 远程运行时，需要可用的 SSH alias。

集群侧需要：

- `sbatch`、`srun`、`squeue`、`sacct`、`scancel`；
- `bash`、`mkdir`、`tee`、`tail` 等基础命令；
- 集群上可访问的项目代码、数据和 Python/CUDA 环境。

SlurmBox 不负责上传代码，也不负责安装 PyTorch、CUDA 或其他训练依赖。

## 3. 安装

在 SlurmBox 项目目录执行：

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

如果只运行工具而不运行测试，可以使用：

```bash
python -m pip install -e .
```

安装后可以检查命令是否可用：

```bash
slurmbox --help
```

## 4. 配置

复制示例配置：

```bash
cp slurmbox.toml.example slurmbox.toml
```

典型的远程配置如下：

```toml
[cluster]
ssh_alias = "scut-hpc"
remote_workdir = "~/SlurmBox"

[allocation]
job_name = "SlurmBox"
partition = "gpuA800"
gres = "gpu:1"
cpus_per_task = 8
time = "08:00:00"
mem = ""
```

字段说明：

- `ssh_alias`：SSH 配置中的主机别名。设为空字符串时，直接在当前机器执行 Slurm 命令；
- `remote_workdir`：远程命令的工作目录，不是代码同步配置；
- `partition`：Slurm 分区；
- `gres`：资源请求，例如 `gpu:1`。不需要 GPU 时设为空字符串；
- `cpus_per_task`：每个任务申请的 CPU 数量；
- `time`：allocation 的最长运行时间；
- `mem`：内存请求，例如 `32G`，留空表示不额外指定。

资源名称和额度必须以目标集群的实际配置为准。

## 5. 准备集群上的代码

SlurmBox 不会自动同步本地代码。先把项目放到 `remote_workdir` 对应的集群目录，
例如：

```bash
rsync -av ./project/ scut-hpc:~/SlurmBox/project/
```

如果代码通过共享文件系统已经可见，则不需要重复上传。训练数据、模型缓存和输出目录
也应放在集群可访问的位置。

## 6. 基本使用流程

### 6.1 检查配置

```bash
slurmbox doctor --dry-run
```

该命令只解析配置并打印 keep-alive 脚本，不提交作业，也不修改集群状态。

### 6.2 启动 allocation

```bash
slurmbox start
```

命令输出 Slurm job id。项目会在本地创建 `.slurmbox/state.json`，保存 job id、提交时
使用的集群配置和资源配置。它是本地控制状态，不是 Slurm 状态的权威来源。

### 6.3 查看状态

```bash
slurmbox status
```

作业仍在活动队列时使用 `squeue` 查询；作业离开活动队列后，SlurmBox 会尝试使用
`sacct` 查询历史记录。

### 6.4 执行命令

```bash
slurmbox exec -- python -m compileall src
slurmbox exec -- python project/train.py --epochs 1
```

命令会作为一个独立 step 执行，并把输出追加到远程工作目录下的
`steps/<step-job-id>.<step-id>.log`。

如果命令返回非零退出码，SlurmBox 会原样返回该退出码，但不会修改 allocation 状态。

需要 shell 展开、管道或重定向时，把完整命令交给远程 `bash -lc`：

```bash
slurmbox exec -- bash -lc 'python train.py --epochs 1 | tee train.log'
```

### 6.5 进入交互式 shell

```bash
slurmbox shell
```

退出 shell 只会结束当前交互式 step，不会自动取消 allocation。

### 6.6 查看 keep-alive 日志

```bash
slurmbox logs
slurmbox logs --lines 100
```

日志文件名为远程工作目录中的 `slurmbox-<job-id>.out`。

### 6.7 停止 allocation

```bash
slurmbox stop
```

只有 `stop` 会主动调用 `scancel` 取消记录的 allocation。取消后，本地状态会标记为
`stopped`。

## 7. 接管已有作业

如果本地 `.slurmbox/state.json` 丢失，但集群上仍有属于当前账号的活动作业，可以先
确认 job id，再执行：

```bash
slurmbox attach <job-id>
```

`attach` 会先通过 `squeue` 验证作业仍处于活动状态，不会凭空记录一个不存在的 job id。

## 8. 失败边界和恢复

| 操作 | 失败时的行为 |
| --- | --- |
| `start` | 不提交本地状态；已有状态不会被覆盖 |
| `exec` | 返回该 step 的退出码；allocation 和状态文件保持不变 |
| `shell` | 只结束当前交互式 step |
| `status` | 报告 Slurm/SSH 错误；不修改状态 |
| `stop` | 成功取消作业后把本地状态标记为 stopped |

如果本地终端断开，远程 allocation 通常仍会继续运行到时间限制。重新连接后可以运行
`status`；如果状态文件丢失，先用 `squeue` 找到自己的作业，再使用 `attach`。

## 9. 常见问题

### 没有记录 allocation

如果看到 `no SlurmBox allocation is recorded`，先运行：

```bash
slurmbox start
```

或者确认当前目录下是否存在正确的 `.slurmbox/state.json`。

### 代码找不到

确认代码已经上传到集群，并检查 `remote_workdir`：

```bash
slurmbox doctor --dry-run
slurmbox exec -- pwd
slurmbox exec -- ls
```

### GPU 不可见

检查 `partition`、`gres` 和账号配额是否正确，并查看：

```bash
slurmbox status
slurmbox exec -- bash -lc 'hostname; echo "$CUDA_VISIBLE_DEVICES"; nvidia-smi'
```

### 作业已经结束

`status` 会尝试显示 `sacct` 历史。查看具体退出原因可以在集群上执行：

```bash
scontrol show job <job-id>
sacct -X -j <job-id> --format=JobID,State,Elapsed,ExitCode
```

## 10. 使用边界

- 不要在登录节点直接运行重型训练或大规模数据处理；通过 `slurmbox exec` 运行；
- 不要把 SSH 密钥、OAuth 信息或其他秘密写入配置和日志；
- 不要把 `.slurmbox/state.json` 放到远程项目目录；
- 不要假设本地目录会自动出现在集群上；
- allocation 到达 walltime、被管理员处理或执行 `stop` 后都会结束。


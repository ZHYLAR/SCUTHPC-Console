# SCUTHPC Console

本项目SCUTHPC Console 是华南理工大学科学计算平台的非官方本地状态控制台。它在你的计算机上通过 SSH 读取 [hpckapok1](https://hpckapok1.scut.edu.cn) 与 [hpckapok2](https://hpckapok2.scut.edu.cn) 的 Slurm 信息，在浏览器中呈现分区资源、在跑作业的显卡状态，以及近两日作业结果。方便实时跟踪和查阅实验进度。

服务只绑定本机。集群凭据留在 `~/.ssh`，不进入本仓库。

## 界面

![总览样例：两个集群的可申请资源与空闲 GPU 节点](docs/screenshots/overview.png)

![在跑作业样例：卡住提示、显卡利用率与日志进度](docs/screenshots/jobs.png)

![作业记录样例：近两日作业及失败日志](docs/screenshots/history.png)

四个分页约每 20 秒刷新一次。

- **总览**给出公共分区可申请的 GPU、空闲 CPU 和在线节点，并列出当前仍有空闲 GPU 的公共节点。名称以 `emic`、`gznet`、`ex`、`telecom` 开头的分区视为专属资源，不计入可申请数量。
- **集群资源**按分区给出在线节点、GPU 与 CPU 的空闲情况。
- **在跑作业**按卡显示利用率、显存、温度和功耗。日志末尾若有步数与 `it/s` 或 `s/it`，会给出实验进度和剩余时间。连续三次采样（约一分钟）所有卡的利用率都低于 5% 时，标为可能卡住。
- **作业记录**列出近两日作业。失败、超时或内存不足时，附上该作业日志的末尾。

右上角的作业历史和文件存储指向 [SCUT HPC 门户](https://hpckapok.scut.edu.cn)。文件链接中的用户名取自集群登录身份。允许浏览器通知后，页面保持打开时，作业完成或失败会收到一条系统通知。

## 相关链接

- [科学计算平台门户](https://hpckapok.scut.edu.cn)
- [用户手册](https://hpc.scut.edu.cn/docs/guide.html)
- [SCUTHPC Skill](https://github.com/IRAgentLab/SCUTHPCSkill/)：登录、分区、作业提交与排障。本控制台不重复这些说明。

## 运行

需要本机已能 SSH 登录两个集群。主机别名、密钥和账号的配法见 [SCUTHPC Skill](https://github.com/IRAgentLab/SCUTHPCSkill/)。

```bash
conda create -n hpc-monitor python=3.11 -y
conda activate hpc-monitor
pip install -r requirements.txt
python serve.py
```

本机打开 [http://127.0.0.1:8765](http://127.0.0.1:8765) 。Tailscale 在运行时，同一网络里的设备用 `tailscale ip -4` 的地址访问同一端口。

默认使用 SSH Host `scut-hpc1`（hpckapok1）和 `scut-hpc`（hpckapok2）。别名不同，或门户地址有变化时，复制 `config.example.json` 为 `config.json` 再改。

Windows cmd 进入其他盘符的目录时使用 `cd /d`。

## 采集范围

每次刷新对每个集群建立一次 SSH 会话，在登录节点读取节点、队列和近两日记账信息。正在运行的 GPU 作业会用 `srun --overlap` 采样一次 `nvidia-smi`。实验进度只来自标准输出末尾，程序若未打印步数，界面不会估计百分比。家目录按每人 1 TB 免费配额显示已用量。用量用家目录统计，约每 30 分钟更新一次，不是整个文件系统的占用。
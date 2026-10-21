#!/bin/bash
# 通用 CPU 作业包装器：把任意 shell 命令放进 Slurm 跑，透传退出码。
# 用法：
#   sbatch --parsable --export=ALL,CMD='python tools/convert.py --in a --out b' cpu_run.sbatch
#   sbatch -J du_home --export=ALL,CMD='du -sh $HOME/* | sort -h > $HOME/du_$(date +%F).txt' cpu_run.sbatch
# CMD 里不能含逗号（--export 用逗号分隔）；多值参数用 `env FOO=.. sbatch --export=ALL cpu_run.sbatch`
# 或把参数写进文件让 CMD 去读。
#
# 所有 #SBATCH 必须紧跟 shebang，在第一条非注释命令之前；中间插任何命令都会让后面的 #SBATCH 失效。
#SBATCH -J cpu_run
#SBATCH -p cpuXeon6458
#SBATCH -N 1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=12:00:00
#SBATCH --propagate=NONE
#SBATCH -o %x_%j.out
#SBATCH -e %x_%j.out

set -u
: "${CMD:?need --export=ALL,CMD='<command>'}"
echo "[$(date '+%F %T')] job=$SLURM_JOB_ID node=$(hostname) cpus=${SLURM_CPUS_PER_TASK:-?}"
echo "[cmd] $CMD"

# 计算节点无外网，走代理；HF 走镜像
export http_proxy=http://login5:3128 https_proxy=http://login5:3128
export no_proxy=localhost,127.0.0.1
export HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1

# 线程预算按申请核数派生，不按节点 64 核开
NT=${SLURM_CPUS_PER_TASK:-${SLURM_CPUS_ON_NODE:-8}}
OMP=$(( NT / 2 > 4 ? NT / 2 : 4 ))
export OMP_NUM_THREADS=$OMP MKL_NUM_THREADS=$OMP OPENBLAS_NUM_THREADS=$OMP NUMEXPR_NUM_THREADS=$OMP

# 项目环境：把 conda/venv 的激活统一写在项目的 env.sh 里
[ -f "$HOME/<project>/env.sh" ] && source "$HOME/<project>/env.sh"
cd "${SLURM_SUBMIT_DIR:-$HOME/<project>}"

eval "$CMD"
RC=$?
echo "[$(date '+%F %T')] RC=$RC"
exit $RC   # 必须透传，否则 --dependency=afterok 拦不住失败

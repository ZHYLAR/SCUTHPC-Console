#!/bin/bash
# GPU 作业模板（gpuA800，hpckapok2）。按需改 -J / 卡数 / 核数 / 时限 / 最后的命令。
# 提交：sbatch --parsable gpu_job.sbatch
# 所有 #SBATCH 必须紧跟 shebang，在第一条非注释命令之前。
#SBATCH -J gpu_job
#SBATCH -p gpuA800
#SBATCH -N 1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1              # 只申请需要的卡：推理/评测 1 卡，微调 2–4 卡
#SBATCH --cpus-per-task=12        # 1 卡 12 核、2 卡 24 核，按卡数线性
#SBATCH --mem=96G
#SBATCH --time=24:00:00
#SBATCH --propagate=NONE          # 不把登录节点 ulimit -u 1000 带进作业，否则多线程程序 pthread_create 失败
##SBATCH --exclude=gpu01,gpu07   # 驱动不匹配/坏掉的节点：去掉一个 # 启用
#SBATCH -o %x_%j.out
#SBATCH -e %x_%j.out

set -u
LOG="${SLURM_SUBMIT_DIR}/${SLURM_JOB_NAME}_${SLURM_JOB_ID}.out"   # 与 -o %x_%j.out 对应；改了 -o 这里也要改
echo "[$(date '+%F %T')] job=$SLURM_JOB_ID node=$(hostname) gpus=${CUDA_VISIBLE_DEVICES:-?} cpus=${SLURM_CPUS_PER_TASK:-?}"

# ---------- 网络：计算节点无外网直连 ----------
export http_proxy=http://login5:3128 https_proxy=http://login5:3128
export no_proxy=localhost,127.0.0.1
export HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1
export HF_HOME="$HOME/.cache/huggingface"

# ---------- 驱动检查：与容器/venv 里的 CUDA 库不一致就退出 75，由提交方加 --exclude 重提 ----------
EXPECT_DRIVER="${EXPECT_DRIVER:-}"     # 例如 545.23.06；留空则跳过
DRV=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
echo "[driver] $DRV"
if [ -n "$EXPECT_DRIVER" ] && [ "$DRV" != "$EXPECT_DRIVER" ]; then
  echo "[driver] mismatch on $(hostname): got $DRV want $EXPECT_DRIVER -> exit 75"
  exit 75
fi

# ---------- 线程预算：按申请核数派生 ----------
NT=${SLURM_CPUS_PER_TASK:-${SLURM_CPUS_ON_NODE:-8}}
OMP=$(( NT / 2 > 4 ? NT / 2 : 4 ))
export OMP_NUM_THREADS=$OMP MKL_NUM_THREADS=$OMP OPENBLAS_NUM_THREADS=$OMP NUMEXPR_NUM_THREADS=$OMP
KIT_THREADS=$(( NT * 2 / 3 > 8 ? NT * 2 / 3 : 8 ))   # Isaac Sim/Kit 的 carb.tasking 线程数，低于 8 会卡死
export KIT_THREADS

# ---------- 环境 ----------
[ -f "$HOME/<project>/env.sh" ] && source "$HOME/<project>/env.sh"
cd "${SLURM_SUBMIT_DIR:-$HOME/<project>}"

# ---------- 看门狗：日志 15 分钟无更新就自杀，防止挂死烧卡 ----------
(
  while sleep 120; do
    if [ -f "$LOG" ]; then
      AGE=$(( $(date +%s) - $(stat -c %Y "$LOG") ))
      if [ "$AGE" -gt 900 ]; then
        echo "[watchdog] log idle ${AGE}s -> scancel $SLURM_JOB_ID"
        scancel "$SLURM_JOB_ID"
      fi
    fi
    # 线程总数抽样：接近 1000 说明 --propagate=NONE 没生效或线程预算开太大
    echo "[threads $(date '+%T')] $(ps -u "$USER" -o nlwp= | awk '{s+=$1} END{print s+0}')"
  done
) &
WATCHDOG=$!

# ---------- 主任务（示例：先用 timeout 做预热/连接类步骤，再跑正式命令） ----------
# timeout 600 python -c "import torch; print(torch.cuda.is_available())" || { echo "warmup failed"; exit 1; }
python train.py --config configs/example.yaml 2>&1
RC=$?

kill "$WATCHDOG" 2>/dev/null
echo "[$(date '+%F %T')] RC=$RC"
exit $RC   # 崩溃时 Slurm 也可能显示 COMPLETED，看日志里的 RC 才准

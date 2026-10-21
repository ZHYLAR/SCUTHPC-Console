# SlurmBox architecture

## Process model

```text
slurmbox start
    └── sbatch sandbox.sbatch  ──> allocation job (sleep/heartbeat loop)

slurmbox exec ... / shell
    └── srun --jobid JOB --overlap ...  ──> independent job step
```

The batch script deliberately does not evaluate a user command. Its only job
is to hold the allocation and emit a low-rate heartbeat. This makes the
allocation's lifetime independent from user code. Slurm may still terminate
the allocation at its time limit, by administrative action, or when the user
runs `slurmbox stop`.

## Control-plane state

`.slurmbox/state.json` is local state and is written atomically. It contains
the job id, transport settings used for submission, and the requested profile.
It is not a source of truth for Slurm status: `status` always queries Slurm.
If state is stale, the operator can inspect the job id with `squeue`/`scontrol`
and remove the local state only after checking it.

## Transport

The transport has two modes:

- Empty `cluster.ssh_alias`: invoke `sbatch`, `srun`, `squeue`, and `scancel`
  locally.
- Non-empty alias: invoke the same commands through `ssh <alias>`, optionally
  after `cd` to `cluster.remote_workdir`.

The first implementation uses argument lists and `shlex.join` at the SSH
boundary. Commands containing pipes or shell expansion should be passed as
`bash -lc '...'` inside the remote step, rather than being interpreted by the
local shell.

## Failure boundaries

- `start` failure: no state is committed.
- `exec` failure: return the step exit code; keep state unchanged.
- `status` failure: report the transport/Slurm error; do not mutate state.
- `stop` success: cancel the recorded job and mark local state stopped.
- Batch script failure: considered a bug; the script must not use `set -e` for
  user-controlled operations because it has no user-controlled operations.


# SlurmBox operations

## Configure

Copy `slurmbox.toml.example` to `slurmbox.toml` and select a partition and
resource profile. The example uses the existing `scut-hpc` SSH alias and an
eight-hour `gpuA800` allocation. Change `remote_workdir` to the directory
where the project is available on the cluster.

Run `slurmbox doctor --dry-run` first. It validates local configuration and
prints the exact remote command shapes without contacting Slurm.

## Start and use a sandbox

```bash
slurmbox start
slurmbox status
slurmbox exec -- python -m compileall src
slurmbox exec -- bash -lc 'python train.py --epochs 1'
slurmbox logs
slurmbox shell
```

Use `shell` for exploratory work. Use `exec` for commands that should be
logged by the terminal or called by another tool. A non-zero result from either
command is a result for that step only.

## Inspect without changing anything

The project does not submit a probe job for health checks. Use the read-only
`doctor` command and, when needed, the existing `scut-hpc` procedures:

```bash
ssh scut-hpc 'squeue -u "$USER" -o "%i %T %j %M %R"'
ssh scut-hpc 'scontrol show job <job-id>'
```

Do not run heavy work on a login node. Start the sandbox first and run work via
`slurmbox exec`.

## Stop and recover

`slurmbox stop` calls `scancel` for the recorded job. If the terminal or local
machine disappears, reconnect and run `status`; Slurm continues the job until
its time limit. If local state is lost, use `squeue` to identify the active
job and record it with `slurmbox attach <job-id>` after checking that it is
yours. `status` falls back to `sacct` after the job leaves the active queue.


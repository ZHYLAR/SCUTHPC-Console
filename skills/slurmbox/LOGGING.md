# Console task names, logs, and progress

Use this contract when running tasks in an existing SlurmBox allocation that
SCUTHPC Console monitors. It does not authorize starting or cancelling jobs.

## Launch a named foreground task

```bash
slurmbox exec --name "eval model-a seed-0" -- python -u eval.py --seed 0
```

Put `--name` and `--kind` before `--`; everything after it belongs to the user
command. Use a short task/experiment name, up to 80 characters, without secrets
or line breaks. Name the workload, rather than `bash`, `env`, or `timeout`.
Shell wrappers are fine when the task has an explicit name:

```bash
slurmbox exec --name "eval model-a" -- bash -lc 'source env.sh && python -u eval.py'
slurmbox exec --name "check environment" --kind utility -- python -c 'import platform; print(platform.python_version(), flush=True)'
```

The CLI sets a Slurm step name, writes `steps/<job-id>.<step-id>.meta.json`, and
tees both stdout and stderr to `steps/<job-id>.<step-id>.log` in the configured
remote work directory. The sidecar preserves the display name after the step
finishes, including when the log tail no longer contains startup messages.
It contains only `schema_version: 1`, `name`, and `kind` (`task` or `utility`),
not the command arguments. Keep the command in the foreground; a detached
child can outlive the step's accounting and log capture.

`--kind utility` is for environment checks and other supporting diagnostics.
Finished utility steps are collapsed on the dashboard. Running steps remain
visible. Older unnamed `bash`/`env` steps with no saved logs are also collapsed,
including failed ones with a failure count; they are not deleted.

Check `slurmbox exec --help` before relying on these options in an older
installation. Upgrade from this repository's bundled `skills/slurmbox` package
when necessary. Old tasks cannot retroactively gain logs that were never saved.

## Flush output and preserve failures

- Python: use `python -u`, `print(..., flush=True)`, or flush the logging handler.
  SlurmBox defaults `PYTHONUNBUFFERED` to `1` when it is not already configured.
- Print startup/configuration summaries, current phase, meaningful progress,
  and the final result to stdout/stderr. Do not print credentials or a complete
  environment dump.
- Do not redirect all output exclusively to another file. If the application
  must keep its own log, also tee it to stdout and preserve the pipeline status:

  ```bash
  slurmbox exec --name "train model-a" -- bash -lc 'set -o pipefail; mkdir -p logs && python -u train.py 2>&1 | tee logs/train.log'
  ```

- Let failures return a nonzero exit code. Do not use `|| true` or finish a failed
  script with a successful `echo`. A failed step must leave the allocation alive.
- `slurmbox logs` reads the allocation's keep-alive log, not task output. The
  dashboard reads the per-step files above; check that directory when debugging.
- `slurmbox shell` opens an interactive terminal and does not automatically
  capture commands typed inside it. Launch monitored workloads with `exec`.

## Report actual progress

For simple programs, newline-delimited output such as `step 10/100` or
`epoch 2/20` is sufficient. Existing tqdm output is also recognized. Prefer
updates every 10–30 seconds or on a meaningful milestone, including a final
update, rather than printing one line per sample.

For explicit labels and an ETA, print a short single-line record:

```python
import json

print("SCUTHPC_PROGRESS " + json.dumps({
    "current": 25,
    "total": 100,
    "label": "validation",
    "eta_seconds": 90,
}, ensure_ascii=False), flush=True)
```

`current` and `total` must be finite numbers with `0 <= current <= total` and
`total > 0`. `label` and nonnegative `eta_seconds` are optional. Keep the entire
record under 240 characters because the monitor reads bounded log tails. Use
the workload's real units (batches, episodes, files, etc.); do not fabricate a
percentage or use the allocation's remaining time as experiment progress.
When the total is unknown, print phase/status messages instead. The dashboard
shows the latest recognizable progress in the current log tail, not a full log
archive; avoid flooding it with unrelated output between progress updates.

## If raw srun is required

Prefer `slurmbox exec` for automatic naming, logging, and the sidecar. If an
existing integration must use `srun --jobid=... --overlap`, provide a meaningful
`--job-name`, run in the configured remote directory, and tee stdout/stderr
inside the step to `steps/${SLURM_JOB_ID}.${SLURM_STEP_ID}.log`. Do not move user
code into the keep-alive batch script. A terminal-only stream or an arbitrary
log path cannot be discovered automatically by this dashboard.

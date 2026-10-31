# SCUTHPC Console agent guide

This repository is the unified SCUTHPC 3-in-1 workspace:

- `scuthpc_console/` contains the monitor backend; `static/` is the browser UI.
- `skills/scut-hpc/` is the bundled SCUTHPC HPC skill. Read its `SKILL.md` before any real cluster operation.
- `skills/slurmbox/` is the bundled SlurmBox skill and installable CLI. Read its `SKILL.md` before changing allocation or step behavior.

The monitor and SlurmBox share the `.slurmbox/state.json` control-plane format. The dashboard may submit an allocation, but never puts user code in the keep-alive batch job; user code must run as an independent `srun --jobid ... --overlap` step.

For console-visible tasks in an existing box, follow `skills/slurmbox/LOGGING.md`: use `slurmbox exec --name "task/experiment" -- python -u ...`, preserve stdout/stderr, and flush meaningful progress. Use `--kind utility` only for supporting diagnostics. Task logs live in `steps/<job>.<step>.log`; `slurmbox logs` shows only the keep-alive log.

For local changes, do not submit or cancel cluster jobs unless the user explicitly requests it. Run `python -m slurmbox --help` from the bundled package, `python -m py_compile app.py serve.py scuthpc_console/*.py`, and `git diff --check`.

Keep SSH aliases, keys, tokens, and command output containing secrets out of chat and source files.

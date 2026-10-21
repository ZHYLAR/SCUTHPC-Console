---
name: slurmbox
description: Develop and operate the SlurmBox persistent allocation CLI, where user commands run as independent Slurm steps inside a long-lived sandbox.
---

# SlurmBox project skill

Use this skill for changes inside `skills/slurmbox/` and for requests to
operate the bundled sandbox CLI.

## Required invariant

The keep-alive batch allocation must not execute user code. User code runs via
an independent `srun --jobid=<job> --overlap` step. A failed step returns its
own status and must leave the allocation and `.slurmbox/state.json` intact.

## Before editing

Read the repository `AGENTS.md` and the relevant bundled docs. For real
cluster work, also read `skills/scut-hpc/SKILL.md`. Treat SSH aliases and credentials as
preconfigured; never ask for or print secrets.

## Development rules

- Keep the implementation standard-library first and testable with fake
  subprocess runners.
- Keep configuration, job submission, status queries, and step execution in
  separate modules so a faster follow-up agent can extend them safely.
- Use atomic state writes and preserve state when a command fails.
- Use `attach` only for an active job verified with `squeue`; use `logs` for
  the keep-alive output and `status` for the active queue or `sacct` history.
- Do not submit or cancel jobs during local development unless the user
  explicitly requests that operation.
- Do not add synchronization implicitly to `start`; remote workspace access
  must be explicit and documented.

## Verification

Run `python -m slurmbox --help` and `git diff --check`. Downstream development
checkouts may add their own test suite separately.
For a requested cluster test, prefer read-only `squeue`, `sinfo`, and
`scontrol` checks before any allocation is submitted.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the process model and
[OPERATIONS.md](OPERATIONS.md) for operator commands.


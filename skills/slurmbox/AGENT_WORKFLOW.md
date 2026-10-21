# Follow-up agent workflow

The next agent should begin by reading `AGENTS.md`, this file, and
`skills/slurmbox/SKILL.md`. The bundled `skills/scut-hpc/SKILL.md` is required for
real cluster operations, but ordinary implementation should remain local.

Use Python 3.11+ for the project. Create and activate a project virtual
environment before running the CLI or development checks.

## Recommended order

1. Run the local tests and inspect the current diff.
2. Complete the MVP code without changing the allocation invariant.
3. Add tests for every new command or transport branch using fake subprocess
   runners.
4. Run `python -m slurmbox --help`, the test suite, and `git diff --check`.
5. Only after the user explicitly asks for an HPC test, run read-only
   connectivity checks, then submit a short, clearly identified allocation.

## Handoff facts

- The default time limit is eight hours by design, but no job has been
  submitted by this planning task.
- Existing SSH aliases are `scut-hpc` and `scut-hpc1`; credentials are already
  configured outside this repository.
- Remote execution assumes the project/workspace is already visible at the
  configured `remote_workdir`. Synchronization is a later phase.
- Keep-alive output must be low-rate and should include the Slurm job id.


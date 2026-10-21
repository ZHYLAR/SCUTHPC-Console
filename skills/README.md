# Bundled skills

`scut-hpc` and `slurmbox` are shipped with SCUTHPC Console so an agent can use one workspace and one set of instructions for monitoring, cluster operations, and persistent sandbox execution.

Install the CLI locally with:

```bash
python3 -m pip install -e skills/slurmbox
```

Load `skills/scut-hpc/SKILL.md` for cluster rules and `skills/slurmbox/SKILL.md` for sandbox invariants before changing or operating either component.

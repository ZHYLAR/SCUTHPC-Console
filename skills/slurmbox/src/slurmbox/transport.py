from __future__ import annotations

from dataclasses import dataclass
import shlex
import subprocess
from typing import Sequence

from .config import ClusterConfig


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class Transport:
    def __init__(self, config: ClusterConfig):
        self.config = config

    def _remote_argv(self, argv: Sequence[str], *, tty: bool = False) -> list[str]:
        command = shlex.join([str(item) for item in argv])
        if self.config.remote_workdir:
            path = self.config.remote_workdir
            if path == "~":
                cd = 'cd "$HOME"'
            elif path.startswith("~/"):
                cd = f'cd "$HOME"/{shlex.quote(path[2:])}'
            else:
                cd = f"cd {shlex.quote(path)}"
            command = f"{cd} && {command}"
        return ["ssh", *(["-tt"] if tty else []), self.config.ssh_alias, "sh", "-lc", command]

    def run(self, argv: Sequence[str], *, input_text: str | None = None) -> CommandResult:
        command = self._remote_argv(argv) if self.config.ssh_alias else [str(item) for item in argv]
        completed = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)

    def interactive(self, argv: Sequence[str]) -> int:
        tty = "--pty" in argv
        command = self._remote_argv(argv, tty=tty) if self.config.ssh_alias else [str(item) for item in argv]
        return subprocess.call(command)


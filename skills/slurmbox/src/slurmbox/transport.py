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
        # Pass stdin as bytes so Windows does not translate the generated
        # Slurm batch script's LF line endings to CRLF.  Slurm rejects CRLF
        # scripts with "Batch script contains DOS line breaks".
        input_bytes = None
        if input_text is not None:
            input_bytes = input_text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        completed = subprocess.run(
            command,
            input=input_bytes,
            capture_output=True,
            check=False,
        )
        def decode(value: bytes | str | None) -> str:
            if isinstance(value, bytes):
                return value.decode("utf-8", "replace")
            return value or ""

        return CommandResult(completed.returncode, decode(completed.stdout), decode(completed.stderr))

    def interactive(self, argv: Sequence[str]) -> int:
        tty = "--pty" in argv
        command = self._remote_argv(argv, tty=tty) if self.config.ssh_alias else [str(item) for item in argv]
        return subprocess.call(command)


from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import os
import tempfile
from typing import Any


def state_path(root: Path) -> Path:
    return root / ".slurmbox" / "state.json"


def read_state(root: Path) -> dict[str, Any] | None:
    path = state_path(root)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_state(root: Path, value: dict[str, Any]) -> None:
    directory = state_path(root).parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="state.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, state_path(root))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


"""Local secrets: one JSON file per service, readable by you only (mode 600)."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_DIR = Path.home() / ".config" / "skoolathome"


def _resolve_dir(directory: Path | None) -> Path:
    if directory is not None:
        return directory
    return Path(os.environ.get("SKOOLATHOME_SECRETS", DEFAULT_DIR))


def read(name: str, directory: Path | None = None) -> dict | None:
    path = _resolve_dir(directory) / f"{name}.json"
    if not path.exists():
        return None
    if path.stat().st_mode & 0o077:
        raise PermissionError(f"{path} est lisible par d'autres : chmod 600 {path}")
    return json.loads(path.read_text())


def write(name: str, content: dict, directory: Path | None = None) -> None:
    root = _resolve_dir(directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / f"{name}.json"
    temporary = path.with_suffix(".tmp")
    temporary.unlink(missing_ok=True)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(content, file)
    os.replace(temporary, path)

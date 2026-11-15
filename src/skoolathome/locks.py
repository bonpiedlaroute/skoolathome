"""One user of the local model at a time: the evening run, or an explanation (T29).

Ollama loads one model at a time (OLLAMA_MAX_LOADED_MODELS=1): two processes asking for two
models would load and unload them in turn, and both would be slow. The evening waits for an
explanation in progress (a few minutes); an explanation never waits for the evening (an hour):
it is made at the next poll after it.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def _path() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "skoolathome-model.lock"


@contextmanager
def model(wait: bool = True) -> Iterator[bool]:
    """Yields True with the lock held; False when `wait` is off and someone else holds it."""
    with open(_path(), "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

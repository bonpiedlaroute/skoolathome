"""One user of the local model at a time: the evening run, or an explanation (T29). And one
École Directe collection at a time, apart from the model.

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


def _path(name: str = "model") -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / f"skoolathome-{name}.lock"


@contextmanager
def model(wait: bool = True) -> Iterator[bool]:
    """Yields True with the lock held; False when `wait` is off and someone else holds it."""
    with _held("model", wait) as held:
        yield held


@contextmanager
def collection(wait: bool = True) -> Iterator[bool]:
    """The École Directe collection, on its own timer: never behind the model. The evening only
    waits for a collection in progress, so that it works on tonight's data."""
    with _held("collect", wait) as held:
        yield held


@contextmanager
def _held(name: str, wait: bool) -> Iterator[bool]:
    with open(_path(name), "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

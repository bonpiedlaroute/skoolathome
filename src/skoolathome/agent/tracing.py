"""Langfuse: traces, scores, release. Everything works without it: tracing is then off.

One trace per sheet, with a trace id derived from the run date and the task. The id is
stored with the sheet (T15): feedback received later is attached to its trace (T22).
"""

from __future__ import annotations

import functools
import logging
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from langfuse import Langfuse, propagate_attributes
from langfuse.langchain import CallbackHandler

from skoolathome import secrets

SECRET_NAME = "langfuse"  # ~/.config/skoolathome/langfuse.json, written by deploy/langfuse/langfuse.sh init
REPO = Path(__file__).resolve().parents[3]

log = logging.getLogger(__name__)


@functools.cache
def release() -> str:
    """The code version on every trace: the git commit, marked -dirty with uncommitted changes."""
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()

    try:
        commit = git("rev-parse", "--short=12", "HEAD")
        dirty = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return commit + ("-dirty" if dirty else "")


@functools.cache
def _conf() -> dict | None:
    return secrets.read(SECRET_NAME)


@functools.cache
def client() -> Langfuse | None:
    conf = _conf()
    if not conf:
        return None
    return Langfuse(
        public_key=conf["public_key"],
        secret_key=conf["secret_key"],
        host=conf["host"],
        release=release(),
    )


def trace_id(*parts: str) -> str:
    """Same parts, same id: a run resumed after a crash writes into the same trace."""
    return Langfuse.create_trace_id(seed="/".join(parts))


def callbacks() -> list:
    """LangChain callbacks for a model call: the call becomes a generation in the current trace."""
    return [CallbackHandler(public_key=_conf()["public_key"])] if client() is not None else []


@contextmanager
def observe(
    name: str,
    trace: str,
    *,
    trace_name: str | None = None,
    metadata: dict[str, str] | None = None,
    tags: list[str] | None = None,
    prompt=None,
    input=None,
) -> Iterator[object | None]:
    """A span named after a graph node, in the trace `trace`. Yields the span, or None without Langfuse."""
    lf = client()
    if lf is None:
        yield None
        return
    with lf.start_as_current_observation(trace_context={"trace_id": trace}, name=name, input=input) as span:
        with propagate_attributes(trace_name=trace_name, metadata=metadata, tags=tags, prompt=prompt):
            yield span


def score(trace: str, name: str, value: float | bool, comment: str | None = None, data_type: str | None = None) -> None:
    lf = client()
    if lf is None:
        return
    if isinstance(value, bool):
        value, data_type = float(value), "BOOLEAN"
    lf.create_score(trace_id=trace, name=name, value=value, comment=comment, data_type=data_type or "NUMERIC")


def trace_url(trace: str) -> str | None:
    lf = client()
    return lf.get_trace_url(trace_id=trace) if lf is not None else None


def flush() -> None:
    lf = client()
    if lf is not None:
        lf.flush()

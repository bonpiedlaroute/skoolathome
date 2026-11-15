"""LangGraph checkpoints in Postgres: the evening graph and the graph of instructions both resume
where they stopped (a laptop gone to sleep, a parent who answers two days later)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo


@contextmanager
def checkpointer(database_url: str) -> Iterator[PostgresSaver]:
    """LangGraph's tables live in the `langgraph` schema of the application database."""
    conninfo = make_conninfo(database_url, options="-c search_path=langgraph")
    with PostgresSaver.from_conn_string(conninfo) as saver:
        saver.setup()
        yield saver

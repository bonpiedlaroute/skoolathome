"""Sheets asked for by the parent, on the laptop: created, picked up, answered."""

from __future__ import annotations

from secrets import token_hex

import psycopg

from skoolathome.schema.request import NewRequest, RequestUpdate

_COLUMNS = "id, subject_code, kind, focus, items, origin, created_at, status, sheet_id, reason"


def create(conn: psycopg.Connection, request: NewRequest, *, student_id: str | None, origin: str = "cli",
           request_id: str | None = None, created_at=None) -> str:
    request_id = request_id or f"{origin}-{token_hex(6)}"
    conn.execute(
        "INSERT INTO agent.requests (id, student_id, subject_code, kind, focus, items, origin, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, coalesce(%s, now())) ON CONFLICT (id) DO NOTHING",
        (request_id, student_id, request.subject, request.kind, request.focus, request.items, origin, created_at),
    )
    return request_id


def pending(conn: psycopg.Connection) -> list[dict]:
    rows = conn.execute(f"SELECT {_COLUMNS} FROM agent.requests WHERE status = 'pending' ORDER BY created_at").fetchall()
    return [dict(zip(_COLUMNS.split(", "), row)) for row in rows]


def recent(conn: psycopg.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(f"SELECT {_COLUMNS} FROM agent.requests ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()
    return [dict(zip(_COLUMNS.split(", "), row)) for row in rows]


def answer(conn: psycopg.Connection, request_id: str, status: str, *, sheet_id: str | None = None,
           reason: str = "") -> None:
    conn.execute(
        "UPDATE agent.requests SET status = %s, sheet_id = %s, reason = %s, handled_at = now() WHERE id = %s",
        (status, sheet_id, reason[:600], request_id),
    )


def to_report(conn: psycopg.Connection) -> list[RequestUpdate]:
    """Requests made online and answered, whose outcome the PWA does not know yet."""
    rows = conn.execute(
        "SELECT id, status, sheet_id, reason FROM agent.requests WHERE origin = 'pwa' AND status <> 'pending' "
        "AND reported_at IS NULL ORDER BY handled_at"
    ).fetchall()
    return [RequestUpdate(id=r[0], status=r[1], sheet_id=r[2], reason=r[3][:600]) for r in rows]


def reported(conn: psycopg.Connection, ids: list[str]) -> None:
    conn.execute("UPDATE agent.requests SET reported_at = now() WHERE id = ANY(%s)", (ids,))


# ------------------------------------------------- made at once (the explain timer)

TRIES = 3  # then the request waits for the evening


def to_try(conn: psycopg.Connection, ids: list[str], tries: int = TRIES) -> list[str]:
    """The requests waiting online that the timer can try now: three times each at most, so that a
    request that always fails does not take the model every five minutes."""
    if not ids:
        return []
    done = dict(conn.execute("SELECT id, tries FROM agent.request_tries WHERE id = ANY(%s)", (ids,)).fetchall())
    return [request_id for request_id in ids if done.get(request_id, 0) < tries]


def tried(conn: psycopg.Connection, ids: list[str]) -> None:
    """Counted before the run: a run that stops half-way counts too."""
    for request_id in ids:
        conn.execute("INSERT INTO agent.request_tries (id, tries, last_at) VALUES (%s, 1, now()) "
                     "ON CONFLICT (id) DO UPDATE SET tries = agent.request_tries.tries + 1, last_at = now()",
                     (request_id,))


def still_pending(conn: psycopg.Connection, ids: list[str]) -> list[str]:
    """The requests of the run that are neither made nor refused: a request unknown here (not
    synced) counts as still waiting."""
    handled = {r[0] for r in conn.execute("SELECT id FROM agent.requests WHERE id = ANY(%s) AND status <> 'pending'",
                                          (ids,)).fetchall()}
    return [request_id for request_id in ids if request_id not in handled]


def failed(conn: psycopg.Connection, ids: list[str], error: str, tries: int = TRIES) -> list[str]:
    """The error of the run, kept with each request. Returns the ones that reached their last try
    and were not given up yet: the parent hears about them once."""
    given_up = []
    for request_id in ids:
        row = conn.execute("SELECT tries, gave_up_at FROM agent.request_tries WHERE id = %s", (request_id,)).fetchone()
        last = row is not None and row[0] >= tries and row[1] is None
        conn.execute("UPDATE agent.request_tries SET last_error = %s, "
                     "gave_up_at = CASE WHEN %s THEN now() ELSE gave_up_at END WHERE id = %s",
                     (error[:300], last, request_id))
        if last:
            given_up.append(request_id)
    return given_up

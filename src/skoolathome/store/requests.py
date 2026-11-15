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

"""Feedback from the tablet, copied to the laptop (T17): answers, reports, finished sheets.

Each row keeps its sheet and question, and the trace of the sheet when the agent made it: T22
sends them to Langfuse as scores, including the ones received before then.
"""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb


def cursor(conn: psycopg.Connection) -> int:
    row = conn.execute("SELECT since FROM feedback.cursor").fetchone()
    return row[0] if row else 0


def save(conn: psycopg.Connection, data: dict) -> dict[str, int]:
    """Store what GET /api/feedback returned, then move the cursor. Received twice, stored once."""
    counts = {"attempts": 0, "reports": 0, "finishes": 0}
    with conn.transaction():
        for a in data.get("attempts", []):
            counts["attempts"] += conn.execute(
                "INSERT INTO feedback.attempts (id, sheet_id, item_id, role, mode, response, correct, rating, "
                "hints_used, answered_at, created_at, trace_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "(SELECT trace_id FROM agent.sheets WHERE id = %s)) ON CONFLICT (id) DO NOTHING",
                (a["id"], a["sheet_id"], a["item_id"], a["role"], a["mode"],
                 None if a.get("response") is None else Jsonb(a["response"]), a["correct"], a.get("rating"),
                 a.get("hints_used", 0), a.get("answered_at"), a["created_at"], a["sheet_id"]),
            ).rowcount
        for r in data.get("reports", []):
            counts["reports"] += conn.execute(
                "INSERT INTO feedback.reports (id, sheet_id, item_id, role, reason, comment, created_at, trace_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, (SELECT trace_id FROM agent.sheets WHERE id = %s)) "
                "ON CONFLICT (id) DO NOTHING",
                (r["id"], r["sheet_id"], r.get("item_id"), r["role"], r["reason"], r.get("comment", ""),
                 r["created_at"], r["sheet_id"]),
            ).rowcount
        for f in data.get("finishes", []):
            counts["finishes"] += conn.execute(
                "INSERT INTO feedback.finishes (id, sheet_id, role, score, total, to_review, finished_at, trace_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, (SELECT trace_id FROM agent.sheets WHERE id = %s)) "
                "ON CONFLICT (id) DO NOTHING",
                (f["id"], f["sheet_id"], f["role"], f["score"], f["total"], Jsonb(f.get("to_review", [])),
                 f["finished_at"], f["sheet_id"]),
            ).rowcount
        conn.execute(
            "INSERT INTO feedback.cursor (id, since) VALUES (true, %s) "
            "ON CONFLICT (id) DO UPDATE SET since = EXCLUDED.since, updated_at = now()",
            (data["until"],),
        )
    return counts

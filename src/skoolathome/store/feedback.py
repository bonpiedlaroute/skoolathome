"""Feedback from the tablet, copied to the laptop (T17): answers, reports, finished sheets, and
the parent's requests (T20), instructions and replies (T21).

Each row keeps its sheet and question, and the trace of the sheet that produced it: agent.scores
sends them to Langfuse as scores (T22), including the ones received before then.
"""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb


# The trace sent back with the row (T22), or the one this laptop recorded with the sheet.
_TRACE = "coalesce(%s, (SELECT trace_id FROM agent.sheets WHERE id = %s))"


def cursor(conn: psycopg.Connection) -> int:
    row = conn.execute("SELECT since FROM feedback.cursor").fetchone()
    return row[0] if row else 0


def save(conn: psycopg.Connection, data: dict, student_id: str | None = None) -> dict[str, int]:
    """Store what GET /api/feedback returned, then move the cursor. Received twice, stored once.
    Sheets the parent asked for from the PWA become requests of this laptop."""
    counts = {"attempts": 0, "reports": 0, "finishes": 0, "requests": 0, "instructions": 0, "replies": 0,
              "annotations": 0, "explanations": 0}
    with conn.transaction():
        for e in data.get("explanations", []):  # « Ça m'a aidé ? » (T29): to Langfuse next
            counts["explanations"] += conn.execute(
                "UPDATE agent.explanations SET helped = %s, helped_at = %s, sent_to_langfuse_at = NULL WHERE id = %s",
                (e["helped"], e["helped_at"], e["id"]),
            ).rowcount
        for n in data.get("annotations", []):  # the weekly annotation (T24): the latest verdict wins
            counts["annotations"] += conn.execute(
                "INSERT INTO feedback.annotations (id, sheet_id, item_id, ok, problem, comment, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET ok = EXCLUDED.ok, "
                "problem = EXCLUDED.problem, comment = EXCLUDED.comment, created_at = EXCLUDED.created_at, "
                "received_at = now(), sent_to_langfuse_at = NULL",
                (n["id"], n["sheet_id"], n["item_id"], n["ok"], n.get("problem"), n.get("comment", ""), n["created_at"]),
            ).rowcount
        for i in data.get("instructions", []):
            counts["instructions"] += conn.execute(
                "INSERT INTO agent.instructions (id, student_id, text, form_rule, origin, created_at) "
                "VALUES (%s, %s, %s, %s, 'pwa', %s) ON CONFLICT (id) DO NOTHING",
                (i["id"], student_id, i.get("text", ""), Jsonb(i["rule"]) if i.get("rule") else None, i["created_at"]),
            ).rowcount
        for r in data.get("instruction_replies", []):
            counts["replies"] += conn.execute(
                "INSERT INTO agent.instruction_replies (id, instruction_id, kind, text, rule, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (f"pwa-{r['id']}", r["instruction_id"], r["kind"], r.get("text", ""),
                 Jsonb(r["rule"]) if r.get("rule") else None, r["created_at"]),
            ).rowcount
        for r in data.get("requests", []):
            counts["requests"] += conn.execute(
                "INSERT INTO agent.requests (id, student_id, subject_code, kind, focus, items, minutes, origin, "
                "created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, 'pwa', %s) ON CONFLICT (id) DO NOTHING",
                (r["id"], student_id, r["subject"], r["kind"], r.get("focus", ""), r.get("items"), r.get("minutes"),
                 r["created_at"]),
            ).rowcount
        for a in data.get("attempts", []):
            counts["attempts"] += conn.execute(
                "INSERT INTO feedback.attempts (id, sheet_id, item_id, role, mode, response, correct, rating, "
                "hints_used, answered_at, created_at, trace_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                f"{_TRACE}) ON CONFLICT (id) DO NOTHING",
                (a["id"], a["sheet_id"], a["item_id"], a["role"], a["mode"],
                 None if a.get("response") is None else Jsonb(a["response"]), a["correct"], a.get("rating"),
                 a.get("hints_used", 0), a.get("answered_at"), a["created_at"], a.get("trace_id"), a["sheet_id"]),
            ).rowcount
        for r in data.get("reports", []):
            counts["reports"] += conn.execute(
                "INSERT INTO feedback.reports (id, sheet_id, item_id, role, reason, comment, created_at, trace_id) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, {_TRACE}) ON CONFLICT (id) DO NOTHING",
                (r["id"], r["sheet_id"], r.get("item_id"), r["role"], r["reason"], r.get("comment", ""),
                 r["created_at"], r.get("trace_id"), r["sheet_id"]),
            ).rowcount
        for f in data.get("finishes", []):
            counts["finishes"] += conn.execute(
                "INSERT INTO feedback.finishes (id, sheet_id, role, score, total, to_review, finished_at, trace_id) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, {_TRACE}) ON CONFLICT (id) DO NOTHING",
                (f["id"], f["sheet_id"], f["role"], f["score"], f["total"], Jsonb(f.get("to_review", [])),
                 f["finished_at"], f.get("trace_id"), f["sheet_id"]),
            ).rowcount
        conn.execute(
            "INSERT INTO feedback.cursor (id, since) VALUES (true, %s) "
            "ON CONFLICT (id) DO UPDATE SET since = EXCLUDED.since, updated_at = now()",
            (data["until"],),
        )
    return counts

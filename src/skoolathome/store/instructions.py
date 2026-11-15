"""The parent's instructions on the laptop: received, asked about, proposed, saved (T21)."""

from __future__ import annotations

from secrets import token_hex

import psycopg
from psycopg.types.json import Jsonb

from skoolathome.rules import privacy
from skoolathome.schema.instruction import InstructionUpdate

_COLUMNS = ("id, text, form_rule, origin, created_at, status, question, summary, proposal, rule_id, rule_version, "
            "reason, trace_id, agent_rule, facts, proposal_key")
WAITING = ("question", "proposed")  # the graph is paused, waiting for the parent


def _rows(conn: psycopg.Connection, where: str, params: tuple = ()) -> list[dict]:
    names = _COLUMNS.split(", ")
    return [dict(zip(names, row)) for row in conn.execute(f"SELECT {_COLUMNS} FROM agent.instructions {where}", params)]


def create(conn: psycopg.Connection, *, text: str = "", form_rule: dict | None = None, student_id: str | None,
           origin: str = "cli", instruction_id: str | None = None, created_at=None, agent_rule: dict | None = None,
           facts: list[str] | None = None, proposal_key: str | None = None) -> str:
    """An instruction of the parent, or a rule proposed by the agent (`agent_rule`, T28)."""
    instruction_id = instruction_id or f"{origin}-{token_hex(6)}"
    conn.execute(
        "INSERT INTO agent.instructions (id, student_id, text, form_rule, origin, created_at, agent_rule, facts, "
        "proposal_key) VALUES (%s, %s, %s, %s, %s, coalesce(%s, now()), %s, %s, %s) ON CONFLICT (id) DO NOTHING",
        (instruction_id, student_id, text.strip(), Jsonb(form_rule) if form_rule else None, origin, created_at,
         Jsonb(agent_rule) if agent_rule else None, Jsonb(facts or []), proposal_key),
    )
    return instruction_id


def add_reply(conn: psycopg.Connection, instruction_id: str, kind: str, *, text: str = "", rule: dict | None = None,
              reply_id: str | None = None, created_at=None) -> str:
    reply_id = reply_id or f"cli-{token_hex(6)}"
    conn.execute(
        "INSERT INTO agent.instruction_replies (id, instruction_id, kind, text, rule, created_at) "
        "VALUES (%s, %s, %s, %s, %s, coalesce(%s, now())) ON CONFLICT (id) DO NOTHING",
        (reply_id, instruction_id, kind, text.strip(), Jsonb(rule) if rule else None, created_at),
    )
    return reply_id


def get(conn: psycopg.Connection, instruction_id: str) -> dict | None:
    found = _rows(conn, "WHERE id = %s", (instruction_id,))
    return found[0] if found else None


def recent(conn: psycopg.Connection, limit: int = 20) -> list[dict]:
    return _rows(conn, "ORDER BY created_at DESC LIMIT %s", (limit,))


def new(conn: psycopg.Connection) -> list[dict]:
    """Instructions the graph has not read yet."""
    return _rows(conn, "WHERE status = 'pending' ORDER BY created_at")


def next_reply(conn: psycopg.Connection, instruction_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, kind, text, rule FROM agent.instruction_replies WHERE instruction_id = %s AND handled_at IS NULL "
        "ORDER BY created_at, id LIMIT 1", (instruction_id,),
    ).fetchone()
    return {"id": row[0], "kind": row[1], "text": row[2], "rule": row[3]} if row else None


def with_replies(conn: psycopg.Connection) -> list[dict]:
    """Instructions with a reply of the parent the graph has not read yet."""
    return _rows(conn, "WHERE id IN (SELECT instruction_id FROM agent.instruction_replies WHERE handled_at IS NULL) "
                       "ORDER BY created_at")


def handled(conn: psycopg.Connection, reply_id: str) -> None:
    conn.execute("UPDATE agent.instruction_replies SET handled_at = now() WHERE id = %s", (reply_id,))


def to_process(conn: psycopg.Connection) -> int:
    return conn.execute(
        "SELECT count(*) FROM agent.instructions i WHERE status = 'pending' OR EXISTS (SELECT 1 FROM "
        "agent.instruction_replies r WHERE r.instruction_id = i.id AND r.handled_at IS NULL)"
    ).fetchone()[0]


def set_state(conn: psycopg.Connection, instruction_id: str, *, status: str, question: str = "", summary: str = "",
              proposal: dict | None = None, rule_id: str | None = None, rule_version: int | None = None,
              reason: str = "", trace_id: str | None = None) -> None:
    conn.execute(
        "UPDATE agent.instructions SET status = %s, question = %s, summary = %s, proposal = %s, rule_id = %s, "
        "rule_version = %s, reason = %s, trace_id = coalesce(%s, trace_id), updated_at = now(), reported_at = NULL "
        "WHERE id = %s",
        (status, question, summary, Jsonb(proposal) if proposal else None, rule_id, rule_version, reason[:600],
         trace_id, instruction_id),
    )


def correction(conn: psycopg.Connection, instruction_id: str, text: str, proposed: dict, final: dict) -> None:
    """A [Modifier]: what the agent understood, and what the parent saved instead."""
    conn.execute(
        "INSERT INTO agent.instruction_corrections (instruction_id, text, proposed, final) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (instruction_id) DO UPDATE SET proposed = EXCLUDED.proposed, final = EXCLUDED.final",
        (instruction_id, text, Jsonb(proposed), Jsonb(final)),
    )


def corrections(conn: psycopg.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT instruction_id, text, proposed, final, created_at FROM agent.instruction_corrections ORDER BY created_at"
    ).fetchall()
    return [{"instruction_id": r[0], "text": r[1], "proposed": r[2], "final": r[3], "created_at": r[4]} for r in rows]


def to_report(conn: psycopg.Connection) -> list[InstructionUpdate]:
    """Instructions made online whose new state the PWA does not know yet. No surname (§17)."""
    rows = _rows(conn, "WHERE origin IN ('pwa', 'agent') AND status <> 'pending' AND reported_at IS NULL "
                       "ORDER BY updated_at")
    names = privacy.teachers(conn)
    return [InstructionUpdate(id=r["id"], status=r["status"], question=privacy.public_text(r["question"], names)[:600],
                              origin="agent" if r["origin"] == "agent" else "parent",
                              text=privacy.public_text(r["text"], names)[:600] if r["origin"] == "agent" else "",
                              facts=[privacy.public_text(f, names)[:300] for f in (r["facts"] or [])][:10],
                              summary=privacy.public_text(r["summary"], names)[:800],
                              proposal=privacy.public_rule(r["proposal"], names), rule_id=r["rule_id"],
                              rule_version=r["rule_version"], reason=privacy.public_text(r["reason"], names)[:600])
            for r in rows]


def reported(conn: psycopg.Connection, ids: list[str]) -> None:
    conn.execute("UPDATE agent.instructions SET reported_at = now() WHERE id = ANY(%s)", (ids,))

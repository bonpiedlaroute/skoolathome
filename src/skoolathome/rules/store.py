"""Rules in the local database: every version kept, the latest one counts."""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

from skoolathome.rules.defaults import DEFAULT_RULES
from skoolathome.schema.rule import Rule

_LATEST = (
    "SELECT DISTINCT ON (id) body FROM agent.rules ORDER BY id, version DESC"
)


def ensure_defaults(conn: psycopg.Connection) -> list[str]:
    """Insert the default rules that were never stored. A default rule the parent changed or
    archived keeps its latest version: it is not put back."""
    added = []
    for rule in DEFAULT_RULES:
        inserted = conn.execute(
            "INSERT INTO agent.rules (id, version, status, body) SELECT %s, %s, %s, %s "
            "WHERE NOT EXISTS (SELECT 1 FROM agent.rules WHERE id = %s) RETURNING id",
            (rule.id, rule.version, rule.status, Jsonb(rule.model_dump(mode="json")), rule.id),
        ).fetchone()
        if inserted:
            added.append(rule.id)
    return added


def latest(conn: psycopg.Connection) -> list[Rule]:
    """The latest version of every rule, whatever its status."""
    return [Rule.model_validate(row[0]) for row in conn.execute(_LATEST).fetchall()]


def active(conn: psycopg.Connection) -> list[Rule]:
    return [rule for rule in latest(conn) if rule.status == "active"]


def save(conn: psycopg.Connection, rule: Rule) -> Rule:
    """Store a new version of the rule (version = previous + 1), whatever version it carries."""
    previous = conn.execute("SELECT max(version) FROM agent.rules WHERE id = %s", (rule.id,)).fetchone()[0]
    stored = rule.model_copy(update={"version": (previous or 0) + 1})
    stored = Rule.model_validate(stored.model_dump())  # validate again after the copy
    conn.execute(
        "INSERT INTO agent.rules (id, version, status, body) VALUES (%s, %s, %s, %s)",
        (stored.id, stored.version, stored.status, Jsonb(stored.model_dump(mode="json"))),
    )
    return stored


def set_status(conn: psycopg.Connection, rule_id: str, status: str) -> Rule:
    """Archive or reactivate a rule: a new version with the new status."""
    current = next((rule for rule in latest(conn) if rule.id == rule_id), None)
    if current is None:
        raise LookupError(f"règle inconnue : {rule_id}")
    return save(conn, current.model_copy(update={"status": status}))

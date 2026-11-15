"""Rules in the local database: every version kept, the latest one counts."""

from __future__ import annotations

from datetime import date

import psycopg
from psycopg.types.json import Jsonb

from skoolathome.rules import privacy
from skoolathome.rules.defaults import DEFAULT_RULES
from skoolathome.rules.describe import describe
from skoolathome.schema.instruction import PublishedRule
from skoolathome.schema.rule import Rule

_LATEST = (
    "SELECT DISTINCT ON (id) body FROM agent.rules ORDER BY id, version DESC"
)


def _content(rule: Rule) -> dict:
    return rule.model_dump(mode="json", exclude={"version", "status"})


def ensure_defaults(conn: psycopg.Connection) -> list[str]:
    """Store the default rules, and follow the code's changes to them, as new versions. A default
    rule the parent changed, archived or reactivated (origin: parent) is never touched again."""
    current = {rule.id: rule for rule in latest(conn)}
    changed = []
    for rule in DEFAULT_RULES:
        stored = current.get(rule.id)
        if stored is None:
            conn.execute("INSERT INTO agent.rules (id, version, status, body) VALUES (%s, %s, %s, %s)",
                         (rule.id, rule.version, rule.status, Jsonb(rule.model_dump(mode="json"))))
            changed.append(rule.id)
        elif stored.origin.author == "default" and stored.status == "active" and _content(stored) != _content(rule):
            save(conn, rule)
            changed.append(rule.id)
    return changed


def by_parent(rule: Rule, **changes) -> Rule:
    """A rule as the parent changed it: it keeps its sentence, and the code no longer updates it."""
    origin = rule.origin.model_copy(update={"author": "parent", "on": date.today()})
    return Rule.model_validate({**rule.model_dump(), **changes, "origin": origin.model_dump()})


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


def get(conn: psycopg.Connection, rule_id: str) -> Rule:
    current = next((rule for rule in latest(conn) if rule.id == rule_id), None)
    if current is None:
        raise LookupError(f"règle inconnue : {rule_id}")
    return current


def set_status(conn: psycopg.Connection, rule_id: str, status: str) -> Rule:
    """Archive or reactivate a rule (a parent's decision): a new version with the new status."""
    return save(conn, by_parent(get(conn, rule_id), status=status))


def published(conn: psycopg.Connection) -> list[PublishedRule]:
    """Every rule as the parent view lists it, sent with the evening's batch (T21). No surname."""
    names = privacy.teachers(conn)
    return [PublishedRule(id=rule.id, version=rule.version, status=rule.status, summary=describe(rule)[:800],
                          body=privacy.public_rule(rule.model_dump(mode="json"), names))
            for rule in sorted(latest(conn), key=lambda r: (r.status != "active", r.priority, r.id))]


def set_fields(conn: psycopg.Connection, rule_id: str, changes: dict) -> Rule:
    """`skoolathome rules set before_test items=4`: a new version, validated, marked as the parent's."""
    bad = sorted((set(changes) - set(Rule.model_fields)) | (set(changes) & {"id", "version", "origin"}))
    if bad:
        raise ValueError(f"champ inconnu ou non modifiable : {', '.join(bad)}")
    return save(conn, by_parent(get(conn, rule_id), **changes))

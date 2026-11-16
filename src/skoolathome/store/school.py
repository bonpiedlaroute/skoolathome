"""Write the common format to Postgres without duplicates.

Each item has a stable key from its source and a fingerprint of its content.
Same key and same fingerprint: nothing is written. Same key, new fingerprint:
the row is updated and `updated_at` moves.
"""

from __future__ import annotations

from collections import Counter

import psycopg
from psycopg.types.json import Jsonb

from skoolathome.rules import assessments
from skoolathome.schema import Model, SchoolSnapshot

# table -> (key columns, model field -> column) ; fields not listed keep their name.
_TABLES = {
    "timetable_slots": ("source", "student_id", "source_id", "starts_at"),
    "lessons": ("source", "student_id", "source_id"),
    "homework": ("source", "student_id", "source_id"),
    "grades": ("source", "student_id", "source_id"),
}


def _row(item: Model, extra: dict | None = None) -> dict:
    row = item.model_dump(mode="python")
    for name in ("documents", "links"):
        if name in row:
            row[name] = Jsonb(list(row[name]))
    row["fingerprint"] = item.fingerprint()
    row.update(extra or {})
    return row


def _upsert(conn: psycopg.Connection, table: str, row: dict) -> str:
    """Returns "inserted", "updated" or "unchanged"."""
    key = _TABLES[table]
    columns = list(row)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in key)
    # is_assessment is derived data: compare it too, so a rule change is applied. The links are
    # out of the fingerprint (the models never read them): compared on their own.
    changed = " OR ".join(
        f"t.{c} IS DISTINCT FROM EXCLUDED.{c}"
        for c in ("fingerprint", "is_assessment", "assessment_reason", "links")
        if c in row
    )
    sql = (
        f"INSERT INTO school.{table} AS t ({', '.join(columns)}) "
        f"VALUES ({', '.join('%(' + c + ')s' for c in columns)}) "
        f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}, updated_at = now() "
        f"WHERE {changed} "
        "RETURNING (xmax = 0) AS inserted"
    )
    result = conn.execute(sql, row).fetchone()
    if result is None:
        return "unchanged"
    return "inserted" if result[0] else "updated"


def save_snapshot(conn: psycopg.Connection, snapshot: SchoolSnapshot) -> dict[str, dict[str, int]]:
    """Counts per table: {"lessons": {"inserted": 3, "updated": 1, "unchanged": 30}, …}."""
    counts: dict[str, Counter] = {table: Counter() for table in _TABLES}
    for slot in snapshot.slots:
        counts["timetable_slots"][_upsert(conn, "timetable_slots", _row(slot))] += 1
    for lesson in snapshot.lessons:
        counts["lessons"][_upsert(conn, "lessons", _row(lesson))] += 1
    for item in snapshot.homework:
        is_assessment, reason = assessments.detect(item)
        extra = {"is_assessment": is_assessment, "assessment_reason": reason}
        counts["homework"][_upsert(conn, "homework", _row(item, extra))] += 1
    for grade in snapshot.grades:
        counts["grades"][_upsert(conn, "grades", _row(grade))] += 1
    return {table: dict(counter) for table, counter in counts.items()}


def start_run(conn: psycopg.Connection, source: str) -> int:
    return conn.execute(
        "INSERT INTO school.collection_runs (source) VALUES (%s) RETURNING id", (source,)
    ).fetchone()[0]


def finish_run(
    conn: psycopg.Connection,
    run_id: int,
    status: str,
    *,
    student_id: str | None = None,
    counts: dict | None = None,
    errors: list | None = None,
) -> None:
    conn.execute(
        "UPDATE school.collection_runs SET finished_at = now(), status = %s, student_id = %s, "
        "counts = %s, errors = %s WHERE id = %s",
        (status, student_id, Jsonb(counts or {}), Jsonb(errors or []), run_id),
    )


def save_raw(
    conn: psycopg.Connection, run_id: int, source: str, kind: str, ref: str, api_version: str, body: dict
) -> None:
    conn.execute(
        "INSERT INTO raw.responses (run_id, source, kind, ref, api_version, body) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (run_id, source, kind, ref, api_version, Jsonb(body)),
    )


def has_lessons(conn: psycopg.Connection, source: str) -> bool:
    return conn.execute(
        "SELECT EXISTS (SELECT 1 FROM school.lessons WHERE source = %s)", (source,)
    ).fetchone()[0]

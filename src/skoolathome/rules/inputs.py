"""What the evening plan reads from the local database (ARCHITECTURE.md §8, inputs)."""

from __future__ import annotations

from datetime import date, timedelta

import psycopg

from skoolathome.rules.plan import (
    HORIZON_DAYS,
    Existing,
    LessonRef,
    Missed,
    PlanInputs,
    RequestRef,
    Slot,
    TestRef,
)
from skoolathome.subjects import label

HISTORY_DAYS = 35  # lessons and timetable looked back at
MODEL_CONFIDENCE = 0.5  # a test read by the model counts from this confidence (recall first)
PAST_TEST_KINDS = r"^(contr|interro|eval|devoir sur table|ds)"  # grade kinds that are tests


def load(conn: psycopg.Connection, student_id: str, run_date: date) -> PlanInputs:
    start, horizon = run_date - timedelta(days=HISTORY_DAYS), run_date + timedelta(days=HORIZON_DAYS)
    slots = [
        Slot(day=row[0], subject_code=row[1], teacher=row[2], cancelled=row[3])
        for row in conn.execute(
            "SELECT starts_at::date, subject_code, teacher, cancelled FROM school.timetable_slots "
            "WHERE student_id = %s AND kind = 'COURS' AND starts_at::date BETWEEN %s AND %s",
            (student_id, start, horizon),
        ).fetchall()
    ]
    holidays = set()
    for first, last in conn.execute(
        "SELECT starts_at::date, ends_at::date FROM school.timetable_slots "
        "WHERE student_id = %s AND kind = 'CONGE' AND ends_at::date >= %s AND starts_at::date <= %s",
        (student_id, start, horizon),
    ).fetchall():
        holidays |= {first + timedelta(days=n) for n in range((last - first).days + 1)}
    # A lesson's text, plus the pages of the files its teacher attached (T25): a lesson with only a
    # PDF gets its sheet, and a sheet is made again when its PDF arrives.
    lessons = [
        LessonRef(source=r[0], source_id=r[1], day=r[2], subject_code=r[3], subject=r[4], chars=r[5], fingerprint=r[6])
        for r in conn.execute(
            "SELECT l.source, l.source_id, l.day, l.subject_code, l.subject, "
            "(CASE WHEN l.is_empty THEN 0 ELSE length(l.content) END) + coalesce(att.chars, 0)::int, "
            "l.fingerprint || coalesce(':' || att.files, '') FROM school.lessons l "
            "LEFT JOIN LATERAL (SELECT sum(length(btrim(p.text))) AS chars, string_agg(DISTINCT a.sha256, ',') AS files "
            "  FROM school.attachments a JOIN material.documents d ON d.source = a.source "
            "  AND d.source_ref = a.sha256 AND d.status = 'indexed' "
            "  JOIN material.pages p ON p.document_id = d.id "
            "  WHERE a.source = l.source AND a.student_id = l.student_id AND a.lesson_source_id = l.source_id "
            "  AND a.status = 'downloaded') att ON true "
            "WHERE l.student_id = %s AND l.day BETWEEN %s AND %s",
            (student_id, start, run_date),
        ).fetchall()
    ]
    return PlanInputs(
        run_date=run_date, slots=slots, lessons=lessons, holidays=holidays,
        tests=upcoming_tests(conn, student_id, run_date, horizon),
        past_tests=_past_tests(conn, student_id, start, run_date),
        existing=_existing(conn, student_id), missed=missed(conn, student_id, run_date),
        requests=pending_requests(conn, student_id),
    )


def pending_requests(conn: psycopg.Connection, student_id: str) -> list[RequestRef]:
    rows = conn.execute(
        "SELECT id, subject_code, kind, focus, items, minutes FROM agent.requests WHERE status = 'pending' "
        "AND (student_id IS NULL OR student_id = %s) ORDER BY created_at", (student_id,),
    ).fetchall()
    return [RequestRef(id=r[0], subject_code=r[1], kind=r[2], focus=r[3], items=r[4], minutes=r[5]) for r in rows]


def upcoming_tests(conn: psycopg.Connection, student_id: str, run_date: date, until: date) -> list[TestRef]:
    """Tests announced in the homework (case ticked, keywords), plus those the model read in
    ambiguous homework (T19)."""
    rows = conn.execute(
        "SELECT subject_code, subject, due_on, assessment_reason, text FROM school.homework "
        "WHERE student_id = %s AND is_assessment = 'yes' AND due_on > %s AND due_on <= %s",
        (student_id, run_date, until),
    ).fetchall()
    tests = [TestRef(subject_code=r[0], subject=label(r[0], r[1]), day=r[2], reason=r[3] or "keywords", text=r[4])
             for r in rows]
    rows = conn.execute(
        "SELECT h.subject_code, h.subject, coalesce(c.day, h.due_on), h.text FROM agent.assessment_checks c "
        "JOIN school.homework h ON h.source = c.source AND h.student_id = c.student_id "
        "AND h.source_id = c.source_id AND h.fingerprint = c.fingerprint "
        "WHERE c.student_id = %s AND c.is_assessment AND c.confidence >= %s AND h.is_assessment <> 'yes' "
        "AND coalesce(c.day, h.due_on) > %s AND coalesce(c.day, h.due_on) <= %s",
        (student_id, MODEL_CONFIDENCE, run_date, until),
    ).fetchall()
    tests += [TestRef(subject_code=r[0], subject=label(r[0], r[1]), day=r[2], reason="model", text=r[3]) for r in rows]
    return tests


def _past_tests(conn: psycopg.Connection, student_id: str, start: date, run_date: date) -> dict[str, list[date]]:
    rows = conn.execute(
        "SELECT subject_code, day FROM school.grades WHERE student_id = %s AND day BETWEEN %s AND %s "
        "AND lower(kind) ~ %s "
        "UNION SELECT subject_code, due_on FROM school.homework WHERE student_id = %s AND is_assessment = 'yes' "
        "AND due_on BETWEEN %s AND %s",
        (student_id, start, run_date, PAST_TEST_KINDS, student_id, start, run_date),
    ).fetchall()
    found: dict[str, list[date]] = {}
    for code, day in rows:
        found.setdefault(code, []).append(day)
    return found


def _existing(conn: psycopg.Connection, student_id: str) -> dict[str, Existing]:
    rows = conn.execute(
        "SELECT id, status, visible_from, minutes, inputs, CASE WHEN kind = 'test_revision' THEN due_by END "
        "FROM agent.sheets WHERE student_id = %s", (student_id,)
    ).fetchall()
    # due_by only for a test revision: its minutes count over the evenings until the test.
    return {r[0]: Existing(id=r[0], status=r[1], visible_from=r[2], minutes=r[3], inputs=r[4], due_by=r[5])
            for r in rows}


def missed(conn: psycopg.Connection, student_id: str, run_date: date, days: int = 14) -> list[Missed]:
    """Questions got wrong alone, or rated ✗ or ~ together, on the agent's sheets (T20)."""
    rows = conn.execute(
        "SELECT s.subject_code, s.body->>'subject', item->>'concept', item->'quote'->>'source_id', "
        "a.created_at::date, a.received_at::date "
        "FROM feedback.attempts a JOIN agent.sheets s ON s.id = a.sheet_id "
        "CROSS JOIN LATERAL jsonb_array_elements(s.body->'items') AS item "
        "WHERE s.student_id = %s AND item->>'id' = a.item_id AND a.created_at::date >= %s "
        "AND ((a.mode = 'solo' AND NOT a.correct) OR a.rating IN ('bad', 'partial'))",
        (student_id, run_date - timedelta(days=days)),
    ).fetchall()
    return [Missed(subject_code=r[0], subject=r[1] or label(r[0]), concept=r[2] or "",
                   source_keys=(r[3],) if r[3] else (), missed_on=r[4], received_on=r[5]) for r in rows]

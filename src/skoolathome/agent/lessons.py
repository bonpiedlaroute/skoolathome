"""Lessons as the evening graph sees them: the text of a lesson, its summary, the homework of its day."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import psycopg

from skoolathome.subjects import label

SUMMARY_MIN_CHARS = 600  # below that, the lesson is its own summary: no model call


@dataclass(frozen=True)
class Lesson:
    source: str
    student_id: str
    source_id: str
    day: date
    subject_code: str
    subject: str
    content: str
    fingerprint: str

    @property
    def key(self) -> str:
        """How a sheet cites it, e.g. lesson:2026-11-12:MATHS."""
        return f"lesson:{self.day.isoformat()}:{self.subject_code}"

    @property
    def label(self) -> str:
        return label(self.subject_code, self.subject)


_COLUMNS = "source, student_id, source_id, day, subject_code, subject, content, fingerprint"


def _lessons(conn: psycopg.Connection, where: str, params: tuple) -> list[Lesson]:
    rows = conn.execute(f"SELECT {_COLUMNS} FROM school.lessons WHERE {where}", params).fetchall()
    return [Lesson(*row) for row in rows]


def get(conn: psycopg.Connection, student_id: str, source: str, source_id: str) -> Lesson | None:
    found = _lessons(conn, "student_id = %s AND source = %s AND source_id = %s", (student_id, source, source_id))
    return found[0] if found else None


def homework_of(conn: psycopg.Connection, student_id: str, subject_code: str, day: date) -> list[str]:
    """What was given on that day: when the lesson itself is empty, it says what the class did
    (« apprendre la leçon p. 86 ») and helps find the right pages."""
    rows = conn.execute(
        "SELECT text FROM school.homework WHERE student_id = %s AND subject_code = %s AND given_on = %s "
        "AND text <> ''", (student_id, subject_code, day),
    ).fetchall()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------- summaries


def without_summary(conn: psycopg.Connection, student_id: str) -> list[Lesson]:
    """Lessons whose current version has no summary yet: the ones that just arrived, or were edited."""
    return _lessons(
        conn,
        "student_id = %s AND NOT is_empty AND length(content) > 0 AND NOT EXISTS ("
        " SELECT 1 FROM agent.lesson_summaries s WHERE s.source = school.lessons.source"
        " AND s.student_id = school.lessons.student_id AND s.source_id = school.lessons.source_id"
        " AND s.fingerprint = school.lessons.fingerprint) ORDER BY day, subject_code",
        (student_id,),
    )


def save_summary(conn: psycopg.Connection, lesson: Lesson, summary: str, model: str,
                 prompt_version: str = "", trace_id: str | None = None) -> None:
    conn.execute(
        "INSERT INTO agent.lesson_summaries (source, student_id, source_id, fingerprint, summary, model, "
        "prompt_version, trace_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (lesson.source, lesson.student_id, lesson.source_id, lesson.fingerprint, summary, model,
         prompt_version, trace_id),
    )


def summary(conn: psycopg.Connection, lesson: Lesson) -> str | None:
    row = conn.execute(
        "SELECT summary FROM agent.lesson_summaries WHERE source = %s AND student_id = %s "
        "AND source_id = %s AND fingerprint = %s",
        (lesson.source, lesson.student_id, lesson.source_id, lesson.fingerprint),
    ).fetchone()
    return row[0] if row else None

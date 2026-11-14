"""Lessons as the evening graph sees them: the tasks of T15, the sources of a task, the summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import psycopg

SUBJECT_LABELS = {
    "MATHS": "Mathématiques", "FRANC": "Français", "AGL1": "Anglais", "HI-GE": "Histoire-géographie",
    "PH-CH": "Physique-chimie", "SVT": "SVT", "TECHN": "Technologie", "LCALA": "Latin",
    "EDMUS": "Éducation musicale", "A-PLA": "Arts plastiques",
}
# Nothing to revise from what is written there.
EXCLUDED_SUBJECTS = {"EPS", "AEAID", "VIE-S"}
# Numbers are made up by the model and recomputed by the code (ARCHITECTURE.md §5.4).
COMPUTATIONAL_SUBJECTS = {"MATHS"}

MIN_CONTENT_CHARS = 120  # below that, not enough to write a sheet from
SUMMARY_MIN_CHARS = 600  # below that, the lesson is its own summary: no model call
MAX_TASKS = 3            # until the evening plan (T19) decides
TARGET_MINUTES = 8


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
        return SUBJECT_LABELS.get(self.subject_code, self.subject.capitalize())


_COLUMNS = "source, student_id, source_id, day, subject_code, subject, content, fingerprint"


def _lessons(conn: psycopg.Connection, where: str, params: tuple) -> list[Lesson]:
    rows = conn.execute(f"SELECT {_COLUMNS} FROM school.lessons WHERE {where}", params).fetchall()
    return [Lesson(*row) for row in rows]


def get(conn: psycopg.Connection, student_id: str, source: str, source_id: str) -> Lesson | None:
    found = _lessons(conn, "student_id = %s AND source = %s AND source_id = %s", (student_id, source, source_id))
    return found[0] if found else None


def usable(lesson: Lesson) -> bool:
    return lesson.subject_code not in EXCLUDED_SUBJECTS and len(lesson.content) >= MIN_CONTENT_CHARS


def fixed_tasks(conn: psycopg.Connection, student_id: str, run_date: date, lesson_ids: list[str] | None = None) -> list[dict]:
    """The tasks of T15, written in code: an exercise sheet for the richest lessons of the last
    school day. T19 replaces this with the evening plan (rules × timetable)."""
    if lesson_ids:
        chosen = _lessons(conn, "student_id = %s AND source_id = ANY(%s) ORDER BY day, subject_code",
                          (student_id, lesson_ids))
    else:
        last_day = conn.execute(
            "SELECT max(day) FROM school.lessons WHERE student_id = %s AND day <= %s AND NOT is_empty "
            "AND length(content) >= %s", (student_id, run_date, MIN_CONTENT_CHARS),
        ).fetchone()[0]
        if last_day is None:
            return []
        candidates = _lessons(conn, "student_id = %s AND day = %s ORDER BY length(content) DESC, subject_code",
                              (student_id, last_day))
        chosen = [lesson for lesson in candidates if usable(lesson)][:MAX_TASKS]
    visible_from = run_date + timedelta(days=1)
    return [
        {
            "id": f"{lesson.day.isoformat()}-{lesson.subject_code.lower()}-exercises",
            "kind": "exercises",
            "subject_code": lesson.subject_code,
            "subject": lesson.label,
            "source": lesson.source,
            "source_id": lesson.source_id,
            "source_key": lesson.key,
            "visible_from": visible_from.isoformat(),
            "target_minutes": TARGET_MINUTES,
            "computational": lesson.subject_code in COMPUTATIONAL_SUBJECTS,
            "rule": "fixed-t15",  # until T19: no rule, a fixed choice
        }
        for lesson in chosen
    ]


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

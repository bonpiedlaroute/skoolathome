"""Evening summary, without any model: upcoming tests, and lessons without content."""

from __future__ import annotations

from datetime import date, timedelta

import psycopg

DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
ASSESSMENT_SLOT_SQL = r"\m(ds|dst|devoirs? surveill[eé]s?|devoir sur table|examens?|brevet blanc)\M"


def _day(day: date) -> str:
    return f"{DAYS[day.weekday()]} {day.day:02d}/{day.month:02d}"


def latest_student(conn: psycopg.Connection) -> str | None:
    row = conn.execute(
        "SELECT student_id FROM school.collection_runs "
        "WHERE student_id IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def build(conn: psycopg.Connection, student_id: str, today: date) -> str:
    """The summary as Markdown, in French: it is read by the parent."""
    horizon = today + timedelta(days=14)
    tests = conn.execute(
        "SELECT due_on, subject, assessment_reason, text FROM school.homework "
        "WHERE student_id = %s AND is_assessment = 'yes' AND due_on BETWEEN %s AND %s "
        "ORDER BY due_on, subject",
        (student_id, today, horizon),
    ).fetchall()
    test_slots = conn.execute(
        "SELECT starts_at, subject FROM school.timetable_slots "
        "WHERE student_id = %s AND NOT cancelled AND starts_at::date BETWEEN %s AND %s "
        "AND lower(subject_code || ' ' || subject) ~ %s ORDER BY starts_at",
        (student_id, today, horizon, ASSESSMENT_SLOT_SQL),
    ).fetchall()
    unknown = conn.execute(
        "SELECT count(*) FROM school.homework "
        "WHERE student_id = %s AND is_assessment = 'unknown' AND due_on BETWEEN %s AND %s",
        (student_id, today, horizon),
    ).fetchone()[0]
    # Subjects taught on a day of the past week, with no content written for that day.
    missing = conn.execute(
        "SELECT DISTINCT s.starts_at::date AS day, s.subject FROM school.timetable_slots s "
        "WHERE s.student_id = %s AND s.kind = 'COURS' AND NOT s.cancelled "
        "AND s.starts_at::date BETWEEN %s AND %s "
        "AND NOT EXISTS (SELECT 1 FROM school.lessons l WHERE l.student_id = s.student_id "
        "AND l.day = s.starts_at::date AND l.subject_code = s.subject_code AND NOT l.is_empty) "
        "ORDER BY day, s.subject",
        (student_id, today - timedelta(days=7), today),
    ).fetchall()
    last_run = conn.execute(
        "SELECT status, finished_at, jsonb_array_length(errors) FROM school.collection_runs "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()

    lines = [f"# Résumé du {_day(today)} {today.year}", ""]
    lines.append("## Contrôles des 14 prochains jours")
    lines.append("")
    if not tests and not test_slots:
        lines.append("Aucun contrôle repéré.")
    for due_on, subject, reason, text in tests:
        why = "case « contrôle » cochée par le prof" if reason == "flag" else "repéré dans le texte"
        first_line = text.split("\n", 1)[0][:140] if text else ""
        lines.append(f"- **{_day(due_on)}** · {subject} ({why})" + (f" : {first_line}" if first_line else ""))
    for starts_at, subject in test_slots:
        lines.append(f"- **{_day(starts_at.date())}** à {starts_at:%H:%M} · créneau « {subject} » à l'emploi du temps")
    if unknown:
        lines.append("")
        lines.append(f"{unknown} devoir(s) à venir sans texte pour l'instant : impossible de dire si c'est un contrôle.")
    lines += ["", "## Cours des 7 derniers jours sans contenu dans le cahier de texte", ""]
    if not missing:
        lines.append("Tous les cours ont un contenu.")
    for day, subject in missing:
        lines.append(f"- {_day(day)} · {subject}")
    lines += ["", "## Dernière collecte", ""]
    if last_run is None:
        lines.append("Aucune collecte enregistrée.")
    else:
        status, finished_at, error_count = last_run
        when = f"{finished_at.astimezone():%d/%m à %H:%M}" if finished_at else "en cours"
        lines.append(f"Statut : `{status}`, {when}, {error_count} erreur(s).")
    return "\n".join(lines) + "\n"

"""The PWA's home page: what comes next, the latest grades with the class's figures, the sheets to do."""

from __future__ import annotations

from datetime import date, datetime

from skoolathome import home

STUDENT = "4242"


def day(n: int) -> date:
    return date(2026, 11, n)


def slot(conn, n: int, hour: int, code: str, *, kind: str = "COURS", cancelled: bool = False) -> None:
    conn.execute(
        "INSERT INTO school.timetable_slots (source, student_id, source_id, starts_at, ends_at, subject_code, subject, "
        "teacher, room, kind, cancelled, fingerprint) VALUES ('ecoledirecte', %s, %s, %s, %s, %s, %s, 'DURAND P.', "
        "'Salle 12', %s, %s, 'fp')",
        (STUDENT, f"{code}-{n}-{hour}", datetime(2026, 11, n, hour, 20), datetime(2026, 11, n, hour + 1, 15), code,
         code, kind, cancelled),
    )


def lesson(conn, n: int, code: str, content: str, documents: str = "[]") -> None:
    conn.execute(
        "INSERT INTO school.lessons (source, student_id, source_id, day, subject_code, subject, teacher, content, "
        "is_empty, documents, fingerprint) VALUES ('ecoledirecte', %s, %s, %s, %s, %s, 'DURAND P.', %s, %s, %s, 'fp')",
        (STUDENT, f"l-{code}-{n}", day(n), code, code, content, not content, documents),
    )


def homework(conn, n: int, code: str, text: str, reason: str = "keywords") -> None:
    conn.execute(
        "INSERT INTO school.homework (source, student_id, source_id, subject_code, subject, given_on, due_on, text, "
        "done, flagged_as_assessment, is_assessment, assessment_reason, fingerprint) VALUES ('ecoledirecte', %s, %s, "
        "%s, %s, %s, %s, %s, false, %s, 'yes', %s, 'fp')",
        (STUDENT, f"h-{code}-{n}", code, code, day(10), day(n), text, reason == "flag", reason),
    )


def grade(conn, n: int, code: str, value: str, *, average=None, low=None, high=None, kind="Contrôle") -> None:
    conn.execute(
        "INSERT INTO school.grades (source, student_id, source_id, subject_code, subject, day, kind, title, raw_value, "
        "value, out_of, coefficient, period, class_average, class_min, class_max, fingerprint) VALUES ('ecoledirecte', "
        "%s, %s, %s, %s, %s, %s, 'Chapitre 3 avec M. DURAND', %s, %s, 20, 1, 'A001', %s, %s, %s, 'fp')",
        (STUDENT, f"g-{code}-{n}", code, code, day(n), kind, value, value.replace(",", ".") if value[0].isdigit() else None,
         average, low, high),
    )


def week(conn) -> None:
    """Friday 13: maths, French and sport on Monday 16; history on Tuesday 17."""
    for n, hour, code in [(16, 8, "FRANC"), (16, 10, "MATHS"), (16, 14, "EPS"), (17, 9, "HI-GE"), (17, 11, "MATHS")]:
        slot(conn, n, hour, code)
    lesson(conn, 12, "MATHS", "- Symétrie centrale : définition\n- Compétences : Chercher, raisonner\n- ex 24 p 209")
    lesson(conn, 13, "FRANC", "FEUILLE DE ROUTE\nSéance 1 : Renart et les anguilles\nObjectifs : Lire la ruse de Renart")
    lesson(conn, 12, "HI-GE", "", documents='[{"id": 1, "name": "cours.pdf"}]')


# ----------------------------------------------------------------------------- helpers


def test_points_keep_what_the_teacher_wrote_without_the_boilerplate():
    text = "FEUILLE DE ROUTE\n- Séance 1 : Renart et les anguilles\nObjectifs : Lire la ruse de Renart\nCompétences : Lire"
    assert home.points(text) == ["Séance 1 : Renart et les anguilles", "Lire la ruse de Renart"]
    assert len(home.points("\n".join(f"- point {n}" for n in range(9)))) == home.MAX_POINTS


def test_a_test_put_off_to_a_later_day_counts_that_day():
    text = "commencer à réviser pour l'évaluation sur le chapitre 3 qui a été décalée au jeudi 19"
    assert home.announced_day(text, day(16)) == day(19)
    assert home.announced_day("apprendre la leçon", day(16)) is None
    assert home.announced_day("pour mercredi dst", day(17)) is None          # no number: not on its own
    assert home.announced_day("pour mercredi dst", day(17), {day(18)}) == day(18)  # the same test, seen twice


# ------------------------------------------------------------------------------ blocks


def test_the_page_from_the_local_database(conn):
    week(conn)
    homework(conn, 16, "FRANC", "Apprendre par coeur l'extrait de Renart", reason="flag")
    homework(conn, 16, "MATHS", "réviser l'évaluation sur le chapitre 3, décalée au jeudi 19")
    homework(conn, 19, "MATHS", "évaluation chapitre 3")
    grade(conn, 6, "MATHS", "16,75", average=14.2, low=6, high=19.5)
    grade(conn, 9, "MATHS", "12")
    grade(conn, 7, "SVT", "Abs")
    page = home.compute(conn, STUDENT, day(13))

    assert home.next_school_day(page, day(13)) == day(16)  # Friday: Monday
    assert [(s.day, s.starts, s.subject_code) for s in page.slots][:2] == [(day(16), "08:20", "FRANC"),
                                                                            (day(16), "10:20", "MATHS")]
    assert [(t.subject_code, t.day, t.reason) for t in page.tests] == [("FRANC", day(16), "flag"),
                                                                       ("MATHS", day(19), "keywords")]
    [maths, svt] = page.grades  # the latest graded subject first
    assert maths.subject == "Mathématiques" and [g.value for g in maths.grades] == ["12", "16,75"]
    second = maths.grades[1]
    assert (second.score, second.out_of, second.class_average, second.class_min, second.class_max) == (
        16.75, 20.0, 14.2, 6.0, 19.5)
    assert maths.grades[0].title == "Chapitre 3 avec M. D." and "DURAND" not in page.model_dump_json()
    assert svt.grades[0].score is None and svt.grades[0].value == "Abs"

    by = {(s.subject_code, s.reason, s.for_day): s for s in page.suggestions}
    test = by[("FRANC", "test", day(16))]
    assert test.lessons[0].points[0] == "Séance 1 : Renart et les anguilles" and test.request.kind == "revision"
    assert test.request.focus.startswith("le contrôle du lundi 16/11")
    lesson_ = by[("MATHS", "lesson", day(16))]
    assert lesson_.lessons[0].day == day(12) and lesson_.request.kind == "extra"
    assert lesson_.lessons[0].points == ["Symétrie centrale : définition", "ex 24 p 209"]
    assert by[("HI-GE", "lesson", day(17))].lessons[0].attachment  # only a document: still worth revising
    assert ("FRANC", "lesson", day(16)) not in by  # the test covers it
    assert not any(s.subject_code == "EPS" for s in page.suggestions)  # nothing to revise in sport


def test_a_sheet_made_for_a_lesson_is_linked(conn):
    week(conn)
    conn.execute(
        "INSERT INTO agent.sheets (id, run_date, task_id, student_id, subject_code, kind, status, body, visible_from, "
        "attempts, trace_id, model, prompt_version, release) "
        "VALUES ('s1', %s, 's1', %s, 'MATHS', 'exercises', 'published', %s, %s, 1, 't', 'm', 'v1', 'r')",
        (day(12), STUDENT, '{"sources": ["lesson:2026-11-12:MATHS"], "items": []}', day(13)),
    )
    page = home.compute(conn, STUDENT, day(13))
    [maths] = [s for s in page.suggestions if s.subject_code == "MATHS" and s.for_day == day(16)]
    assert maths.sheet_ids == ["s1"]


def test_a_cancelled_lesson_and_the_holidays(conn):
    slot(conn, 16, 8, "MATHS", cancelled=True)
    slot(conn, 17, 0, "", kind="CONGE")
    slot(conn, 18, 8, "FRANC")
    page = home.compute(conn, STUDENT, day(15))
    assert page.holidays == [day(17)]
    assert home.next_school_day(page, day(15)) == day(18)  # Monday cancelled, Tuesday off


def test_sent_once_per_collection(conn):
    sent = []

    class Client:
        def home(self, page):
            sent.append(page)

    assert not home.pending(conn)  # no collection yet
    conn.execute("INSERT INTO school.collection_runs (source, student_id, status) VALUES ('ecoledirecte', %s, 'ok')",
                 (STUDENT,))
    assert home.pending(conn)
    home.send(conn, Client(), STUDENT, day(13))
    assert len(sent) == 1 and not home.pending(conn)
    conn.execute("INSERT INTO school.collection_runs (source, student_id, status) VALUES ('ecoledirecte', %s, 'ok')",
                 (STUDENT,))
    assert home.pending(conn)

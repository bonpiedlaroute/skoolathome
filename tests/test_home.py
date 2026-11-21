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


# ----------------------------------------------------------------------------- averages


def scored(conn, n: int, code: str, value: float, out_of: float, coefficient: float, average: float | None,
           *, significant: bool = True, period: str = "A001") -> None:
    conn.execute(
        "INSERT INTO school.grades (source, student_id, source_id, subject_code, subject, day, kind, title, raw_value, "
        "value, out_of, coefficient, period, class_average, significant, fingerprint) VALUES ('ecoledirecte', %s, %s, "
        "%s, %s, %s, 'Contrôle', 'Test', %s, %s, %s, %s, %s, %s, %s, 'fp')",
        (STUDENT, f"s-{code}-{n}-{value}", code, code, day(n), str(value), value, out_of, coefficient, period,
         average, significant),
    )


def test_the_averages_are_counted_as_ecole_directe_does(conn):
    # Maths: 15/20 coef 2 and 8/10 coef 1 (16 on 20): (30 + 16) / 3 = 15.33; the class (12 and 7/10).
    scored(conn, 3, "MATHS", 15, 20, 2, 12)
    scored(conn, 4, "MATHS", 8, 10, 1, 7)
    scored(conn, 5, "MATHS", 2, 20, 1, 11, significant=False)      # « non significative »: shown, not counted
    scored(conn, 2, "MATHS", 20, 20, 1, 10, period="A000")          # an earlier period
    scored(conn, 6, "FRANC", 12, 20, 1, 13)
    scored(conn, 7, "SVT", 18, 20, 1, None)                         # no class average: out of the general one
    page = home.compute(conn, STUDENT, day(13))
    subjects = {s.subject_code: s for s in page.grades}
    assert (subjects["MATHS"].average, subjects["MATHS"].class_average) == (15.33, 12.67)
    assert (subjects["SVT"].average, subjects["SVT"].class_average) == (18.0, None)
    # The general average: the subject averages added up, divided by their number (maths, French).
    assert page.averages.model_dump() == {"period": "", "average": 13.67, "class_average": 12.83, "subjects": 2}
    assert "Moyenne générale" in home.describe(page, day(13))


def test_no_grade_no_average(conn):
    page = home.compute(conn, STUDENT, day(13))
    assert page.averages is None and page.grades == []


# ------------------------------------------------------------- the teachers' files and links


PDF = b"%PDF-1.7 the lesson"
SHA = __import__("hashlib").sha256(PDF).hexdigest()


def attached(conn, tmp_path, *, size: int | None = None) -> None:
    """Thursday 12: a maths lesson with its PDF (downloaded), a photo (not yet) and a GeoGebra link."""
    path = tmp_path / f"{SHA}.pdf"
    path.write_bytes(PDF)
    slot(conn, 16, 10, "MATHS")
    lesson(conn, 12, "MATHS", "Symétrie centrale", documents='[{"source_id": "d1", "name": "cours.pdf", "size": 19}, '
                                                             '{"source_id": "d2", "name": "tableau.png", "size": 900}]')
    conn.execute("UPDATE school.lessons SET links = %s WHERE source_id = 'l-MATHS-12'",
                 ('[{"url": "https://www.geogebra.org/m/abc", "label": "Activité 1.1 – GeoGebra"}]',))
    for document, name, status, sha in [("d1", "cours.pdf", "downloaded", SHA), ("d2", "tableau.png", "pending", None)]:
        conn.execute(
            "INSERT INTO school.attachments (source, student_id, lesson_source_id, document_id, name, kind, size, status, "
            "sha256, path) VALUES ('ecoledirecte', %s, 'l-MATHS-12', %s, %s, 'FICHIER_CDT', %s, %s, %s, %s)",
            (STUDENT, document, name, size or 19, status, sha, str(path) if sha else None))


def test_a_lesson_brings_its_files_and_links(conn, tmp_path):
    attached(conn, tmp_path)
    page = home.compute(conn, STUDENT, day(13))
    [maths] = [s for s in page.suggestions if s.subject_code == "MATHS"]
    [shown] = maths.lessons
    assert [(d.name, d.sha256) for d in shown.documents] == [("cours.pdf", SHA), ("tableau.png", None)]
    assert [(link.url, link.label) for link in shown.links] == [("https://www.geogebra.org/m/abc",
                                                                 "Activité 1.1 – GeoGebra")]


class FilesClient:
    def __init__(self, missing: list[str]) -> None:
        self.missing, self.uploaded, self.pages = missing, [], []

    def missing_documents(self, sha256):
        return [sha for sha in sha256 if sha in self.missing]

    def upload_document(self, sha256, name, media_type, content):
        self.uploaded.append((sha256, name, media_type, content))

    def home(self, page):
        self.pages.append(page)


def test_the_files_go_online_with_the_page_once(conn, tmp_path):
    attached(conn, tmp_path)
    client = FilesClient(missing=[SHA])
    home.send(conn, client, STUDENT, day(13))
    assert client.uploaded == [(SHA, "cours.pdf", "application/pdf", PDF)]  # a plain name already
    client = FilesClient(missing=[])  # already online: not sent again
    home.send(conn, client, STUDENT, day(13))
    assert client.uploaded == [] and len(client.pages) == 1


def test_a_file_that_cannot_go_online_stays_on_ecole_directe(conn, tmp_path, monkeypatch):
    attached(conn, tmp_path)
    monkeypatch.setattr(home, "ONLINE_MAX_BYTES", 5)  # the PDF is now too large for one call
    page = home.compute(conn, STUDENT, day(13))
    assert all(d.sha256 is None for s in page.suggestions for lesson_ in s.lessons for d in lesson_.documents)
    (tmp_path / f"{SHA}.pdf").unlink()  # data/ wiped: the same
    monkeypatch.setattr(home, "ONLINE_MAX_BYTES", 4_000_000)
    client = FilesClient(missing=[SHA])
    page = home.send(conn, client, STUDENT, day(13))
    assert client.uploaded == [] and not home._files(page)


def test_a_file_name_travels_plain_in_the_signed_url():
    """Vercel re-encodes a query string with accents, spaces or brackets: the signature breaks."""
    assert home.plain_name("5-cours chap4 Symétries-26.pdf") == "5-cours-chap4-Symetries-26.pdf"
    assert home.plain_name("MI5-C10-exercices-a-completer (1).pdf") == "MI5-C10-exercices-a-completer-1.pdf"
    assert home.plain_name("Évolution des ponts.odp") == "Evolution-des-ponts.odp"
    assert home.plain_name("sans extension") == "sans-extension"
    assert home.plain_name("éé.png") == "ee.png" and home.plain_name("数学.png") == "document.png"

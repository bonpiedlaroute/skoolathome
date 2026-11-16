"""Translation of École Directe responses (anonymized real ones) to the common format."""

from __future__ import annotations

import base64
import copy
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from conftest import load
from skoolathome.collectors.ecoledirecte import mapping


def test_html_to_text():
    markup = "<p>Les&nbsp;fractions &eacute;gales</p><ul><li>ex. 12 p. 86</li><li>ex. 13</li></ul><br>Fin"
    assert mapping.html_to_text(markup) == "Les fractions égales\nex. 12 p. 86\nex. 13\nFin"
    assert mapping.html_to_text("<p> </p>") == ""


def test_timetable():
    slots = mapping.map_timetable(load("timetable"), "42")
    assert slots, "the fixture has slots"
    first = slots[0]
    assert first.source == "ecoledirecte" and first.student_id == "42"
    assert first.starts_at < first.ends_at
    assert first.kind == "COURS" and first.cancelled is False


def test_upcoming_homework_has_dates_and_flag_but_no_text():
    homework = mapping.map_upcoming_homework(load("homework"), "42")
    assert homework
    assert all(item.text == "" for item in homework)
    assert all(item.given_on is not None and item.given_on <= item.due_on for item in homework)
    assert {item.flagged_as_assessment for item in homework} <= {True, False}


def test_diary_gives_lessons_and_homework_with_text():
    response = load("diary-2026-11-03")
    lessons, homework = mapping.map_diary(response, "42")
    assert lessons and homework
    assert all(lesson.day == date(2026, 11, 3) for lesson in lessons)
    assert any(lesson.content.startswith("Contenu") for lesson in lessons)
    assert all(item.due_on == date(2026, 11, 3) and item.text for item in homework)


def test_lesson_without_content_nor_document_is_empty():
    response = load("diary-2026-11-03")
    subject = next(m for m in response["data"]["matieres"] if m.get("contenuDeSeance"))
    subject["contenuDeSeance"]["contenu"] = base64.b64encode(b"<p>&nbsp;</p>").decode()
    subject["contenuDeSeance"]["documents"] = []
    lessons, _ = mapping.map_diary(response, "42")
    assert next(l for l in lessons if l.source_id == str(subject["id"])).is_empty


def test_attached_documents_are_referenced():
    found = []
    for name in ("diary-2026-11-02", "diary-2026-11-03", "diary-2026-11-04"):
        lessons, homework = mapping.map_diary(load(name), "42")
        found += [d for item in lessons + homework for d in item.documents]
    assert found, "the fixtures contain attached files"
    assert all(d.kind == "FICHIER_CDT" and d.source_id for d in found)


def test_grades():
    grades = mapping.map_grades(load("grades"), "42")
    assert grades
    assert grades[0].value == Decimal("12.5")
    assert grades[0].kind in ("Contrôle", "Interrogation")


def test_grade_that_is_not_a_number():
    response = load("grades")
    response["data"]["notes"][0]["valeur"] = "Abs"
    grade = mapping.map_grades(response, "42")[0]
    assert grade.value is None and grade.raw_value == "Abs"


def test_merge_keeps_the_version_with_text():
    listed = mapping.map_upcoming_homework(load("homework"), "42")
    with_text = listed[0].model_copy(update={"text": "Réviser la leçon"})
    merged = mapping.merge_homework(listed, [with_text])
    assert len(merged) == len(listed)
    assert next(m for m in merged if m.source_id == with_text.source_id).text == "Réviser la leçon"


@pytest.mark.parametrize(
    ("fixture", "translate", "remove"),
    [
        ("timetable", mapping.map_timetable, lambda r: r["data"][0].pop("isAnnule")),
        ("homework", mapping.map_upcoming_homework, lambda r: next(iter(r["data"].values()))[0].pop("interrogation")),
        ("diary-2026-11-03", mapping.map_diary, lambda r: r["data"]["matieres"][0].pop("codeMatiere")),
        ("grades", mapping.map_grades, lambda r: r["data"]["notes"][0].pop("typeDevoir")),
    ],
)
def test_missing_field_is_a_clear_validation_error(fixture, translate, remove):
    response = copy.deepcopy(load(fixture))
    removed = remove(response)
    assert removed is not None
    with pytest.raises(ValidationError, match="Field required"):
        translate(response, "42")


def test_grade_with_the_class_average_lowest_and_highest():
    response = load("grades")
    note = response["data"]["notes"][0]
    note.update(moyenneClasse="8.45", minClasse="1.50", maxClasse="10.00")
    grade = mapping.map_grades(response, "42")[0]
    assert (grade.class_average, grade.class_min, grade.class_max) == (Decimal("8.45"), Decimal("1.50"), Decimal("10.00"))


def test_grade_without_class_figures_yet():
    response = load("grades")
    response["data"]["notes"][0].update(moyenneClasse="", minClasse="", maxClasse="")
    grade = mapping.map_grades(response, "42")[0]
    assert grade.class_average is None and grade.class_min is None and grade.class_max is None

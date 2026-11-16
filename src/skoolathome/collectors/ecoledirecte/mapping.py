"""Translate raw École Directe responses into the common format."""

from __future__ import annotations

import base64
import html
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from skoolathome.collectors.ecoledirecte import raw
from skoolathome.schema import Document, Grade, Homework, Lesson, TimetableSlot

SOURCE = "ecoledirecte"

_BLOCK_END = re.compile(r"</(p|div|li|h[1-6]|tr)>|<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")


def html_to_text(markup: str) -> str:
    """Plain text of a small HTML fragment: one line per block, entities decoded."""
    text = _BLOCK_END.sub("\n", markup)
    text = html.unescape(_TAG.sub("", text))
    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n", text).strip()


def decode_content(encoded: str) -> str:
    """École Directe sends rich text as base64-encoded HTML."""
    if not encoded:
        return ""
    return html_to_text(base64.b64decode(encoded).decode("utf-8"))


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ".").strip())
    except InvalidOperation:
        return None


def _documents(items: list[raw.RawDocument]) -> tuple[Document, ...]:
    return tuple(
        Document(source_id=str(d.id), name=d.libelle, kind=d.type, size=d.taille) for d in items
    )


def map_timetable(response: dict, student_id: str) -> list[TimetableSlot]:
    return [
        TimetableSlot(
            source=SOURCE,
            source_id=str(slot.id),
            student_id=student_id,
            subject_code=slot.codeMatiere,
            subject=slot.matiere,
            teacher=slot.prof,
            room=slot.salle,
            starts_at=datetime.fromisoformat(slot.start_date),
            ends_at=datetime.fromisoformat(slot.end_date),
            kind=slot.typeCours,
            cancelled=slot.isAnnule,
        )
        for slot in raw.RawTimetable.model_validate(response).data
    ]


def map_upcoming_homework(response: dict, student_id: str) -> list[Homework]:
    """The list of homework to come: no text here, only dates and the test flag."""
    found = []
    for due_on, items in raw.RawUpcoming.model_validate(response).data.items():
        for item in items:
            found.append(
                Homework(
                    source=SOURCE,
                    source_id=str(item.idDevoir),
                    student_id=student_id,
                    subject_code=item.codeMatiere,
                    subject=item.matiere,
                    given_on=date.fromisoformat(item.donneLe),
                    due_on=date.fromisoformat(due_on),
                    text="",
                    done=item.effectue,
                    flagged_as_assessment=item.interrogation,
                )
            )
    return found


def map_diary(response: dict, student_id: str) -> tuple[list[Lesson], list[Homework]]:
    """One day of the homework diary: lesson contents, and homework due that day with its text."""
    data = raw.RawDiary.model_validate(response).data
    day = date.fromisoformat(data.date)
    lessons, homework = [], []
    for subject in data.matieres:
        if subject.contenuDeSeance is not None:
            content = decode_content(subject.contenuDeSeance.contenu)
            documents = _documents(subject.contenuDeSeance.documents)
            lessons.append(
                Lesson(
                    source=SOURCE,
                    source_id=str(subject.id),
                    student_id=student_id,
                    day=day,
                    subject_code=subject.codeMatiere,
                    subject=subject.matiere,
                    teacher=subject.nomProf,
                    content=content,
                    is_empty=not content and not documents,
                    documents=documents,
                )
            )
        if subject.aFaire is not None:
            homework.append(
                Homework(
                    source=SOURCE,
                    source_id=str(subject.aFaire.idDevoir),
                    student_id=student_id,
                    subject_code=subject.codeMatiere,
                    subject=subject.matiere,
                    given_on=date.fromisoformat(subject.aFaire.donneLe),
                    due_on=day,
                    text=decode_content(subject.aFaire.contenu),
                    done=subject.aFaire.effectue,
                    flagged_as_assessment=subject.interrogation,
                    documents=_documents(subject.aFaire.documents),
                )
            )
    return lessons, homework


def map_grades(response: dict, student_id: str) -> list[Grade]:
    return [
        Grade(
            source=SOURCE,
            source_id=str(grade.id),
            student_id=student_id,
            subject_code=grade.codeMatiere,
            subject=grade.libelleMatiere,
            day=date.fromisoformat(grade.date),
            kind=grade.typeDevoir,
            title=grade.devoir,
            raw_value=grade.valeur,
            value=_decimal(grade.valeur),
            out_of=_decimal(grade.noteSur),
            coefficient=_decimal(grade.coef),
            period=grade.codePeriode,
            class_average=_decimal(grade.moyenneClasse),
            class_min=_decimal(grade.minClasse),
            class_max=_decimal(grade.maxClasse),
        )
        for grade in raw.RawGrades.model_validate(response).data.notes
    ]


def merge_homework(listed: list[Homework], detailed: list[Homework]) -> list[Homework]:
    """Same homework seen in the list and in a diary day: the diary version has the text."""
    by_id = {item.source_id: item for item in listed}
    by_id.update({item.source_id: item for item in detailed})
    return sorted(by_id.values(), key=lambda item: (item.due_on, item.source_id))

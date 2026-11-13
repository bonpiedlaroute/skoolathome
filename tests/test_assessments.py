"""Test detection on hand-annotated cases, traps included. Recall must be 100 %."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from skoolathome.rules.assessments import detect, is_assessment_slot
from skoolathome.schema import Homework, TimetableSlot


def homework(text: str, flagged: bool = False) -> Homework:
    return Homework(
        source="test", source_id="1", student_id="42", subject_code="MATHS", subject="MATHEMATIQUES",
        given_on=date(2026, 11, 10), due_on=date(2026, 11, 17), text=text, done=False,
        flagged_as_assessment=flagged,
    )


# (text, flag ticked by the teacher, expected answer, expected reason)
CASES = [
    # --- real tests: none may be missed
    ("Apprendre la leçon sur les fractions.", True, "yes", "flag"),
    ("Contrôle sur le chapitre 3 : les fractions.", False, "yes", "keywords"),
    ("CONTROLE chapitre 2", False, "yes", "keywords"),
    ("Réviser pour l'évaluation de jeudi.", False, "yes", "keywords"),
    ("Evaluation de lecture", False, "yes", "keywords"),
    ("Interro sur les verbes irréguliers", False, "yes", "keywords"),
    ("Interrogation orale sur la leçon précédente", False, "yes", "keywords"),
    ("DS de physique : chapitres 1 et 2", False, "yes", "keywords"),
    ("Devoir surveillé n°2", False, "yes", "keywords"),
    ("Bilan de fin de séquence", False, "yes", "keywords"),
    ("Petit test de vocabulaire", False, "yes", "keywords"),
    ("Dictée préparée n°4", False, "yes", "keywords"),
    ("Récitation du poème", False, "yes", "keywords"),
    ("Signer le contrôle. Réviser pour l'interro de lundi.", False, "yes", "keywords"),
    # --- traps: the keyword is there, but there is no test to prepare
    ("Faire signer le contrôle.", False, "no", ""),
    ("Correction du contrôle : refaire l'exercice 3.", False, "no", ""),
    ("Corriger l'évaluation en vert.", False, "no", ""),
    ("Rapporter le contrôle signé.", False, "no", ""),
    # --- traps: a keyword hidden inside another word
    ("Exercices 12 et 13 p. 86.", False, "no", ""),
    ("Lire le texte p. 42 et répondre aux questions.", False, "no", ""),
    ("Terminer la carte des reliefs.", False, "no", ""),
    ("Apporter le protestant... euh, le livre de contes.", False, "no", ""),
    ("Lire les deux premiers chapitres.", False, "no", ""),
    # --- listed without its text yet
    ("", False, "unknown", ""),
]


@pytest.mark.parametrize(("text", "flagged", "expected", "reason"), CASES)
def test_annotated_cases(text, flagged, expected, reason):
    assert detect(homework(text, flagged)) == (expected, reason)


def test_recall_is_total():
    real = [case for case in CASES if case[2] == "yes"]
    found = [case for case in real if detect(homework(case[0], case[1]))[0] == "yes"]
    assert len(found) == len(real)


def slot(code: str, subject: str, cancelled: bool = False) -> TimetableSlot:
    return TimetableSlot(
        source="test", source_id="1", student_id="42", subject_code=code, subject=subject, teacher="",
        room="", starts_at=datetime(2026, 11, 17, 8), ends_at=datetime(2026, 11, 17, 10), kind="COURS",
        cancelled=cancelled,
    )


def test_supervised_test_slot_in_the_timetable():
    assert is_assessment_slot(slot("DST", "DEVOIR SUR TABLE"))
    assert is_assessment_slot(slot("X", "Devoir surveillé"))
    assert not is_assessment_slot(slot("DST", "DEVOIR SUR TABLE", cancelled=True))
    assert not is_assessment_slot(slot("MATHS", "MATHEMATIQUES"))
    assert not is_assessment_slot(slot("HI-GE", "HISTOIRE-GEOGRAPHIE"))

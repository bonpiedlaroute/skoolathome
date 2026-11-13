"""Detect upcoming tests in homework, with rules only (no model).

Goal: never miss a test (recall first). A doubtful case is reported as a test;
the model will take over the ambiguous ones in T19.
"""

from __future__ import annotations

import re
import unicodedata

from skoolathome.schema import Assessment, Homework, TimetableSlot

# Matched on lower-cased text without accents.
_KEYWORDS = re.compile(
    r"\b("
    r"controles?|evaluations?|eval|interros?|interrogations?|"
    r"ds|dst|devoirs? surveilles?|devoir sur table|bilans?|tests?|"
    r"dictees?|examens?|brevet blanc|oral note|recitations?"
    r")\b"
)

# The keyword is there, but the test is behind us or is not a test.
_NOT_A_TEST = re.compile(
    r"\b("
    r"correction (du |de la |de l'|des )(controle|evaluation|interro|interrogation|ds|dst|bilan|test|dictee)|"
    r"corriger (le |la |l'|les )(controle|evaluation|interro|interrogation|ds|dst|bilan|test|dictee)|"
    r"(faire )?signer (le |la |l'|les )(controle|evaluation|interro|interrogation|ds|dst|bilan|test|dictee|copie)|"
    r"(rapporter|rendre) (le |la |l'|les )(controle|evaluation|interro|interrogation|ds|dst|copie) signe"
    r")"
)

_ASSESSMENT_SLOT = re.compile(r"\b(ds|dst|devoirs? surveilles?|devoir sur table|examens?|brevet blanc)\b")


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower().replace("\u2019", "'"))
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def detect(homework: Homework) -> tuple[Assessment, str]:
    """(is it a test?, why). The reason is "flag", "keywords" or ""."""
    if homework.flagged_as_assessment:
        return "yes", "flag"
    if not homework.text:
        return "unknown", ""  # listed without its text yet
    text = _normalize(homework.text)
    if _KEYWORDS.search(_NOT_A_TEST.sub(" ", text)):
        return "yes", "keywords"
    return "no", ""


def is_assessment_slot(slot: TimetableSlot) -> bool:
    """A timetable slot that is itself a test, e.g. a supervised test period."""
    if slot.cancelled:
        return False
    label = _normalize(f"{slot.subject_code} {slot.subject}")
    return bool(_ASSESSMENT_SLOT.search(label))

"""Subjects as the agent sees them: French names, the ones without content to revise, the ones
whose numbers the code recomputes. Codes are École Directe's."""

from __future__ import annotations

from datetime import date

SUBJECT_LABELS = {
    "MATHS": "Mathématiques", "FRANC": "Français", "AGL1": "Anglais", "HI-GE": "Histoire-géographie",
    "PH-CH": "Physique-chimie", "SVT": "SVT", "TECHN": "Technologie", "LCALA": "Latin",
    "EDMUS": "Éducation musicale", "A-PLA": "Arts plastiques", "ESP": "Espagnol", "ESP2": "Espagnol",
}
# Nothing to revise from what is written there: sport, homework help, class life, study hours,
# supervised test periods (the test itself is announced in the homework).
EXCLUDED_SUBJECTS = frozenset({"EPS", "AEAID", "VIE-S", "VICLA", "ETU", "DST", "CREL"})
# Numbers are made up by the model and recomputed by the code (ARCHITECTURE.md §5.4).
COMPUTATIONAL_SUBJECTS = frozenset({"MATHS"})

_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def label(code: str, raw: str = "") -> str:
    return SUBJECT_LABELS.get(code, raw.capitalize() if raw else code)


def fr_day(day: date) -> str:
    """« mardi 4/11 »: how the family reads a date."""
    return f"{_DAYS[day.weekday()]} {day.day}/{day.month}"


def weekday(day: date) -> str:
    return _DAYS[day.weekday()]

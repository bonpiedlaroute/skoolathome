"""Subjects as the agent sees them: French names, the ones without content to revise, the ones
whose numbers the code recomputes. Codes are École Directe's."""

from __future__ import annotations

import unicodedata
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
# What a parent types, without accents, to subject codes.
_ALIASES = {
    "maths": "MATHS", "math": "MATHS", "mathematiques": "MATHS", "francais": "FRANC", "histoire": "HI-GE",
    "geographie": "HI-GE", "geo": "HI-GE", "histoire-geo": "HI-GE", "histoire-geographie": "HI-GE", "hg": "HI-GE",
    "anglais": "AGL1", "physique": "PH-CH", "physique-chimie": "PH-CH", "chimie": "PH-CH", "svt": "SVT",
    "techno": "TECHN", "technologie": "TECHN", "latin": "LCALA", "espagnol": "ESP", "musique": "EDMUS",
    "arts-plastiques": "A-PLA",
}


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower().strip())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").replace(" ", "-")


def code_of(text: str) -> str | None:
    """« maths », « Histoire-géo », « HI-GE » → the École Directe code; None when unknown."""
    name = _plain(text)
    by_code = {code.lower(): code for code in SUBJECT_LABELS}
    by_label = {_plain(value): code for code, value in SUBJECT_LABELS.items()}
    return _ALIASES.get(name) or by_code.get(name) or by_label.get(name)


def label(code: str, raw: str = "") -> str:
    return SUBJECT_LABELS.get(code, raw.capitalize() if raw else code)


def fr_day(day: date) -> str:
    """« mardi 4/11 »: how the family reads a date."""
    return f"{_DAYS[day.weekday()]} {day.day}/{day.month}"


def weekday(day: date) -> str:
    return _DAYS[day.weekday()]

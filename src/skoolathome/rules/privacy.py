"""No surname online (ARCHITECTURE.md §17): what leaves the laptop shows a teacher as « M. D. ».

The laptop keeps the full name as École Directe writes it (« M. DURAND P. »): the evening plan
compares it with the timetable. A rule changed in the PWA comes back with « M. D. », and gets its
full name back here, when only one teacher of the timetable fits.
"""

from __future__ import annotations

import re

import psycopg

TITLES = {"m", "mr", "mme", "mlle", "monsieur", "madame", "mademoiselle"}


def teachers(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT teacher FROM school.timetable_slots WHERE teacher <> '' UNION "
                        "SELECT DISTINCT teacher FROM school.lessons WHERE teacher <> ''").fetchall()
    return sorted(row[0].strip() for row in rows)


def _words(name: str) -> list[str]:
    return [w for w in re.split(r"[\s.]+", name) if w]


_SHOWN_TITLES = {"m": "M.", "mr": "M.", "monsieur": "M.", "mme": "Mme", "madame": "Mme", "mlle": "Mlle",
                 "mademoiselle": "Mlle"}


def masked(name: str) -> str:
    """« M. DURAND P. » → « M. D. »: the title and the first letter of the surname."""
    words = _words(name)
    title = _SHOWN_TITLES.get(words[0].lower(), "") if words else ""
    rest = words[1:] if title else words  # « Mme M. »: the second M is the initial, not a title
    surname = rest[0] if rest else ""
    return " ".join(part for part in (title, f"{surname[0].upper()}." if surname else "") if part) or "le prof"


def public_text(text: str, names: list[str]) -> str:
    """Every surname of `names` replaced by its initial, whatever its case."""
    for name in names:
        for word in _words(name):
            if len(word) >= 3 and word.lower() not in TITLES:
                text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", f"{word[0].upper()}.", text, flags=re.IGNORECASE)
    return text.replace("..", ".")


def public_rule(body: dict | None, names: list[str]) -> dict | None:
    """A rule as it may go online: the teacher masked, the parent's sentence without a surname."""
    if body is None:
        return None
    found = dict(body)
    if found.get("teacher"):
        found["teacher"] = masked(found["teacher"])
    if isinstance(found.get("origin"), dict) and found["origin"].get("text"):
        found["origin"] = {**found["origin"], "text": public_text(found["origin"]["text"], names)}
    return found


def restore(body: dict, names: list[str]) -> dict:
    """A rule back from the PWA: « M. D. » becomes the timetable's name, if only one fits."""
    teacher = body.get("teacher")
    if not teacher or teacher in names:
        return body
    fits = [name for name in names if masked(name) == masked(teacher)]
    return {**body, "teacher": fits[0]} if len(fits) == 1 else body

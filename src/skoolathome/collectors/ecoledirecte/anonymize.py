"""Turn the real responses saved by the probe into test fixtures with no real data.

Safe by construction: a value is kept only if its key is in a short allow-list of
structural fields (codes, dates, types). Every other string is replaced, every id
is renumbered, rich-text contents are replaced by a placeholder.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

# Structural values: needed by the mapping, and not personal.
KEEP_STRINGS = {"codeMatiere", "typeCours", "typeDevoir", "codePeriode", "type", "coef", "noteSur"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")
CONTENT_KEYS = {"contenu"}
MAX_ITEMS = 4  # enough variety, small files


class Anonymizer:
    def __init__(self) -> None:
        self._ids: dict[int, int] = {}
        self._texts = 0

    def _id(self, value: int) -> int:
        if value == 0:
            return 0
        return self._ids.setdefault(value, 1000 + len(self._ids))

    def _string(self, key: str, value: str) -> str:
        if not value or key in KEEP_STRINGS or DATE.match(value):
            return value
        if key in CONTENT_KEYS:
            self._texts += 1
            return base64.b64encode(f"<p>Contenu {self._texts}</p>".encode()).decode()
        if key == "valeur":
            return "12,5"
        return f"{key}-x"

    def clean(self, value: object, key: str = "") -> object:
        if isinstance(value, dict):
            cleaned = {}
            for index, (k, v) in enumerate(value.items()):
                if DATE.match(k):  # homework list: keyed by due date
                    if index < MAX_ITEMS:
                        cleaned[k] = self.clean(v, k)
                elif k not in ("token", "photo"):
                    cleaned[k] = self.clean(v, k)
            return cleaned
        if isinstance(value, list):
            return [self.clean(item, key) for item in value[:MAX_ITEMS]]
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, int):
            return self._id(value) if key.lower().startswith("id") or key == "id" else value
        if isinstance(value, str):
            return self._string(key, value)
        return value


def anonymize_directory(source: Path, target: Path, student_id: int) -> list[str]:
    """Writes timetable, homework, grades and up to three diary days. Returns the file names."""
    target.mkdir(parents=True, exist_ok=True)
    anonymizer = Anonymizer()
    written = []
    names = [f"{student_id}-timetable", f"{student_id}-homework", f"{student_id}-grades"]
    diaries = sorted(p.stem for p in source.glob(f"{student_id}-diary-*.json") if ".readable" not in p.name)
    with_content = [n for n in diaries if json.loads((source / f"{n}.json").read_text())["data"]["matieres"]]
    for name in names + with_content[:3]:
        path = source / f"{name}.json"
        if not path.exists():
            continue
        cleaned = anonymizer.clean(json.loads(path.read_text()))
        out_name = name.replace(f"{student_id}-", "", 1) + ".json"
        (target / out_name).write_text(json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n")
        written.append(out_name)
    return written

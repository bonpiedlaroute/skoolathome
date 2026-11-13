"""École Directe as a SchoolSource: calls, validation, translation to the common format."""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import httpx
from pydantic import ValidationError

from skoolathome.collectors.ecoledirecte import mapping
from skoolathome.collectors.ecoledirecte.client import (
    API_VERSION,
    EcoleDirecteClient,
    EcoleDirecteError,
    TwoFactorRequired,
)
from skoolathome.schema import Homework, Lesson, SchoolSnapshot

CODE_FORBIDDEN = 403

# The École Directe module each kind of call needs (see probe.py: a call to a
# disabled module is answered with 403 and a warning, so it is never made).
REQUIRED_MODULE = {
    "timetable": "EDT",
    "homework": "CAHIER_DE_TEXTES",
    "diary": "CAHIER_DE_TEXTES",
    "grades": "NOTES",
}

# (kind, ref, raw response) -> None ; called before validation, to keep the raw response.
RawSink = Callable[[str, str, dict], None]


class AccessRefused(Exception):
    """École Directe refused a call (403): stop everything, never insist."""


class EcoleDirecteSource:
    name = mapping.SOURCE

    def __init__(
        self,
        client: EcoleDirecteClient,
        student: dict,
        *,
        today: date | None = None,
        pause: float = 1.0,
        on_raw: RawSink | None = None,
    ) -> None:
        self._client = client
        self._student = student
        self._student_id = str(student["id"])
        self._today = today or date.today()
        self._pause = pause
        self._on_raw = on_raw
        self.errors: list[dict] = []

    def fetch(self, start: date, end: date) -> SchoolSnapshot:
        """Lessons from `start` to today; homework and timetable up to `end`; all grades."""
        sid = self._student["id"]
        slots = self._get("timetable", "", lambda: self._client.timetable(sid, start, end), mapping.map_timetable) or []
        listed = self._get("homework", "", lambda: self._client.upcoming_homework(sid), mapping.map_upcoming_homework) or []
        grades = self._get("grades", "", lambda: self._client.grades(sid), mapping.map_grades) or []

        lessons: list[Lesson] = []
        detailed: list[Homework] = []
        for day in self._diary_days(start, end, slots, listed):
            result = self._get(
                "diary", day.isoformat(), lambda: self._client.day_diary(sid, day), mapping.map_diary
            )
            if result:
                lessons += result[0]
                detailed += result[1]

        return SchoolSnapshot(
            student_id=self._student_id,
            slots=tuple(slots),
            lessons=tuple(lessons),
            homework=tuple(mapping.merge_homework(listed, detailed)),
            grades=tuple(grades),
        )

    def _diary_days(self, start: date, end: date, slots: list, listed: list[Homework]) -> list[date]:
        """Past days that had lessons (their content), and coming days with homework (its text)."""
        past = {
            slot.starts_at.date()
            for slot in slots
            if slot.kind == "COURS" and start <= slot.starts_at.date() <= self._today
        }
        coming = {item.due_on for item in listed if self._today <= item.due_on <= end}
        return sorted(past | coming)

    def _get(self, kind: str, ref: str, call: Callable[[], dict], translate: Callable):
        """One call: keep the raw response, validate, translate. An error is recorded, not raised,
        except a refusal (403) or a two-factor request, which stop the collection."""
        module = REQUIRED_MODULE[kind]
        if module not in self._student["modules"]:
            self._error(kind, ref, f"module {module} disabled for this student: call skipped")
            return None
        try:
            response = call()
        except TwoFactorRequired:
            raise
        except EcoleDirecteError as error:
            if error.code == CODE_FORBIDDEN:
                raise AccessRefused(f"{kind} {ref}: {error}") from error
            self._error(kind, ref, str(error))
            return None
        except httpx.HTTPError as error:
            self._error(kind, ref, f"{type(error).__name__}: {error}")
            return None
        finally:
            if self._pause:
                time.sleep(self._pause)
        if self._on_raw is not None:
            self._on_raw(kind, ref, {k: v for k, v in response.items() if k != "token"})
        try:
            return translate(response, self._student_id)
        except (ValidationError, ValueError, KeyError) as error:
            self._error(kind, ref, f"validation: {_short(error)}")
            return None

    def _error(self, kind: str, ref: str, message: str) -> None:
        self.errors.append({"kind": kind, "ref": ref, "error": message})


def _short(error: Exception) -> str:
    """Validation errors without the input values: no student data in the run log."""
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors(include_input=False)[:5]
        )
    return f"{type(error).__name__}: {error}"


class ReplayClient:
    """Stands in for EcoleDirecteClient, reading the files saved by the probe (T01).

    Lets the collector run on real responses without calling École Directe.
    """

    version = API_VERSION

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self.accounts: list[dict] = self._load("accounts") or []

    def _load(self, name: str) -> dict | list | None:
        path = self._directory / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def connect(self, answer=None) -> list[dict]:
        return self.accounts

    def close(self) -> None:
        pass

    def students(self) -> list[dict]:
        client = EcoleDirecteClient("", "")
        client.accounts = self.accounts
        try:
            return client.students()
        finally:
            client.close()

    def _saved(self, name: str, empty: dict | list) -> dict:
        return self._load(name) or {"code": 210, "data": empty}

    def timetable(self, student_id: int, start: date, end: date) -> dict:
        return self._saved(f"{student_id}-timetable", [])

    def upcoming_homework(self, student_id: int) -> dict:
        return self._saved(f"{student_id}-homework", {})

    def day_diary(self, student_id: int, day: date) -> dict:
        return self._saved(f"{student_id}-diary-{day.isoformat()}", {"date": day.isoformat(), "matieres": []})

    def grades(self, student_id: int) -> dict:
        return self._saved(f"{student_id}-grades", {"notes": []})


def first_window(today: date) -> tuple[date, date]:
    """First collection: six weeks of lessons, for the revisions."""
    return today - timedelta(weeks=6), today + timedelta(days=14)


def daily_window(today: date) -> tuple[date, date]:
    """Daily collection: the last two days again, to catch contents written late."""
    return today - timedelta(days=2), today + timedelta(days=14)

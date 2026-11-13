"""Common format: what the agent knows about school, whatever the school tool.

Collectors translate their own responses into these models. Nothing here is
specific to École Directe.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    def fingerprint(self) -> str:
        """Hash of the content: a changed fingerprint means the source changed the item."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Document(Model):
    """A file attached by a teacher. Downloaded later (T25), only referenced here."""

    source_id: str
    name: str
    kind: str  # the source's own file type, needed to download it
    size: int | None = None


class TimetableSlot(Model):
    source: str
    source_id: str
    student_id: str
    subject_code: str
    subject: str
    teacher: str
    room: str
    starts_at: datetime
    ends_at: datetime
    kind: str  # "COURS", "CONGE"… as given by the source
    cancelled: bool


class Lesson(Model):
    """What was done in class for one subject on one day."""

    source: str
    source_id: str
    student_id: str
    day: date
    subject_code: str
    subject: str
    teacher: str
    content: str  # plain text, HTML removed
    is_empty: bool
    documents: tuple[Document, ...] = ()


class Homework(Model):
    source: str
    source_id: str
    student_id: str
    subject_code: str
    subject: str
    given_on: date | None
    due_on: date
    text: str  # plain text; empty when the source only lists the homework
    done: bool
    flagged_as_assessment: bool  # the teacher ticked the source's own "test" box
    documents: tuple[Document, ...] = ()


class Grade(Model):
    source: str
    source_id: str
    student_id: str
    subject_code: str
    subject: str
    day: date
    kind: str  # "Contrôle", "Interrogation"… as given by the source
    title: str
    raw_value: str  # as displayed: "15,5", "Abs", ""
    value: Decimal | None  # None when the grade is not a number
    out_of: Decimal | None
    coefficient: Decimal | None
    period: str


class SchoolSnapshot(Model):
    """Everything one collection brought back for one student."""

    student_id: str
    slots: tuple[TimetableSlot, ...] = ()
    lessons: tuple[Lesson, ...] = ()
    homework: tuple[Homework, ...] = ()
    grades: tuple[Grade, ...] = ()


Assessment = Literal["yes", "no", "unknown"]

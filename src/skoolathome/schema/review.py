"""The weekly review (ARCHITECTURE.md §14.4, T24) and the parent's annotations. Shared with the API.

What goes online: counts, rates, reasons, subjects and the sheets to annotate. Never a lesson's text.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# What the parent can say is wrong with a question: the judge's criteria (agent/judge.py).
Problem = Literal["faithful", "answer_correct", "level_ok", "useful"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Indicator(Strict):
    name: str
    label: str              # « Fiches bloquées par les garde-fous »
    value: str              # as shown: « 2 sur 12 (17 %) »
    alert: bool = False     # above its alert threshold: shown first, highlighted
    detail: str = Field(default="", max_length=400)


class SheetToAnnotate(Strict):
    id: str
    subject: str
    title: str


class WeeklyReview(Strict):
    week: str = Field(pattern=r"^\d{4}-W\d{2}$")  # ISO week, e.g. 2026-W46
    start: date
    end: date
    title: str = Field(max_length=120)
    body: str = Field(max_length=600)
    indicators: list[Indicator] = Field(default=[], max_length=30)
    to_annotate: list[SheetToAnnotate] = Field(default=[], max_length=10)


class ItemAnnotation(Strict):
    item_id: str = Field(max_length=20)
    ok: bool                       # the question is right and useful
    problem: Problem | None = None  # when not ok: which criterion fails
    comment: str = Field(default="", max_length=300)


class Annotation(Strict):
    """The parent's verdict on every question of a sheet (one call per sheet)."""

    sheet_id: str
    items: list[ItemAnnotation] = Field(min_length=1, max_length=20)

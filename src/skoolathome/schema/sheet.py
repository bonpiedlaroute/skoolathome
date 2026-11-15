"""A revision sheet, as published to the API and displayed by the PWA (ARCHITECTURE.md §9.4).

Shared by the generator (laptop) and the API (Vercel): one definition, one validation.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SheetType = Literal["exercises", "lesson_recap", "quick_quiz", "test_revision"]
ItemType = Literal["choice", "number", "fraction", "short_answer", "fill_blank", "matching", "open"]
CheckMode = Literal["exact", "variants", "number", "fraction", "choice", "self_assessment"]

# Which correction modes make sense for each question type.
ALLOWED_MODES: dict[str, set[str]] = {
    "choice": {"choice"},
    "number": {"number"},
    "fraction": {"fraction"},
    "short_answer": {"exact", "variants"},
    "fill_blank": {"exact", "variants"},
    "matching": {"exact"},
    "open": {"self_assessment"},
}
LIST_ANSWER_TYPES = {"fill_blank", "matching"}
DECIMAL = re.compile(r"^\d+([.,]\d+)?$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Quote(Strict):
    source_id: str
    excerpt: str  # copied word for word from the source


class Check(Strict):
    mode: CheckMode
    tolerance: str = "0"  # a decimal written as text, so Python and the browser read the same value

    @model_validator(mode="after")
    def _tolerance_is_a_decimal(self) -> Check:
        if not DECIMAL.match(self.tolerance):
            raise ValueError("tolerance must be a non-negative decimal such as '0.01'")
        return self


class ParentGuide(Strict):
    explanation: str = ""
    common_mistake: str = ""
    oral_followup: str = ""


class RuleRef(Strict):
    id: str
    version: int


class Item(Strict):
    id: str
    type: ItemType
    prompt: str  # for fill_blank, each blank is written ___
    quote: Quote | None = None
    answer: str | list[str] = ""
    variants: list[str | list[str]] = []  # other accepted answers
    check: Check
    expression: str | None = None  # maths: the answer is recomputed from it (T16)
    choices: list[str] = []  # choice: the options ; matching: the right-hand column
    left: list[str] = []  # matching: the left-hand column
    why_wrong: dict[str, str] = {}  # wrong choice -> explanation
    hints: list[str] = []  # more and more precise
    solution: str = ""
    parent_guide: ParentGuide | None = None
    concept: str = ""
    difficulty: int = Field(default=1, ge=1, le=3)

    @model_validator(mode="after")
    def _is_consistent(self) -> Item:
        if self.check.mode not in ALLOWED_MODES[self.type]:
            raise ValueError(f"check mode '{self.check.mode}' is not valid for a '{self.type}' item")
        wants_list = self.type in LIST_ANSWER_TYPES
        if wants_list != isinstance(self.answer, list):
            raise ValueError(f"a '{self.type}' item needs {'a list of answers' if wants_list else 'a single answer'}")
        if any(isinstance(v, list) != wants_list for v in self.variants):
            raise ValueError("variants must have the same shape as the answer")
        if self.type == "choice":
            if len(self.choices) < 2 or self.answer not in self.choices:
                raise ValueError("a choice item needs at least two choices, one of them being the answer")
        if self.type == "matching":
            if not self.left or len(self.left) != len(self.answer) or set(self.answer) - set(self.choices):
                raise ValueError("a matching item needs one answer per left entry, all taken from choices")
        if self.type == "fill_blank" and self.prompt.count("___") != len(self.answer):
            raise ValueError("a fill_blank item needs one answer per ___ in the prompt")
        if self.type != "open" and self.answer in ("", []):
            raise ValueError("answer is required")
        return self


class Example(Strict):
    statement: str
    solution: str
    quote: Quote | None = None  # the lesson passage it comes from


class Recap(Strict):
    """The « cours en clair » of a lesson_recap sheet (ARCHITECTURE.md §9.4), read before its questions."""

    summary: str  # in a 5e student's words
    example: Example
    key_points: list[str] = Field(min_length=1, max_length=5)


class Sheet(Strict):
    id: str
    student: str
    subject: str
    title: str
    type: SheetType
    rule: RuleRef | None = None
    sources: list[str] = []
    # How each source is shown to the family: {"lesson:2026-11-04:HI-GE": "ton cours du mardi 4/11"}.
    source_labels: dict[str, str] = {}
    visible_from: date
    due_by: date | None = None
    estimated_minutes: int = Field(ge=1, le=120)
    trace_id: str | None = None
    recap: Recap | None = None
    items: list[Item] = Field(min_length=1)

    @model_validator(mode="after")
    def _item_ids_are_unique(self) -> Sheet:
        ids = [item.id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("item ids must be unique within a sheet")
        if self.type == "lesson_recap" and self.recap is None:
            raise ValueError("a lesson_recap sheet needs its recap")
        return self

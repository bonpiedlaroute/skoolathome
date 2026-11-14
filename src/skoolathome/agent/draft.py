"""What the model writes, and how the code turns it into a Sheet.

The model fills a smaller shape than Sheet: no ids, dates, sources or correction modes. The
code adds those, so that a small model has less to get wrong. Its JSON schema is given to
Ollama as the `format`: the output can only be JSON of this shape.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from skoolathome.schema.sheet import Check, Item, ParentGuide, Quote, Sheet

DraftItemType = Literal["choice", "number", "fraction", "short_answer", "fill_blank", "open"]

# Minutes a 5e student needs, per question type: the code estimates the duration, not the model.
MINUTES = {"choice": 0.75, "number": 1.5, "fraction": 1.5, "short_answer": 1.0, "fill_blank": 1.5,
           "matching": 2.0, "open": 3.0}
AVERAGE_MINUTES = 1.25  # to choose how many questions to ask for


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WrongChoice(Draft):
    choice: str
    explanation: str


class DraftGuide(Draft):
    explanation: str
    common_mistake: str
    oral_followup: str


class DraftItem(Draft):
    type: DraftItemType
    prompt: str
    quote: str = Field(description="copied word for word from the lesson")
    answer: str | list[str] = Field(description="a list of words for fill_blank, a text otherwise")
    variants: list[str] = []
    choices: list[str] = []
    expression: str | None = Field(default=None, description="maths: the computation giving the answer")
    why_wrong: list[WrongChoice] = []
    hints: list[str] = []
    solution: str
    parent_guide: DraftGuide
    concept: str
    difficulty: int = Field(ge=1, le=3)


class DraftSheet(Draft):
    title: str
    items: list[DraftItem] = Field(min_length=1, max_length=20)


def item_count(target_minutes: int) -> int:
    return max(3, min(12, round(target_minutes / AVERAGE_MINUTES)))


def estimated_minutes(items: list[Item]) -> int:
    return max(1, math.ceil(sum(MINUTES[item.type] for item in items)))


def _check(item: DraftItem) -> Check:
    if item.type in ("choice", "number", "fraction"):
        return Check(mode=item.type)
    if item.type == "open":
        return Check(mode="self_assessment")
    return Check(mode="variants" if item.variants and item.type == "short_answer" else "exact")


def to_item(index: int, item: DraftItem, source_id: str) -> Item:
    """Raises pydantic.ValidationError when the item is inconsistent (format guard)."""
    answer = item.answer
    if item.type == "fill_blank" and isinstance(answer, str):
        answer = [answer]  # one blank, written as a plain text
    if item.type != "fill_blank" and isinstance(answer, list):
        answer = answer[0] if len(answer) == 1 else ", ".join(answer)
    return Item(
        id=f"q{index}",
        type=item.type,
        prompt=item.prompt,
        quote=Quote(source_id=source_id, excerpt=item.quote) if item.quote.strip() else None,
        answer=answer,
        variants=item.variants if item.type == "short_answer" else [],
        check=_check(item),
        expression=item.expression or None,
        choices=item.choices if item.type == "choice" else [],
        why_wrong={w.choice: w.explanation for w in item.why_wrong if item.type == "choice"},
        hints=item.hints[:3],
        solution=item.solution,
        parent_guide=ParentGuide(**item.parent_guide.model_dump()),
        concept=item.concept,
        difficulty=item.difficulty,
    )


def to_sheet(
    draft: DraftSheet,
    *,
    sheet_id: str,
    subject: str,
    source_id: str,
    kind: str,
    visible_from: date,
    trace_id: str,
    student: str = "eleve",
) -> Sheet:
    """Raises pydantic.ValidationError when the draft cannot make a valid Sheet (format guard)."""
    items = [to_item(index, item, source_id) for index, item in enumerate(draft.items, start=1)]
    return Sheet(
        id=sheet_id,
        student=student,  # never a name online
        subject=subject,
        title=draft.title,
        type=kind,
        sources=[source_id],
        visible_from=visible_from,
        estimated_minutes=estimated_minutes(items),
        trace_id=trace_id,
        items=items,
    )

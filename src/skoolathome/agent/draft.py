"""What the model writes, and how the code turns it into a Sheet.

The model fills a smaller shape than Sheet: no ids, dates, sources or correction modes. The
code adds those, so that a small model has less to get wrong. Its JSON schema is given to
Ollama as the `format`: the output can only be JSON of this shape.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rapidfuzz import fuzz

from skoolathome.schema.sheet import Check, Example, Item, ParentGuide, Quote, Recap, RuleRef, Sheet

DraftItemType = Literal["choice", "number", "fraction", "short_answer", "fill_blank", "open"]
QuizItemType = Literal["choice", "number", "short_answer", "fill_blank"]  # an oral quiz: short answers

# Minutes a 5e student needs, per question type: the code estimates the duration, not the model.
# An open question is a written answer: a few sentences, a problem in several steps.
MINUTES = {"choice": 0.75, "number": 1.5, "fraction": 1.5, "short_answer": 1.0, "fill_blank": 1.5,
           "matching": 2.0, "open": 4.0}
AVERAGE_MINUTES = 1.25  # to choose how many questions to ask for
# From 20 minutes, a sheet to prepare a test, often done on paper: more written exercises, so
# more time per question. 45 minutes are 18 questions.
LONG_MINUTES = 20
LONG_AVERAGE_MINUTES = 2.5
MAX_ITEMS = 24
RECAP_READING_MINUTES = 3  # reading the « cours en clair » before its questions
QUIZ_ITEMS = 5
RECAP_ITEMS = 2
RECAP_KEY_POINTS = 3


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


class QuizItem(DraftItem):
    type: QuizItemType


class DraftSheet(Draft):
    title: str
    items: list[DraftItem] = Field(min_length=1, max_length=20)


class DraftQuiz(Draft):
    """quick_quiz: five questions as in an oral quiz, short answers (T20)."""

    title: str
    items: list[QuizItem] = Field(min_length=QUIZ_ITEMS, max_length=QUIZ_ITEMS)


class DraftExample(Draft):
    statement: str
    quote: str = Field(description="the lesson passage the example comes from, word for word")
    solution: str


class DraftRecap(Draft):
    """lesson_recap: the lesson in a 5e student's words, a solved example, three points to
    remember and two check questions (ARCHITECTURE.md §9.4)."""

    title: str
    summary: str
    example: DraftExample
    key_points: list[str] = Field(min_length=RECAP_KEY_POINTS, max_length=RECAP_KEY_POINTS)
    items: list[DraftItem] = Field(min_length=RECAP_ITEMS, max_length=RECAP_ITEMS)


class AssessmentVerdict(Draft):
    """The model's reading of an ambiguous homework (T19): is a test announced?"""

    is_assessment: bool
    date: str | None = Field(default=None, description="the test date, YYYY-MM-DD, if written")
    scope: str = Field(default="", description="what the test is on, in a few words")
    confidence: float = Field(ge=0, le=1)


def item_count(target_minutes: int) -> int:
    if target_minutes >= LONG_MINUTES:
        return max(3, min(MAX_ITEMS, round(target_minutes / LONG_AVERAGE_MINUTES)))
    return max(3, min(12, round(target_minutes / AVERAGE_MINUTES)))


def estimated_minutes(items: list[Item], recap: Recap | None = None) -> int:
    reading = RECAP_READING_MINUTES if recap is not None else 0
    return max(1, math.ceil(reading + sum(MINUTES[item.type] for item in items)))


def locate(excerpt: str, sources: dict[str, str]) -> str:
    """The source the excerpt was copied from: the code finds it, the model does not have to say it.
    With no good match, the first source: the citation guard then says the excerpt is not there."""
    from skoolathome.agent.guards import flat  # the same normalization as the guard

    if not sources:
        return ""
    target = flat(excerpt)
    scores = {key: fuzz.partial_ratio(target, flat(text)) for key, text in sources.items()}
    return max(scores, key=lambda key: scores[key])


def _check(item: DraftItem) -> Check:
    if item.type in ("choice", "number", "fraction"):
        return Check(mode=item.type)
    if item.type == "open":
        return Check(mode="self_assessment")
    return Check(mode="variants" if item.variants and item.type == "short_answer" else "exact")


def _quote(excerpt: str, sources: dict[str, str]) -> Quote | None:
    return Quote(source_id=locate(excerpt, sources), excerpt=excerpt) if excerpt.strip() else None


def to_item(index: int, item: DraftItem, sources: dict[str, str]) -> Item:
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
        quote=_quote(item.quote, sources),
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


def to_items(drafted: list[DraftItem], sources: dict[str, str]) -> tuple[list[Item], list[str]]:
    """The questions that make valid items, and why the others do not: one question badly built
    no longer throws the whole sheet away (exercises and test revisions, whose length varies)."""
    items, reasons = [], []
    for index, item in enumerate(drafted, start=1):
        try:
            items.append(to_item(index, item, sources))
        except ValidationError as error:
            detail = "; ".join(e["msg"].removeprefix("Value error, ") for e in error.errors()[:2])
            reasons.append(f"question « {item.prompt[:60]} » écartée, mal construite : {detail}")
    return items, reasons


def numbered(items: list[Item]) -> list[Item]:
    """q1, q2, … in their order: the questions kept from an earlier attempt, then the new ones."""
    return [item.model_copy(update={"id": f"q{index}"}) for index, item in enumerate(items, start=1)]


def with_items(sheet: Sheet, items: list[Item]) -> Sheet:
    """The same sheet with fewer questions (those the checks refused are left out)."""
    cited = {item.quote.source_id for item in items if item.quote}
    used = [key for key in sheet.sources if key in cited] or sheet.sources
    return Sheet.model_validate({
        **sheet.model_dump(), "items": [item.model_dump() for item in items], "sources": used,
        "source_labels": {key: label for key, label in sheet.source_labels.items() if key in used},
        "estimated_minutes": estimated_minutes(items, sheet.recap),
    })


def to_sheet(
    draft: DraftSheet | DraftQuiz | DraftRecap | None,
    *,
    task: dict,
    sources: dict[str, str],
    labels: dict[str, str],
    trace_id: str,
    student: str = "eleve",
    items: list[Item] | None = None,
    title: str = "",
) -> Sheet:
    """Raises pydantic.ValidationError when the draft cannot make a valid Sheet (format guard).
    With `items`, those questions (already checked one by one) instead of the draft's."""
    if items is None:
        items = [to_item(index, item, sources) for index, item in enumerate(draft.items, start=1)]
    recap = None
    if isinstance(draft, DraftRecap):
        recap = Recap(
            summary=draft.summary,
            example=Example(statement=draft.example.statement, solution=draft.example.solution,
                            quote=_quote(draft.example.quote, sources)),
            key_points=draft.key_points,
        )
    cited = {item.quote.source_id for item in items if item.quote}
    if recap and recap.example.quote:
        cited.add(recap.example.quote.source_id)
    used = [key for key in sources if key in cited] or list(sources)
    return Sheet(
        id=task["id"],
        student=student,  # never a name online
        subject=task["subject"],
        title=title or draft.title,
        type=task["kind"],
        rule=RuleRef(id=task["rule"], version=task["rule_version"]) if task.get("rule_version") else None,
        sources=used,
        source_labels={key: labels[key] for key in used if key in labels},
        visible_from=date.fromisoformat(task["visible_from"]),
        due_by=date.fromisoformat(task["due_by"]) if task.get("due_by") else None,
        estimated_minutes=estimated_minutes(items, recap),
        trace_id=trace_id,
        recap=recap,
        items=items,
    )

"""Long sheets (a test preparation of 30 minutes to an hour): written in parts, the questions
badly built or refused left out one by one, the next attempt completing those kept."""

from __future__ import annotations

import itertools

from test_guards import CHOICE, NUMBER, item

from skoolathome.agent import draft, guards, making, prompts
from skoolathome.agent.draft import DraftSheet
from skoolathome.agent.llm import ModelError

LESSONS = {
    f"lesson:2026-11-{day}:MATHS": (
        "Un nombre premier est un nombre entier qui a exactement deux diviseurs : 1 et lui-même. "
        "Par exemple, 2, 3, 5, 7 et 11 sont des nombres premiers. Pour décomposer un nombre en produit "
        "de facteurs premiers, on le divise successivement par 2, puis 3, puis 5. " * 4)
    for day in (10, 12, 17)
}
TASK = {"id": "2026-11-26-maths-test-j3", "subject": "Mathématiques", "kind": "test_revision",
        "rule": "before_test", "rule_version": 2, "visible_from": "2026-11-23", "due_by": "2026-11-26",
        "target_minutes": 45, "computational": True, "items": None}
PROMPT = prompts.Prompt("sheet-test-revision", None, [
    {"role": "user", "content": "Fiche : {{item_count}} questions, {{target_minutes}} min.\n{{context}}\n"
                                "<cours>\n{{sources}}\n</cours>{{feedback}}"}])
BAD_BLANKS = item(type="fill_blank", prompt="Un nombre ___ a ___ diviseurs.", answer=["premier"],
                  quote="Un nombre premier est un nombre entier qui a exactement deux diviseurs")


class Generator:
    """Answers each call with the next reply: a DraftSheet, a function making one (new questions
    at each call), or an Exception to raise. The last reply answers every call after it."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.asked: list[str] = []

    def structured(self, role, messages, schema, callbacks):
        self.asked.append(messages[-1]["content"])
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        reply = reply() if callable(reply) else reply
        if isinstance(reply, Exception):
            raise reply
        return reply


FACTORS = itertools.count(2)


def calculation() -> dict:
    """A question never asked before in the test run: other numbers make another exercise."""
    a, b = next(FACTORS), next(FACTORS)
    return item(type="number", prompt=f"Calcule {a} × {b}.", quote="on le divise successivement par 2, puis 3, puis 5",
                answer=str(a * b), variants=[], expression=f"{a} * {b}")


def part(count: int, *extra: dict):
    """A part of `count` new questions at each call."""
    return lambda: DraftSheet.model_validate({"title": "Préparer le contrôle",
                                              "items": [calculation() for _ in range(count)] + list(extra)})


def test_45_minutes_are_18_questions_written_in_three_parts_one_lesson_set_each():
    assert draft.item_count(45) == 18 and draft.item_count(30) == 12 and draft.item_count(8) == 6
    model = Generator(part(6))
    written = making.write(model, PROMPT, TASK, LESSONS, {}, "", [], "t")
    assert written.items == 18 and len(model.asked) == 3 and len(written.sheet.items) == 18
    assert [i.id for i in written.sheet.items] == [f"q{n}" for n in range(1, 19)]
    # Each part reads its own lesson, and is told which questions the sheet already has.
    assert ["lesson:2026-11-10:MATHS" in a for a in model.asked] == [True, False, False]
    assert "Partie 3 : 6 questions" in model.asked[2] and "Questions déjà dans la fiche" in model.asked[2]


def test_a_question_badly_built_or_a_part_lost_does_not_lose_the_sheet():
    model = Generator(part(5, BAD_BLANKS), ModelError("le serveur du modèle a renvoyé une erreur"), part(6))
    written = making.write(model, PROMPT, TASK, LESSONS, {}, "", [], "t")
    assert len(written.sheet.items) == 11  # 5 + 0 + 6
    assert any("écartée, mal construite" in d for d in written.dropped)
    assert any("le serveur du modèle" in d for d in written.dropped)
    # 11 of 18: not enough before the last attempt; the next one completes them.
    settled = making.settle(written.sheet, [], TASK, written.items, last=False, notes=written.dropped)
    assert settled.sheet is None and len(settled.kept["items"]) == 11
    assert "écris-en 7 autres" in settled.failures[-1]
    again = Generator(part(7))
    completed = making.write(again, PROMPT, TASK, LESSONS, {}, "", settled.failures, "t", settled.kept)
    assert len(again.asked) == 2 and len(completed.sheet.items) == 18  # 7 missing: two parts
    first = written.sheet.items[0].prompt
    assert "q1 :" not in again.asked[0] and first in again.asked[0]  # no stale numbers, listed to avoid repeats


def test_the_refused_questions_are_left_out_when_enough_remain():
    model = Generator(part(6))
    sheet = making.write(model, PROMPT, TASK, LESSONS, {}, "", [], "t").sheet
    refused = guards.GuardResult("citation", False, [f"q{n} : la citation n'est pas dans le cours." for n in (2, 5, 9)])
    settled = making.settle(sheet, [refused, guards.GuardResult("duration", True)], TASK, 18, last=False)
    assert settled.sheet is not None and len(settled.sheet.items) == 15 and settled.dropped == ["q2", "q5", "q9"]
    assert settled.sheet.estimated_minutes < sheet.estimated_minutes
    # The judge's refusals too; at the last attempt, three good questions make a shorter sheet.
    judge = guards.GuardResult("judge", False, [f"q{n} : (correcteur) faux." for n in range(1, 16)])
    assert making.settle(sheet, [judge], TASK, 18, last=False).sheet is None
    assert len(making.settle(sheet, [judge], TASK, 18, last=True).sheet.items) == 3


def test_a_short_sheet_keeps_its_old_rules():
    short = {**TASK, "kind": "exercises", "target_minutes": 8}
    model = Generator(DraftSheet.model_validate({"title": "t", "items": [item(), NUMBER, CHOICE]}))
    written = making.write(model, PROMPT, short, LESSONS, {}, "", [], "t")
    assert len(model.asked) == 1 and len(written.sheet.items) == 3
    # One question of three refused: two left, not enough (70 % of three): written again.
    refused = guards.GuardResult("maths", False, ["q2 : 2 * 2 * 3 * 5 = 60, pas 61."])
    settled = making.settle(written.sheet, [refused], short, written.items, last=False)
    assert settled.sheet is None and [i["prompt"] for i in settled.kept["items"]] == [item()["prompt"], CHOICE["prompt"]]
    # A quiz has a fixed shape: any problem sends it back whole.
    quiz = making.settle(written.sheet, [refused], {**short, "kind": "quick_quiz"}, 5, last=False)
    assert quiz.sheet is None and quiz.kept is None


def test_a_question_asked_again_by_another_part_is_left_out():
    """Seen in a 45-minute English sheet: three questions three times each, the end reworded."""
    tourist = item(type="open", prompt="Imagine que tu es un touriste à New York City. Écris 3 phrases en anglais "
                                       "pour décrire ce que tu vois. Utilise : 1. un verbe de goût 2. look.",
                   answer="I love the skyline.", variants=[])
    again = {**tourist, "prompt": "Imagine que tu es un touriste à New York City. Écris 3 phrases en anglais pour "
                                  "décrire ce que tu vois et ce que tu ressens. Utilise au moins un verbe de goût."}
    model = Generator(part(5, tourist), part(4, again, NUMBER), part(5, NUMBER))  # three parts of six
    written = making.write(model, PROMPT, TASK, LESSONS, {}, "", [], "t")
    prompts = [i.prompt for i in written.sheet.items]
    assert prompts.count(tourist["prompt"]) == 1 and again["prompt"] not in prompts
    assert prompts.count(NUMBER["prompt"]) == 1 and len(prompts) == 16
    assert sum("déjà dans la fiche" in d for d in written.dropped) == 2
    # The same calculation with other numbers is another exercise.
    assert not making.repeated(draft.to_item(1, DraftSheet.model_validate({"title": "t", "items": [calculation()]}).items[0], LESSONS),
                               written.sheet.items)

"""T16: each deterministic guard, with cases that must pass and cases that must fail."""

from __future__ import annotations

from datetime import date
from fractions import Fraction

import pytest

from skoolathome.agent import draft, guards
from skoolathome.schema.sheet import Sheet

SOURCE = "lesson:2026-11-12:MATHS"
LESSON = (
    "Un nombre premier est un nombre entier qui a exactement deux diviseurs : 1 et lui-même. "
    "Par exemple, 2, 3, 5, 7 et 11 sont des nombres premiers. Pour décomposer un nombre en produit "
    "de facteurs premiers, on le divise successivement par 2, puis 3, puis 5. La bataille d'Alésia a "
    "eu lieu en 52 av. J.-C."
)
SOURCES = {SOURCE: LESSON}


def item(**fields) -> dict:
    base = {
        "type": "short_answer", "prompt": "Comment appelle-t-on un nombre qui a exactement deux diviseurs ?",
        "quote": "Un nombre premier est un nombre entier qui a exactement deux diviseurs",
        "answer": "un nombre premier", "variants": ["premier"], "choices": [], "expression": None,
        "why_wrong": [], "hints": ["Relis la définition."], "solution": "C'est un nombre premier.",
        "parent_guide": {"explanation": "", "common_mistake": "", "oral_followup": ""},
        "concept": "nombres premiers", "difficulty": 1,
    }
    return {**base, **fields}


def sheet(*items: dict, minutes_target: int = 8) -> Sheet:
    written = draft.DraftSheet.model_validate({"title": "Essai", "items": list(items)})
    return draft.to_sheet(written, sheet_id="s", subject="Mathématiques", source_id=SOURCE, kind="exercises",
                          visible_from=date(2026, 11, 14), trace_id="t")


NUMBER = item(type="number", prompt="Calcule 2 × 2 × 3 × 5.", quote="on le divise successivement par 2, puis 3, puis 5",
              answer="60", variants=[], expression="2 * 2 * 3 * 5")
CHOICE = item(type="choice", prompt="Lequel est premier ?", quote="2, 3, 5, 7 et 11 sont des nombres premiers",
              answer="7", variants=[], choices=["4", "7", "9"],
              why_wrong=[{"choice": "4", "explanation": "4 = 2 × 2"}, {"choice": "9", "explanation": "9 = 3 × 3"}])


# ----------------------------------------------------------------------- format


def test_the_draft_becomes_a_valid_sheet():
    built = sheet(item(), NUMBER, CHOICE)
    assert [i.id for i in built.items] == ["q1", "q2", "q3"]
    assert [i.check.mode for i in built.items] == ["variants", "number", "choice"]
    assert built.items[0].quote.source_id == SOURCE and built.trace_id == "t"
    assert built.estimated_minutes == 4  # 1 + 1.5 + 0.75, rounded up


def test_an_inconsistent_draft_is_refused():
    with pytest.raises(ValueError):
        sheet(item(type="choice", choices=["a", "b"], answer="c"))  # the answer is not a choice
    with pytest.raises(ValueError):
        sheet(item(type="fill_blank", prompt="Un ___ a deux ___.", answer=["nombre premier"]))  # 2 blanks, 1 answer


# -------------------------------------------------------------------- citations


def test_quotes_found_in_the_lesson_pass():
    assert guards.citations(sheet(item(), NUMBER), SOURCES).ok


def test_small_differences_are_tolerated():
    loose = item(quote="un nombre premier est un nombre entier, qui a exactement deux diviseurs")
    assert guards.citations(sheet(loose), SOURCES).ok


@pytest.mark.parametrize("quote", [
    "Un nombre premier est un nombre pair qui a exactement trois diviseurs",  # changed words
    "Les nombres premiers sont infinis, comme l'a montré Euclide",  # not in the lesson at all
    "deux diviseurs",  # too short to prove anything
])
def test_invented_or_too_short_quotes_fail(quote):
    result = guards.citations(sheet(item(quote=quote)), SOURCES)
    assert not result.ok and result.reasons[0].startswith("q1")


def test_a_quote_of_an_unknown_source_fails():
    assert not guards.citations(sheet(item()), {"lesson:other": LESSON}).ok


# --------------------------------------------------------------- answer in quote


def test_answer_in_the_quote_passes():
    date_item = item(type="number", prompt="En quelle année av. J.-C. a eu lieu la bataille d'Alésia ?",
                     quote="La bataille d'Alésia a eu lieu en 52 av. J.-C.", answer="52", variants=[])
    assert guards.answers_in_quotes(sheet(item(), date_item), computational=False).ok


def test_answer_missing_from_the_quote_fails():
    wrong = item(answer="un nombre composé", variants=[])
    result = guards.answers_in_quotes(sheet(wrong), computational=False)
    assert not result.ok and "nombre compose" in result.reasons[0].lower().replace("é", "e")


def test_a_date_must_match_exactly():
    wrong_date = item(type="number", prompt="Année ?", quote="La bataille d'Alésia a eu lieu en 52 av. J.-C.",
                      answer="5", variants=[])
    assert not guards.answers_in_quotes(sheet(wrong_date), computational=False).ok


def test_computed_answers_are_left_to_the_maths_guard():
    assert guards.answers_in_quotes(sheet(NUMBER, CHOICE), computational=True).ok


# ------------------------------------------------------------------------- maths


@pytest.mark.parametrize(("expression", "value"), [
    ("3/4 + 1/6", Fraction(11, 12)),
    ("2 × (3 + 4)", Fraction(14)),
    ("0,1 + 0,2", Fraction(3, 10)),  # exact: no binary float
    ("28 / 7 * 2", Fraction(8)),
    ("7 : 2", Fraction(7, 2)),
])
def test_expressions_are_computed_exactly(expression, value):
    assert guards.evaluate(expression) == value


@pytest.mark.parametrize("expression", ["__import__('os')", "x + 1", "2 ** 999999", "1/0", "", "2(3)"])
def test_anything_but_arithmetic_is_refused(expression):
    with pytest.raises((ValueError, SyntaxError, TypeError)):
        guards.evaluate(expression)


def test_maths_pass_when_the_answer_is_the_computed_value():
    fraction = item(type="fraction", prompt="Calcule 3/4 + 1/6.", answer="11/12", variants=[], expression="3/4 + 1/6")
    equivalent = item(type="fraction", prompt="Calcule 1/4 + 1/4.", answer="2/4", variants=[], expression="1/4 + 1/4")
    assert guards.maths(sheet(NUMBER, fraction, equivalent), computational=True).ok


def test_maths_fail_on_a_wrong_result():
    wrong = item(type="number", prompt="Calcule.", answer="61", variants=[], expression="2 * 2 * 3 * 5")
    result = guards.maths(sheet(wrong), computational=True)
    assert not result.ok and "= 60, pas 61" in result.reasons[0]


def test_a_computation_without_expression_fails_in_maths():
    bare = item(type="number", prompt="Calcule 6 × 7.", answer="42", variants=[])
    assert not guards.maths(sheet(bare), computational=True).ok
    assert guards.maths(sheet(bare), computational=False).ok  # a date in history needs none


# -------------------------------------------------------------------- correction


def test_a_consistent_corrector_passes():
    assert guards.correction(sheet(item(), NUMBER, CHOICE)).ok


def test_two_right_choices_fail():
    twice = item(**{**CHOICE, "choices": ["7", "4", " 7 "], "why_wrong": []})
    result = guards.correction(sheet(twice))
    assert not result.ok and "2 propositions justes" in result.reasons[0]


def test_an_explanation_for_the_right_answer_fails():
    confused = item(**{**CHOICE, "why_wrong": [{"choice": "7", "explanation": "?"}]})
    assert not guards.correction(sheet(confused)).ok


def test_a_number_answer_that_is_not_a_number_fails():
    words = item(type="number", prompt="Combien ?", answer="soixante", variants=[], expression=None)
    assert not guards.correction(sheet(words)).ok


# ---------------------------------------------------------------------- duration


def test_duration_within_target_passes():
    assert guards.duration(sheet(item(), NUMBER, CHOICE), target_minutes=8).ok


def test_a_sheet_too_long_fails():
    opens = [item(type="open", prompt=f"Explique {n}.", answer="", variants=[]) for n in range(5)]
    result = guards.duration(sheet(*opens), target_minutes=8)  # 5 × 3 = 15 min
    assert not result.ok and "15 min pour 8" in result.reasons[0]


def test_run_all_names_every_guard():
    names = [r.name for r in guards.run_all(sheet(item()), SOURCES, 8, computational=True)]
    assert names == ["citation", "answer_in_quote", "maths", "correction", "duration"]

"""Deterministic guards (ARCHITECTURE.md §9.5): the code checks everything it can check.

Each guard returns its verdict and, when it fails, reasons written for the model: they go
back into the prompt of the next generation, and become the abstention reason after the last.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

from rapidfuzz import fuzz
from sympy import Rational
from sympy.parsing.sympy_parser import parse_expr, rationalize, standard_transformations

from skoolathome.correction import check, normalize, parse_number
from skoolathome.schema.sheet import Item, Sheet

CITATION_THRESHOLD = 90  # rapidfuzz partial_ratio, 0 to 100: small differences of punctuation pass
MIN_QUOTE_CHARS = 15
ANSWER_THRESHOLD = 90
DURATION_TOLERANCE = 1.25  # a sheet may last up to 25 % more than its target

# Questions whose answer must be read in the quote: course questions, definitions, dates.
# Computations (with an expression) and open questions are checked otherwise.
ANSWER_IN_QUOTE_TYPES = {"choice", "short_answer", "fill_blank", "number"}

_EXPRESSION = re.compile(r"^[0-9\s+\-*/×÷:(),.]+$")
_PUNCTUATION = re.compile(r"[^\w\s]")


@dataclass
class GuardResult:
    name: str
    ok: bool
    reasons: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        return {"name": self.name, "ok": self.ok, "reasons": self.reasons}


def _flat(text: str) -> str:
    """Compare texts without accents, case, punctuation or extra spaces."""
    return " ".join(_PUNCTUATION.sub(" ", normalize(text)).split())


# --------------------------------------------------------------------- citations


def citations(sheet: Sheet, sources: dict[str, str], threshold: int = CITATION_THRESHOLD) -> GuardResult:
    """Every quote exists in the source it cites, small differences tolerated."""
    reasons = []
    flat_sources = {key: _flat(text) for key, text in sources.items()}
    for item in sheet.items:
        if item.quote is None:
            reasons.append(f"{item.id} : pas de citation du cours.")
            continue
        excerpt = _flat(item.quote.excerpt)
        source = flat_sources.get(item.quote.source_id)
        if source is None:
            reasons.append(f"{item.id} : la citation renvoie à une source inconnue.")
        elif len(excerpt) < MIN_QUOTE_CHARS:
            reasons.append(f"{item.id} : citation trop courte, recopie une phrase entière du cours.")
        elif fuzz.partial_ratio(excerpt, source) < threshold:
            reasons.append(f"{item.id} : la citation « {item.quote.excerpt[:80]} » n'est pas dans le cours, "
                           "recopie-la mot pour mot.")
    return GuardResult("citation", not reasons, reasons)


def _answers(item: Item) -> list[str]:
    return item.answer if isinstance(item.answer, list) else [item.answer]


def _found(answer: str, quote: str) -> bool:
    flat_answer, flat_quote = _flat(answer), _flat(quote)
    if not flat_answer:
        return False
    if len(flat_answer) <= 4 or flat_answer.replace(" ", "").isdigit():  # short words and dates: exact
        return re.search(rf"(?<!\w){re.escape(flat_answer)}(?!\w)", flat_quote) is not None
    return fuzz.partial_ratio(flat_answer, flat_quote) >= ANSWER_THRESHOLD


def answers_in_quotes(sheet: Sheet, computational: bool) -> GuardResult:
    """For course questions, definitions and dates, the answer is in the quote."""
    reasons = []
    for item in sheet.items:
        if item.type not in ANSWER_IN_QUOTE_TYPES or item.expression or item.quote is None:
            continue
        if computational and item.type in ("number", "choice"):
            continue  # maths: computed answers, checked by the maths guard
        missing = [a for a in _answers(item) if not _found(a, item.quote.excerpt)]
        if missing:
            reasons.append(f"{item.id} : la réponse « {', '.join(missing)[:60]} » n'est pas dans la citation : "
                           "cite le passage du cours qui la contient.")
    return GuardResult("answer_in_quote", not reasons, reasons)


# -------------------------------------------------------------------------- maths


def evaluate(expression: str) -> Fraction:
    """Exact value of an arithmetic expression. Only digits and + - * / ( ): never evaluate names."""
    if len(expression) > 120 or not _EXPRESSION.match(expression) or "**" in expression:
        # no power: 9**9**9 alone would keep the CPU busy for hours
        raise ValueError("seulement des nombres, + - * / et des parenthèses")
    text = expression.replace("×", "*").replace("÷", "/").replace(":", "/").replace(",", ".")
    # Decimals become exact fractions: 0.1 is 1/10, not the nearest binary float.
    value = parse_expr(text, transformations=standard_transformations + (rationalize,), evaluate=True)
    if not value.is_number or not value.is_finite:
        raise ValueError("le calcul ne donne pas un nombre")
    rational = Rational(value)
    return Fraction(int(rational.p), int(rational.q))


def maths(sheet: Sheet, computational: bool) -> GuardResult:
    """sympy recomputes each expression; the result must equal the answer."""
    reasons = []
    for item in sheet.items:
        if item.expression:
            try:
                expected = evaluate(item.expression)
            except (ValueError, SyntaxError, TypeError, ZeroDivisionError) as error:
                reasons.append(f"{item.id} : expression « {item.expression} » illisible ({error}).")
                continue
            given = parse_number(item.answer) if isinstance(item.answer, str) else None
            if given is None:
                reasons.append(f"{item.id} : la réponse « {item.answer} » n'est pas un nombre.")
            elif given != expected:
                shown = f"{expected.numerator}/{expected.denominator}" if expected.denominator != 1 else str(expected.numerator)
                reasons.append(f"{item.id} : {item.expression} = {shown}, pas {item.answer}.")
        elif computational and item.type in ("number", "fraction"):
            reasons.append(f"{item.id} : calcul sans `expression`, le résultat ne peut pas être vérifié.")
    return GuardResult("maths", not reasons, reasons)


# --------------------------------------------------------------------- correction


def correction(sheet: Sheet) -> GuardResult:
    """The automatic correction accepts the answer and every variant, refuses every wrong choice,
    and a choice question has exactly one right answer. Same code as the server, same vectors as
    the PWA (tests/correction/*.json)."""
    reasons = []
    for item in sheet.items:
        mode, tolerance = item.check.mode, item.check.tolerance
        if mode == "self_assessment":
            continue
        if not check(mode, item.answer, item.answer, variants=item.variants, tolerance=tolerance):
            reasons.append(f"{item.id} : la correction refuse la réponse elle-même « {item.answer} ».")
        for variant in item.variants:
            if not check(mode, item.answer, variant, variants=item.variants, tolerance=tolerance):
                reasons.append(f"{item.id} : la variante « {variant} » serait refusée.")
        if item.type == "choice":
            accepted = [c for c in item.choices if check(mode, item.answer, c)]
            if len(accepted) != 1:
                reasons.append(f"{item.id} : {len(accepted)} propositions justes au lieu d'une "
                               "(propositions en double ?).")
            for wrong in item.why_wrong:
                if wrong not in item.choices or check(mode, item.answer, wrong):
                    reasons.append(f"{item.id} : l'explication d'erreur « {wrong} » ne correspond à "
                                   "aucune mauvaise proposition.")
        if item.type in ("number", "fraction") and parse_number(str(item.answer)) is None:
            reasons.append(f"{item.id} : la réponse « {item.answer} » n'est pas un nombre.")
    return GuardResult("correction", not reasons, reasons)


# ----------------------------------------------------------------------- duration


def duration(sheet: Sheet, target_minutes: int) -> GuardResult:
    if sheet.estimated_minutes > target_minutes * DURATION_TOLERANCE:
        return GuardResult("duration", False, [
            f"fiche trop longue : {sheet.estimated_minutes} min pour {target_minutes} visées, "
            "mets moins de questions ou des questions plus courtes."])
    return GuardResult("duration", True)


def run_all(sheet: Sheet, sources: dict[str, str], target_minutes: int, computational: bool) -> list[GuardResult]:
    return [
        citations(sheet, sources),
        answers_in_quotes(sheet, computational),
        maths(sheet, computational),
        correction(sheet),
        duration(sheet, target_minutes),
    ]

"""Deterministic correction of an answer. No network, no model.

The browser has the same logic in web/src/lib/check.ts. Both are run against
tests/correction/*.json and must give exactly the same results.
"""

from __future__ import annotations

import re
import unicodedata
from fractions import Fraction

_SPACES = re.compile(r"\s+")
_DECIMAL = re.compile(r"^([+-]?)(\d+)(?:[.,](\d+))?$")
_FRACTION = re.compile(r"^([+-]?\d+)/([+-]?\d+)$")


def normalize(text: str) -> str:
    """Ignore accents, case, extra spaces and the kind of apostrophe."""
    text = text.replace("’", "'").replace("œ", "oe").replace("Œ", "OE")
    decomposed = unicodedata.normalize("NFD", text)
    without_accents = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return _SPACES.sub(" ", without_accents).strip().lower()


def parse_number(text: str) -> Fraction | None:
    """'3', '-3', '0,75', '1 250', '3/4' as an exact value; None if it is not a number."""
    compact = _SPACES.sub("", text).replace("−", "-")  # U+2212: the typographic minus
    decimal = _DECIMAL.match(compact)
    if decimal:
        sign, whole, fractional = decimal.groups()
        fractional = fractional or ""
        value = Fraction(int(whole + fractional), 10 ** len(fractional))
        return -value if sign == "-" else value
    fraction = _FRACTION.match(compact)
    if fraction and int(fraction.group(2)) != 0:
        return Fraction(int(fraction.group(1)), int(fraction.group(2)))
    return None


def _same_text(response: object, accepted: list) -> bool:
    if isinstance(response, list):
        if not all(isinstance(part, str) for part in response):
            return False
        given = [normalize(part) for part in response]
        return any(isinstance(a, list) and given == [normalize(part) for part in a] for a in accepted)
    if not isinstance(response, str):
        return False
    return any(isinstance(a, str) and normalize(response) == normalize(a) for a in accepted)


def check(mode: str, answer: object, response: object, *, variants: list | None = None, tolerance: str = "0") -> bool:
    """Is `response` right? `mode` is the item's check mode."""
    if mode == "self_assessment":
        return response is True
    if mode in ("exact", "variants", "choice"):
        accepted = [answer] + (list(variants or []) if mode != "choice" else [])
        return _same_text(response, accepted)
    if mode in ("number", "fraction"):
        if not isinstance(response, str) or not isinstance(answer, str):
            return False
        given, expected = parse_number(response), parse_number(answer)
        if given is None or expected is None:
            return False
        if mode == "fraction":
            return given == expected
        return abs(given - expected) <= (parse_number(tolerance) or 0)
    raise ValueError(f"unknown check mode: {mode}")

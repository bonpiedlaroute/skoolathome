"""Progress on a sheet, item by item, from the answers stored by the API.

The browser applies the same rules in web/src/lib/progress.ts.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_SOLO_TRIES = 2  # a wrong answer can be tried again once


@dataclass
class ItemProgress:
    solo_right: int = 0
    solo_wrong: int = 0
    rating: str | None = None  # latest rating given together: good | partial | bad

    def as_json(self, item_type: str) -> dict:
        return {
            "solo_right": self.solo_right,
            "solo_wrong": self.solo_wrong,
            "rating": self.rating,
            "solo_done": solo_done(self, item_type),
            "to_review": to_review(self, item_type),
        }


def solo_done(p: ItemProgress, item_type: str) -> bool:
    if item_type == "open":
        return p.solo_right + p.solo_wrong > 0  # the student compared with the model answer
    return p.solo_right > 0 or p.solo_wrong >= MAX_SOLO_TRIES


def to_review(p: ItemProgress, item_type: str) -> bool:
    """To see together: open questions, and questions missed twice, until rated together."""
    if p.rating is not None:
        return False
    if item_type == "open":
        return solo_done(p, item_type)
    return p.solo_right == 0 and p.solo_wrong >= MAX_SOLO_TRIES

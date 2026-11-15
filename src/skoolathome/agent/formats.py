"""The four sheet formats (ARCHITECTURE.md §7.2 and §9.4, T20): each one has its own prompt,
managed in Langfuse, and its own shape imposed on the model."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from skoolathome.agent import draft


MIN_ITEMS = 3
CHARS_PER_ITEM = 150  # about one question per sentence or two of lesson: no padding from thin content


@dataclass(frozen=True)
class Format:
    kind: str
    prompt: str                   # name of the prompt, in Langfuse and in prompts/
    shape: type[BaseModel]        # what the model fills
    items: Callable[[int], int]   # how many questions for a target duration
    scales: bool = True           # fewer questions when the sources are short

    def item_count(self, target_minutes: int, source_chars: int, wanted: int | None = None) -> int:
        """The rule's or the request's number when given, else from the duration; fewer when the
        sources are short (at least 3), so the model does not pad with invented questions."""
        if not self.scales:
            return self.items(target_minutes)
        return min(wanted or self.items(target_minutes), max(MIN_ITEMS, source_chars // CHARS_PER_ITEM))


FORMATS = {
    "exercises": Format("exercises", "sheet-generator", draft.DraftSheet, draft.item_count),
    "test_revision": Format("test_revision", "sheet-test-revision", draft.DraftSheet, draft.item_count),
    "quick_quiz": Format("quick_quiz", "sheet-quick-quiz", draft.DraftQuiz, lambda _minutes: draft.QUIZ_ITEMS, False),
    "lesson_recap": Format("lesson_recap", "sheet-lesson-recap", draft.DraftRecap, lambda _minutes: draft.RECAP_ITEMS, False),
}
JUDGE_PROMPT = "sheet-judge"
SUMMARY_PROMPT = "lesson-summary"
ASSESSMENT_PROMPT = "assessment-check"

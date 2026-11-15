"""A rule: one explicit piece of the agent's behaviour (ARCHITECTURE.md §7.1).

Everything the agent does in the evening comes from rules: the default ones (daily exercise,
revision before a test, maximum load) and the parent's. Shared with the API (T21).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Trigger = Literal[
    "after_each_lesson",   # the evening of a lesson, on that lesson
    "before_each_lesson",  # the evening before each lesson, on the previous one
    "before_test",         # days before a detected test
    "after_feedback",      # the day after missed questions (T20)
    "weekly",              # not handled by the evening plan yet
    "on_homework",         # not handled by the evening plan yet
    "load_limit",          # not a sheet: the maximum minutes per evening
]
Scope = Literal["this_lesson", "previous_lesson", "last_n_lessons", "since_last_test", "missed_items"]
SheetFormat = Literal["exercises", "lesson_recap", "quick_quiz", "test_revision"]
RuleStatus = Literal["proposed", "active", "archived"]

# Revision before a test comes first, then the parent's rules, then the daily exercise (§7.3).
PRIORITY_REVISION, PRIORITY_PARENT, PRIORITY_DEFAULT = 0, 1, 2


class Origin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = ""  # the sentence the rule comes from
    author: Literal["default", "parent", "agent"] = "default"
    on: date | None = None


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,40}$")
    version: int = Field(default=1, ge=1)
    status: RuleStatus = "active"
    subject: str | None = None  # École Directe code (HI-GE…); None: every subject
    teacher: str | None = None  # optional, compared with the timetable
    trigger: Trigger
    when: str = "eve_18h"       # before_each_lesson: shown the evening before the lesson
    scope: Scope = "this_lesson"
    format: SheetFormat = "exercises"
    style: str = ""             # e.g. oral_quiz: told to the model
    suggested_mode: Literal["together", "solo"] = "solo"
    target_minutes: int = Field(default=8, ge=1, le=60)
    replaces_default: bool = False  # this subject gets no daily exercise
    offsets_days: list[int] = []    # before_test: days before the test, e.g. [4, 2, 1]
    n_lessons: int = Field(default=3, ge=1, le=10)  # scope last_n_lessons
    max_minutes: int | None = Field(default=None, ge=1, le=180)  # load_limit
    origin: Origin = Origin()

    @model_validator(mode="after")
    def _is_complete(self) -> Rule:
        if self.trigger == "before_test" and not self.offsets_days:
            raise ValueError("a before_test rule needs offsets_days, e.g. [4, 2, 1]")
        if any(offset < 1 or offset > 14 for offset in self.offsets_days):
            raise ValueError("offsets_days are between 1 and 14")
        if self.trigger == "load_limit" and self.max_minutes is None:
            raise ValueError("a load_limit rule needs max_minutes")
        if self.trigger == "before_each_lesson" and not self.subject:
            raise ValueError("a before_each_lesson rule needs a subject")
        return self

    @property
    def priority(self) -> int:
        if self.trigger in ("before_test", "after_feedback"):
            return PRIORITY_REVISION
        return PRIORITY_DEFAULT if self.origin.author == "default" else PRIORITY_PARENT

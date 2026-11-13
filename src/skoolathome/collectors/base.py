"""Interfaces every school tool adapter implements."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from skoolathome.schema import SchoolSnapshot


class SchoolSource(Protocol):
    """School life: what happened in class, what is due, what is graded."""

    name: str

    def fetch(self, start: date, end: date) -> SchoolSnapshot: ...

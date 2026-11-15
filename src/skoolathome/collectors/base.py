"""Interfaces every school tool adapter implements (ARCHITECTURE.md §5.1)."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from skoolathome.schema import SchoolSnapshot
from skoolathome.schema.material import CourseDocument, DocumentRef


class SchoolSource(Protocol):
    """School life: what happened in class, what is due, what is graded."""

    name: str

    def fetch(self, start: date, end: date) -> SchoolSnapshot: ...


class CourseMaterialSource(Protocol):
    """Course material: textbooks, handouts, documents."""

    name: str

    def list_documents(self) -> list[DocumentRef]: ...

    def fetch(self, ref: DocumentRef) -> CourseDocument: ...

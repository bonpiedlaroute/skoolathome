"""A sheet asked for by the parent: a revision on a precise point, or an extra exercise.

Made from the PWA (stored online, picked up by the laptop's next run) or from the laptop
(`skoolathome ask`). The agent reads everything it knows of the subject before writing.

From the PWA, a request can bring photos of the notebook: the laptop reads them, the parent
checks the text, and the sheet is written from it first.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_PHOTOS = 6
PAGE_CHARS = 6000  # a page of a notebook copied out is about 2000 characters

RequestKind = Literal["revision", "extra"]
RequestStatus = Literal["pending", "done", "abstained"]
FORMAT_OF = {"revision": "test_revision", "extra": "exercises"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewRequest(Strict):
    subject: str = Field(min_length=1, max_length=20)  # École Directe code, e.g. MATHS
    kind: RequestKind = "extra"
    focus: str = Field(default="", max_length=300)    # what to work on, in the parent's words
    items: int | None = Field(default=None, ge=1, le=15)
    # How long the sheet should last: from 20 minutes, a long sheet for paper too (a test to prepare).
    minutes: int | None = Field(default=None, ge=5, le=60)
    # Photos of the notebook the PWA sends next (PUT /api/requests/{id}/photos/{n}): the course
    # as written in class, which École Directe does not have.
    photos: int = Field(default=0, ge=0, le=MAX_PHOTOS)


class PhotoPage(Strict):
    """The text the laptop read on one photo, or why it could not."""

    text: str = Field(default="", max_length=PAGE_CHARS)
    problem: str = Field(default="", max_length=300)


class Transcription(Strict):
    """Sent by the laptop: one page per photo, in their order."""

    pages: list[PhotoPage] = Field(min_length=1, max_length=MAX_PHOTOS)


class ReviewedPages(Strict):
    """Sent by the parent: the text of each photo, corrected if need be."""

    pages: list[str] = Field(min_length=1, max_length=MAX_PHOTOS)

    @field_validator("pages")
    @classmethod
    def _not_too_long(cls, pages: list[str]) -> list[str]:
        if any(len(page) > PAGE_CHARS for page in pages):
            raise ValueError(f"a page has {PAGE_CHARS} characters at most")
        if not any(page.strip() for page in pages):
            raise ValueError("every page is empty")
        return [page.strip() for page in pages]


class RequestUpdate(Strict):
    """Sent back with the evening's batch: what became of a request made online."""

    id: str = Field(min_length=1, max_length=64)
    status: Literal["done", "abstained"]
    sheet_id: str | None = None
    reason: str = Field(default="", max_length=600)

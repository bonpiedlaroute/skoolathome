"""A sheet asked for by the parent: a revision on a precise point, or an extra exercise.

Made from the PWA (stored online, picked up by the laptop's next run) or from the laptop
(`skoolathome ask`). The agent reads everything it knows of the subject before writing.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


class RequestUpdate(Strict):
    """Sent back with the evening's batch: what became of a request made online."""

    id: str = Field(min_length=1, max_length=64)
    status: Literal["done", "abstained"]
    sheet_id: str | None = None
    reason: str = Field(default="", max_length=600)

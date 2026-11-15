"""« Je n'ai pas compris » (T29): an explanation asked on a question of a sheet. Shared with the API.

The tablet asks; the request waits online; the laptop, when it is on, writes an explanation from
the lesson the sheet came from, checks it like a sheet, and sends it back.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from skoolathome.schema.sheet import Quote


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExplanationRequest(Strict):
    sheet_id: str = Field(max_length=120)
    item_id: str = Field(max_length=20)
    question: str = Field(default="", max_length=300)  # what is not clear, in the student's words


class ExplanationBody(Strict):
    text: str = Field(min_length=1, max_length=1500)
    example: str = Field(default="", max_length=800)
    quote: Quote | None = None   # the lesson passage the explanation rests on
    source_label: str = ""       # « ton cours du mardi 4/11 »


class ExplanationAnswer(Strict):
    """From the laptop (NIP-98): the explanation, or why there is none."""

    status: Literal["done", "abstained"]
    explanation: ExplanationBody | None = None
    reason: str = Field(default="", max_length=600)

    @model_validator(mode="after")
    def _is_complete(self) -> ExplanationAnswer:
        if (self.status == "done") != (self.explanation is not None):
            raise ValueError("an explanation when done, none when abstained")
        return self


class Helped(Strict):
    helped: bool  # « Ça m'a aidé ? » Oui / Toujours pas

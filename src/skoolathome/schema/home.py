"""The PWA's home page: what comes next, the latest grades, the sheets to do. Shared with the API.

Computed on the laptop after each collection and sent with one signed call. Everything is dated:
the PWA picks « tomorrow » and « within 3 days » from its own clock, so a page computed on Sunday
evening is still right on Monday morning. No surname (§17): teachers are not sent, and the
teachers' texts go through the same masking as the rules.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HomeSlot(Strict):
    day: date
    starts: str = Field(pattern=r"^\d{2}:\d{2}$")  # school time, « 08:20 »
    ends: str = Field(pattern=r"^\d{2}:\d{2}$")
    subject_code: str = Field(max_length=20)
    subject: str = Field(max_length=60)
    room: str = Field(default="", max_length=40)
    cancelled: bool = False


class HomeTest(Strict):
    day: date
    subject_code: str = Field(max_length=20)
    subject: str = Field(max_length=60)
    what: str = Field(default="", max_length=240)  # what the teacher wrote, shortened
    reason: Literal["flag", "keywords", "model"]   # ticked by the teacher, words, read by the model


class HomeGrade(Strict):
    day: date
    kind: str = Field(max_length=40)    # Contrôle, Interrogation, Devoir maison…
    title: str = Field(max_length=120)
    value: str = Field(max_length=12)   # as École Directe shows it: « 15,5 », « Abs »
    score: float | None = None          # the same, as a number; None: not a number
    out_of: float | None = None
    coefficient: float | None = None
    class_average: float | None = None
    class_min: float | None = None
    class_max: float | None = None


class HomeSubjectGrades(Strict):
    subject_code: str = Field(max_length=20)
    subject: str = Field(max_length=60)
    grades: list[HomeGrade] = Field(max_length=5)  # the latest first
    # Over the current period, as École Directe counts them: each grade on 20, weighted by its
    # coefficient, the grades marked « non significative » left out. None: nothing to count.
    average: float | None = None
    class_average: float | None = None


class HomeAverages(Strict):
    """The general average: the subject averages added up and divided by their number (no subject
    coefficients at this school), the child's and the class's, over the same subjects."""

    period: str = Field(default="", max_length=40)  # « 1er trimestre »
    average: float | None = None
    class_average: float | None = None
    subjects: int = 0


class HomeLink(Strict):
    """A web page the teacher linked in the lesson (GeoGebra, a video…), given as it is."""

    url: str = Field(max_length=500, pattern=r"^https?://")
    label: str = Field(default="", max_length=120)


class HomeDocument(Strict):
    """A file the teacher attached to the lesson. With its sha256, the PWA can open it: the laptop
    downloaded it during the collection and put it online with the page."""

    name: str = Field(max_length=160)
    size: int | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")  # None: only on École Directe


class HomeLesson(Strict):
    day: date
    points: list[str] = Field(default=[], max_length=5)  # the first lines of the lesson, as written
    attachment: bool = False                              # the teacher attached a document
    documents: list[HomeDocument] = Field(default=[], max_length=12)
    links: list[HomeLink] = Field(default=[], max_length=10)


class HomeRequest(Strict):
    """What the PWA sends if the parent asks for this sheet (the same as « Demander une fiche »)."""

    kind: Literal["revision", "extra"]
    focus: str = Field(max_length=300)


class HomeSuggestion(Strict):
    subject_code: str = Field(max_length=20)
    subject: str = Field(max_length=60)
    reason: Literal["test", "lesson"]   # a test that day, or a lesson that day
    for_day: date
    lessons: list[HomeLesson] = Field(default=[], max_length=4)  # what to revise, from the notebook
    sheet_ids: list[str] = Field(default=[], max_length=5)        # sheets already made for it
    request: HomeRequest


class Home(Strict):
    generated_at: datetime
    collected_at: datetime | None = None  # the École Directe collection it comes from
    slots: list[HomeSlot] = Field(default=[], max_length=200)
    holidays: list[date] = Field(default=[], max_length=60)
    tests: list[HomeTest] = Field(default=[], max_length=40)
    grades: list[HomeSubjectGrades] = Field(default=[], max_length=30)
    averages: HomeAverages | None = None
    suggestions: list[HomeSuggestion] = Field(default=[], max_length=40)

"""Pydantic models of the raw École Directe responses.

Fields we use are validated strictly; the others are allowed and ignored.
A validation error here is an alarm: the unofficial API has changed.
Field names are École Directe's own, hence in French.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Raw(BaseModel):
    model_config = ConfigDict(extra="allow")


class RawDocument(Raw):
    id: int
    libelle: str
    type: str
    taille: int | None = None


class RawSlot(Raw):
    id: int
    matiere: str
    codeMatiere: str
    typeCours: str
    start_date: str
    end_date: str
    prof: str
    salle: str
    isAnnule: bool


class RawTimetable(Raw):
    data: list[RawSlot]


class RawUpcomingHomework(Raw):
    idDevoir: int
    matiere: str
    codeMatiere: str
    donneLe: str
    effectue: bool
    interrogation: bool


class RawUpcoming(Raw):
    data: dict[str, list[RawUpcomingHomework]]


class RawLessonContent(Raw):
    contenu: str
    documents: list[RawDocument] = []


class RawHomeworkContent(Raw):
    idDevoir: int
    contenu: str
    donneLe: str
    effectue: bool
    documents: list[RawDocument] = []


class RawSubjectDay(Raw):
    id: int
    matiere: str
    codeMatiere: str
    nomProf: str
    interrogation: bool
    aFaire: RawHomeworkContent | None = None
    contenuDeSeance: RawLessonContent | None = None


class RawDiaryData(Raw):
    date: str
    matieres: list[RawSubjectDay]


class RawDiary(Raw):
    data: RawDiaryData


class RawGrade(Raw):
    id: int
    devoir: str
    codePeriode: str
    codeMatiere: str
    libelleMatiere: str
    typeDevoir: str
    date: str
    coef: str
    noteSur: str
    valeur: str


class RawGradesData(Raw):
    notes: list[RawGrade]


class RawGrades(Raw):
    data: RawGradesData

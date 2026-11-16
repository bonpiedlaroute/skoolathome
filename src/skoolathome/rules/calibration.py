"""The target difficulty of each subject (ARCHITECTURE.md §14.2, T33): a deterministic rule, no model.

Over the answers given alone (together, the parent helps: success would be overrated), in the order
they were given, per subject:
    - the success over the last 10 questions;
    - above 85 %, the target goes up one step; below 60 %, down one step;
    - between 1 and 3;
    - two changes at least 10 answers apart: the window after a change holds only new answers.
Replayed from the whole history at each evening: the same answers always give the same level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import psycopg

WINDOW = 10
UP, DOWN = 0.85, 0.60
LOWEST, HIGHEST = 1, 3
START = 2      # before any change: the middle
GAP = 10       # answers between two changes


@dataclass(frozen=True)
class Change:
    answer: int              # the n-th answer (from 1) that made it change
    before: int
    after: int
    rate: float              # the success over the last 10 answers then
    at: datetime | None = None


@dataclass
class Calibration:
    level: int = START
    answers: int = 0
    rate: float | None = None  # over the last 10 answers, None below 10
    changes: list[Change] = field(default_factory=list)

    @property
    def calibrated(self) -> bool:
        """Enough answers to say something: before, the formats' own difficulty is kept."""
        return self.answers >= WINDOW


def replay(answers: list[bool], times: list[datetime] | None = None, start: int = START) -> Calibration:
    level, since = start, 0
    changes = []
    for n in range(1, len(answers) + 1):
        if n - since < GAP:
            continue
        rate = sum(answers[n - WINDOW:n]) / WINDOW
        step = 1 if rate > UP else -1 if rate < DOWN else 0
        if step and LOWEST <= level + step <= HIGHEST:
            changes.append(Change(n, level, level + step, rate, times[n - 1] if times else None))
            level, since = level + step, n
    rate = sum(answers[-WINDOW:]) / WINDOW if len(answers) >= WINDOW else None
    return Calibration(level, len(answers), rate, changes)


def history(conn: psycopg.Connection, student_id: str) -> dict[str, list[tuple[bool, datetime]]]:
    """The first answer given alone to each question of the agent's sheets, per subject, oldest first."""
    rows = conn.execute(
        "SELECT DISTINCT ON (a.sheet_id, a.item_id) s.subject_code, a.correct, a.created_at "
        "FROM feedback.attempts a JOIN agent.sheets s ON s.id = a.sheet_id "
        "WHERE s.student_id = %s AND a.mode = 'solo' AND a.correct IS NOT NULL "
        "ORDER BY a.sheet_id, a.item_id, a.created_at", (student_id,),
    ).fetchall()
    found: dict[str, list[tuple[bool, datetime]]] = {}
    for code, correct, at in sorted(rows, key=lambda r: r[2]):
        found.setdefault(code, []).append((bool(correct), at))
    return found


def levels(conn: psycopg.Connection, student_id: str) -> dict[str, Calibration]:
    return {code: replay([ok for ok, _ in answers], [at for _, at in answers])
            for code, answers in history(conn, student_id).items()}


def instruction(level: int, subject: str) -> str:
    """What the generator is told about the difficulty (graph._context). A harder sheet stays in the
    lesson: the citation guards apply at level 3 as at level 1."""
    if level <= LOWEST:
        return (f"Difficulté visée : 1 sur 3. L'élève a eu du mal récemment en {subject} : surtout des questions de "
                "difficulté 1, directes, en une étape.")
    if level >= HIGHEST:
        return (f"Difficulté visée : 3 sur 3. L'élève réussit presque tout seul en {subject} : surtout des questions "
                "de difficulté 2 et 3, qui demandent d'appliquer ou de combiner. Plus difficile ne veut pas dire hors "
                "du cours : chaque question reste appuyée sur sa citation.")
    return "Difficulté visée : 2 sur 3 : surtout des questions de difficulté 2, une ou deux de difficulté 1 pour commencer."

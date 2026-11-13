"""One collection run: log in once, fetch, save without duplicates, write the run log."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import psycopg

from skoolathome.collectors.ecoledirecte.client import API_VERSION, EcoleDirecteError, TwoFactorRequired
from skoolathome.collectors.ecoledirecte.source import (
    AccessRefused,
    EcoleDirecteSource,
    daily_window,
    first_window,
)
from skoolathome.store import school


@dataclass
class RunResult:
    run_id: int
    status: str  # ok | partial | two_factor_required | failed
    student_id: str | None = None
    counts: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)


def pick_student(students: list[dict], wanted: int | None) -> dict:
    """The requested student, or the only one whose school enabled the homework diary."""
    if wanted is not None:
        matches = [s for s in students if s["id"] == wanted]
        if not matches:
            raise LookupError(f"élève {wanted} introuvable sur ce compte")
        return matches[0]
    candidates = [s for s in students if "CAHIER_DE_TEXTES" in s["modules"]]
    if len(candidates) != 1:
        ids = ", ".join(str(s["id"]) for s in candidates) or "aucun"
        raise LookupError(f"précise l'élève avec --student (cahier de texte activé pour : {ids})")
    return candidates[0]


def collect(
    conn: psycopg.Connection,
    client,
    *,
    student_id: int | None = None,
    today: date | None = None,
    pause: float = 1.0,
) -> RunResult:
    """`conn` must be in autocommit mode: the run log is written even if saving fails."""
    today = today or date.today()
    run_id = school.start_run(conn, "ecoledirecte")
    result = RunResult(run_id=run_id, status="failed")
    try:
        # One login attempt, no answerer: a two-factor request stops the run. Never a retry.
        client.connect()
        student = pick_student(client.students(), student_id)
        result.student_id = str(student["id"])
        version = getattr(client, "version", API_VERSION)

        def keep_raw(kind: str, ref: str, body: dict) -> None:
            school.save_raw(conn, run_id, "ecoledirecte", kind, ref, version, body)

        source = EcoleDirecteSource(client, student, today=today, pause=pause, on_raw=keep_raw)
        start, end = daily_window(today) if school.has_lessons(conn, source.name) else first_window(today)
        snapshot = source.fetch(start, end)
        with conn.transaction():
            result.counts = school.save_snapshot(conn, snapshot)
        result.errors = source.errors
        result.status = "partial" if source.errors else "ok"
    except TwoFactorRequired:
        result.status = "two_factor_required"
        result.errors = [{"kind": "login", "ref": "", "error": "two-factor authentication required"}]
    except (AccessRefused, EcoleDirecteError, LookupError) as error:
        result.errors = [{"kind": "run", "ref": "", "error": str(error)}]
    finally:
        school.finish_run(
            conn, run_id, result.status,
            student_id=result.student_id, counts=result.counts, errors=result.errors,
        )
    return result

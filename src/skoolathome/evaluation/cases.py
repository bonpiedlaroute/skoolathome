"""The reference set (ARCHITECTURE.md §14.3, T23): real cases anonymized, traps, past reports.

Two families for now: « fiches » (a task and what the model read → a good sheet, or an abstention)
and « contrôles » (a homework → is a test announced?). T31 adds « recherche » and « consignes ».

The cases live in the local database (agent.eval_cases) and in Langfuse datasets: Langfuse can be
emptied without losing them, and `make eval` also runs without it.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from skoolathome import secrets
from skoolathome.agent import tracing
from skoolathome.rules.inputs import PAST_TEST_KINDS
from skoolathome.subjects import EXCLUDED_SUBJECTS, label

REPO = Path(__file__).resolve().parents[3]
TRAPS = REPO / "evals" / "traps.json"
DATASETS = {"sheets": "skoolathome-fiches", "tests": "skoolathome-controles"}
NAMES_SECRET = "names"  # ~/.config/skoolathome/names.json: {"names": ["Léo", …]}, never in the repo
TITLES = {"m", "mr", "mme", "mlle", "monsieur", "madame", "mademoiselle"}
PLACEHOLDER = "Camille"  # a name instead of every real one, so the text still reads naturally


@dataclass
class Case:
    id: str
    family: str             # sheets | tests
    origin: str             # real | trap | report
    input: dict
    expected: dict
    metadata: dict = field(default_factory=dict)


# ------------------------------------------------------------------------ the local copy


def save(conn: psycopg.Connection, case: Case) -> None:
    conn.execute(
        "INSERT INTO agent.eval_cases (id, family, origin, input, expected, metadata) VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (id) DO UPDATE SET family = EXCLUDED.family, origin = EXCLUDED.origin, input = EXCLUDED.input, "
        "expected = EXCLUDED.expected, metadata = EXCLUDED.metadata, updated_at = now(), synced_at = NULL "
        "WHERE (agent.eval_cases.input, agent.eval_cases.expected, agent.eval_cases.metadata) "
        "IS DISTINCT FROM (EXCLUDED.input, EXCLUDED.expected, EXCLUDED.metadata)",
        (case.id, case.family, case.origin, Jsonb(case.input), Jsonb(case.expected), Jsonb(case.metadata)),
    )


def load(conn: psycopg.Connection, family: str | None = None) -> list[Case]:
    rows = conn.execute(
        "SELECT id, family, origin, input, expected, metadata FROM agent.eval_cases "
        "WHERE %s::text IS NULL OR family = %s ORDER BY origin, id", (family, family),
    ).fetchall()
    return [Case(*row) for row in rows]


def load_traps(conn: psycopg.Connection, path: Path = TRAPS) -> int:
    """The traps of evals/traps.json, written by hand: loaded (again) into the reference set."""
    data = json.loads(path.read_text())["cases"]
    for trap in data:
        save(conn, Case(trap["id"], trap["family"], "trap", trap["input"], trap["expected"],
                        {"note": trap.get("note", "")}))
    return len(data)


def sync(conn: psycopg.Connection) -> int:
    """Send the new or changed cases to their Langfuse dataset (same id: an update, not a copy)."""
    lf = tracing.client()
    if lf is None:
        return 0
    for name in DATASETS.values():
        lf.create_dataset(name=name, description="Jeu de référence de skoolathome (T23), voir evals/")
    rows = conn.execute("SELECT id, family, origin, input, expected, metadata FROM agent.eval_cases "
                        "WHERE synced_at IS NULL").fetchall()
    for row in rows:
        case = Case(*row)
        lf.create_dataset_item(dataset_name=DATASETS[case.family], id=case.id, input=case.input,
                               expected_output=case.expected, metadata={**case.metadata, "origin": case.origin})
        conn.execute("UPDATE agent.eval_cases SET synced_at = now() WHERE id = %s", (case.id,))
    lf.flush()
    return len(rows)


# ------------------------------------------------------------------------- anonymizing


def private_names(conn: psycopg.Connection) -> list[str]:
    """The names to take out of a case: the teachers of the timetable, and the family's names kept
    in ~/.config/skoolathome/names.json."""
    found = set((secrets.read(NAMES_SECRET) or {}).get("names", []))
    for (teacher,) in conn.execute("SELECT DISTINCT teacher FROM school.timetable_slots UNION "
                                   "SELECT DISTINCT teacher FROM school.lessons").fetchall():
        words = re.split(r"[\s.]+", teacher)
        found |= {w for w in words if len(w) >= 3 and w.lower() not in TITLES}
    return sorted(found, key=len, reverse=True)


def remember_names(names: list[str]) -> None:
    known = set((secrets.read(NAMES_SECRET) or {}).get("names", []))
    secrets.write(NAMES_SECRET, {"names": sorted(known | {n.strip() for n in names if n.strip()})})


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def anonymize(value, names: list[str]):
    """Every name replaced, in every string of a JSON value, whatever its case or accents."""
    if isinstance(value, dict):
        return {key: anonymize(item, names) for key, item in value.items()}
    if isinstance(value, list):
        return [anonymize(item, names) for item in value]
    if not isinstance(value, str) or not names:
        return value
    text = value
    for name in names:
        pattern = "".join(f"[{c}{c.upper()}{_accented(c)}]" if c.isalpha() else re.escape(c) for c in _plain(name))
        text = re.sub(rf"(?<!\w){pattern}(?!\w)", PLACEHOLDER, text)
    return text


_ACCENTS = {"a": "àâä", "e": "éèêë", "i": "îï", "o": "ôö", "u": "ùûü", "c": "ç", "y": "ÿ"}


def _accented(letter: str) -> str:
    return "".join(a + a.upper() for a in _ACCENTS.get(letter, ""))


# ------------------------------------------------------------------- cases from real data


def _model_input(conn: psycopg.Connection, sheet_id: str) -> tuple[dict, dict]:
    row = conn.execute("SELECT model_input, body, student_id, subject_code, kind, rule_id, rule_version, "
                       "visible_from, due_by, minutes FROM agent.sheets WHERE id = %s", (sheet_id,)).fetchone()
    if row is None:
        raise LookupError(f"fiche inconnue sur ce portable : {sheet_id}")
    model_input, body, student, code, kind, rule, version, visible_from, due_by, minutes = row
    if model_input:
        return model_input, body or {}
    # A sheet made before T23: the lessons it cites, read again (the pages are not kept).
    sources, labels = {}, {}
    for key in (body or {}).get("sources", []):
        if key.startswith("lesson:"):
            day = key.split(":")[1]
            texts = [r[0] for r in conn.execute(
                "SELECT content FROM school.lessons WHERE student_id = %s AND subject_code = %s AND day = %s "
                "AND NOT is_empty", (student, code, day)).fetchall()]
            if texts:
                sources[key], labels[key] = "\n".join(texts), (body.get("source_labels") or {}).get(key, "")
    task = {"id": sheet_id, "kind": kind, "subject": label(code), "subject_code": code, "rule": rule,
            "rule_version": version, "target_minutes": minutes or 8, "visible_from": str(visible_from),
            "due_by": str(due_by) if due_by else None, "computational": code == "MATHS"}
    return {"task": task, "sources": sources, "labels": labels, "context": ""}, body or {}


def from_sheet(conn: psycopg.Connection, sheet_id: str, names: list[str]) -> Case:
    """A sheet made by the agent and judged good: what the model read, anonymized."""
    model_input, body = _model_input(conn, sheet_id)
    case_input = anonymize(model_input, names)
    return Case(f"real-{sheet_id}", "sheets", "real", case_input, {"abstain": False},
                {"sheet": sheet_id, "subject": model_input["task"].get("subject_code", ""),
                 "format": model_input["task"].get("kind", "")})


def from_report(conn: psycopg.Connection, report_id: int, names: list[str], answer: str = "") -> Case:
    """A report of the tablet becomes a case: the same sources, and what must not happen again.
    `answer`: the right answer, for a wrong_answer report."""
    row = conn.execute("SELECT sheet_id, item_id, reason, comment FROM feedback.reports WHERE id = %s",
                       (report_id,)).fetchone()
    if row is None:
        raise LookupError(f"signalement inconnu : {report_id}")
    sheet_id, item_id, reason, comment = row
    model_input, body = _model_input(conn, sheet_id)
    item = next((i for i in body.get("items", []) if i["id"] == item_id), None)
    if reason == "wrong_answer" and not answer:
        raise ValueError("pour une réponse fausse, donne la bonne : --answer « … »")
    reported = {"reason": reason, "comment": comment, "item": item_id,
                "prompt": item["prompt"] if item else "", "answer": item["answer"] if item else ""}
    if answer:
        reported["correct_answer"] = answer
    return Case(f"report-{report_id}", "sheets", "report", anonymize(model_input, names),
                {"abstain": False, "report": anonymize(reported, names)},
                {"sheet": sheet_id, "reason": reason, "subject": model_input["task"].get("subject_code", "")})


def from_grades(conn: psycopg.Connection, student_id: str, days: int = 120) -> list[Case]:
    """Tests seen afterwards in the grades (Contrôle, Interrogation…): the homework that announced
    each one, due that day in that subject, is a case where a test must be detected."""
    rows = conn.execute(
        "SELECT g.source_id, g.subject_code, g.day, h.source_id, h.text, h.flagged_as_assessment, h.given_on, h.due_on "
        "FROM school.grades g JOIN school.homework h ON h.student_id = g.student_id "
        "AND h.subject_code = g.subject_code AND h.due_on BETWEEN g.day - 1 AND g.day "
        "WHERE g.student_id = %s AND lower(g.kind) ~ %s AND g.day >= current_date - %s AND h.text <> '' "
        "AND g.subject_code <> ALL(%s)",
        (student_id, PAST_TEST_KINDS, timedelta(days=days), list(EXCLUDED_SUBJECTS)),
    ).fetchall()
    names = private_names(conn)
    found = {}
    for grade, code, day, homework, text, flagged, given_on, due_on in rows:
        found[homework] = Case(
            f"test-{homework}", "tests", "real",
            {"subject": label(code), "text": anonymize(text, names), "flagged": flagged,
             "given_on": str(given_on) if given_on else None, "due_on": str(due_on)},
            {"is_assessment": True}, {"grade": grade, "test_day": str(day), "subject": code})
    return list(found.values())


def observed_recall(conn: psycopg.Connection, student_id: str, since, until) -> dict:
    """Tests seen in the grades between two dates, and how many the agent had detected before the
    day: a homework marked as a test, or read as one by the model (§14.2, rappel des contrôles).
    Only the tests after the first collection count: before it, the agent could not see them. The
    subjects without revision (EPS…) do not count either."""
    rows = conn.execute(
        "SELECT DISTINCT g.subject_code, g.day, g.title FROM school.grades g "
        "WHERE g.student_id = %s AND lower(g.kind) ~ %s AND g.day BETWEEN %s AND %s AND g.subject_code <> ALL(%s) "
        "AND g.day > (SELECT min(started_at)::date FROM school.collection_runs WHERE status IN ('ok', 'partial')) "
        "ORDER BY g.day",
        (student_id, PAST_TEST_KINDS, since, until, list(EXCLUDED_SUBJECTS)),
    ).fetchall()
    missed = []
    for code, day, title in rows:
        detected = conn.execute(
            "SELECT 1 FROM school.homework h WHERE h.student_id = %(s)s AND h.subject_code = %(c)s "
            "AND h.due_on BETWEEN %(d)s::date - 1 AND %(d)s AND h.first_seen::date < %(d)s AND (h.is_assessment = 'yes' "
            "OR EXISTS (SELECT 1 FROM agent.assessment_checks a WHERE a.student_id = h.student_id "
            "AND a.source = h.source AND a.source_id = h.source_id AND a.is_assessment AND a.confidence >= 0.5)) "
            "LIMIT 1", {"s": student_id, "c": code, "d": day},
        ).fetchone()
        if detected is None:
            missed.append({"subject": label(code), "day": day.isoformat(), "title": title})
    return {"observed": len(rows), "detected": len(rows) - len(missed), "missed": missed}

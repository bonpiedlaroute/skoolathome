"""T19: rules (model, versioned table), the plan's inputs read from the database; T17: feedback kept locally."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from skoolathome.rules import assessments, inputs, store
from skoolathome.schema.rule import PRIORITY_DEFAULT, PRIORITY_PARENT, PRIORITY_REVISION, Origin, Rule
from skoolathome.store import feedback

STUDENT = "4242"


def test_a_rule_says_what_it_needs():
    with pytest.raises(ValidationError):
        Rule(id="x-quiz", trigger="before_each_lesson")  # which subject?
    with pytest.raises(ValidationError):
        Rule(id="x-test", trigger="before_test")  # how many days before?
    with pytest.raises(ValidationError):
        Rule(id="Bad Id", trigger="after_each_lesson")
    assert Rule(id="hg", subject="HI-GE", trigger="before_each_lesson", origin=Origin(author="parent")).priority == PRIORITY_PARENT
    assert Rule(id="test-3", trigger="before_test", offsets_days=[3]).priority == PRIORITY_REVISION
    assert Rule(id="daily", trigger="after_each_lesson").priority == PRIORITY_DEFAULT
    assert Rule(id="daily", trigger="after_each_lesson", origin=Origin(author="parent")).priority == PRIORITY_DEFAULT
    assert Rule(id="extra-maths", trigger="after_each_lesson", subject="MATHS").priority == PRIORITY_PARENT


def test_default_rules_are_stored_once_and_every_change_is_a_new_version(conn):
    assert store.ensure_defaults(conn) == ["daily", "before_test", "missed_concepts", "max_evening_load"]
    assert store.ensure_defaults(conn) == []
    daily = next(r for r in store.active(conn) if r.id == "daily")
    changed = store.save(conn, daily.model_copy(update={"target_minutes": 6}))
    assert changed.version == 2
    assert next(r for r in store.active(conn) if r.id == "daily").target_minutes == 6
    archived = store.set_status(conn, "daily", "archived")
    assert archived.version == 3 and "daily" not in {r.id for r in store.active(conn)}
    assert store.ensure_defaults(conn) == []  # an archived default rule is not put back
    assert conn.execute("SELECT count(*) FROM agent.rules WHERE id = 'daily'").fetchone()[0] == 3
    with pytest.raises(LookupError):
        store.set_status(conn, "unknown", "active")


def test_untouched_default_rules_follow_the_code_and_the_parent_s_are_kept(conn):
    old = Rule(id="before_test", trigger="before_test", offsets_days=[4, 2, 1], target_minutes=10, format="test_revision")
    conn.execute("INSERT INTO agent.rules (id, version, status, body) VALUES ('before_test', 1, 'active', %s)",
                 (Jsonb(old.model_dump(mode="json")),))
    assert "before_test" in store.ensure_defaults(conn)  # one 45-minute preparation now, as in the code
    revision = store.get(conn, "before_test")
    assert (revision.version, revision.offsets_days, revision.target_minutes) == (2, [3], 45)
    mine = store.set_fields(conn, "before_test", {"items": 4})
    assert (mine.version, mine.items, mine.origin.author) == (3, 4, "parent") and mine.origin.text == revision.origin.text
    assert store.ensure_defaults(conn) == [] and store.get(conn, "before_test").items == 4
    with pytest.raises(ValueError, match="champ inconnu ou non modifiable : questions"):
        store.set_fields(conn, "before_test", {"questions": 4})
    with pytest.raises(ValidationError):
        store.set_fields(conn, "before_test", {"items": 40})


@pytest.mark.parametrize("text, ambiguous", [
    ("Merci de répondre au questionnaire. apprendre ch 2 il y a aussi un qcm pour le prochain cours", True),
    ("Connaître les 3 types de villains", True),
    ("Contrôle sur le chapitre 4", False),  # already a test, by keywords
    ("Faire ex 12 à 14 p 208", False),
    ("", False),
])
def test_ambiguous_homework_goes_to_the_model(text, ambiguous):
    assert assessments.is_ambiguous(text) is ambiguous


# ------------------------------------------------------------------- plan inputs


def add(conn, table: str, **row) -> None:
    columns = ", ".join(row)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({', '.join(['%s'] * len(row))})", list(row.values()))


def slot(conn, starts: str, code: str, kind: str = "COURS", cancelled: bool = False, ends: str | None = None) -> None:
    add(conn, "school.timetable_slots", source="ecoledirecte", student_id=STUDENT, source_id=f"{code}-{starts}",
        starts_at=datetime.fromisoformat(starts), ends_at=datetime.fromisoformat(ends or starts), subject_code=code,
        subject=code, teacher="M. X", room="", kind=kind, cancelled=cancelled, fingerprint="f")


def homework(conn, source_id: str, code: str, due: date, text: str, flag: str = "no", reason: str = "") -> None:
    add(conn, "school.homework", source="ecoledirecte", student_id=STUDENT, source_id=source_id, subject_code=code,
        subject=code, given_on=date(2026, 11, 12), due_on=due, text=text, done=False, flagged_as_assessment=False,
        is_assessment=flag, assessment_reason=reason, fingerprint=f"fp-{source_id}")


def test_plan_inputs_come_from_the_database(conn):
    slot(conn, "2026-11-17 10:00", "HI-GE")
    slot(conn, "2026-11-19 10:00", "HI-GE", cancelled=True)
    slot(conn, "2026-11-21 00:00", "", kind="CONGE", ends="2026-11-23 23:59")
    add(conn, "school.lessons", source="ecoledirecte", student_id=STUDENT, source_id="l1", day=date(2026, 11, 12),
        subject_code="MATHS", subject="MATHEMATIQUES", teacher="M. Y", content="Les fractions égales…" * 10,
        is_empty=False, fingerprint="fp-l1")
    homework(conn, "h1", "MATHS", date(2026, 11, 20), "Contrôle chapitre 4", "yes", "keywords")
    homework(conn, "h2", "PH-CH", date(2026, 11, 18), "il y a un qcm au prochain cours")
    add(conn, "agent.assessment_checks", source="ecoledirecte", student_id=STUDENT, source_id="h2",
        fingerprint="fp-h2", is_assessment=True, day=None, confidence=0.7, model="m")
    add(conn, "school.grades", source="ecoledirecte", student_id=STUDENT, source_id="g1", subject_code="MATHS",
        subject="MATHS", day=date(2026, 11, 5), kind="Contrôle", title="", raw_value="12", period="T1",
        fingerprint="f")

    known = inputs.load(conn, STUDENT, date(2026, 11, 16))
    assert {(s.day.isoformat(), s.cancelled) for s in known.slots} == {("2026-11-17", False), ("2026-11-19", True)}
    assert known.holidays == {date(2026, 11, 21), date(2026, 11, 22), date(2026, 11, 23)}
    assert [(l.key, l.chars) for l in known.lessons] == [("lesson:2026-11-12:MATHS", 210)]
    tests = sorted((t.subject_code, t.day.isoformat(), t.reason) for t in known.tests)
    assert tests == [("MATHS", "2026-11-20", "keywords"), ("PH-CH", "2026-11-18", "model")]
    assert [t.subject for t in known.tests if t.subject_code == "MATHS"] == ["Mathématiques"]
    assert known.past_tests == {"MATHS": [date(2026, 11, 5)]}


# ---------------------------------------------------------------- feedback (T17)


FEEDBACK = {
    "since": 0, "until": 1_000,
    "attempts": [{"id": 7, "sheet_id": "s1", "item_id": "q1", "role": "child", "mode": "solo", "response": "3/4",
                  "correct": False, "rating": None, "hints_used": 1, "answered_at": None,
                  "created_at": "2026-11-16T17:00:00+00:00"}],
    "reports": [{"id": 3, "sheet_id": "s1", "item_id": "q2", "role": "child", "reason": "unclear", "comment": "?",
                 "created_at": "2026-11-16T17:01:00+00:00"}],
    "finishes": [{"id": "finish-0001", "sheet_id": "s1", "role": "child", "score": 2, "total": 3,
                  "to_review": ["q1"], "finished_at": "2026-11-16T17:02:00+00:00"}],
}


def test_feedback_is_kept_once_with_the_trace_of_its_sheet(conn):
    conn.execute(
        "INSERT INTO agent.sheets (id, run_date, task_id, student_id, subject_code, kind, status, body, attempts, "
        "trace_id, model, prompt_version, release) VALUES ('s1', '2026-11-15', 's1', %s, 'MATHS', 'exercises', "
        "'published', %s, 1, 'trace-s1', 'm', 'v1', 'r')",
        (STUDENT, Jsonb({"subject": "Mathématiques", "items": [{"id": "q1", "concept": "fractions égales",
                                                                "quote": {"source_id": "lesson:2026-11-12:MATHS"}}]})),
    )
    assert feedback.cursor(conn) == 0
    first = feedback.save(conn, FEEDBACK)
    assert {kind: count for kind, count in first.items() if count} == {"attempts": 1, "reports": 1, "finishes": 1}
    assert not any(feedback.save(conn, FEEDBACK).values())
    assert feedback.cursor(conn) == 1_000
    assert conn.execute("SELECT trace_id, item_id FROM feedback.attempts").fetchone() == ("trace-s1", "q1")
    # The missed question comes back as a concept to work on (T20).
    [missed] = inputs.missed(conn, STUDENT, date(2026, 11, 16))
    assert (missed.subject_code, missed.concept, missed.source_keys) == ("MATHS", "fractions égales",
                                                                         ("lesson:2026-11-12:MATHS",))

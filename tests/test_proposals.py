"""T28: the agent proposes a rule from a teacher's habit, with its facts, and waits for the parent."""

from __future__ import annotations

from datetime import date

import pytest

from skoolathome.agent import instructions, proposals
from skoolathome.evaluation import cases, weekly
from skoolathome.rules import store as rules_store
from skoolathome.store import db
from skoolathome.store import feedback as feedback_store
from skoolathome.store import instructions as store

TODAY = date(2026, 11, 14)


def lesson(conn, source_id: str, day: str, content: str, code: str = "HI-GE") -> None:
    conn.execute(
        "INSERT INTO school.lessons (source, student_id, source_id, day, subject_code, subject, teacher, content, "
        "is_empty, fingerprint) VALUES ('ecoledirecte', '4242', %s, %s, %s, %s, 'M. DURAND P.', %s, false, %s)",
        (source_id, day, code, code, content, f"fp-{source_id}"))


def homework(conn, source_id: str, day: str, text: str, code: str = "HI-GE") -> None:
    conn.execute(
        "INSERT INTO school.homework (source, student_id, source_id, subject_code, subject, given_on, due_on, text, "
        "done, flagged_as_assessment, fingerprint) VALUES ('ecoledirecte', '4242', %s, %s, %s, %s, %s, %s, false, "
        "false, 'f')", (source_id, code, code, day, day, text))


def grade(conn, source_id: str, day: str, kind: str = "Interrogation", title: str = "Leçon 3", code: str = "HI-GE") -> None:
    conn.execute(
        "INSERT INTO school.grades (source, student_id, source_id, subject_code, subject, day, kind, title, raw_value, "
        "period, fingerprint) VALUES ('ecoledirecte', '4242', %s, %s, %s, %s, %s, %s, '12', 'A001', 'f')",
        (source_id, code, code, day, kind, title))


def slots(conn, code: str = "HI-GE", count: int = 6) -> None:
    for n in range(count):
        conn.execute(
            "INSERT INTO school.timetable_slots (source, student_id, source_id, starts_at, ends_at, subject_code, "
            "subject, teacher, room, kind, cancelled, fingerprint) VALUES ('ecoledirecte', '4242', %s, %s, %s, %s, %s, "
            "'M. DURAND P.', '1', 'COURS', false, 'f')",
            (f"{code}-{n}", f"2026-11-{2 + n * 2:02d} 09:00", f"2026-11-{2 + n * 2:02d} 10:00", code, code))


def history_habit(conn) -> None:
    slots(conn)
    lesson(conn, "h1", "2026-11-05", "Interrogation orale de quelques élèves sur le cours précédent, puis Byzance.")
    lesson(conn, "h2", "2026-11-12", "Interro orale sur la leçon. Vu avec M. Durand : l'empire carolingien.")


@pytest.fixture
def url(conn):
    return db.database_url(conn.info.dbname)


# --------------------------------------------------------------------------- detection


def test_a_quiz_seen_in_two_lessons_is_a_habit_with_its_facts(conn):
    history_habit(conn)
    [habit] = proposals.detect(conn, "4242", TODAY)
    assert habit.key == "lesson_quiz:HI-GE" and habit.oral
    lines = proposals.facts(habit)
    assert lines[0].startswith("Le jeudi 12/11, la séance dit : « Interro orale sur la leçon. Vu avec M. Durand")
    assert lines[-1] == "Environ 4 cours d'histoire-géographie par semaine."  # 6 in 11 days
    rule = proposals.draft(habit, TODAY)
    assert (rule.id, rule.trigger, rule.scope, rule.format, rule.style, rule.suggested_mode) == (
        "hi-ge-before-quick-quiz", "before_each_lesson", "previous_lesson", "quick_quiz", "oral_quiz", "together")
    assert rule.origin.author == "agent"


@pytest.mark.parametrize("setup, found", [
    ("a quiz in one lesson only", False),
    ("a quiz in one lesson, and a quiz graded", True),
    ("a QCM announced in the homework: a test, not a habit", False),
    ("the homework says: interro at the start of each class", True),
    ("three quizzes graded, one every other lesson", True),
])
def test_what_counts_as_a_habit(conn, setup, found):
    slots(conn)
    if setup.startswith("a quiz in one lesson"):
        lesson(conn, "h1", "2026-11-05", "Interrogation orale sur le cours précédent.")
    if setup == "a quiz in one lesson, and a quiz graded":
        grade(conn, "g1", "2026-10-20")
    if setup.startswith("a QCM"):
        homework(conn, "w1", "2026-11-05", "Il y a un QCM au prochain cours.")
        grade(conn, "g1", "2026-11-09")
    if setup.startswith("the homework says"):
        homework(conn, "w1", "2026-11-05", "Interro écrite sur la leçon au début de chaque cours.")
    if setup.startswith("three quizzes"):
        for n, day in enumerate(["2026-11-03", "2026-11-06", "2026-11-10"]):
            grade(conn, f"g{n}", day, title="Interro orale")
    assert bool(proposals.detect(conn, "4242", TODAY)) == found


# --------------------------------------------------------------------- proposal, decision


def proposed(conn, url) -> dict:
    history_habit(conn)
    assert proposals.propose(conn, "4242", TODAY) == ["lesson_quiz:HI-GE"]
    instructions.process(url, None, "4242", TODAY, only_agent=True)
    [row] = [r for r in store.recent(conn) if r["origin"] == "agent"]
    return row


def reply(conn, instruction_id: str, kind: str, text: str = "", rule: dict | None = None, n: int = 1) -> None:
    feedback_store.save(conn, {"until": n, "instruction_replies": [
        {"id": n, "instruction_id": instruction_id, "kind": kind, "text": text, "rule": rule,
         "created_at": "2026-11-14T19:00:00+00:00"}]}, "4242")


def test_a_proposal_waits_for_the_parent_without_any_model(conn, url):
    row = proposed(conn, url)
    assert row["status"] == "proposed" and row["summary"].startswith("L'agent propose : En Histoire-géographie")
    assert "hi-ge-before-quick-quiz" not in {r.id for r in rules_store.latest(conn)}
    [update] = store.to_report(conn)
    assert update.origin == "agent" and update.facts and "DURAND" not in update.model_dump_json().upper()
    assert "Vu avec M. D." in " ".join(update.facts)  # no surname online (§17)
    assert proposals.propose(conn, "4242", TODAY) == []  # once


def test_an_accepted_proposal_becomes_the_agent_s_rule(conn, url):
    row = proposed(conn, url)
    reply(conn, row["id"], "approve")
    instructions.process(url, None, "4242", TODAY)
    rule = rules_store.get(conn, "hi-ge-before-quick-quiz")
    assert rule.status == "active" and rule.origin.author == "agent"
    assert proposals.acceptance(conn) == {"decided": 1, "accepted": 1, "edited": 0, "rejected": 0, "waiting": 0,
                                          "rate": 1.0}
    shown = {i.name: i for i in weekly.compute(conn, "4242", date(2026, 11, 13)).payload.indicators}
    assert shown["proposals"].value == "1 acceptée sur 1 (100 %)"


def test_a_refused_proposal_is_never_made_again_and_becomes_a_case(conn, url):
    row = proposed(conn, url)
    reply(conn, row["id"], "reject", "il interroge seulement avant les contrôles")
    instructions.process(url, None, "4242", TODAY)
    assert store.get(conn, row["id"])["status"] == "rejected"
    lesson(conn, "h3", "2026-11-13", "Interrogation orale sur le cours précédent.")
    assert proposals.propose(conn, "4242", TODAY) == []
    [case] = cases.load(conn, "instructions")
    assert case.expected == {"decision": "rejected", "rule": None, "reason": "il interroge seulement avant les contrôles"}


def test_an_edited_proposal_is_saved_as_changed_and_becomes_a_case(conn, url):
    row = proposed(conn, url)
    reply(conn, row["id"], "edit", rule={**row["proposal"], "format": "lesson_recap"})
    instructions.process(url, None, "4242", TODAY)
    assert rules_store.get(conn, "hi-ge-before-quick-quiz").format == "lesson_recap"
    [case] = cases.load(conn, "instructions")
    assert case.expected["decision"] == "edited" and case.expected["rule"]["format"] == "lesson_recap"
    assert proposals.acceptance(conn)["edited"] == 1

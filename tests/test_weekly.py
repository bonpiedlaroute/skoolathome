"""T24: the weekly review, the sheets to annotate, the agreement between the judge and the parent."""

from __future__ import annotations

from datetime import date

from psycopg.types.json import Jsonb

from skoolathome.agent import scores
from skoolathome.evaluation import weekly
from skoolathome.store import feedback
from test_graph import MATHS, FakeApi, FakeModels, add_lesson, run as run_evening

FRIDAY = date(2026, 11, 13)
JUDGED = {"available": True, "ok": True, "reasons": [], "verdict": {"recap_faithful": True, "recap_problem": "", "items": [
    {"id": "q1", "faithful": True, "answer_correct": True, "level_ok": True, "useful": True, "problem": ""},
    {"id": "q2", "faithful": True, "answer_correct": False, "level_ok": True, "useful": True, "problem": ""}]}}
FAILED = [{"attempt": n, "name": "citation", "ok": False, "reasons": ["q1 : n'est pas dans le cours"]} for n in (1, 2, 3)]


def sheet(conn, sheet_id, status="published", attempts=1, guards=(), judge=JUDGED, run_date=FRIDAY, rule="daily"):
    conn.execute(
        "INSERT INTO agent.sheets (id, run_date, task_id, student_id, subject_code, kind, status, body, attempts, guards, "
        "trace_id, model, prompt_version, release, judge, rule_id, rule_version) VALUES (%s, %s, %s, '4242', 'MATHS', "
        "'exercises', %s, %s, %s, %s, %s, 'm', 'v1', 'r', %s, %s, 1)",
        (sheet_id, run_date, sheet_id, status, Jsonb({"title": f"Fiche {sheet_id}"}) if status != "abstained" else None,
         attempts, Jsonb(list(guards)), f"trace-{sheet_id}", Jsonb(judge) if judge else None, rule))


def received(conn, **data) -> None:
    feedback.save(conn, {"until": 1, **data}, "4242")


def annotation(n, sheet_id, item, ok, problem=None):
    return {"id": n, "sheet_id": sheet_id, "item_id": item, "ok": ok, "problem": problem, "comment": "",
            "created_at": "2026-11-13T20:00:00+00:00"}


def test_the_review_puts_the_alerts_first(conn):
    for n in range(1, 7):
        sheet(conn, f"s{n}", attempts=2 if n == 1 else 1)
    sheet(conn, "a1", status="abstained", attempts=3, guards=FAILED, judge=None)
    sheet(conn, "a2", status="abstained", attempts=0, judge=None)  # nothing to read: not blocked by a guard
    sheet(conn, "old", run_date=date(2026, 11, 2))  # last week
    received(conn,
             reports=[{"id": 1, "sheet_id": "s1", "item_id": "q2", "role": "child", "reason": "wrong_answer",
                       "created_at": "2026-11-12T17:00:00+00:00"}],
             attempts=[{"id": n, "sheet_id": "s2", "item_id": f"q{n}", "role": "child", "mode": "solo",
                        "correct": n <= 7, "created_at": "2026-11-12T17:00:00+00:00"} for n in range(1, 11)])
    review = weekly.compute(conn, "4242", FRIDAY)
    payload = review.payload
    assert (payload.week, payload.start, payload.end) == ("2026-W46", date(2026, 11, 7), FRIDAY)
    shown = {i.name: i for i in payload.indicators}
    assert shown["blocked"].value == "1 sur 8 (12 %)" and "citation 3" in shown["blocked"].detail
    assert shown["abstained"].value == "2 sur 8 (25 %)" and not shown["abstained"].alert
    assert shown["reports"].alert and "réponse fausse 1" in shown["reports"].detail and "daily 1" in shown["reports"].detail
    assert shown["success"].value == "70 % sur 10 réponses" and not shown["success"].alert
    assert [i.name for i in payload.indicators if i.alert] == ["reports"] and payload.indicators[0].name == "reports"
    assert payload.title == "Révisions : bilan de la semaine (1 alerte)"
    assert payload.body.startswith("1 point d'attention : signalements. 8 fiches, 2 abstention(s), 1 signalement(s).")
    assert len(payload.to_annotate) == 5 and "old" not in {s.id for s in payload.to_annotate}
    assert weekly.compute(conn, "4242", FRIDAY).payload.to_annotate == payload.to_annotate  # the same draw


def test_the_agreement_compares_the_judge_and_the_parent_question_by_question(conn, monkeypatch):
    sheet(conn, "s1")
    received(conn, annotations=[annotation(1, "s1", "q1", True), annotation(2, "s1", "q2", True),
                                annotation(3, "s1", "q3", False, "useful")])
    found = weekly.agreement(conn)
    # q1: both OK; q2: the judge doubted the answer, the parent did not; q3: not judged, the parent says no.
    assert (found["items"], found["agreed"], found["disagreements"]) == (3, 1, ["s1 q2", "s1 q3"])
    langfuse = type("Fake", (), {"create_score": lambda self, **s: sent.append(s), "flush": lambda self: None})()
    sent: list[dict] = []
    monkeypatch.setattr(scores.tracing, "client", lambda: langfuse)
    assert scores.send(conn)["annotations"] == 3
    assert sorted((s["name"], s["value"]) for s in sent if s["comment"].startswith("q2")) == [
        ("judge.agreement", 0.0), ("parent.item_ok", 1.0)]
    # A sheet annotated is not proposed again.
    assert "s1" not in {s.id for s in weekly.to_annotate(conn, "4242", date(2026, 11, 7), FRIDAY, "2026-W46")}


def test_the_review_is_due_once_a_week_from_friday(conn):
    assert not weekly.due(conn, date(2026, 11, 12))  # Thursday
    assert weekly.due(conn, FRIDAY)
    weekly.save(conn, weekly.compute(conn, "4242", FRIDAY))
    assert not weekly.due(conn, date(2026, 11, 14)) and weekly.due(conn, date(2026, 11, 20))
    assert weekly.unsent(conn).week == "2026-W46"
    weekly.sent(conn, "2026-W46")
    assert weekly.unsent(conn) is None


def test_the_friday_evening_sends_the_review_with_its_batch(conn):
    from skoolathome.store import db

    url = db.database_url(conn.info.dbname)
    add_lesson(conn, "m1", "MATHS", MATHS)
    api = FakeApi()
    run_evening(url, FakeModels(), api=api, publish=True)
    [review] = api.reviews
    assert review.week == "2026-W46" and api.sessions == 4  # in the batch: no signature more (the 4th: home page)
    run_evening(url, FakeModels(), api=api, publish=True, restart=True)
    assert len(api.reviews) == 1  # once a week

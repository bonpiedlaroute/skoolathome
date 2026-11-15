"""T22: real feedback reaches the trace of the generation that made the question."""

from __future__ import annotations

from psycopg.types.json import Jsonb

from skoolathome.agent import scores, tracing
from skoolathome.store import feedback

SHEET = ("INSERT INTO agent.sheets (id, run_date, task_id, student_id, subject_code, kind, status, body, attempts, "
         "trace_id, model, prompt_version, release, rule_id, rule_version) VALUES (%s, '2026-11-13', %s, '4242', "
         "'HI-GE', 'quick_quiz', 'published', %s, 1, %s, 'ministral', 'v3', 'abc123', 'hi-ge-before-quick-quiz', 2)")


class FakeLangfuse:
    def __init__(self) -> None:
        self.scores: dict[str, dict] = {}  # by score id: sent twice, kept once

    def create_score(self, **score) -> None:
        self.scores[score["score_id"]] = score

    def flush(self) -> None:
        pass


def received(conn) -> None:
    """Answers and reports received before T22 (no trace sent with them), and one with its trace."""
    conn.execute(SHEET, ("s1", "s1", Jsonb({"items": []}), "trace-s1"))
    feedback.save(conn, {
        "until": 10,
        "attempts": [
            {"id": 1, "sheet_id": "s1", "item_id": "q1", "role": "child", "mode": "solo", "correct": False,
             "hints_used": 1, "created_at": "2026-11-14T17:00:00+00:00"},
            {"id": 2, "sheet_id": "s1", "item_id": "q2", "role": "parent", "mode": "together", "correct": True,
             "rating": "partial", "created_at": "2026-11-14T17:01:00+00:00", "trace_id": "trace-s1"},
            {"id": 3, "sheet_id": "by-hand", "item_id": "q1", "role": "child", "mode": "solo", "correct": True,
             "created_at": "2026-11-14T17:02:00+00:00"},
        ],
        "reports": [{"id": 7, "sheet_id": "s1", "item_id": "q3", "role": "child", "reason": "wrong_answer",
                     "comment": "c'est 1515", "created_at": "2026-11-14T17:03:00+00:00"}],
        "finishes": [{"id": "fin-1", "sheet_id": "s1", "role": "child", "score": 4, "total": 5, "to_review": ["q1"],
                      "finished_at": "2026-11-14T17:04:00+00:00"}],
    }, "4242")


def test_each_kind_of_feedback_becomes_a_score_on_its_sheet_trace(conn):
    received(conn)
    waiting, untraced = scores.pending(conn)
    assert untraced == 1  # the sheet written by hand has no trace
    found = {(s.name, s.value) for s in waiting}
    assert found == {("feedback.correct", False), ("feedback.parent_rating", "partial"),
                     ("feedback.report", "wrong_answer"), ("feedback.solo_score", 0.8)}
    assert {s.trace_id for s in waiting} == {"trace-s1"}
    report = next(s for s in waiting if s.name == "feedback.report")
    assert report.comment == "q3 : c'est 1515"
    assert report.metadata == {"rule": "hi-ge-before-quick-quiz", "rule_version": "2", "subject": "HI-GE",
                               "format": "quick_quiz", "model": "ministral", "prompt_version": "v3",
                               "release": "abc123", "item": "q3", "role": "child"}


def test_the_catch_up_is_sent_once(conn, monkeypatch):
    received(conn)
    assert scores.send(conn) == {"skipped": "Langfuse non configuré"}  # nothing marked: it waits
    langfuse = FakeLangfuse()
    monkeypatch.setattr(tracing, "client", lambda: langfuse)
    result = scores.send(conn)
    assert result == {"attempts": 2, "reports": 1, "finishes": 1, "untraced": 1}
    rating = langfuse.scores["skoolathome-attempts-2"]
    assert (rating["value"], rating["data_type"], rating["trace_id"]) == ("partial", "CATEGORICAL", "trace-s1")
    assert langfuse.scores["skoolathome-attempts-1"]["data_type"] == "BOOLEAN"
    assert scores.send(conn) == {"untraced": 1}  # already sent
    assert conn.execute("SELECT count(*) FROM feedback.attempts WHERE sent_to_langfuse_at IS NULL").fetchone()[0] == 1

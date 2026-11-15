"""T29: « Je n'ai pas compris »: an explanation from the lesson, checked like a sheet."""

from __future__ import annotations

from datetime import date

import psycopg
import pytest
from psycopg.types.json import Jsonb

from skoolathome import locks
from skoolathome.agent import explain, scores, tracing
from skoolathome.agent.explain import ExplanationDraft
from skoolathome.agent.llm import ModelError
from skoolathome.evaluation import cases, weekly
from skoolathome.schema.sheet import Item
from skoolathome.store import db, feedback

LESSON = ("Un nombre premier est un nombre entier qui a exactement deux diviseurs : 1 et lui-même. "
          "Par exemple, 2, 3, 5, 7 et 11 sont des nombres premiers.")
ITEM = {"id": "q1", "type": "short_answer", "prompt": "Combien de diviseurs a un nombre premier ?", "answer": "deux",
        "variants": ["2"], "check": {"mode": "variants"}, "solution": "Exactement deux : 1 et lui-même.",
        "concept": "nombres premiers"}
GOOD = ExplanationDraft(
    explanation="Un nombre premier se divise seulement par 1 et par lui-même. Il a donc exactement deux diviseurs.",
    quote="Un nombre premier est un nombre entier qui a exactement deux diviseurs", example="7 : ses diviseurs sont 1 et 7.")
OFF = ExplanationDraft(explanation="Il en a plusieurs.", quote="Les nombres premiers sont infinis.", example="")


class FakeModels:
    def __init__(self, *replies) -> None:
        self.replies, self.prompts = list(replies), []

    def structured(self, role, messages, schema, callbacks):
        assert schema is ExplanationDraft and role == "generator"
        self.prompts.append(messages[-1]["content"])
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeApi:
    def __init__(self, *pending: dict) -> None:
        self.pending, self.answers = list(pending), {}

    def pending_explanations(self) -> list[dict]:
        return self.pending

    def answer_explanation(self, explanation_id, answer) -> dict:
        self.answers[explanation_id] = answer
        return {"ok": True}


def sheet(conn, sheet_id: str = "s1", lesson: str = LESSON) -> None:
    conn.execute(
        "INSERT INTO agent.sheets (id, run_date, task_id, student_id, subject_code, kind, status, body, attempts, "
        "trace_id, model, prompt_version, release, model_input) VALUES (%s, '2026-11-13', %s, '4242', 'MATHS', "
        "'exercises', 'published', %s, 1, 'trace-s1', 'm', 'v1', 'r', %s)",
        (sheet_id, sheet_id, Jsonb({"title": "Nombres premiers", "items": [ITEM]}),
         Jsonb({"task": {"subject": "Mathématiques", "subject_code": "MATHS"},
                "sources": {"lesson:2026-11-12:MATHS": lesson},
                "labels": {"lesson:2026-11-12:MATHS": "ton cours du jeudi 12/11"}, "context": ""})))


def request(explanation_id: str = "exp-1", sheet_id: str = "s1") -> dict:
    return {"id": explanation_id, "sheet_id": sheet_id, "item_id": "q1", "question": "je ne vois pas pourquoi deux",
            "created_at": "2026-11-13T18:00:00+00:00"}


# --------------------------------------------------------------------------- the guards


def test_an_explanation_must_quote_the_lesson_and_reach_the_answer():
    item = Item.model_validate(ITEM)
    sources = {"lesson:2026-11-12:MATHS": LESSON}
    assert explain.check(GOOD, item, sources) == []
    problems = explain.check(OFF, item, sources)
    assert len(problems) == 2 and "n'est pas dans le cours" in problems[0] and "« deux »" in problems[1]
    by_variant = GOOD.model_copy(update={"explanation": "Il a 2 diviseurs : 1 et lui-même."})
    assert explain.check(by_variant, item, sources) == []  # « 2 » is an accepted answer too


# --------------------------------------------------------------------------- the making


def test_a_failed_explanation_is_written_again_with_its_reasons(conn):
    sheet(conn)
    models = FakeModels(OFF, GOOD)
    made = explain.make(conn, models, request())
    assert made.status == "done" and made.attempts == 2
    assert "Corrige ces points" in models.prompts[1] and "je ne vois pas pourquoi deux" in models.prompts[0]
    assert made.body.quote.source_id == "lesson:2026-11-12:MATHS" and made.body.source_label == "ton cours du jeudi 12/11"


@pytest.mark.parametrize("case, reason", [
    ("hand-written sheet", "écrite à la main"),
    ("lesson too short", "trop court"),
    ("the model says the lesson is not enough", "ne permet pas d'expliquer"),
    ("three refusals", "refusée après 3 essais"),
])
def test_without_usable_content_the_agent_abstains_and_says_why(conn, case, reason):
    models = FakeModels(GOOD)
    if case == "lesson too short":
        sheet(conn, lesson="Les nombres premiers.")
    elif case != "hand-written sheet":
        sheet(conn)
    if case == "the model says the lesson is not enough":
        models = FakeModels(GOOD.model_copy(update={"explanation": ""}))
    if case == "three refusals":
        models = FakeModels(OFF)
    made = explain.make(conn, models, request())
    assert made.status == "abstained" and reason in made.reason
    assert made.answer().explanation is None


def test_the_queue_is_answered_and_kept_on_the_laptop(conn):
    sheet(conn)
    api = FakeApi(request("exp-1"), request("exp-2", sheet_id="by-hand"))
    connect = lambda: psycopg.connect(db.database_url(conn.info.dbname), autocommit=True)  # noqa: E731
    done = explain.process(connect, api, FakeModels(ModelError("pas de réponse"), GOOD))
    assert done == [{"id": "exp-1", "status": "done"}, {"id": "exp-2", "status": "abstained"}]
    assert api.answers["exp-1"].explanation.text.startswith("Un nombre premier")
    row = conn.execute("SELECT status, attempts, trace_id FROM agent.explanations WHERE id = 'exp-1'").fetchone()
    assert row[:2] == ("done", 2) and row[2] == tracing.trace_id("explanation", "exp-1")


def test_still_not_understood_reaches_langfuse_the_reference_set_and_the_review(conn, monkeypatch):
    sheet(conn)
    api = FakeApi(request("exp-1"))
    connect = lambda: psycopg.connect(db.database_url(conn.info.dbname), autocommit=True)  # noqa: E731
    explain.process(connect, api, FakeModels(GOOD))
    feedback.save(conn, {"until": 1, "explanations": [{"id": "exp-1", "helped": False,
                                                       "helped_at": "2026-11-13T19:00:00+00:00"}]}, "4242")
    assert explain.unhelpful_cases(conn) == 1 and explain.unhelpful_cases(conn) == 0
    [case] = cases.load(conn, "explanations")
    assert case.expected["helped"] is False and case.input["item"]["id"] == "q1"
    sent = []
    monkeypatch.setattr(tracing, "client", lambda: type("F", (), {"create_score": lambda s, **k: sent.append(k),
                                                                  "flush": lambda s: None})())
    assert scores.send(conn)["explanations"] == 1
    [score] = [s for s in sent if s["name"] == "explanation.helped"]
    assert score["value"] == 0.0 and score["trace_id"] == tracing.trace_id("explanation", "exp-1")
    shown = {i.name: i for i in weekly.compute(conn, "4242", date(2026, 11, 13)).payload.indicators}
    indicator = shown["not_understood"]
    assert indicator.alert and indicator.value == "1 demande"
    assert indicator.detail == "Matières : Mathématiques 1. Notions : nombres premiers 1. Ça a aidé : 0 sur 1."


def test_the_evening_and_an_explanation_never_use_the_model_together(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    with locks.model(wait=True) as held:
        assert held
        with locks.model(wait=False) as second:
            assert not second
    with locks.model(wait=False) as free:
        assert free

"""T15 and T16: the evening graph with a fake model and a throw-away database."""

from __future__ import annotations

from datetime import date

import pytest

from skoolathome.agent import graph, lessons
from skoolathome.agent.draft import DraftSheet
from skoolathome.agent.llm import ModelError
from skoolathome.store import db
from test_guards import CHOICE, NUMBER, item

RUN = date(2026, 11, 13)
MATHS = ("Un nombre premier est un nombre entier qui a exactement deux diviseurs : 1 et lui-même. "
         "Par exemple, 2, 3, 5, 7 et 11 sont des nombres premiers. Pour décomposer un nombre en produit "
         "de facteurs premiers, on le divise successivement par 2, puis 3, puis 5.")
FRENCH = ("Yvain ou le Chevalier au lion est un roman de Chrétien de Troyes, écrit vers 1177. " * 8).strip()
GOOD = DraftSheet.model_validate({"title": "Nombres premiers", "items": [item(), NUMBER, CHOICE]})
INVENTED = DraftSheet.model_validate({"title": "Nombres premiers", "items": [item(quote="Les nombres premiers sont infinis selon Euclide")]})
FRENCH_GOOD = DraftSheet.model_validate({"title": "Yvain", "items": [item(
    prompt="Qui a écrit Yvain ou le Chevalier au lion ?", answer="Chrétien de Troyes", variants=[],
    quote="Yvain ou le Chevalier au lion est un roman de Chrétien de Troyes")]})


class FakeModels:
    """Answers per subject, in turn (the last reply repeats). An Exception is raised instead."""

    def __init__(self, maths=(GOOD,), french=(FRENCH_GOOD,)):
        self.replies = {"Mathématiques": list(maths), "Français": list(french)}
        self.prompts: list[tuple[str, list[dict]]] = []
        self.summaries = 0
        self.summary_error: Exception | None = None

    def text(self, role, messages, callbacks):
        self.summaries += 1
        if self.summary_error is not None:
            raise self.summary_error
        return "Résumé : Chrétien de Troyes, Yvain."

    def structured(self, role, messages, schema, callbacks):
        subject = next(line.removeprefix("Matière : ") for line in messages[-1]["content"].splitlines()
                       if line.startswith("Matière : "))
        self.prompts.append((subject, messages))
        queue = self.replies[subject]
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def calls(self, subject: str) -> int:
        return sum(1 for s, _ in self.prompts if s == subject)


def add_lesson(conn, source_id: str, subject_code: str, content: str, day: date = RUN) -> None:
    conn.execute(
        "INSERT INTO school.lessons (source, student_id, source_id, day, subject_code, subject, teacher, content, "
        "is_empty, fingerprint) VALUES ('ecoledirecte', '3030', %s, %s, %s, %s, 'M. X', %s, %s, %s)",
        (source_id, day, subject_code, subject_code, content, not content, f"fp-{source_id}-{len(content)}"),
    )


@pytest.fixture
def url(conn):
    return db.database_url(conn.info.dbname)


def run(url, models, **kwargs):
    return graph.run(RUN, "3030", models, url, **kwargs)


def sheets(conn) -> dict[str, tuple]:
    rows = conn.execute("SELECT id, status, attempts, reason, body IS NOT NULL, trace_id FROM agent.sheets").fetchall()
    return {r[0]: r[1:] for r in rows}


# ---------------------------------------------------------------- generation


def test_a_good_draft_is_saved_with_its_trace_id(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    results, resumed = run(url, FakeModels())
    assert not resumed
    assert [(r["task_id"], r["status"], r["attempts"]) for r in results] == [("2026-11-13-maths-exercises", "ready", 1)]
    status, attempts, reason, has_body, trace_id = sheets(conn)["2026-11-13-maths-exercises"]
    body = conn.execute("SELECT body FROM agent.sheets").fetchone()[0]
    assert status == "ready" and has_body and len(trace_id) == 32
    assert body["trace_id"] == trace_id and body["visible_from"] == "2026-11-14"
    assert body["sources"] == ["lesson:2026-11-13:MATHS"]


def test_a_failed_guard_regenerates_with_the_reason(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    models = FakeModels(maths=[INVENTED, GOOD])
    results, _ = run(url, models)
    assert results[0]["status"] == "ready" and results[0]["attempts"] == 2
    second_prompt = models.prompts[1][1][-1]["content"]
    assert "refusée par les vérifications" in second_prompt and "n'est pas dans le cours" in second_prompt
    log = conn.execute("SELECT guards FROM agent.sheets").fetchone()[0]
    assert {(g["attempt"], g["name"], g["ok"]) for g in log} >= {(1, "citation", False), (2, "citation", True)}


def test_after_three_failures_the_agent_abstains(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    models = FakeModels(maths=[INVENTED])
    results, _ = run(url, models)
    assert results[0]["status"] == "abstained" and results[0]["attempts"] == 3
    assert len(models.prompts) == 3
    status, attempts, reason, has_body, _ = sheets(conn)["2026-11-13-maths-exercises"]
    assert status == "abstained" and not has_body and "après 3 essais" in reason and "n'est pas dans le cours" in reason


def test_output_out_of_format_counts_as_a_failure(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    results, _ = run(url, FakeModels(maths=[ModelError("sortie hors format"), GOOD]))
    assert results[0]["status"] == "ready" and results[0]["attempts"] == 2


def test_a_lesson_without_text_is_not_sent_to_the_model(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    conn.execute("UPDATE school.lessons SET content = ''")
    models = FakeModels()
    results, _ = run(url, models, lesson_ids=["m1"])
    assert results[0]["status"] == "abstained" and "Rien d'exploitable" in results[0]["reason"]
    assert models.prompts == []


def test_one_branch_per_task(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    add_lesson(conn, "f1", "FRANC", FRENCH)
    add_lesson(conn, "e1", "EPS", "Course d'endurance : 3 tours de piste à allure régulière, puis étirements. " * 3)
    add_lesson(conn, "l1", "LCALA", "Rosa, rosae.")  # too short
    results, _ = run(url, FakeModels())
    assert sorted((r["task_id"], r["status"]) for r in results) == [
        ("2026-11-13-franc-exercises", "ready"), ("2026-11-13-maths-exercises", "ready")]


# ------------------------------------------------------------------ summaries


def test_each_lesson_is_summarized_once(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)  # short: its own summary
    add_lesson(conn, "f1", "FRANC", FRENCH)  # long: summarized by the model
    models = FakeModels()
    run(url, models)
    assert models.summaries == 1
    run(url, models, restart=True)
    assert models.summaries == 1  # nothing new arrived
    rows = dict(conn.execute("SELECT source_id, model FROM agent.lesson_summaries").fetchall())
    assert rows["m1"] == "none" and rows["f1"] != "none"
    conn.execute("UPDATE school.lessons SET content = content || ' Modifié.', fingerprint = 'new' WHERE source_id = 'f1'")
    run(url, models, restart=True)
    assert models.summaries == 2  # the teacher edited the lesson: summarized again


def test_a_summary_that_fails_is_tried_again_next_evening(conn, url):
    add_lesson(conn, "f1", "FRANC", FRENCH)
    models = FakeModels()
    models.summary_error = ModelError("pas de réponse du modèle : Request timed out.")
    results, _ = run(url, models)
    assert results[0]["status"] == "ready"  # the sheet reads the lesson itself
    assert conn.execute("SELECT count(*) FROM agent.lesson_summaries").fetchone()[0] == 0
    models.summary_error = None
    run(url, models, restart=True)
    assert conn.execute("SELECT model FROM agent.lesson_summaries").fetchone()[0] != "none"


# --------------------------------------------------------------------- resume


def test_a_run_stopped_half_way_resumes_without_redoing_finished_work(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    add_lesson(conn, "f1", "FRANC", FRENCH)
    # One task at a time, French first (the richer lesson). The machine "stops" during the maths one.
    crashing = FakeModels(maths=[RuntimeError("power cut")])
    with pytest.raises(RuntimeError):
        run(url, crashing)
    assert (crashing.calls("Français"), crashing.calls("Mathématiques")) == (1, 1)
    assert set(sheets(conn)) == {"2026-11-13-franc-exercises"}

    resuming = FakeModels()
    results, resumed = run(url, resuming)
    assert resumed
    assert (resuming.calls("Français"), resuming.calls("Mathématiques")) == (0, 1)  # French is not redone
    assert resuming.summaries == 0
    assert sorted((r["task_id"], r["status"]) for r in results) == [
        ("2026-11-13-franc-exercises", "ready"), ("2026-11-13-maths-exercises", "ready")]


def test_fixed_tasks_take_the_last_school_day(conn):
    add_lesson(conn, "old", "MATHS", MATHS, day=date(2026, 11, 12))
    add_lesson(conn, "new", "FRANC", FRENCH, day=date(2026, 11, 13))
    tasks = lessons.fixed_tasks(conn, "3030", date(2026, 11, 15))  # a Sunday
    assert [t["id"] for t in tasks] == ["2026-11-13-franc-exercises"]
    assert tasks[0]["visible_from"] == "2026-11-16" and not tasks[0]["computational"]
    forced = lessons.fixed_tasks(conn, "3030", date(2026, 11, 15), ["old"])
    assert [t["id"] for t in forced] == ["2026-11-12-maths-exercises"] and forced[0]["computational"]

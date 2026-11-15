"""T15 to T20: the evening graph with fake models, a fake API and a throw-away database."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pytest
from psycopg.types.json import Jsonb

from skoolathome.agent import graph
from skoolathome.agent.draft import AssessmentVerdict, DraftQuiz, DraftRecap, DraftSheet
from skoolathome.agent.judge import ItemVerdict, Verdict
from skoolathome.agent.llm import ModelError
from skoolathome.collectors.manual.source import ManualSource
from skoolathome.material import search
from skoolathome.publish.client import ApiError
from skoolathome.schema.request import NewRequest
from skoolathome.store import db
from skoolathome.store import requests as requests_store
from test_guards import CHOICE, NUMBER, RECAP, item
from test_material import FakeEmbedder, write_pdf

RUN = date(2026, 11, 13)  # a Friday
MATHS = ("Un nombre premier est un nombre entier qui a exactement deux diviseurs : 1 et lui-même. "
         "Par exemple, 2, 3, 5, 7 et 11 sont des nombres premiers. Pour décomposer un nombre en produit "
         "de facteurs premiers, on le divise successivement par 2, puis 3, puis 5.")
FRENCH = ("Yvain ou le Chevalier au lion est un roman de Chrétien de Troyes, écrit vers 1177. " * 8).strip()
GOOD = DraftSheet.model_validate({"title": "Nombres premiers", "items": [item(), NUMBER, CHOICE]})
INVENTED = DraftSheet.model_validate({"title": "Nombres premiers", "items": [item(quote="Les nombres premiers sont infinis selon Euclide")]})
FRENCH_GOOD = DraftSheet.model_validate({"title": "Yvain", "items": [item(
    prompt="Qui a écrit Yvain ou le Chevalier au lion ?", answer="Chrétien de Troyes", variants=[],
    quote="Yvain ou le Chevalier au lion est un roman de Chrétien de Troyes", concept="Yvain")]})
QUIZ = DraftQuiz.model_validate({"title": "Interro flash", "items": [item(), CHOICE, item(), CHOICE, item()]})
APPROVED = Verdict(items=[], recap_faithful=True)
REJECTED = Verdict(items=[ItemVerdict(id="q1", faithful=False, answer_correct=True, level_ok=True, useful=True,
                                      problem="la question parle d'autre chose que la citation")], recap_faithful=True)


class FakeModels:
    """Sheets per subject, in turn (the last reply repeats); verdicts of the judge, in turn. An
    Exception in a list is raised instead of answering."""

    def __init__(self, maths=(GOOD,), french=(FRENCH_GOOD,), verdicts=(APPROVED,), test=None):
        self.replies = {"Mathématiques": list(maths), "Français": list(french)}
        self.verdicts = list(verdicts)
        self.test = test or AssessmentVerdict(is_assessment=False, confidence=0.9)
        self.log: list[tuple[str, str, str]] = []  # (role, what, subject)
        self.prompts: list[tuple[str, list[dict]]] = []
        self.summaries = 0
        self.summary_error: Exception | None = None

    def text(self, role, messages, callbacks):
        self.summaries += 1
        if self.summary_error is not None:
            raise self.summary_error
        return "Résumé : Chrétien de Troyes, Yvain."

    @staticmethod
    def _next(queue: list):
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def structured(self, role, messages, schema, callbacks):
        subject = next(line.removeprefix("Matière : ") for line in messages[-1]["content"].splitlines()
                       if line.startswith("Matière : "))
        self.log.append((role, schema.__name__, subject))
        if schema is Verdict:
            return self._next(self.verdicts)
        if schema is AssessmentVerdict:
            return self.test
        self.prompts.append((subject, messages))
        if schema is DraftQuiz:
            return QUIZ
        if schema is DraftRecap:
            return DraftRecap.model_validate(RECAP)
        return self._next(self.replies[subject])

    def calls(self, subject: str) -> int:
        return sum(1 for s, _ in self.prompts if s == subject)

    def roles(self) -> list[str]:
        return [role for role, what, _ in self.log if what != "AssessmentVerdict"]


class FakeApi:
    """The online API through the bunker: one session per use, like the real one."""

    def __init__(self, feedback: dict | None = None, publish_error: Exception | None = None) -> None:
        self.sessions = 0
        self.data = feedback or {"since": 0, "until": 500, "attempts": [], "reports": [], "finishes": []}
        self.publish_error = publish_error
        self.cursors: list[int] = []
        self.published: list[str] = []
        self.answered: list[dict] = []
        self.reports: list[dict] = []

    @contextmanager
    def __call__(self):
        self.sessions += 1
        yield self

    def feedback(self, since: int) -> dict:
        self.cursors.append(since)
        return self.data

    def publish(self, sheets, requests=None) -> list[str]:
        if self.publish_error:
            raise self.publish_error
        self.published += [s.id for s in sheets]
        self.answered += [r.model_dump() for r in requests or []]
        return [s.id for s in sheets]

    def report(self, report: dict) -> dict:
        self.reports.append(report)
        return {"push": {"sent": 1}}


def add_lesson(conn, source_id: str, subject_code: str, content: str, day: date = RUN) -> None:
    conn.execute(
        "INSERT INTO school.lessons (source, student_id, source_id, day, subject_code, subject, teacher, content, "
        "is_empty, fingerprint) VALUES ('ecoledirecte', '3030', %s, %s, %s, %s, 'M. X', %s, %s, %s)",
        (source_id, day, subject_code, subject_code, content, not content, f"fp-{source_id}-{len(content)}"),
    )


@pytest.fixture
def url(conn):
    return db.database_url(conn.info.dbname)


def run(url, models, *, api=None, collector=None, embedder=None, material=None, progress=None,
        **kwargs) -> graph.Evening:
    services = graph.Services(models=models, api=api, collector=collector, embedder=embedder, material=material,
                              progress=progress)
    return graph.run(RUN, "3030", services, url, **kwargs)


def sheets(conn) -> dict[str, tuple]:
    rows = conn.execute("SELECT id, status, attempts, reason, body IS NOT NULL, trace_id FROM agent.sheets").fetchall()
    return {r[0]: r[1:] for r in rows}


def statuses(evening: graph.Evening) -> list[tuple[str, str]]:
    return sorted((r["task_id"], r["status"]) for r in evening.results)


# ---------------------------------------------------------------- generation, judge


def test_a_good_draft_is_judged_then_saved_with_its_trace_id(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    evening = run(url, FakeModels())
    assert not evening.resumed
    assert [(r["task_id"], r["status"], r["attempts"], r["rule"]) for r in evening.results] == [
        ("2026-11-13-maths-exercises", "ready", 1, "daily")]
    status, attempts, reason, has_body, trace_id = sheets(conn)["2026-11-13-maths-exercises"]
    body, judged, minutes, rule = conn.execute("SELECT body, judge, minutes, rule_id FROM agent.sheets").fetchone()
    assert status == "ready" and has_body and len(trace_id) == 32
    assert body["trace_id"] == trace_id and body["visible_from"] == "2026-11-14"
    assert body["sources"] == ["lesson:2026-11-13:MATHS"]
    assert body["source_labels"] == {"lesson:2026-11-13:MATHS": "ton cours du vendredi 13/11"}
    assert body["rule"] == {"id": "daily", "version": 1}
    assert judged["available"] and judged["ok"] and minutes == body["estimated_minutes"] and rule == "daily"


def test_all_generations_come_before_all_judgments(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    add_lesson(conn, "f1", "FRANC", FRENCH)
    # French first, then maths; the judge sends maths back once.
    models = FakeModels(verdicts=[APPROVED, REJECTED, APPROVED])
    evening = run(url, models)
    assert models.roles() == ["generator", "generator", "judge", "judge", "generator", "judge"]
    assert statuses(evening) == [("2026-11-13-franc-exercises", "ready"), ("2026-11-13-maths-exercises", "ready")]


def test_the_judge_sends_a_sheet_back_with_its_problem(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    models = FakeModels(verdicts=[REJECTED, APPROVED])
    [result] = run(url, models).results
    assert result["status"] == "ready" and result["attempts"] == 2
    second_prompt = models.prompts[1][1][-1]["content"]
    assert "q1 : (correcteur) la question parle d'autre chose" in second_prompt
    log = conn.execute("SELECT guards FROM agent.sheets").fetchone()[0]
    assert [(g["attempt"], g["ok"]) for g in log if g["name"] == "judge"] == [(1, False), (2, True)]


def test_the_judge_counts_in_the_three_attempts(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    models = FakeModels(verdicts=[REJECTED])
    [result] = run(url, models).results
    assert result["status"] == "abstained" and result["attempts"] == 3 and "(correcteur)" in result["reason"]
    assert models.calls("Mathématiques") == 3


def test_a_judge_that_cannot_answer_does_not_block_the_sheet(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    [result] = run(url, FakeModels(verdicts=[ModelError("pas de réponse du modèle")])).results
    assert result["status"] == "ready"
    judged = conn.execute("SELECT judge FROM agent.sheets").fetchone()[0]
    assert judged["available"] is False and "pas de réponse" in judged["error"]


def test_a_failed_guard_regenerates_with_the_reason(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    models = FakeModels(maths=[INVENTED, GOOD])
    [result] = run(url, models).results
    assert result["status"] == "ready" and result["attempts"] == 2
    second_prompt = models.prompts[1][1][-1]["content"]
    assert "refusée par les vérifications" in second_prompt and "n'est pas dans le cours" in second_prompt
    assert models.roles() == ["generator", "generator", "judge"]  # the guards' regeneration is immediate


def test_after_three_failures_the_agent_abstains(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    models = FakeModels(maths=[INVENTED])
    [result] = run(url, models).results
    assert result["status"] == "abstained" and result["attempts"] == 3
    assert models.roles() == ["generator"] * 3  # never judged
    status, attempts, reason, has_body, _ = sheets(conn)["2026-11-13-maths-exercises"]
    assert status == "abstained" and not has_body and "après 3 essais" in reason and "n'est pas dans le cours" in reason


def test_output_out_of_format_counts_as_a_failure(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    [result] = run(url, FakeModels(maths=[ModelError("sortie hors format"), GOOD])).results
    assert result["status"] == "ready" and result["attempts"] == 2


def test_a_lesson_without_text_is_not_sent_to_the_model(conn, url):
    add_lesson(conn, "m1", "MATHS", "")
    models = FakeModels()
    [result] = run(url, models, lesson_ids=["m1"]).results
    assert result["status"] == "abstained"
    assert result["reason"] == ("Rien de saisi pour le cours du vendredi 13/11 : demande-lui son cahier, "
                                "ou dépose une photo dans data/manual/.")
    assert models.prompts == []


def test_too_little_text_abstains_without_calling_the_model(conn, url):
    add_lesson(conn, "p1", "PH-CH", "Finir les exercices 15 à 22 du chapitre.")
    models = FakeModels()
    [result] = run(url, models, lesson_ids=["p1"]).results
    assert result["status"] == "abstained" and result["reason"].startswith("Trop peu de texte pour une fiche (40 caractères)")
    assert models.prompts == []


def test_short_lessons_get_fewer_questions(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)  # 230 characters: three questions, not six
    add_lesson(conn, "f1", "FRANC", FRENCH)  # 670 characters: four
    models = FakeModels()
    run(url, models)
    asked = {subject: messages[-1]["content"].split("Fiche : ")[1].split(" questions")[0] for subject, messages in models.prompts}
    assert asked == {"Mathématiques": "3", "Français": "4"}


def test_the_evening_says_what_it_is_doing(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    said: list[str] = []
    run(url, FakeModels(verdicts=[REJECTED, APPROVED]), progress=said.append)
    assert said[0] == "Collecte École Directe : sautée"
    assert "Plan : 1 fiche(s) à faire ce soir, 0 tâche(s) sautée(s), 0 déjà faite(s)" in said
    task = "2026-11-13-maths-exercises"
    assert [m for m in said if m.startswith(task) and "…" not in m][:4] == [
        f"{task} : fiche écrite en 0 s", f"{task} : garde-fous OK",
        f"{task} : renvoyée par le correcteur (q1 : (correcteur) la question parle d'autre chose que la citation)",
        f"{task} : fiche écrite en 0 s"]


def test_the_plan_decides_the_tasks_and_says_why_one_is_missing(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    add_lesson(conn, "f1", "FRANC", FRENCH)
    add_lesson(conn, "e1", "EPS", "Course d'endurance : 3 tours de piste à allure régulière, puis étirements. " * 3)
    add_lesson(conn, "l1", "LCALA", "Rosa, rosae.")  # too short
    evening = run(url, FakeModels())
    assert statuses(evening) == [("2026-11-13-franc-exercises", "ready"), ("2026-11-13-maths-exercises", "ready")]
    assert {s["task"]: s["reason"] for s in evening.plan["skipped"]} == {
        "2026-11-13-lcala-exercises": "séance du vendredi 13/11 trop courte (12 caractères)"}
    assert "daily v1" in evening.plan["rules"]
    # Made tonight: not made again by a second run of the same evening.
    again = run(url, FakeModels(), restart=True)
    assert again.results == [] and sorted(again.plan["done"]) == ["2026-11-13-franc-exercises", "2026-11-13-maths-exercises"]


# --------------------------------------------------------------------- formats (T20)


@pytest.mark.parametrize("kind, items", [("quick_quiz", 5), ("lesson_recap", 2), ("test_revision", 3)])
def test_each_format_is_made_with_its_own_prompt_and_shape(conn, url, kind, items):
    add_lesson(conn, "m1", "MATHS", MATHS)
    models = FakeModels()
    [result] = run(url, models, lesson_ids=["m1"], forced_format=kind).results
    assert result["status"] == "ready", result["reason"]
    body = conn.execute("SELECT body FROM agent.sheets").fetchone()[0]
    assert body["type"] == kind and len(body["items"]) == items
    assert (body["recap"] is not None) == (kind == "lesson_recap")
    first_line = models.prompts[0][1][0]["content"].splitlines()[0]
    expected = {"quick_quiz": "interro flash", "lesson_recap": "cours en clair", "test_revision": "révision"}[kind]
    assert expected in first_line.lower()


def test_a_missed_concept_comes_back_in_the_next_day_sheet(conn, url):
    add_lesson(conn, "m0", "MATHS", MATHS, day=date(2026, 11, 10))
    conn.execute(
        "INSERT INTO agent.sheets (id, run_date, task_id, student_id, subject_code, kind, status, body, attempts, "
        "trace_id, model, prompt_version, release) VALUES ('old', '2026-11-10', 'old', '3030', 'MATHS', 'exercises', "
        "'published', %s, 1, 'trace-old', 'm', 'v1', 'r')",
        (Jsonb({"subject": "Mathématiques", "items": [{"id": "q2", "concept": "nombres premiers",
                                                      "quote": {"source_id": "lesson:2026-11-10:MATHS"}}]}),),
    )
    conn.execute(
        "INSERT INTO feedback.attempts (id, sheet_id, item_id, role, mode, correct, created_at, received_at) "
        "VALUES (1, 'old', 'q2', 'child', 'solo', false, %s, %s)", (RUN, RUN))
    models = FakeModels()
    evening = run(url, models)
    assert ("2026-11-14-maths-missed", "ready") in statuses(evening)
    prompt = models.prompts[0][1][-1]["content"]
    assert "Notions ratées récemment" in prompt and "nombres premiers" in prompt
    log = conn.execute("SELECT guards FROM agent.sheets WHERE id = '2026-11-14-maths-missed'").fetchone()[0]
    assert any(g["name"] == "focus" and g["ok"] for g in log)


# ------------------------------------------------------------------ tests (T19)


def test_ambiguous_homework_read_by_the_model_brings_a_revision(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    conn.execute(
        "INSERT INTO school.homework (source, student_id, source_id, subject_code, subject, given_on, due_on, text, "
        "done, flagged_as_assessment, is_assessment, fingerprint) VALUES ('ecoledirecte', '3030', 'h1', 'MATHS', "
        "'MATHEMATIQUES', '2026-11-13', '2026-11-16', 'il y a un qcm au prochain cours', false, false, 'no', 'fp-h1')")
    models = FakeModels(test=AssessmentVerdict(is_assessment=True, date="2026-11-16", scope="nombres premiers", confidence=0.8))
    evening = run(url, models)
    assert conn.execute("SELECT is_assessment, day FROM agent.assessment_checks").fetchone() == (True, date(2026, 11, 16))
    made = [task for task, _ in statuses(evening)]
    assert "2026-11-16-maths-test-j1" in made and "2026-11-16-maths-test-j2" in made
    assert any(s["task"] == "2026-11-16-maths-test-j4" and "trop tard" in s["reason"] for s in evening.plan["skipped"])
    # Asked once per version of the homework text.
    run(url, models, restart=True)
    assert sum(1 for _, what, _ in models.log if what == "AssessmentVerdict") == 1


# -------------------------------------------------------------- documents (T18)


PAGE = ("Le crible d'Ératosthène permet de trouver tous les nombres premiers inférieurs à 100 : "
        "on barre les multiples de 2, de 3, de 5 et de 7.")
SIEVE = DraftSheet.model_validate({"title": "Crible", "items": [item(
    prompt="Comment trouve-t-on les nombres premiers inférieurs à 100 ?", answer="le crible d'Ératosthène",
    variants=[], quote="Le crible d'Ératosthène permet de trouver tous les nombres premiers inférieurs à 100")]})


def test_a_sheet_can_cite_the_manual_page_found_for_its_lesson(conn, url, tmp_path, monkeypatch):
    monkeypatch.setattr(search, "MIN_SCORE", 0.1)  # the fake embedder's scores are low
    write_pdf(tmp_path / "maths" / "manuel-maths-p86.pdf", PAGE)
    add_lesson(conn, "m1", "MATHS", MATHS)
    evening = run(url, FakeModels(maths=[SIEVE]), embedder=FakeEmbedder(), material=ManualSource(tmp_path))
    assert statuses(evening) == [("2026-11-13-maths-exercises", "ready")]
    body = conn.execute("SELECT body FROM agent.sheets").fetchone()[0]
    page_key = next(key for key in body["sources"] if key.startswith("doc:"))
    assert body["items"][0]["quote"]["source_id"] == page_key and page_key.endswith(":p86")
    assert body["source_labels"][page_key] == "ton manuel p. 86"


# ------------------------------------------------- collect, feedback, publish, report


def test_a_full_evening_collects_syncs_publishes_and_reports(conn, url):
    def collector() -> dict:
        add_lesson(conn, "m1", "MATHS", MATHS)  # what École Directe brought tonight
        return {"status": "ok", "student_id": "3030", "counts": {}, "errors": []}

    api = FakeApi()
    evening = run(url, FakeModels(), api=api, collector=collector, publish=True)
    assert statuses(evening) == [("2026-11-13-maths-exercises", "published")]
    assert api.published == ["2026-11-13-maths-exercises"]
    assert sheets(conn)["2026-11-13-maths-exercises"][0] == "published"
    assert api.cursors == [0] and conn.execute("SELECT since FROM feedback.cursor").fetchone()[0] == 500
    assert api.sessions == 3  # feedback, batch, report: one signature each
    [sent] = api.reports
    assert sent["status"] == "ok" and sent["body"].startswith("OK : 1 fiche.") and "RAM max" in sent["body"]
    assert evening.report["sent"] is True
    assert conn.execute("SELECT status, title FROM agent.runs").fetchone() == ("ok", "Révisions : soirée OK")


def test_a_failed_publication_is_a_ko_and_the_sheets_wait_for_tomorrow(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    api = FakeApi(publish_error=ApiError(503, "nip98: not configured"))
    evening = run(url, FakeModels(), api=api, publish=True)
    assert evening.report["status"] == "ko" and "publication en échec : 503" in evening.report["body"]
    assert sheets(conn)["2026-11-13-maths-exercises"][0] == "ready"
    assert api.reports[0]["status"] == "ko"


def test_a_collection_needing_two_factor_is_a_ko_but_the_sheets_are_made(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    evening = run(url, FakeModels(), collector=lambda: {"status": "two_factor_required", "errors": []})
    assert evening.report["status"] == "ko" and "double authentification" in evening.report["body"]
    assert statuses(evening) == [("2026-11-13-maths-exercises", "ready")]


def test_a_sheet_made_to_try_a_format_is_never_published_automatically(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS, day=date(2026, 11, 10))  # no daily task tonight
    api = FakeApi()
    evening = run(url, FakeModels(), api=api, publish=True, lesson_ids=["m1"], forced_format="quick_quiz")
    assert statuses(evening) == [("2026-11-10-maths-quick-quiz", "ready")]
    assert api.published == [] and evening.report["status"] == "ok"


def test_without_publication_nothing_goes_online(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    api = FakeApi()
    evening = run(url, FakeModels(), api=api, publish=False)
    assert api.published == [] and api.reports == [] and api.sessions == 1  # the feedback only
    assert "(non publiées)" in evening.report["body"]


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
    [result] = run(url, models).results
    assert result["status"] == "ready"  # the sheet reads the lesson itself
    assert conn.execute("SELECT count(*) FROM agent.lesson_summaries").fetchone()[0] == 0
    models.summary_error = None
    run(url, models, restart=True)
    assert conn.execute("SELECT model FROM agent.lesson_summaries").fetchone()[0] != "none"


# --------------------------------------------------------------------- resume


def test_a_run_stopped_half_way_resumes_without_redoing_finished_work(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    add_lesson(conn, "f1", "FRANC", FRENCH)
    # One task at a time, French first. The machine "stops" during the maths one.
    crashing = FakeModels(maths=[RuntimeError("power cut")])
    with pytest.raises(RuntimeError):
        run(url, crashing)
    assert (crashing.calls("Français"), crashing.calls("Mathématiques")) == (1, 1)
    assert sheets(conn) == {}  # nothing judged yet

    resuming = FakeModels()
    evening = run(url, resuming)
    assert evening.resumed
    assert (resuming.calls("Français"), resuming.calls("Mathématiques")) == (0, 1)  # French is not redone
    assert resuming.summaries == 0
    assert statuses(evening) == [("2026-11-13-franc-exercises", "ready"), ("2026-11-13-maths-exercises", "ready")]


# ----------------------------------------------------------- the parent's requests


FRACTIONS = ("Fractions égales : on ne change pas la valeur d'une fraction quand on multiplie le numérateur "
             "et le dénominateur par un même nombre non nul. Exemple : 3/4 = 6/8. Simplifier une fraction, "
             "c'est diviser le numérateur et le dénominateur par un même nombre.")
ON_FRACTIONS = DraftSheet.model_validate({"title": "Fractions égales", "items": [item(
    prompt="Que fait-on au numérateur et au dénominateur pour obtenir une fraction égale ?",
    answer="on multiplie le numérateur et le dénominateur par un même nombre non nul", variants=[],
    quote="on multiplie le numérateur et le dénominateur par un même nombre non nul", concept="fractions égales")]})


def test_a_request_keeps_the_closest_lessons_and_sees_the_whole_subject(conn, url, monkeypatch):
    monkeypatch.setattr(graph, "MAX_SOURCE_CHARS", 300)  # room for one lesson only
    add_lesson(conn, "m0", "MATHS", FRACTIONS, day=date(2026, 11, 9))
    add_lesson(conn, "m1", "MATHS", MATHS)  # the most recent, but on prime numbers
    request_id = requests_store.create(conn, NewRequest(subject="MATHS", kind="revision", focus="les fractions égales",
                                                        items=4), student_id="3030")
    models = FakeModels(maths=[ON_FRACTIONS])
    evening = run(url, models, only_requests=True)
    [result] = evening.results
    assert result["task_id"] == f"2026-11-13-maths-{request_id}" and result["status"] == "ready"
    prompt = models.prompts[0][1][-1]["content"]
    cours = prompt.rsplit("<cours>", 1)[1]
    assert "lesson:2026-11-09:MATHS" in cours and "lesson:2026-11-13:MATHS" not in cours  # the closest one
    assert "fiche demandée par le parent : une révision demandée par le parent sur « les fractions égales »" in prompt
    overview = prompt.split("seulement de <cours>) :")[1].rsplit("<cours>", 1)[0]
    assert "lundi 9/11" in overview and "vendredi 13/11" in overview  # every lesson, as a summary
    assert "Fiche : 3 questions" in prompt  # 4 asked, but 270 characters of lesson
    assert requests_store.recent(conn)[0]["status"] == "done"
    threads = {row[0] for row in conn.execute("SELECT DISTINCT thread_id FROM langgraph.checkpoints")}
    assert threads == {"2026-11-13-requests"}  # the evening's own run is untouched
    assert evening.report["title"] == "Révisions : fiches demandées OK"


def test_a_request_made_on_the_pwa_is_answered_in_the_batch(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    api = FakeApi(feedback={"since": 0, "until": 9, "attempts": [], "reports": [], "finishes": [], "requests": [
        {"id": "pwa-0a1b2c", "subject": "MATHS", "kind": "extra", "focus": "", "items": None,
         "created_at": "2026-11-13T18:00:00+00:00"},
        {"id": "pwa-3d4e5f", "subject": "SVT", "kind": "revision", "focus": "les volcans", "items": None,
         "created_at": "2026-11-13T18:01:00+00:00"}]})
    evening = run(url, FakeModels(), api=api, publish=True, only_requests=True)
    assert statuses(evening) == [("2026-11-13-maths-pwa-0a1b2c", "published"), ("2026-11-13-svt-pwa-3d4e5f", "abstained")]
    answered = {a["id"]: a for a in api.answered}
    assert answered["pwa-0a1b2c"]["status"] == "done" and answered["pwa-0a1b2c"]["sheet_id"] == "2026-11-13-maths-pwa-0a1b2c"
    assert answered["pwa-3d4e5f"]["status"] == "abstained" and "Rien en SVT" in answered["pwa-3d4e5f"]["reason"]
    assert conn.execute("SELECT count(*) FROM agent.requests WHERE reported_at IS NULL").fetchone()[0] == 0
    assert api.reports == []  # a requests-only run does not replace the evening's report

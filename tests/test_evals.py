"""T23: the reference set, `make eval` and its blocking thresholds, promotion."""

from __future__ import annotations

from datetime import date

import pytest
from psycopg.types.json import Jsonb

from skoolathome.agent import prompts
from skoolathome.agent.draft import AssessmentVerdict
from skoolathome.agent.judge import ItemVerdict, Verdict
from skoolathome.evaluation import cases, measures, runner
from test_graph import APPROVED, INVENTED, MATHS, FakeModels, add_lesson, run as run_evening

TASK = {"id": "2026-11-13-maths-exercises", "kind": "exercises", "subject": "Mathématiques", "subject_code": "MATHS",
        "target_minutes": 8, "computational": True, "visible_from": "2026-11-14", "rule": "daily", "rule_version": 1}
REAL = cases.Case("real-1", "sheets", "real", {"task": TASK, "sources": {"lesson:2026-11-13:MATHS": MATHS},
                                               "labels": {}, "context": ""}, {"abstain": False})
WRONG_ON_Q1 = Verdict(items=[ItemVerdict(id="q1", faithful=True, answer_correct=False, level_ok=True, useful=True,
                                         problem="")], recap_faithful=True)  # no problem written: the code lets it through


def test_names_are_taken_out_whatever_their_case_or_accents():
    value = {"a": ["M. DURAND a dit à Léo", "durand, Leo et LÉO"], "b": 3}
    assert cases.anonymize(value, ["Durand", "Léo"]) == {
        "a": ["M. Camille a dit à Camille", "Camille, Camille et Camille"], "b": 3}
    assert cases.anonymize("Léonard", ["Léo"]) == "Léonard"  # whole words only


def test_a_real_sheet_becomes_a_case_with_what_the_model_read(conn, url):
    add_lesson(conn, "m1", "MATHS", MATHS)
    conn.execute("UPDATE school.lessons SET teacher = 'M. EUCLIDE P.'")
    conn.execute("UPDATE school.lessons SET content = content || ' Vu avec M. Euclide.'")
    run_evening(url, FakeModels())
    case = cases.from_sheet(conn, "2026-11-13-maths-exercises", cases.private_names(conn))
    assert case.id == "real-2026-11-13-maths-exercises" and case.expected == {"abstain": False}
    assert case.input["task"]["kind"] == "exercises" and "Vu avec M. Camille." in case.input["sources"]["lesson:2026-11-13:MATHS"]
    cases.save(conn, case)
    cases.save(conn, case)  # unchanged: still one case, still marked as synced or not
    assert [c.id for c in cases.load(conn, "sheets")] == [case.id]


def test_a_report_becomes_a_case_with_the_right_answer(conn):
    conn.execute(
        "INSERT INTO agent.sheets (id, run_date, task_id, student_id, subject_code, kind, status, body, attempts, "
        "trace_id, model, prompt_version, release, model_input) VALUES ('s1', '2026-11-13', 's1', '4242', 'MATHS', "
        "'exercises', 'published', %s, 1, 't', 'm', 'v1', 'r', %s)",
        (Jsonb({"items": [{"id": "q2", "prompt": "Combien font 3/4 + 1/4 ?", "answer": "2"}]}), Jsonb(REAL.input)))
    conn.execute("INSERT INTO feedback.reports (id, sheet_id, item_id, role, reason, comment, created_at) "
                 "VALUES (9, 's1', 'q2', 'child', 'wrong_answer', 'ça fait 1', now())")
    with pytest.raises(ValueError, match="donne la bonne"):
        cases.from_report(conn, 9, [])
    case = cases.from_report(conn, 9, [], "1")
    assert case.id == "report-9" and case.origin == "report"
    assert case.expected["report"] == {"reason": "wrong_answer", "comment": "ça fait 1", "item": "q2",
                                       "prompt": "Combien font 3/4 + 1/4 ?", "answer": "2", "correct_answer": "1"}


def test_the_traps_are_loaded_from_the_repo():
    traps = [c for c in [cases.Case(t["id"], t["family"], "trap", t["input"], t["expected"])
                         for t in __import__("json").loads(cases.TRAPS.read_text())["cases"]]]
    assert {c.family for c in traps} == {"sheets", "tests"}
    assert all(c.expected.get("abstain") or "must_not_cite" in c.expected for c in traps if c.family == "sheets")


# ------------------------------------------------------------------------------ measures


def ready(sheet_items, verdict=APPROVED, attempts=1) -> dict:
    return {"status": "ready", "attempts": attempts, "sheet": {"items": sheet_items},
            "judge": {"available": True, "verdict": verdict.model_dump()}}


def test_a_trap_passes_by_abstaining_or_by_not_citing_the_page():
    abstained = {"status": "abstained", "attempts": 3, "sheet": None}
    assert measures.measure_sheet(abstained, {"abstain": True}, "trap") == {"traps": 1.0}
    on_page = ready([{"id": "q1", "prompt": "?", "answer": "x", "quote": {"source_id": "doc:999:p42"}}])
    found = measures.measure_sheet(on_page, {"abstain": False, "must_not_cite": ["doc:999:p42"]}, "trap")
    assert found["traps"] == 0.0 and found["grounded"] == 0.0


def test_a_reported_wrong_answer_that_comes_back_breaks_the_answers():
    expected = {"abstain": False, "report": {"reason": "wrong_answer", "item": "q2", "prompt": "Combien font 3/4 + 1/4 ?",
                                             "answer": "2", "correct_answer": "1"}}
    again = ready([{"id": "q1", "prompt": "Combien font 3/4 + 1/4 ?", "answer": "2", "quote": None}])
    assert measures.measure_sheet(again, expected, "report")["answers_correct"] == 0.0
    fixed = ready([{"id": "q1", "prompt": "Combien font 3/4 + 1/4 ?", "answer": "1", "quote": None}])
    found = measures.measure_sheet(fixed, expected, "report")
    assert found["answers_correct"] == 1.0 and found["reports_fixed"] == 1.0


def test_without_the_judge_the_answers_are_not_measured():
    output = {"status": "ready", "attempts": 1, "sheet": {"items": []}, "judge": {"available": False}}
    found = measures.measure_sheet(output, {"abstain": False}, "real")
    assert found["answers_correct"] is None and found["produced"] == 1.0
    assert measures.aggregate([found, {"answers_correct": 1.0}])["answers_correct"] == {"value": 1.0, "cases": 1}


# ---------------------------------------------------------------------------- the runs


@pytest.fixture
def url(conn):
    from skoolathome.store import db

    return db.database_url(conn.info.dbname)


def evaluate(conn, family, label, models, only=None):
    return runner.evaluate(conn, family, label, models, only=only)


def test_make_eval_measures_the_candidate_against_production(conn):
    cases.load_traps(conn)
    cases.save(conn, REAL)
    baseline = evaluate(conn, "sheets", "production", FakeModels(), only=["real-1", "trap-empty-lesson", "trap-too-short"])
    assert baseline.passed and baseline.metrics["traps"] == {"value": 1.0, "cases": 2}
    assert baseline.metrics["answers_correct"]["value"] == 1.0 and baseline.metrics["produced"]["value"] == 1.0
    # A degraded candidate: the judge finds a wrong answer it cannot explain; the sheet still goes out.
    degraded = evaluate(conn, "sheets", "staging", FakeModels(verdicts=[WRONG_ON_Q1]), only=["real-1"])
    assert not degraded.passed and measures.failed_thresholds(degraded.metrics) == ["answers_correct"]
    text = runner.report(degraded, runner.latest(conn, "sheets", "production"))
    assert "Corrigés justes" in text and "100% (1)" in text and "0% (1)" in text and "BLOQUANT" in text
    assert "ce candidat ne passe pas en production" in text
    # A candidate that abstains is not blocked on the answers, but the report shows it.
    shy = evaluate(conn, "sheets", "staging", FakeModels(maths=[INVENTED]), only=["real-1"])
    assert shy.metrics["produced"]["value"] == 0.0


def test_test_detection_must_find_every_test(conn):
    cases.load_traps(conn)
    found = evaluate(conn, "tests", "staging", FakeModels(test=AssessmentVerdict(is_assessment=True, confidence=0.7)))
    assert found.passed and found.metrics["test_recall"] == {"value": 1.0, "cases": 3}
    assert found.metrics["false_alarms"]["value"] == 0.0  # « exercices 4 et 5 »: not asked to the model
    blind = evaluate(conn, "tests", "staging", FakeModels(test=AssessmentVerdict(is_assessment=False, confidence=0.9)))
    assert not blind.passed and blind.metrics["test_recall"]["value"] == pytest.approx(1 / 3, abs=0.001)


def test_a_prompt_is_promoted_only_after_a_passing_run_of_that_version(conn, monkeypatch):
    versions = {("sheet-generator", "staging"): 4, ("sheet-generator", "production"): 3}
    monkeypatch.setattr(prompts, "label_version", lambda name, label: versions.get((name, label)))
    monkeypatch.setattr(prompts, "names", lambda: ["sheet-generator"])
    monkeypatch.setattr(prompts, "same_text", lambda name, label, other: False)
    moved = []
    monkeypatch.setattr(prompts, "move_label", lambda name, version, label: moved.append((name, version)))
    assert runner.promote(conn).refused == {"sheet-generator": "aucun make eval avec la version 4 (jeu « fiches »)"}

    def record(passed: bool) -> None:
        conn.execute("INSERT INTO agent.eval_runs (family, label, config, finished_at, metrics, passed) VALUES "
                     "('sheets', 'staging', %s, now(), %s, %s)",
                     (Jsonb({"prompts": {"sheet-generator": "v4"}}), Jsonb({"grounded": {"value": 0.5, "cases": 2}}),
                      passed))

    record(False)
    assert "seuil non tenu (Ancrage dans le cours)" in runner.promote(conn).refused["sheet-generator"]
    record(True)
    assert runner.promote(conn).moved == {"sheet-generator": (3, 4)} and moved == [("sheet-generator", 4)]


def test_the_recall_of_tests_is_measured_against_the_grades(conn):
    conn.execute("INSERT INTO school.collection_runs (source, student_id, started_at, status) VALUES "
                 "('ecoledirecte', '4242', '2026-11-04 18:00+00', 'ok')")
    conn.execute(
        "INSERT INTO school.grades (source, student_id, source_id, subject_code, subject, day, kind, title, raw_value, "
        "period, fingerprint) VALUES ('ecoledirecte', '4242', 'g0', 'MATHS', 'MATHS', '2026-11-03', 'Contrôle', "
        "'Avant la première collecte', '12', 'A001', 'f'), ('ecoledirecte', '4242', 'g3', 'EPS', 'EPS', '2026-11-12', "
        "'Contrôle', 'Tennis de table', '14', 'A001', 'f')")
    conn.execute(
        "INSERT INTO school.grades (source, student_id, source_id, subject_code, subject, day, kind, title, raw_value, "
        "period, fingerprint) VALUES ('ecoledirecte', '4242', 'g1', 'MATHS', 'MATHS', '2026-11-12', 'Contrôle', "
        "'Fractions', '15', 'A001', 'f'), ('ecoledirecte', '4242', 'g2', 'HI-GE', 'HG', '2026-11-10', 'Interrogation', "
        "'Leçon 3', '8', 'A001', 'f')")
    conn.execute(
        "INSERT INTO school.homework (source, student_id, source_id, subject_code, subject, given_on, due_on, text, done, "
        "flagged_as_assessment, is_assessment, fingerprint, first_seen) VALUES ('ecoledirecte', '4242', 'h1', 'MATHS', "
        "'MATHS', '2026-11-05', '2026-11-12', 'Contrôle sur les fractions', false, false, 'yes', 'f', '2026-11-05'), "
        "('ecoledirecte', '4242', 'h2', 'HI-GE', 'HG', '2026-11-05', '2026-11-10', 'Apprendre la leçon 3', false, false, "
        "'no', 'f', '2026-11-05')")
    found = cases.observed_recall(conn, "4242", date(2026, 11, 1), date(2026, 11, 15))
    assert (found["observed"], found["detected"]) == (2, 1)
    assert found["missed"] == [{"subject": "Histoire-géographie", "day": "2026-11-10", "title": "Leçon 3"}]
    made = cases.from_grades(conn, "4242", days=100000)
    assert sorted(c.input["text"] for c in made) == ["Apprendre la leçon 3", "Contrôle sur les fractions"]

"""T31, T32, T33: the search, the instructions, the plan and the judge in make eval; the journal of
changes, rollback and watching after; the target difficulty."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

from skoolathome.agent import evaluate_instructions, prompts
from skoolathome.agent.judge import ItemVerdict, Verdict
from skoolathome.evaluation import cases, changes, plan_tests, runner, weekly
from skoolathome.material.index import vector_literal
from skoolathome.rules import calibration
from test_instructions import FakeModels as ReadingModels
from test_instructions import reading
from test_material import FakeEmbedder

SAMPLE = json.loads((Path(__file__).parent.parent / "examples" / "sheet-sample.json").read_text())


# ------------------------------------------------------------------- difficulty (T33)


def test_twelve_right_answers_out_of_twelve_raise_the_difficulty():
    found = calibration.replay([True] * 12)
    assert found.level == 3 and [(c.answer, c.before, c.after) for c in found.changes] == [(10, 2, 3)]


def test_four_out_of_ten_lower_it():
    found = calibration.replay([True, False, False, True, False, True, False, False, True, False])
    assert found.level == 1 and found.changes[0].rate == 0.4


def test_two_changes_are_at_least_ten_answers_apart_and_stay_between_one_and_three():
    found = calibration.replay([True] * 35)
    assert found.level == 3 and len(found.changes) == 1  # 3 is the top
    down_up = calibration.replay([False] * 10 + [True] * 9 + [True] * 10)
    answers = [c.answer for c in down_up.changes]
    assert answers == [10, 20] and all(b - a >= calibration.GAP for a, b in zip(answers, answers[1:]))
    middle = calibration.replay([True, False] * 15)  # 50 %, then 50 %… : down once, then nothing below 1
    assert middle.level == 1 and len(middle.changes) == 1
    assert not calibration.replay([True] * 9).calibrated


def answer(conn, sheet_id: str, item: str, correct: bool, mode: str = "solo", minutes: int = 0) -> None:
    conn.execute(
        "INSERT INTO feedback.attempts (id, sheet_id, item_id, role, mode, response, correct, created_at) "
        "VALUES ((SELECT coalesce(max(id), 0) + 1 FROM feedback.attempts), %s, %s, 'child', %s, %s, %s, %s)",
        (sheet_id, item, mode, Jsonb("x"), correct, datetime(2026, 11, 10, 18) + timedelta(minutes=minutes)),
    )


def sheet_row(conn, sheet_id: str, code: str = "MATHS", kind: str = "exercises", version: str = "v4",
              created: datetime | None = None, status: str = "published", body: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO agent.sheets (id, run_date, task_id, student_id, subject_code, kind, status, body, attempts, "
        "trace_id, model, prompt_version, release, created_at, visible_from) VALUES (%s, '2026-11-10', %s, '4242', %s, "
        "%s, %s, %s, 1, 't', 'm', %s, 'r', %s, '2026-11-10')",
        (sheet_id, sheet_id, code, kind, status, Jsonb(body or {"items": []}), version, created or datetime.now()),
    )


def test_only_the_answers_given_alone_count(conn):
    sheet_row(conn, "s1")
    for n in range(12):
        answer(conn, "s1", f"q{n}", True, minutes=n)
    for n in range(12, 20):
        answer(conn, "s1", f"q{n}", False, mode="together", minutes=n)  # the parent helps: not counted
    [maths] = calibration.levels(conn, "4242").values()
    assert maths.answers == 12 and maths.level == 3 and maths.changes[0].at is not None


def test_the_target_difficulty_reaches_the_generator():
    from skoolathome.agent.graph import _context

    task = {"kind": "exercises", "subject": "Mathématiques", "computational": True, "difficulty": 3}
    assert "Difficulté visée : 3 sur 3" in _context(task, {}) and "citation" in _context(task, {})
    assert "Difficulté" not in _context({**task, "kind": "quick_quiz"}, {})  # the quiz keeps its own
    assert "Difficulté" not in _context({k: v for k, v in task.items() if k != "difficulty"}, {})


# --------------------------------------------------------------------- search (T31)


def page(conn, title: str, number: int, text: str, embedder, subject: str = "MATHS") -> None:
    row = conn.execute("SELECT id FROM material.documents WHERE title = %s", (title,)).fetchone()
    if row is None:
        row = conn.execute(
            "INSERT INTO material.documents (source, source_ref, title, subject_code, sha256, status, embedding_model) "
            "VALUES ('manual', %s, %s, %s, 'sha', 'indexed', %s) RETURNING id",
            (f"{title}.pdf", title, subject, embedder.model)).fetchone()
    conn.execute("INSERT INTO material.pages (document_id, number, text, ocr) VALUES (%s, %s, %s, false)",
                 (row[0], number, text))
    conn.execute("INSERT INTO material.chunks (document_id, page, position, text, embedding) VALUES (%s, %s, 0, %s, "
                 "%s::vector)", (row[0], number, text, vector_literal(embedder.embed([text])[0])))


def lesson(conn, source_id: str, content: str) -> None:
    conn.execute(
        "INSERT INTO school.lessons (source, student_id, source_id, day, subject_code, subject, teacher, content, "
        "is_empty, fingerprint) VALUES ('ecoledirecte', '4242', %s, '2026-11-12', 'MATHS', 'MATHS', 'M. X', %s, "
        "false, 'fp')", (source_id, content))


class ConstantEmbedder:
    """A deliberately bad model: every text is the same vector."""

    model = "bad-embedder"

    def embed(self, texts):
        return [[1.0] + [0.0] * 1023 for _ in texts]


def search_set(conn) -> None:
    good = FakeEmbedder()
    page(conn, "Manuel de maths", 86, "Symétrie centrale : le symétrique d'un point par rapport à un centre", good)
    page(conn, "Manuel de maths", 87, "Les fractions égales : multiplier numérateur et dénominateur", good)
    page(conn, "Manuel de maths", 88, "Périmètre du cercle et aire du disque, nombre pi", good)
    page(conn, "Manuel de maths", 89, "Angles alternes internes et droites parallèles", good)
    lesson(conn, "l1", "Symétrie centrale : construire le symétrique d'un point, centre de symétrie")
    lesson(conn, "l2", "Fractions égales : simplifier une fraction, numérateur et dénominateur")
    cases.annotate_lesson(conn, "l1", "Manuel de maths", 86)
    cases.annotate_lesson(conn, "l2", "Manuel de maths", 87)


def test_the_recall_at_three_of_the_search(conn):
    search_set(conn)
    found = runner.evaluate(conn, "retrieval", "staging", None, embedder=FakeEmbedder())
    assert found.passed and found.metrics["retrieval_recall"] == {"value": 1.0, "cases": 2}
    assert found.results[0]["output"]["hits"][0]["page"] in (86, 87)


def test_a_bad_embedding_model_is_blocked_without_touching_the_index(conn):
    search_set(conn)
    lesson(conn, "l3", "Angles alternes internes formés par deux droites parallèles")
    lesson(conn, "l4", "Le disque : son aire et le périmètre du cercle avec pi")
    cases.annotate_lesson(conn, "l3", "Manuel de maths", 89)
    cases.annotate_lesson(conn, "l4", "Manuel de maths", 88)
    bad = runner.evaluate(conn, "retrieval", "staging", None, embedder=ConstantEmbedder())
    assert not bad.passed and bad.metrics["retrieval_recall"]["value"] < 0.9
    assert "BLOQUANT" in runner.report(bad, None)
    models = conn.execute("SELECT DISTINCT embedding_model FROM material.documents").fetchall()
    assert models == [("fake-embedder",)]  # the index is untouched


def test_a_lesson_is_annotated_with_a_known_document_only(conn):
    lesson(conn, "l1", "Symétrie centrale")
    with pytest.raises(LookupError, match="document inconnu"):
        cases.annotate_lesson(conn, "l1", "Un livre qui n'existe pas", 3)
    with pytest.raises(LookupError, match="séance inconnue"):
        cases.annotate_lesson(conn, "nope", "Manuel", 3)


# ---------------------------------------------------------------- instructions (T31)


def test_the_sentences_of_the_repo_and_the_corrections_are_cases(conn):
    loaded = cases.load_files(conn)
    sentences = [c for c in cases.load(conn, "instructions") if c.origin == "sentence"]
    assert len(sentences) == 10 and loaded >= 10 + 1
    conn.execute(
        "INSERT INTO agent.instruction_corrections (instruction_id, text, proposed, final) VALUES ('i1', %s, %s, %s)",
        ("En histoire, une interro la veille de chaque cours.",
         Jsonb({"subject": "HI-GE", "trigger": "before_each_lesson", "scope": "previous_lesson", "format": "exercises",
                "items": None}),
         Jsonb({"subject": "HI-GE", "trigger": "before_each_lesson", "scope": "previous_lesson", "format": "quick_quiz",
                "items": 5})))
    assert cases.from_corrections(conn) == 1
    [correction] = [c for c in cases.load(conn, "instructions") if c.origin == "correction"]
    assert correction.expected == {"subject": "HI-GE", "trigger": "before_each_lesson", "scope": "previous_lesson",
                                   "format": "quick_quiz", "items": 5}


def test_the_interpretation_is_compared_field_by_field(conn):
    cases.save(conn, cases.Case("sentence-history", "instructions", "sentence",
                                {"sentence": "Le prof d'histoire interroge à chaque cours sur le cours précédent."},
                                {"subject": "HI-GE", "trigger": "before_each_lesson", "format": "quick_quiz"}))
    cases.save(conn, cases.Case("proposal-x", "instructions", "proposal", {"facts": []}, {"decision": "rejected"}))
    right = runner.evaluate(conn, "instructions", "staging", ReadingModels(reading()))
    assert right.passed and right.metrics["instructions_correct"] == {"value": 1.0, "cases": 1}  # the proposal: kept aside
    wrong = runner.evaluate(conn, "instructions", "staging", ReadingModels(reading(format="exercises")))
    assert not wrong.passed
    assert wrong.results[0]["output"]["problems"] == ["format : 'exercises' au lieu de 'quick_quiz'"]


# ------------------------------------------------------------------------ judge (T33)


class JudgeModels:
    def __init__(self, wrong: set[str]) -> None:
        self.wrong = wrong

    def structured(self, role, messages, schema, callbacks):
        assert role == "judge" and schema is Verdict
        return Verdict(items=[ItemVerdict(id=i["id"], faithful=True, answer_correct=i["id"] not in self.wrong,
                                          level_ok=True, useful=True,
                                          problem="faux" if i["id"] in self.wrong else "")
                              for i in SAMPLE["items"]], recap_faithful=True)


def annotated(conn) -> None:
    """The parent found q2 wrong on the sample sheet, the rest right."""
    model_input = {"task": {"kind": "exercises", "subject": "Mathématiques", "subject_code": "MATHS"},
                   "sources": {"lesson:2026-11-10:MATHS": "Les fractions égales."}, "labels": {}, "context": ""}
    sheet_row(conn, "sample", body=SAMPLE)
    conn.execute("UPDATE agent.sheets SET model_input = %s WHERE id = 'sample'", (Jsonb(model_input),))
    for n, item in enumerate(SAMPLE["items"]):
        conn.execute("INSERT INTO feedback.annotations (id, sheet_id, item_id, ok, created_at) VALUES (%s, 'sample', %s, "
                     "%s, now())", (n + 1, item["id"], item["id"] != "q2"))


def test_the_judge_set_comes_from_the_annotations_and_blocks_a_degraded_judge(conn):
    annotated(conn)
    assert cases.judge_cases(conn) == 1
    [case] = cases.load(conn, "judge")
    assert case.expected["items"]["q2"] is False and case.input["sheet"]["id"] == SAMPLE["id"]
    agreeing = runner.evaluate(conn, "judge", "staging", JudgeModels(wrong={"q2"}))
    assert agreeing.passed and agreeing.metrics["judge_agreement"]["value"] == 1.0
    degraded = runner.evaluate(conn, "judge", "staging", JudgeModels(wrong={"q1", "q3", "q4"}))
    assert not degraded.passed and degraded.metrics["judge_agreement"]["value"] == pytest.approx(3 / 7, abs=0.001)


# ------------------------------------------------------------------------- plan (T31)


def test_the_plan_tests_are_a_family_of_make_eval(conn):
    found = runner.evaluate(conn, "plan", "staging", None)
    assert found.passed and found.metrics["plan_tests"]["cases"] > 10
    failing = plan_tests.run(("tests/test_plan.py::test_daily_exercise_the_evening_of_a_lesson", "tests/nope.py"))
    assert failing and failing[0]["measures"]["plan_tests"] == 0.0
    text = runner.overview([found])
    assert "plan du soir" in text and "Tests du plan du soir" in text and "OK" in text


# --------------------------------------------------------------------- journal (T32)


@pytest.fixture
def langfuse_labels(monkeypatch):
    """Prompt labels in memory: {(name, label): version}."""
    labels = {("sheet-generator", "production"): 3, ("sheet-generator", "staging"): 4}
    monkeypatch.setattr(prompts, "label_version", lambda name, label: labels.get((name, label)))

    def move(name, version, label):
        labels[(name, label)] = version

    monkeypatch.setattr(prompts, "move_label", move)
    return labels


def passing_run(conn, version: int, passed: bool = True) -> None:
    for family in runner.users("sheet-generator"):
        conn.execute("INSERT INTO agent.eval_runs (family, label, config, finished_at, metrics, passed) VALUES "
                     "(%s, 'staging', %s, now(), %s, %s)",
                     (family, Jsonb({"prompts": {"sheet-generator": f"v{version}"}}),
                      Jsonb({"answers_correct": {"value": 1.0 if passed else 0.5, "cases": 2}}), passed))


def test_a_version_without_a_passing_make_eval_is_refused(conn, langfuse_labels):
    assert "aucun make eval avec la version 4" in changes.promote(conn, "sheet-generator", 4)
    passing_run(conn, 4, passed=False)
    assert "seuil non tenu" in changes.promote(conn, "sheet-generator", 4)
    assert changes.journal(conn) == [] and langfuse_labels[("sheet-generator", "production")] == 3


def test_promote_then_roll_back_in_one_command_both_in_the_journal(conn, langfuse_labels):
    passing_run(conn, 4)
    assert changes.promote(conn, "sheet-generator", 4, "3 réponses fausses en maths (bilan W46)") == ""
    assert langfuse_labels[("sheet-generator", "production")] == 4
    [promoted] = changes.journal(conn)
    assert (promoted["kind"], promoted["before"], promoted["after"]) == ("prompt", "v3", "v4")
    assert promoted["why"].startswith("3 réponses") and promoted["eval"]["runs"][0]["passed"]
    assert promoted["command"] == "skoolathome prompt rollback sheet-generator"
    assert changes.rollback(conn, "sheet-generator", "réponses fausses depuis") == ("", 3)
    assert langfuse_labels[("sheet-generator", "production")] == 3
    assert changes.journal(conn)[0]["kind"] == "rollback"
    assert changes.rollback(conn, "sheet-generator")[0].startswith("aucune promotion")  # v3 came before the journal


def test_a_wrong_answer_on_a_version_just_promoted_alerts_once(conn, langfuse_labels):
    passing_run(conn, 4)
    changes.promote(conn, "sheet-generator", 4)
    sheet_row(conn, "new", version="v4")
    sheet_row(conn, "other", kind="quick_quiz", version="v4")  # another prompt: not concerned
    for n, sheet in enumerate(("new", "other")):
        conn.execute("INSERT INTO feedback.reports (id, sheet_id, item_id, role, reason, comment, created_at) "
                     "VALUES (%s, %s, 'q1', 'parent', 'wrong_answer', '', now())", (n + 1, sheet))
    [alert] = changes.alerts(conn)
    assert alert["sheet"] == "new" and "skoolathome prompt rollback sheet-generator" in alert["body"]
    changes.notified(conn, [alert])
    assert changes.alerts(conn) == []


def test_a_change_of_model_is_noted_once(conn, monkeypatch):
    from skoolathome.agent import config

    assert set(changes.record_config(conn)) == {"engine", "generator", "judge", "vision", "embedder"}
    assert changes.record_config(conn) == []
    real = config.load()
    other = config.Config(real.engine, {**real.models, "generator": config.Model(**{
        **real.models["generator"].__dict__, "model": "mistral-small:24b", "quantization": "Q4_K_M"})})
    assert changes.record_config(conn, other) == ["generator"]
    latest = changes.journal(conn)[0]
    assert latest["before"].startswith("ministral") and latest["after"] == "mistral-small:24b Q4_K_M"
    assert "git revert" in latest["command"]


def test_the_review_compares_before_and_after_a_change(conn, langfuse_labels):
    sheet_row(conn, "old", version="v3", created=datetime.now() - timedelta(days=3))
    answer(conn, "old", "q1", False)
    passing_run(conn, 4)
    changes.promote(conn, "sheet-generator", 4)
    sheet_row(conn, "new", version="v4")
    answer(conn, "new", "q1", True)
    [watched] = changes.recent(conn, datetime.now().date())
    assert watched["compared"]["before"]["success"] == 0.0 and watched["compared"]["after"]["success"] == 1.0
    review = weekly.compute(conn, "4242", datetime.now().date())
    [indicator] = [i for i in review.payload.indicators if i.name == "changes"]
    assert "sheet-generator v3 → v4" in indicator.detail and "réussite 0 % → 100 %" in indicator.detail


def test_the_export_is_enough_to_rebuild_the_sets(conn, tmp_path):
    cases.load_files(conn)
    changes.record(conn, "prompt", "sheet-generator", "v3", "v4", "bilan")
    folder = changes.export(conn, tmp_path, datetime(2026, 11, 20).date())
    assert {p.name for p in folder.iterdir()} == {"cases.json", "prompts.json", "changes.json", "runs.json"}
    exported = json.loads((folder / "cases.json").read_text())
    assert len(exported) == len(cases.load(conn))
    assert json.loads((folder / "prompts.json").read_text())["sheet-generator"][0]["messages"]  # Langfuse off: local
    conn.execute("DELETE FROM agent.eval_cases")
    assert changes.restore(conn, folder)["cases"] == len(exported) == len(cases.load(conn))
    assert changes.exported_this_week(tmp_path, datetime(2026, 11, 21).date())
    assert not changes.exported_this_week(tmp_path, datetime(2026, 11, 24).date())


# ------------------------------------------------------------ drift of the judge (T33)


def test_the_week_after_a_drift_proposes_ten_sheets(conn):
    conn.execute("INSERT INTO agent.weekly_reviews (week, start_day, end_day, body, metrics) VALUES "
                 "('2026-W46', '2026-11-08', '2026-11-14', '{}', %s)", (Jsonb({"alerts": ["agreement"]}),))
    for n in range(12):
        sheet_row(conn, f"s{n}")
        conn.execute("UPDATE agent.sheets SET run_date = '2026-11-18', judge = '{\"available\": true}' WHERE id = %s",
                     (f"s{n}",))
    review = weekly.compute(conn, "4242", datetime(2026, 11, 20).date())
    assert len(review.payload.to_annotate) == 10 and review.metrics["annotate_count"] == 10
    quiet = weekly.compute(conn, "4242", datetime(2026, 11, 27).date())  # W48: the previous review is W46, still
    assert quiet.metrics["annotate_count"] in (0, 10)


def test_interpretation_problems_are_read_from_json():
    assert evaluate_instructions.problems({"question": True}, {"rule": None, "question": "Quelle matière ?"}) == []
    assert evaluate_instructions.problems({"subject": "HI-GE"}, {"rule": None, "question": "?", "error": "panne"}) == [
        "panne"]


def test_a_held_case_is_not_seen_while_tuning(conn):
    cases.save(conn, cases.Case("sentence-a", "instructions", "sentence", {"sentence": "Le prof d'histoire interroge."},
                                {"subject": "HI-GE"}))
    cases.save(conn, cases.Case("sentence-b", "instructions", "sentence", {"sentence": "Le prof d'histoire interroge."},
                                {"subject": "HI-GE"}))
    assert cases.hold(conn, "sentence-b") and not cases.hold(conn, "nope")
    tuning = runner.evaluate(conn, "instructions", "staging", ReadingModels(reading()), tuning=True)
    full = runner.evaluate(conn, "instructions", "staging", ReadingModels(reading()))
    assert [r["case"] for r in tuning.results] == ["sentence-a"] and len(full.results) == 2

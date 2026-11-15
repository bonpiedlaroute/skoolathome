"""T21: instructions in plain French → a rule, saved only once the parent validated it."""

from __future__ import annotations

from datetime import date

import pytest

from skoolathome.agent import evaluate_instructions, instructions
from skoolathome.agent.instructions import Interpretation, Known, RuleDraft, resolve
from skoolathome.agent.llm import ModelError
from skoolathome.rules import privacy
from skoolathome.rules import store as rules_store
from skoolathome.rules.describe import describe
from skoolathome.schema.instruction import InstructionReply, NewInstruction
from skoolathome.schema.rule import Rule
from skoolathome.store import db
from skoolathome.store import feedback as feedback_store
from skoolathome.store import instructions as store

TODAY = date(2026, 11, 13)
HISTORY = "Le prof d'histoire interroge à chaque cours sur le cours précédent, et c'est noté."


def draft(**changes) -> RuleDraft:
    values = dict(subject="HI-GE", teacher="", trigger="before_each_lesson", scope="previous_lesson",
                  format="quick_quiz", oral=True, together=True, target_minutes=0, items=0, replaces_default=True,
                  offsets_days=[], n_lessons=0, max_minutes=0)
    return RuleDraft(**{**values, **changes})


def reading(**changes) -> Interpretation:
    return Interpretation(clear=True, question="", rule=draft(**changes))


def unclear(question: str) -> Interpretation:
    return Interpretation(clear=False, question=question, rule=draft(subject=""))


class FakeModels:
    """Readings in turn (the last one repeats); an Exception is raised instead."""

    def __init__(self, *readings) -> None:
        self.readings = list(readings)
        self.prompts: list[str] = []

    def structured(self, role, messages, schema, callbacks):
        assert schema is Interpretation
        self.prompts.append("\n".join(m["content"] for m in messages))
        reply = self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


KNOWN = Known(subjects={"HI-GE": "Histoire-géographie", "AGL1": "Anglais", "MATHS": "Mathématiques"},
              teachers={"HI-GE": ["M. DURAND P."], "AGL1": ["Mme ZEPHIR C.", "M. PETIT L."], "MATHS": ["Mme ROUX A."]})


# ------------------------------------------------------------------- resolve: the code decides


def test_the_history_teacher_is_linked_to_the_subject_and_teacher_known():
    found = resolve(reading(), KNOWN, sentence=HISTORY, today=TODAY)
    rule = found.rule
    assert (rule.id, rule.subject, rule.trigger, rule.scope, rule.format) == (
        "hi-ge-before-quick-quiz", "HI-GE", "before_each_lesson", "previous_lesson", "quick_quiz")
    assert rule.teacher is None and found.teachers == ["M. DURAND P."]  # one teacher: no filter, but shown
    assert (rule.style, rule.suggested_mode, rule.target_minutes, rule.items) == ("oral_quiz", "together", 5, None)
    assert rule.origin.author == "parent" and rule.origin.text == HISTORY and rule.status == "active"
    assert describe(rule, found.teachers) == (
        "En Histoire-géographie (cours de M. D.), la veille de chaque cours, une interro flash de 5 questions "
        "sur le cours précédent, comme une interrogation orale, à faire ensemble, environ 5 min. Elle remplace "
        "l'exercice quotidien de cette matière.")


def test_a_teacher_named_by_the_parent_gives_the_subject():
    found = resolve(reading(subject="", teacher="Mme Zephir"), KNOWN, sentence="Mme Zephir interroge…", today=TODAY)
    assert found.rule.subject == "AGL1" and found.rule.teacher == "Mme ZEPHIR C."  # two teachers: filtered


@pytest.mark.parametrize("changes, asked", [
    ({"subject": ""}, "Pour quelle matière ?"),
    ({"subject": "astronomie"}, "Je ne connais pas la matière « astronomie »"),
    ({"subject": "latin"}, "Je ne vois pas de cours de Latin dans l'emploi du temps"),
    ({"teacher": "M. Lebrun"}, "Je ne trouve pas « M. Lebrun » parmi les profs de Histoire-géographie"),
    ({"trigger": "load_limit", "subject": ""}, "Combien de minutes"),
])
def test_what_is_missing_becomes_a_question(changes, asked):
    found = resolve(reading(**changes), KNOWN, sentence="…", today=TODAY)
    assert found.rule is None and found.question.startswith(asked), found.question


def test_an_unclear_reading_is_a_question_not_a_guess():
    found = resolve(unclear("Pour quelle matière ?"), KNOWN, sentence="Il interroge à l'oral.", today=TODAY)
    assert found.rule is None and found.question == "Pour quelle matière ?"


def test_without_a_subject_the_default_rule_is_the_one_changed():
    base = {r.id: r for r in [Rule(id="before_test", trigger="before_test", offsets_days=[4, 2, 1], items=5,
                                   format="test_revision", target_minutes=8)]}
    found = resolve(reading(subject="", trigger="before_test", format="test_revision", offsets_days=[7, 3, 1],
                            oral=False, together=False, replaces_default=False), KNOWN, sentence="…", today=TODAY,
                    existing=base)
    assert (found.rule.id, found.rule.offsets_days, found.rule.items) == ("before_test", [7, 3, 1], 5)
    limit = resolve(reading(subject="", trigger="load_limit", max_minutes=30), KNOWN, sentence="…", today=TODAY)
    assert (limit.rule.id, limit.rule.max_minutes) == ("max_evening_load", 30)


def test_the_shared_shapes_check_the_rule_of_the_form():
    assert NewInstruction(text="Le prof d'histoire…").rule is None
    with pytest.raises(ValueError):
        NewInstruction(text="", rule=None)
    with pytest.raises(ValueError, match="règle invalide"):
        NewInstruction(rule={"id": "x-y", "trigger": "before_each_lesson"})  # no subject
    with pytest.raises(ValueError):
        InstructionReply(kind="edit")


# --------------------------------------------------------------------------- the graph


@pytest.fixture
def url(conn):
    conn.execute(
        "INSERT INTO school.timetable_slots (source, student_id, source_id, starts_at, ends_at, subject_code, subject, "
        "teacher, room, kind, cancelled, fingerprint) VALUES "
        "('ecoledirecte', '4242', '1', '2026-11-16 09:00', '2026-11-16 10:00', 'HI-GE', 'HISTOIRE-GEO', "
        "'M. DURAND P.', '101', 'COURS', false, 'f1'), "
        "('ecoledirecte', '4242', '2', '2026-11-16 10:00', '2026-11-16 11:00', 'AGL1', 'ANGLAIS', "
        "'Mme ZEPHIR C.', '102', 'COURS', false, 'f2')")
    return db.database_url(conn.info.dbname)


def received(conn, *instructions_, replies=()):
    """What GET /api/feedback would bring."""
    return feedback_store.save(conn, {
        "until": 1, "instructions": [{"id": i, "text": t, "rule": r, "created_at": "2026-11-13T18:00:00+00:00"}
                                     for i, t, r in instructions_],
        "instruction_replies": [{"id": n, "instruction_id": i, "kind": k, "text": t, "rule": r,
                                 "created_at": "2026-11-13T19:00:00+00:00"} for n, (i, k, t, r) in enumerate(replies, 1)],
    }, "4242")


def process(url, models):
    return instructions.process(url, models, "4242", TODAY)


def test_the_history_instruction_becomes_an_active_rule_in_two_passes(conn, url):
    models = FakeModels(reading())
    assert received(conn, ("ins-1", HISTORY, None))["instructions"] == 1
    first = process(url, models)
    assert (first["handled"], first["waiting"], first["saved"]) == (1, 1, 0)
    row = store.get(conn, "ins-1")
    assert row["status"] == "proposed" and row["summary"].startswith("J'ai compris : En Histoire-géographie")
    assert "M. DURAND P." in models.prompts[0] and HISTORY in models.prompts[0]  # the timetable, for the link
    assert "hi-ge-before-quick-quiz" not in {r.id for r in rules_store.latest(conn)}  # nothing without validation
    [update] = store.to_report(conn)
    assert update.status == "proposed" and update.proposal["subject"] == "HI-GE"

    received(conn, replies=[("ins-1", "approve", "", None)])
    second = process(url, models)
    assert second["saved"] == 1 and len(models.prompts) == 1  # the approval needs no model
    rule = next(r for r in rules_store.active(conn) if r.id == "hi-ge-before-quick-quiz")
    assert rule.version == 1 and rule.origin.text == HISTORY and rule.origin.author == "parent"
    row = store.get(conn, "ins-1")
    assert (row["status"], row["rule_id"], row["rule_version"]) == ("saved", "hi-ge-before-quick-quiz", 1)
    assert row["summary"].startswith("Règle enregistrée : En Histoire-géographie")


def test_an_ambiguous_instruction_asks_then_reads_the_answer(conn, url):
    models = FakeModels(unclear("Pour quelle matière ?"), reading(subject="AGL1", teacher=""))
    received(conn, ("ins-2", "Il interroge à l'oral à chaque fois.", None))
    process(url, models)
    assert store.get(conn, "ins-2")["status"] == "question" and store.get(conn, "ins-2")["question"] == "Pour quelle matière ?"
    received(conn, replies=[("ins-2", "answer", "en anglais", None)])
    process(url, models)
    assert "Réponse du parent : en anglais" in models.prompts[1]
    row = store.get(conn, "ins-2")
    assert row["status"] == "proposed" and row["proposal"]["subject"] == "AGL1"


def test_after_two_questions_the_parent_is_sent_to_the_form(conn, url):
    models = FakeModels(unclear("Pour quelle matière ?"))
    received(conn, ("ins-3", "Fais-en plus.", None))
    process(url, models)
    for n, answer in enumerate(["je ne sais pas", "toutes"], 1):
        received(conn, replies=[("ins-3", "answer", answer, None)] * n)  # the same ids: stored once
        process(url, models)
    row = store.get(conn, "ins-3")
    assert row["status"] == "failed" and "formulaire" in row["reason"]


def test_an_edit_is_saved_as_written_and_kept_as_a_correction(conn, url):
    received(conn, ("ins-4", HISTORY, None))
    process(url, FakeModels(reading()))
    proposal = store.get(conn, "ins-4")["proposal"]
    edited = {**proposal, "format": "exercises", "items": 4, "target_minutes": 6}
    received(conn, replies=[("ins-4", "edit", "", edited)])
    process(url, FakeModels(reading()))
    rule = next(r for r in rules_store.latest(conn) if r.id == "hi-ge-before-quick-quiz")
    assert (rule.format, rule.items, rule.target_minutes, rule.origin.text) == ("exercises", 4, 6, HISTORY)
    [correction] = store.corrections(conn)
    assert correction["text"] == HISTORY
    assert correction["proposed"]["format"] == "quick_quiz" and correction["final"]["format"] == "exercises"


def test_a_correction_in_words_is_read_again(conn, url):
    models = FakeModels(reading(), reading(subject="AGL1"))
    received(conn, ("ins-5", HISTORY, None))
    process(url, models)
    received(conn, replies=[("ins-5", "answer", "non, c'est la prof d'anglais", None)])
    process(url, models)
    assert store.get(conn, "ins-5")["proposal"]["subject"] == "AGL1" and len(models.prompts) == 2


def test_cancelled_saves_nothing(conn, url):
    received(conn, ("ins-6", HISTORY, None))
    process(url, FakeModels(reading()))
    received(conn, replies=[("ins-6", "reject", "", None)])
    process(url, FakeModels(reading()))
    assert store.get(conn, "ins-6")["status"] == "rejected"
    assert all(r.origin.author == "default" for r in rules_store.latest(conn))


def test_a_rule_written_in_the_form_is_saved_without_the_model(conn, url):
    form = {"id": "agl1-oral", "subject": "AGL1", "trigger": "before_each_lesson", "scope": "previous_lesson",
            "format": "quick_quiz", "target_minutes": 5}
    received(conn, ("ins-7", "", form))
    models = FakeModels(ModelError("never called"))
    assert process(url, models)["saved"] == 1 and models.prompts == []
    rule = rules_store.get(conn, "agl1-oral")
    assert rule.origin.author == "parent" and rule.status == "active"


def test_a_model_failure_leaves_the_instruction_for_the_next_run(conn, url):
    received(conn, ("ins-8", HISTORY, None))
    failed = process(url, FakeModels(ModelError("pas de réponse du modèle")))
    assert failed["errors"] and store.get(conn, "ins-8")["status"] == "pending"
    assert process(url, FakeModels(reading()))["waiting"] == 1
    assert store.get(conn, "ins-8")["status"] == "proposed"


def test_a_reply_without_a_pause_is_ignored(conn, url):
    received(conn, ("ins-9", "", {"id": "maths-x", "subject": "MATHS", "trigger": "after_each_lesson"}))
    process(url, FakeModels(reading()))
    received(conn, replies=[("ins-9", "approve", "", None)])
    assert process(url, FakeModels(reading()))["handled"] == 0
    assert store.to_process(conn) == 0


def test_the_test_sentences_are_compared_field_by_field(conn, url):
    cases = [{"sentence": HISTORY, "expected": {"subject": "HI-GE", "trigger": "before_each_lesson", "format": "quick_quiz"}},
             {"sentence": "Il interroge à l'oral.", "expected": {"question": True}},
             {"sentence": "Contrôles de maths : une semaine avant.", "expected": {"subject": "MATHS", "offsets_contains": 7}}]
    models = FakeModels(reading(), unclear("Quelle matière ?"), reading(subject="HI-GE"))
    results = evaluate_instructions.run(conn, cases, "4242", TODAY, models=models)
    assert [r["ok"] for r in results] == [True, True, False]
    assert results[2]["problems"][0].startswith("subject")


# ------------------------------------------------------------------- no surname online (§17)


def test_a_teacher_is_shown_online_by_an_initial_only():
    assert privacy.masked("M. DURAND P.") == "M. D." and privacy.masked("Mme ZEPHIR C.") == "Mme Z."
    assert privacy.masked("M. D.") == "M. D."
    names = ["M. DURAND P.", "Mme ZEPHIR C.", "Mme ZOLA L."]
    public = privacy.public_rule({"teacher": "Mme ZEPHIR C.", "origin": {"text": "Mme Zephir interroge à l'oral"}}, names)
    assert public == {"teacher": "Mme Z.", "origin": {"text": "Mme Z. interroge à l'oral"}}
    assert privacy.restore({"teacher": "Mme Z."}, names) == {"teacher": "Mme Z."}  # Zephir or Zola: kept as is
    assert privacy.restore({"teacher": "M. D."}, names) == {"teacher": "M. DURAND P."}


def test_what_goes_online_has_no_surname_and_comes_back_whole(conn, url):
    conn.execute(
        "INSERT INTO school.timetable_slots (source, student_id, source_id, starts_at, ends_at, subject_code, subject, "
        "teacher, room, kind, cancelled, fingerprint) VALUES ('ecoledirecte', '4242', '3', '2026-11-17 10:00', "
        "'2026-11-17 11:00', 'AGL1', 'ANGLAIS', 'M. PETIT L.', '103', 'COURS', false, 'f3')")
    received(conn, ("ins-10", "Mme Zephir fait réciter le vocabulaire à chaque cours.", None))
    process(url, FakeModels(reading(subject="", teacher="Mme Zephir")))
    [update] = store.to_report(conn)
    sent = update.model_dump_json()
    assert "ZEPHIR" not in sent.upper() and update.proposal["teacher"] == "Mme Z."
    assert "(avec Mme Z.)" in update.summary
    edited = {**update.proposal, "target_minutes": 7}  # the form sends back what it received
    received(conn, replies=[("ins-10", "edit", "", edited)])
    process(url, FakeModels(reading()))
    saved = next(r for r in rules_store.latest(conn) if r.subject == "AGL1")
    assert saved.teacher == "Mme ZEPHIR C." and saved.target_minutes == 7
    assert all("ZEPHIR" not in r.model_dump_json().upper() for r in rules_store.published(conn))

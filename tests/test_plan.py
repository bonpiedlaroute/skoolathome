"""T19: the evening plan, without model or database. Timetable and rules in, tasks out.

The weeks used: Monday 9 to Friday 27 November 2026.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from skoolathome.rules import plan
from skoolathome.rules.defaults import DEFAULT_RULES
from skoolathome.rules.plan import Existing, LessonRef, Missed, PlanInputs, RequestRef, Slot, TestRef
from skoolathome.schema.rule import Origin, Rule


def day(n: int) -> date:
    return date(2026, 11, n)


def lesson(n: int, code: str = "MATHS", chars: int = 300, fingerprint: str = "") -> LessonRef:
    return LessonRef(source="ecoledirecte", source_id=f"{code}-{n}", day=day(n), subject_code=code, subject=code,
                     chars=chars, fingerprint=fingerprint or f"fp-{code}-{n}")


def slots(code: str, *days: int, cancelled: tuple[int, ...] = ()) -> list[Slot]:
    return [Slot(day=day(n), subject_code=code, teacher="M. X", cancelled=n in cancelled) for n in days]


def run(today: int, rules=DEFAULT_RULES, **inputs) -> plan.Plan:
    return plan.plan(PlanInputs(run_date=day(today), **inputs), list(rules))


def ids(result: plan.Plan) -> list[str]:
    return sorted(task.id for task in result.tasks)


def reasons(result: plan.Plan) -> dict[str, str]:
    return {skip["task"]: skip["reason"] for skip in result.skipped}


HISTORY_QUIZ = Rule(
    id="hist-oral-quiz", subject="HI-GE", trigger="before_each_lesson", scope="previous_lesson",
    format="quick_quiz", style="oral_quiz", suggested_mode="together", target_minutes=5, replaces_default=True,
    origin=Origin(text="Le prof d'histoire interroge à chaque cours sur le cours précédent.", author="parent"),
)
# History on Tuesdays and Thursdays, two weeks.
HISTORY_SLOTS = slots("HI-GE", 10, 12, 17, 19, 24, 26)
HISTORY_LESSONS = [lesson(10, "HI-GE"), lesson(12, "HI-GE"), lesson(17, "HI-GE"), lesson(19, "HI-GE")]


# ------------------------------------------------------------------------- daily


def test_daily_exercise_the_evening_of_a_lesson():
    result = run(16, lessons=[lesson(16)], slots=slots("MATHS", 16, 18, 20))
    [task] = result.tasks
    assert task.id == "2026-11-16-maths-exercises" and task.kind == "exercises" and task.rule_id == "daily"
    assert task.visible_from == day(17) and task.due_by == day(18) and task.target_minutes == 8
    assert [l.key for l in task.lessons] == ["lesson:2026-11-16:MATHS"]


@pytest.mark.parametrize("lessons, expected, skipped", [
    ([lesson(16, chars=80)], [], "trop courte (80 caractères)"),
    ([lesson(16, chars=0)], [], "sans contenu"),
    ([lesson(16, "EPS")], [], None),                                # no content to revise
    ([lesson(14)], ["2026-11-14-maths-exercises"], None),          # filled in late: caught up
    ([lesson(13)], [], None),                                       # too old
])
def test_daily_cases(lessons, expected, skipped):
    result = run(16, lessons=lessons)
    assert ids(result) == expected
    if skipped:
        assert skipped in reasons(result)["2026-11-16-maths-exercises"]


# ---------------------------------------------------------- before each lesson (T19)


@pytest.mark.parametrize("evening, quiz_for, shows_on", [
    (12, 17, "lundi"),     # Thursday's lesson known: the quiz for Tuesday shows Monday evening
    (17, 19, "mercredi"),  # Tuesday's lesson known: the quiz for Thursday shows Wednesday evening
])
def test_history_tuesday_and_thursday_with_the_oral_quiz_rule(evening, quiz_for, shows_on):
    result = run(evening, rules=[*DEFAULT_RULES, HISTORY_QUIZ], slots=HISTORY_SLOTS, lessons=HISTORY_LESSONS)
    quizzes = [t for t in result.tasks if t.rule_id == "hist-oral-quiz"]
    assert [t.id for t in quizzes] == [f"2026-11-{quiz_for}-hi-ge-hist-oral-quiz"]
    [quiz] = quizzes
    assert quiz.kind == "quick_quiz" and quiz.style == "oral_quiz" and quiz.suggested_mode == "together"
    assert plan.fr_day(quiz.visible_from).startswith(shows_on) and quiz.due_by == day(quiz_for)
    assert [l.day for l in quiz.lessons] == [day(evening)]  # the previous lesson
    # The parent's rule replaces the daily exercise in history.
    assert "remplacée par la règle hist-oral-quiz" in reasons(result)[f"2026-11-{evening}-hi-ge-exercises"]


def test_a_cancelled_lesson_gives_no_task():
    result = run(17, rules=[HISTORY_QUIZ], slots=slots("HI-GE", 10, 12, 17, 19, 24, 26, cancelled=(19,)),
                 lessons=HISTORY_LESSONS[:3])
    assert "annulé" in reasons(result)["2026-11-19-hi-ge-hist-oral-quiz"]
    # Thursday is cancelled: Tuesday's lesson is the previous one of the next Tuesday.
    assert ids(result) == ["2026-11-24-hi-ge-hist-oral-quiz"]


def test_a_holiday_week_gives_no_task():
    holidays = {day(n) for n in range(21, 30)}
    # Before the holidays, nothing is planned for the lessons of the holiday week.
    before = run(20, rules=[HISTORY_QUIZ], slots=slots("HI-GE", 17, 19), lessons=HISTORY_LESSONS, holidays=holidays)
    assert before.tasks == []
    # During the holidays: no lesson, no task.
    during = run(24, slots=[], lessons=[], holidays=holidays)
    assert during.tasks == [] and during.skipped == []
    # A slot left in the timetable during the holidays is skipped, with its reason.
    stray = run(19, rules=[HISTORY_QUIZ], slots=slots("HI-GE", 19, 24), lessons=HISTORY_LESSONS, holidays=holidays)
    assert stray.tasks == [] and "vacances" in reasons(stray)["2026-11-24-hi-ge-hist-oral-quiz"]


def test_an_empty_previous_lesson_still_gives_a_task_that_will_abstain_with_a_message():
    result = run(17, rules=[HISTORY_QUIZ], slots=HISTORY_SLOTS, lessons=[lesson(17, "HI-GE", chars=0)])
    [task] = result.tasks
    assert task.lessons == [] and task.scope_days == [day(17)]
    assert task.missing.startswith("Rien de saisi pour le cours du mardi 17/11")


# ------------------------------------------------------------------- before a test


MATHS_TEST = TestRef(subject_code="MATHS", subject="Mathématiques", day=day(26), reason="flag", text="Contrôle chapitre 4")
# A parent's spaced revision instead of the default preparation: three short sheets.
SPACED = Rule(id="before_test", trigger="before_test", scope="since_last_test", format="test_revision",
              offsets_days=[4, 2, 1], target_minutes=8, items=5, origin=Origin(author="parent"))
SPACED_RULES = [SPACED if rule.id == "before_test" else rule for rule in DEFAULT_RULES]


@pytest.mark.parametrize("evening, shown", [
    (19, None),        # a week before: too early
    (21, day(23)),     # three days before: shows Monday 23, made up to two evenings before
    (23, day(23)),
    (24, day(24)),     # known only on the 24th: shows at once
    (25, day(25)),     # even the day before
    (26, None),        # the day of the test: too late
])
def test_one_complete_preparation_before_a_test(evening, shown):
    lessons = [lesson(n) for n in (5, 9, 12, 16, 17, 19, 23) if n <= evening]
    result = run(evening, tests=[MATHS_TEST], lessons=lessons, past_tests={"MATHS": [day(6)]})
    revisions = [t for t in result.tasks if t.rule_id == "before_test"]
    if shown is None:
        assert revisions == []
        return
    [task] = revisions
    assert task.id == "2026-11-26-maths-test-j3" and task.visible_from == shown and task.due_by == day(26)
    assert task.target_minutes == 45 and task.items is None  # 18 questions, from the duration
    assert "préparation complète" in task.stage_text and "rédiger" in task.stage_text


def test_a_preparation_counts_over_the_evenings_until_the_test():
    # 45 minutes shown Monday 23 for Thursday 26: 15 a day, Monday to Wednesday. Each daily
    # exercise (8 min) still fits under the 20 minutes.
    lessons = [lesson(19), lesson(23), lesson(23, "FRANC")]
    result = run(23, tests=[MATHS_TEST], lessons=lessons)
    assert "2026-11-26-maths-test-j3" in ids(result)
    assert "charge maximale" in reasons(result).get("2026-11-23-franc-exercises", "")  # 15 + 8 > 20 on the 24th
    made = {"prep": Existing("prep", "published", day(23), 45, "x", due_by=day(26))}
    assert "15 min déjà prévues" in reasons(run(24, lessons=[lesson(24)], existing=made))["2026-11-24-maths-exercises"]


@pytest.mark.parametrize("evening, expected, too_late", [
    (19, [], []),                                   # a week before: too early
    (20, ["2026-11-26-maths-test-j4"], []),         # J-4 shows Sunday 22, made up to two evenings before
    (22, ["2026-11-26-maths-test-j2", "2026-11-26-maths-test-j4"], []),
    (23, ["2026-11-26-maths-test-j1", "2026-11-26-maths-test-j2"], ["2026-11-26-maths-test-j4"]),
    (25, ["2026-11-26-maths-test-j1"], ["2026-11-26-maths-test-j2", "2026-11-26-maths-test-j4"]),
])
def test_spaced_revision_before_a_test(evening, expected, too_late):
    lessons = [lesson(n) for n in (5, 9, 12, 16, 17, 19, 23) if n <= evening]
    result = run(evening, SPACED_RULES, tests=[MATHS_TEST], lessons=lessons, past_tests={"MATHS": [day(6)]})
    assert sorted(t.id for t in result.tasks if t.rule_id == "before_test") == expected
    assert sorted(k for k, v in reasons(result).items() if "trop tard" in v) == too_late
    for task in result.tasks:
        if task.rule_id == "before_test":
            assert task.kind == "test_revision" and task.due_by == day(26) and task.test == MATHS_TEST
            assert task.visible_from == day(26) - timedelta(days=int(task.stage[2:]))
            # Since the last test (6/11), at most the four most recent lessons.
            assert all(l.day > day(6) for l in task.lessons) and len({l.day for l in task.lessons}) <= 4


def test_stages_say_what_kind_of_revision():
    result = run(23, SPACED_RULES, tests=[MATHS_TEST], lessons=[lesson(23)])
    stages = {t.stage: t.stage_text for t in result.tasks if t.rule_id == "before_test"}
    assert "contrôle d'entraînement" in stages["j-1"] and "mélangés" in stages["j-2"]


def test_the_same_test_found_twice_is_revised_once():
    keywords = TestRef(subject_code="MATHS", subject="Mathématiques", day=day(26), reason="keywords")
    result = run(22, SPACED_RULES, tests=[keywords, MATHS_TEST], lessons=[lesson(19)])
    revisions = [t for t in result.tasks if t.rule_id == "before_test"]
    assert len(revisions) == 2 and all(t.test.reason == "flag" for t in revisions)


# ----------------------------------------------------------------- priorities, load


def test_over_the_maximum_load_the_least_important_tasks_are_skipped():
    # Shown on Tuesday 17: maths revision (10 min), the quiz for Wednesday's history (5 min), three
    # daily exercises (8 min each).
    test = TestRef(subject_code="MATHS", subject="Mathématiques", day=day(18), reason="flag")
    result = run(16, rules=[*SPACED_RULES, HISTORY_QUIZ], tests=[test], slots=slots("HI-GE", 12, 18),
                 lessons=[lesson(12, "HI-GE"), lesson(16, "FRANC"), lesson(16, "AGL1"), lesson(16)])
    kept = {t.id: t.target_minutes for t in result.tasks if t.visible_from == day(17)}
    assert "2026-11-18-maths-test-j1" in kept and "2026-11-18-hi-ge-hist-oral-quiz" in kept
    assert sum(kept.values()) <= 20
    over = [k for k, v in reasons(result).items() if v.startswith("charge maximale")]
    assert over and all(k.endswith("-exercises") for k in over)
    assert "limite 20 min" in reasons(result)[over[0]]


def test_sheets_made_on_an_earlier_evening_count_in_the_load():
    existing = {"2026-11-15-franc-exercises": Existing("2026-11-15-franc-exercises", "published", day(17), 16, "x")}
    result = run(16, lessons=[lesson(16)], existing=existing)
    assert result.tasks == [] and "16 min déjà prévues" in reasons(result)["2026-11-16-maths-exercises"]


def test_a_task_already_made_is_not_made_again_unless_its_lesson_changed():
    first = run(16, lessons=[lesson(16)])
    [task] = first.tasks
    made = {task.id: Existing(task.id, "ready", task.visible_from, 8, task.inputs)}
    assert run(16, lessons=[lesson(16)], existing=made).done == [task.id]
    edited = [lesson(16, fingerprint="edited by the teacher")]
    assert ids(run(16, lessons=edited, existing=made)) == [task.id]
    published = {task.id: Existing(task.id, "published", task.visible_from, 8, task.inputs)}
    assert "déjà publiée" in reasons(run(16, lessons=edited, existing=published))[task.id]


def test_a_trigger_not_handled_yet_is_said():
    weekly = Rule(id="weekly-recap", trigger="weekly", subject="FRANC", origin=Origin(author="parent"))
    result = run(16, rules=[weekly])
    assert "pas encore pris en charge" in reasons(result)["règle weekly-recap"]


# --------------------------------------------------------------- missed concepts (T20)


def missed(n: int, concept: str, code: str = "MATHS", received: int = 16) -> Missed:
    return Missed(subject_code=code, subject="Mathématiques", concept=concept,
                  source_keys=(f"lesson:2026-11-{n:02d}:{code}",), missed_on=day(received), received_on=day(received))


def test_missed_concepts_come_back_the_next_day():
    result = run(16, lessons=[lesson(12)], missed=[missed(12, "fractions égales"), missed(12, "simplifier")])
    [task] = result.tasks
    assert task.id == "2026-11-17-maths-missed" and task.stage == "missed" and task.kind == "test_revision"
    assert task.focus == ["fractions égales", "simplifier"] and task.visible_from == day(17)
    assert [l.key for l in task.lessons] == ["lesson:2026-11-12:MATHS"]
    # Received on an earlier evening: already brought back then.
    assert run(17, lessons=[lesson(12)], missed=[missed(12, "simplifier")]).tasks == []


def test_missed_concepts_go_into_the_test_revision_of_the_same_day():
    test = TestRef(subject_code="MATHS", subject="Mathématiques", day=day(18), reason="flag")
    result = run(16, SPACED_RULES, tests=[test], lessons=[lesson(12)], missed=[missed(12, "simplifier")])
    [revision] = [t for t in result.tasks if t.kind == "test_revision" and t.visible_from == day(17)]
    assert revision.id == "2026-11-18-maths-test-j1" and revision.focus == ["simplifier"]
    assert "reprises dans 2026-11-18-maths-test-j1" in reasons(result)["2026-11-17-maths-missed"]


# ---------------------------------------------------------------------- forced


def test_forced_tasks_try_a_format_on_chosen_lessons():
    inputs = PlanInputs(run_date=day(16), lessons=[lesson(12), lesson(13, "FRANC")])
    result = plan.forced(inputs, ["MATHS-12", "unknown"], "lesson_recap")
    [task] = result.tasks
    assert task.id == "2026-11-12-maths-lesson-recap" and task.kind == "lesson_recap" and task.target_minutes == 6
    assert reasons(result) == {"unknown": "séance inconnue dans les 5 dernières semaines"}


# ------------------------------------------------------------- the parent's requests


def test_a_parent_request_comes_first_reads_the_whole_subject_and_skips_the_load():
    full = {"x": Existing("x", "published", day(17), 20, "x")}  # tomorrow is already full
    request = RequestRef(id="cli-abc123", subject_code="MATHS", kind="revision", focus="les fractions égales")
    result = run(16, SPACED_RULES, lessons=[lesson(9), lesson(12), lesson(16), lesson(16, "FRANC")], existing=full,
                 requests=[request])
    first = result.tasks[0]
    assert first.id == "2026-11-16-maths-cli-abc123" and first.kind == "test_revision" and first.rule_id == "request"
    assert first.visible_from == day(16) and first.items == 5  # the before_test rule's number
    assert [l.day for l in first.lessons] == [day(9), day(12), day(16)]  # every lesson of the subject
    assert first.request_focus == "les fractions égales" and "les fractions égales" in first.stage_text
    assert "charge maximale" in reasons(result)["2026-11-16-maths-exercises"]  # the others still have a limit


def test_only_the_requests_when_asked():
    request = RequestRef(id="pwa-1", subject_code="FRANC", kind="extra", items=4)
    inputs = PlanInputs(run_date=day(16), lessons=[lesson(16), lesson(16, "FRANC")], requests=[request])
    result = plan.plan(inputs, list(DEFAULT_RULES), only_requests=True)
    assert [(t.id, t.kind, t.items) for t in result.tasks] == [("2026-11-16-franc-pwa-1", "exercises", 4)]


def test_the_rule_s_number_of_questions_goes_into_its_tasks():
    result = run(23, SPACED_RULES, tests=[MATHS_TEST], lessons=[lesson(23)])
    assert {t.items for t in result.tasks if t.rule_id == "before_test"} == {5}
    daily = [t for t in run(16, lessons=[lesson(16)]).tasks]
    assert daily[0].items is None  # from the duration

"""The evening plan (ARCHITECTURE.md §8): rules × timetable × tests → the tasks of the evening.

No model and no database here: inputs in, tasks out, so every case is a plain test. Each task
that one could expect and that is not made comes out too, with its reason: the `plan` node puts
both in the trace.

Generate early, show on time: a sheet is generated as soon as its content is known, usually the
evening of the lesson, and shows on its own date, even a Sunday when the laptop stays off.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from skoolathome.schema.request import FORMAT_OF
from skoolathome.schema.rule import DAILY_RULE_ID, PRIORITY_REQUEST, PRIORITY_REVISION, Rule
from skoolathome.subjects import COMPUTATIONAL_SUBJECTS, EXCLUDED_SUBJECTS, fr_day, label

HORIZON_DAYS = 14          # the timetable and the tests looked at
CATCH_UP_DAYS = 2          # a lesson filled in late, or an evening the laptop was off
GENERATE_AHEAD_DAYS = 2    # a revision stage is generated up to two evenings before it shows
MIN_CONTENT_CHARS = 120    # below that, not enough for a daily exercise
MAX_REVISION_LESSONS = 4   # the most recent lessons since the last test
REVISION_LOOKBACK_DAYS = 28
FOCUS_LOOKBACK_DAYS = 14   # missed concepts brought back in a test revision
MAX_FOCUS = 5
DEFAULT_TARGETS = {"exercises": 8, "quick_quiz": 5, "lesson_recap": 6, "test_revision": 8}
_TEST_REASONS = ("flag", "keywords", "model")  # from the most reliable to the least

ASK_FOR_NOTEBOOK = ("demande une fiche avec des photos de son cahier (Demander une fiche, "
                    "Ajouter des photos du cours).")


# ----------------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class Slot:
    day: date
    subject_code: str
    teacher: str = ""
    cancelled: bool = False


@dataclass(frozen=True)
class LessonRef:
    source: str
    source_id: str
    day: date
    subject_code: str
    subject: str
    chars: int  # length of the lesson text
    fingerprint: str

    @property
    def key(self) -> str:
        """How a sheet cites it, e.g. lesson:2026-11-12:MATHS."""
        return f"lesson:{self.day.isoformat()}:{self.subject_code}"


@dataclass(frozen=True)
class TestRef:
    __test__ = False  # not a pytest test class
    subject_code: str
    subject: str
    day: date
    reason: str  # flag | keywords | model
    text: str = ""


@dataclass(frozen=True)
class Existing:
    """A task already made on a previous evening (agent.sheets)."""

    id: str
    status: str  # ready | abstained | published
    visible_from: date | None
    minutes: int
    inputs: str
    due_by: date | None = None


@dataclass(frozen=True)
class Missed:
    """A question got wrong (solo) or rated ✗ or ~ (together), from the feedback (T17, T20)."""

    subject_code: str
    subject: str
    concept: str
    source_keys: tuple[str, ...]
    missed_on: date    # when it was answered
    received_on: date  # when the laptop received it


@dataclass(frozen=True)
class RequestRef:
    """A sheet the parent asked for, not made yet."""

    id: str
    subject_code: str
    kind: str  # revision | extra
    focus: str = ""
    items: int | None = None
    minutes: int | None = None  # how long the parent wants it; None: the format's default


@dataclass
class PlanInputs:
    run_date: date
    slots: list[Slot] = field(default_factory=list)       # COURS slots, past and coming
    lessons: list[LessonRef] = field(default_factory=list)  # lessons up to run_date
    tests: list[TestRef] = field(default_factory=list)    # tests detected after run_date
    past_tests: dict[str, list[date]] = field(default_factory=dict)  # subject -> test days so far
    existing: dict[str, Existing] = field(default_factory=dict)
    missed: list[Missed] = field(default_factory=list)
    holidays: set[date] = field(default_factory=set)
    requests: list[RequestRef] = field(default_factory=list)


# ----------------------------------------------------------------------------- output


@dataclass
class Task:
    id: str
    kind: str  # sheet format
    rule_id: str
    rule_version: int
    priority: int
    subject_code: str
    subject: str
    visible_from: date
    due_by: date | None
    target_minutes: int
    lessons: list[LessonRef]
    stage: str = ""        # test_revision: j-4, j-2, j-1, missed
    stage_text: str = ""   # told to the model
    focus: list[str] = field(default_factory=list)  # missed concepts to bring back
    style: str = ""
    suggested_mode: str = "solo"
    test: TestRef | None = None
    missing: str = ""      # the abstention message when nothing usable is found
    scope_days: list[date] = field(default_factory=list)  # lesson days in scope, even without text
    items: int | None = None  # questions wanted (the rule's, or the request's); None: from the duration
    request_id: str = ""      # a sheet the parent asked for
    request_focus: str = ""   # what the parent wants worked on
    inputs: str = ""       # fingerprint of what the task is made from

    @property
    def computational(self) -> bool:
        return self.subject_code in COMPUTATIONAL_SUBJECTS

    def as_json(self) -> dict:
        lessons = [{"source": l.source, "source_id": l.source_id, "day": l.day.isoformat(),
                    "subject_code": l.subject_code, "key": l.key} for l in self.lessons]
        return {
            "id": self.id, "kind": self.kind, "rule": self.rule_id, "rule_version": self.rule_version,
            "priority": self.priority, "subject_code": self.subject_code, "subject": self.subject,
            "visible_from": self.visible_from.isoformat(),
            "due_by": self.due_by.isoformat() if self.due_by else None,
            "target_minutes": self.target_minutes, "lessons": lessons, "stage": self.stage,
            "stage_text": self.stage_text, "focus": self.focus, "style": self.style,
            "suggested_mode": self.suggested_mode, "computational": self.computational,
            "test": ({**asdict(self.test), "day": self.test.day.isoformat()} if self.test else None),
            "missing": self.missing, "scope_days": [d.isoformat() for d in self.scope_days],
            "items": self.items, "request_id": self.request_id, "request_focus": self.request_focus,
            "inputs": self.inputs, "pages": [],
        }


@dataclass
class Plan:
    tasks: list[Task] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # {"task": id or description, "reason": …}
    done: list[str] = field(default_factory=list)      # made on a previous evening, unchanged since

    def skip(self, what: str, reason: str) -> None:
        self.skipped.append({"task": what, "reason": reason})

    def as_json(self) -> dict:
        return {"tasks": [t.as_json() for t in self.tasks], "skipped": self.skipped, "done": self.done}


# ------------------------------------------------------------------------------ plan


def plan(inputs: PlanInputs, rules: list[Rule], *, only_requests: bool = False) -> Plan:
    """The tasks of the evening. The parent's requests come first, outside the load limit; with
    `only_requests`, nothing else (`evening --only-requests`, `ask --now`)."""
    out = Plan()
    requested = _requests(inputs, rules, out)
    if only_requests:
        out.tasks = requested
        return out
    limit = min((r.max_minutes for r in rules if r.trigger == "load_limit" and r.max_minutes), default=None)
    replaced = {r.subject: r.id for r in rules if r.replaces_default and r.subject and r.id != DAILY_RULE_ID}
    candidates: list[Task] = []
    for rule in sorted(rules, key=lambda r: (r.priority, r.id)):
        if rule.trigger == "after_each_lesson":
            candidates += _after_lesson(inputs, rule, replaced, out)
        elif rule.trigger == "before_each_lesson":
            candidates += _before_lesson(inputs, rule, out)
        elif rule.trigger == "before_test":
            candidates += _before_test(inputs, rule, out)
        elif rule.trigger == "after_feedback":
            candidates += _missed(inputs, rule, out)
        elif rule.trigger != "load_limit":
            out.skip(f"règle {rule.id}", f"le déclencheur « {rule.trigger} » n'est pas encore pris en charge")
    candidates = _merge_missed_into_revisions(candidates, out)
    candidates = _unique(candidates)
    fresh = _not_done(candidates, inputs.existing, out)
    out.tasks = requested + _within_load(fresh, inputs.existing, limit, out)
    return out


def forced(inputs: PlanInputs, lesson_ids: list[str], kind: str = "exercises") -> Plan:
    """Tasks on chosen lessons (`evening --lesson`), outside the rules: to try a format on real data."""
    out = Plan()
    chosen = [l for l in inputs.lessons if l.source_id in lesson_ids]
    for day, code in sorted({(l.day, l.subject_code) for l in chosen}):
        lessons = [l for l in chosen if l.day == day and l.subject_code == code]
        task = Task(
            id=_task_id(day, code, kind.replace("_", "-")), kind=kind, rule_id="forced", rule_version=0,
            priority=PRIORITY_REVISION, subject_code=code, subject=label(code, lessons[0].subject),
            visible_from=inputs.run_date + timedelta(days=1), due_by=None,
            target_minutes=DEFAULT_TARGETS[kind], lessons=lessons,
            stage="j-2" if kind == "test_revision" else "",
            stage_text=_stage(2)[1] if kind == "test_revision" else "",
            missing=f"Rien de saisi pour le cours du {fr_day(day)} : {ASK_FOR_NOTEBOOK}",
            scope_days=[day],
        )
        task.inputs = fingerprint(task)
        out.tasks.append(task)
    missing = set(lesson_ids) - {l.source_id for l in chosen}
    for source_id in sorted(missing):
        out.skip(source_id, "séance inconnue dans les 5 dernières semaines")
    return out


# ------------------------------------------------------------------------- triggers


def _after_lesson(inputs: PlanInputs, rule: Rule, replaced: dict[str, str], out: Plan) -> list[Task]:
    """The evening of a lesson (or up to two evenings later), an exercise on that lesson."""
    today = inputs.run_date
    tasks = []
    for day, code in sorted({(l.day, l.subject_code) for l in inputs.lessons}):
        if not today - timedelta(days=CATCH_UP_DAYS) <= day <= today:
            continue
        if rule.subject and code != rule.subject:
            continue
        if not rule.subject and code in EXCLUDED_SUBJECTS:
            continue
        lessons = [l for l in inputs.lessons if l.day == day and l.subject_code == code]
        name = "exercises" if rule.id == DAILY_RULE_ID else rule.id
        task_id = _task_id(day, code, name)
        if rule.id == DAILY_RULE_ID and code in replaced:
            out.skip(task_id, f"remplacée par la règle {replaced[code]}")
            continue
        chars = sum(l.chars for l in lessons)
        if chars < MIN_CONTENT_CHARS:
            if day == today:
                out.skip(task_id, f"séance du {fr_day(day)} trop courte ({chars} caractères)" if chars
                         else f"séance du {fr_day(day)} sans contenu dans le cahier de texte")
            continue
        tasks.append(Task(
            id=task_id, kind=rule.format, rule_id=rule.id, rule_version=rule.version, priority=rule.priority,
            subject_code=code, subject=label(code, lessons[0].subject),
            visible_from=today + timedelta(days=1), due_by=_next_lesson_day(inputs, code, after=today),
            target_minutes=rule.target_minutes, lessons=lessons, style=rule.style,
            suggested_mode=rule.suggested_mode, items=rule.items,
            missing=f"Rien d'exploitable dans la séance du {fr_day(day)} : {ASK_FOR_NOTEBOOK}",
            scope_days=[day],
        ))
    return tasks


def _before_lesson(inputs: PlanInputs, rule: Rule, out: Plan) -> list[Task]:
    """The evening before each lesson of the subject, a sheet on the previous lesson."""
    today, code = inputs.run_date, rule.subject
    horizon = today + timedelta(days=HORIZON_DAYS)
    slots = [s for s in inputs.slots if s.subject_code == code and _teacher_matches(rule, s)]
    tasks = []
    for day in sorted({s.day for s in slots if today < s.day <= horizon}):
        task_id = _task_id(day, code, rule.id)
        if all(s.cancelled for s in slots if s.day == day):
            out.skip(task_id, f"cours du {fr_day(day)} annulé")
            continue
        if day in inputs.holidays:
            out.skip(task_id, f"{fr_day(day)} : vacances")
            continue
        previous = _previous_lesson_day(inputs, code, before=day, slots=slots)
        if previous is None:
            out.skip(task_id, f"aucun cours de {label(code)} connu avant le {fr_day(day)}")
            continue
        if previous > today:
            continue  # its content is not known yet: a later evening makes it
        if rule.scope == "last_n_lessons":
            days = sorted({l.day for l in inputs.lessons if l.subject_code == code and l.day <= previous and l.chars})
            scope_days = days[-rule.n_lessons:] or [previous]
        else:
            scope_days = [previous]
        lessons = [l for l in inputs.lessons if l.subject_code == code and l.day in scope_days and l.chars]
        subject = label(code, next((l.subject for l in inputs.lessons if l.subject_code == code), ""))
        tasks.append(Task(
            id=task_id, kind=rule.format, rule_id=rule.id, rule_version=rule.version, priority=rule.priority,
            subject_code=code, subject=subject, visible_from=day - timedelta(days=1), due_by=day,
            target_minutes=rule.target_minutes, lessons=lessons, style=rule.style,
            suggested_mode=rule.suggested_mode, items=rule.items,
            missing=f"Rien de saisi pour le cours du {fr_day(previous)} : {ASK_FOR_NOTEBOOK}",
            scope_days=scope_days,
        ))
    return tasks


def _before_test(inputs: PlanInputs, rule: Rule, out: Plan) -> list[Task]:
    """One sheet per offset: by default a single complete preparation, three days before (a
    test known later shows it at once); a parent's rule can space the revision (four days
    before, two days before, the day before)."""
    today = inputs.run_date
    tasks = []
    for test in unique_tests(inputs.tests):
        if not today < test.day <= today + timedelta(days=HORIZON_DAYS):
            continue
        if rule.subject and test.subject_code != rule.subject:
            continue
        if not rule.subject and test.subject_code in EXCLUDED_SUBJECTS:
            continue
        lessons = _since_last_test(inputs, test)
        focus = _recent_concepts(inputs, test.subject_code)
        offsets = sorted(set(rule.offsets_days), reverse=True)
        for offset in offsets:
            show = test.day - timedelta(days=offset)
            task_id = _task_id(test.day, test.subject_code, f"test-j{offset}")
            if show < today and offset == offsets[-1] and task_id not in inputs.existing:
                show = today  # the test was known late: its last stage shows at once
            if show < today:
                if task_id not in inputs.existing:
                    out.skip(task_id, f"trop tard pour la révision à J-{offset} du contrôle du {fr_day(test.day)}")
                continue
            if today < show - timedelta(days=GENERATE_AHEAD_DAYS):
                continue  # a later evening, with the lessons until then
            stage, text = _stage(offset, single=len(offsets) == 1)
            tasks.append(Task(
                id=task_id, kind=rule.format, rule_id=rule.id, rule_version=rule.version, priority=rule.priority,
                subject_code=test.subject_code, subject=test.subject, visible_from=show, due_by=test.day,
                target_minutes=rule.target_minutes, lessons=lessons, stage=stage, stage_text=text,
                focus=focus, style=rule.style, suggested_mode=rule.suggested_mode, test=test, items=rule.items,
                missing=f"Rien de saisi en {test.subject} depuis le dernier contrôle : {ASK_FOR_NOTEBOOK}",
                scope_days=sorted({l.day for l in lessons}),
            ))
    return tasks


def _missed(inputs: PlanInputs, rule: Rule, out: Plan) -> list[Task]:
    """The day after, a short sheet on the concepts missed in the answers received tonight."""
    today = inputs.run_date
    received = [m for m in inputs.missed if m.received_on == today and (not rule.subject or m.subject_code == rule.subject)]
    tasks = []
    for code in sorted({m.subject_code for m in received}):
        group = [m for m in received if m.subject_code == code]
        task_id = _task_id(today + timedelta(days=1), code, "missed")
        keys = {key for m in group for key in m.source_keys}
        lessons = [l for l in inputs.lessons if l.key in keys]
        if not lessons:
            out.skip(task_id, "notions ratées sur une fiche sans séance source connue")
            continue
        tasks.append(Task(
            id=task_id, kind=rule.format, rule_id=rule.id, rule_version=rule.version, priority=rule.priority,
            subject_code=code, subject=group[0].subject, visible_from=today + timedelta(days=1),
            due_by=_next_lesson_day(inputs, code, after=today), target_minutes=rule.target_minutes,
            lessons=lessons, stage="missed", stage_text="reprendre les notions ratées dans les dernières fiches",
            items=rule.items,
            focus=list(dict.fromkeys(m.concept for m in group if m.concept))[:MAX_FOCUS],
            missing="Notions ratées, mais leur séance n'a plus de texte exploitable.",
            scope_days=sorted({l.day for l in lessons}),
        ))
    return tasks


def _requests(inputs: PlanInputs, rules: list[Rule], out: Plan) -> list[Task]:
    """The sheets the parent asked for: on every lesson of the subject known to the agent; the
    sources keep the ones closest to what was asked (gather_sources)."""
    today = inputs.run_date
    questions = {"test_revision": next((r.items for r in rules if r.id == "before_test"), None),
                 "exercises": next((r.items for r in rules if r.id == DAILY_RULE_ID), None)}
    tasks = []
    for request in inputs.requests:
        code, kind = request.subject_code, FORMAT_OF[request.kind]
        lessons = sorted((l for l in inputs.lessons if l.subject_code == code and l.chars), key=lambda l: l.day)
        subject = label(code, lessons[0].subject if lessons else "")
        what = f" sur « {request.focus} »" if request.focus else ""
        task = Task(
            id=f"{today.isoformat()}-{code.lower()}-{request.id}", kind=kind, rule_id="request", rule_version=0,
            priority=PRIORITY_REQUEST, subject_code=code, subject=subject, visible_from=today, due_by=None,
            target_minutes=request.minutes or DEFAULT_TARGETS[kind], lessons=lessons, stage="request",
            stage_text=("une révision demandée par le parent" if request.kind == "revision"
                        else "un exercice en plus, demandé par le parent") + what,
            # A duration chosen: the number of questions follows from it.
            items=request.items or (None if request.minutes else questions.get(kind)),
            request_id=request.id, request_focus=request.focus,
            missing=f"Rien en {subject} dans le cahier de texte des 5 dernières semaines : {ASK_FOR_NOTEBOOK}",
            scope_days=sorted({l.day for l in lessons}),
        )
        task.inputs = fingerprint(task)
        tasks.append(task)
    return tasks


# ------------------------------------------------------------------- after the rules


def _merge_missed_into_revisions(tasks: list[Task], out: Plan) -> list[Task]:
    """A test revision showing the same day already brings the missed concepts back."""
    revisions = {(t.subject_code, t.visible_from): t for t in tasks if t.kind == "test_revision" and t.stage != "missed"}
    kept = []
    for task in tasks:
        revision = revisions.get((task.subject_code, task.visible_from)) if task.stage == "missed" else None
        if revision is None:
            kept.append(task)
            continue
        revision.focus = list(dict.fromkeys(task.focus + revision.focus))[:MAX_FOCUS]
        out.skip(task.id, f"notions ratées reprises dans {revision.id}")
    return kept


def _unique(tasks: list[Task]) -> list[Task]:
    """Two rules giving the same task: the most important one wins."""
    best: dict[str, Task] = {}
    for task in tasks:
        if task.id not in best or task.priority < best[task.id].priority:
            best[task.id] = task
    return list(best.values())


def _not_done(tasks: list[Task], existing: dict[str, Existing], out: Plan) -> list[Task]:
    fresh = []
    for task in tasks:
        task.inputs = fingerprint(task)
        previous = existing.get(task.id)
        if previous is None:
            fresh.append(task)
        elif previous.inputs == task.inputs:
            out.done.append(task.id)
        elif previous.status == "published":
            out.skip(task.id, "déjà publiée ; ses sources ont changé depuis, elle n'est pas refaite")
        else:
            fresh.append(task)  # its sources changed (the teacher edited the lesson): made again
    return fresh


def _within_load(tasks: list[Task], existing: dict[str, Existing], limit: int | None, out: Plan) -> list[Task]:
    """At most `limit` minutes shown per day. Sheets already made count first; then the most
    important tasks: test revision, the parent's rules, the daily exercise."""
    if limit is None:
        return sorted(tasks, key=_importance)
    redone = {t.id for t in tasks}
    load: dict[date, int] = defaultdict(int)
    for previous in existing.values():
        if previous.status in ("ready", "published") and previous.visible_from and previous.id not in redone:
            for day, minutes in _spread(previous.visible_from, previous.due_by, previous.minutes):
                load[day] += minutes
    kept = []
    for task in sorted(tasks, key=_importance):
        days = _spread(task.visible_from, task.due_by if task.kind == "test_revision" else None, task.target_minutes)
        if task.kind == "test_revision":
            # Never too big by itself: known late, it takes the whole evening, the others give way.
            days = [(day, min(minutes, limit)) for day, minutes in days]
        full = [(day, load[day]) for day, minutes in days if load[day] + minutes > limit]
        if full:
            day, used = full[0]
            out.skip(task.id, f"charge maximale : {used} min déjà prévues pour le {fr_day(day)}, limite {limit} min")
            continue
        for day, minutes in days:
            load[day] += minutes
        kept.append(task)
    return kept


def _spread(visible_from: date, due_by: date | None, minutes: int) -> list[tuple[date, int]]:
    """A test preparation counts over the evenings until the test: 45 minutes shown three days
    before are 15 minutes a day. Any other sheet counts on the day it shows."""
    days = (due_by - visible_from).days if due_by else 0
    if days <= 1:
        return [(visible_from, minutes)]
    share = -(-minutes // days)  # rounded up
    return [(visible_from + timedelta(days=n), share) for n in range(days)]


# --------------------------------------------------------------------------- helpers


def fingerprint(task: Task) -> str:
    """What the task is made from: a lesson edited by the teacher, or a rule changed, makes it again."""
    data = {
        "kind": task.kind, "rule": [task.rule_id, task.rule_version], "stage": task.stage,
        "focus": sorted(task.focus), "target": task.target_minutes, "style": task.style,
        "items": task.items, "request": [task.request_id, task.request_focus],
        "lessons": sorted([l.key, l.fingerprint] for l in task.lessons),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def _importance(task: Task) -> tuple:
    return (task.priority, task.due_by or date.max, task.visible_from, task.subject_code, task.id)


def _task_id(day: date, code: str, name: str) -> str:
    return f"{day.isoformat()}-{code.lower()}-{name}"


PREPARATION = ("une préparation complète du contrôle, à faire en une ou plusieurs fois, sur la tablette ou "
               "imprimée : des questions de cours (définitions, vocabulaire, dates, règles), des exercices de "
               "base, des exercices mélangés sur tout le programme, et des exercices comme au contrôle, dont "
               "plusieurs à rédiger (type `open` : expliquer, justifier, résoudre un problème en plusieurs étapes)")


def _stage(offset: int, single: bool = False) -> tuple[str, str]:
    if single:
        return f"j-{offset}", PREPARATION
    if offset >= 3:
        return f"j-{offset}", "rappel de cours et exercices de base"
    if offset == 2:
        return "j-2", "exercices mélangés sur tout ce qui sera au contrôle"
    return "j-1", "un petit contrôle d'entraînement, comme le vrai"


def _teacher_matches(rule: Rule, slot: Slot) -> bool:
    return not rule.teacher or rule.teacher.lower().strip() in slot.teacher.lower()


def _previous_lesson_day(inputs: PlanInputs, code: str, *, before: date, slots: list[Slot]) -> date | None:
    days = {s.day for s in slots if not s.cancelled and s.day < before}
    days |= {l.day for l in inputs.lessons if l.subject_code == code and l.day < before}
    return max(days, default=None)


def _next_lesson_day(inputs: PlanInputs, code: str, *, after: date) -> date | None:
    days = [s.day for s in inputs.slots if s.subject_code == code and not s.cancelled and s.day > after]
    return min(days, default=None)


def unique_tests(tests: list[TestRef]) -> list[TestRef]:
    """One test per subject and day, with the most reliable reason."""
    def rank(test: TestRef) -> int:
        return _TEST_REASONS.index(test.reason) if test.reason in _TEST_REASONS else len(_TEST_REASONS)

    best: dict[tuple[str, date], TestRef] = {}
    for test in tests:
        key = (test.subject_code, test.day)
        if key not in best or rank(test) < rank(best[key]):
            best[key] = test
    return sorted(best.values(), key=lambda t: (t.day, t.subject_code))


def _since_last_test(inputs: PlanInputs, test: TestRef) -> list[LessonRef]:
    """The lessons of the subject since its previous test (at most four weeks), the most recent ones."""
    earlier = [d for d in inputs.past_tests.get(test.subject_code, []) if d < inputs.run_date]
    start = max(earlier + [test.day - timedelta(days=REVISION_LOOKBACK_DAYS)])
    lessons = [l for l in inputs.lessons if l.subject_code == test.subject_code and start < l.day <= inputs.run_date and l.chars]
    days = sorted({l.day for l in lessons})[-MAX_REVISION_LESSONS:]
    return [l for l in lessons if l.day in days]


def _recent_concepts(inputs: PlanInputs, code: str) -> list[str]:
    since = inputs.run_date - timedelta(days=FOCUS_LOOKBACK_DAYS)
    concepts = [m.concept for m in inputs.missed if m.subject_code == code and m.missed_on >= since and m.concept]
    return list(dict.fromkeys(concepts))[:MAX_FOCUS]

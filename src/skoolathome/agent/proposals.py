"""The graph of proposals (ARCHITECTURE.md §7.5 and §9.3, T28).

    detect_patterns ─► draft_rule ─► interrupt(parent authorization) ─► save_rule

Once a week, the agent looks in what was already collected for a teacher's habit: an oral quiz
on the lesson at the start of each class. It proposes the matching rule, with the facts behind it,
and saves nothing without the parent's authorization. A proposal is an instruction of the agent:
the same graph pauses on it (agent/instructions.py), the parent answers it in the same view,
[Accepter], [Modifier] or [Refuser]. A refused habit is never proposed again.

Everything here is code: the signals, the thresholds, the rule and its wording. No model.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

import psycopg
from psycopg.types.json import Jsonb

from skoolathome.agent import tracing
from skoolathome.evaluation import cases
from skoolathome.rules import privacy
from skoolathome.rules import store as rules_store
from skoolathome.rules.describe import describe
from skoolathome.schema.rule import Origin, Rule
from skoolathome.store import instructions as store
from skoolathome.subjects import EXCLUDED_SUBJECTS, fr_day, label

LOOKBACK = timedelta(weeks=10)
# What a lesson's content says when the teacher quizzed the class on the lesson. Not « QCM » or
# « quiz »: announced in the homework, they are tests, which before_test already prepares.
_QUIZ = re.compile(
    r"(interro\w*|interrog\w*)\s+(orale?s?|a\s+l'oral|de\s+cours|sur\s+(la\s+lecon|le\s+cours|les?\s+\w+))"
    r"|evaluations?\s+orales?|recitations?|questions?\s+de\s+cours")
# What says it happens every time: « interro à chaque début de cours ».
_EVERY = re.compile(r"(a|au)\s+(chaque|debut\s+de\s+chaque|debut\s+du|debut\s+de)\s+(cours|seance|heure)"
                    r"|chaque\s+debut\s+de\s+(cours|seance)")
_QUIZ_GRADE = re.compile(r"^interro|oral|recit|quiz|qcm")


def _plain(text: str) -> str:
    """Lower case, no accents, one character for one: a match found here is at the same place in
    the original text, which the facts quote as the teacher wrote it."""
    return "".join("'" if c == "’" else unicodedata.normalize("NFD", c)[0] for c in text.lower())


@dataclass
class Habit:
    subject_code: str
    mentions: list[tuple[date, str]] = field(default_factory=list)  # lessons describing a quiz
    every_lesson: list[tuple[date, str]] = field(default_factory=list)
    graded: list[tuple[date, str]] = field(default_factory=list)     # quiz grades
    lessons_per_week: float = 0.0
    weeks: float = 1.0

    @property
    def key(self) -> str:
        return f"lesson_quiz:{self.subject_code}"

    @property
    def found(self) -> bool:
        """Enough to propose: said to happen every time, or seen twice, or seen once and graded,
        or graded at least every other lesson."""
        days = {d for d, _ in self.mentions}
        graded = {d for d, _ in self.graded}
        return bool(self.every_lesson or len(days) >= 2 or (days and graded)
                    or (len(graded) >= 3 and len(graded) / self.weeks >= 0.5 * self.lessons_per_week))

    @property
    def oral(self) -> bool:
        return any("oral" in _plain(text) or "recit" in _plain(text)
                   for _, text in self.mentions + self.graded + self.every_lesson)


def _excerpt(text: str, match: re.Match, width: int = 45) -> str:
    """The passage around a match, from the original text, on one line."""
    start, end = max(0, match.start() - width), min(len(text), match.end() + width)
    return ("…" if start else "") + " ".join(text[start:end].split()) + ("…" if end < len(text) else "")


def detect(conn: psycopg.Connection, student_id: str, today: date) -> list[Habit]:
    """detect_patterns: per subject, the lessons that describe a quiz on the lesson, the texts that
    say it happens every class, and the quiz grades, compared with the number of lessons."""
    since = today - LOOKBACK
    habits: dict[str, Habit] = defaultdict(lambda: Habit(""))
    for code, day, text, in_class in conn.execute(
        "SELECT subject_code, day, content, true FROM school.lessons WHERE student_id = %s AND day >= %s "
        "AND NOT is_empty UNION ALL SELECT subject_code, coalesce(given_on, due_on), text, false FROM school.homework "
        "WHERE student_id = %s AND coalesce(given_on, due_on) >= %s",
        (student_id, since, student_id, since),
    ).fetchall():
        text = text or ""
        flat = _plain(text)
        habit = habits[code]
        for match in _QUIZ.finditer(flat if in_class else ""):  # what happened in class, not what is announced
            habit.mentions.append((day, _excerpt(text, match)))
        for match in _EVERY.finditer(flat):
            around = flat[max(0, match.start() - 80):match.end() + 80]
            if _QUIZ.search(around) or "interro" in around:  # « à chaque cours » about a quiz, not anything
                habit.every_lesson.append((day, _excerpt(text, match)))
    for code, day, kind, title in conn.execute(
        "SELECT subject_code, day, kind, title FROM school.grades WHERE student_id = %s AND day >= %s",
        (student_id, since),
    ).fetchall():
        if _QUIZ_GRADE.search(_plain(kind)) or _QUIZ_GRADE.search(_plain(title)):
            habits[code].graded.append((day, title))
    slots = conn.execute(
        "SELECT subject_code, count(*), greatest(1, (max(starts_at)::date - min(starts_at)::date + 1) / 7.0) "
        "FROM school.timetable_slots WHERE student_id = %s AND kind = 'COURS' AND NOT cancelled GROUP BY 1",
        (student_id,),
    ).fetchall()
    per_week = {code: count / float(weeks) for code, count, weeks in slots}
    first = conn.execute("SELECT min(day) FROM school.grades WHERE student_id = %s AND day >= %s",
                         (student_id, since)).fetchone()[0]
    weeks = max(1.0, (today - first).days / 7) if first else 1.0
    found = []
    for code, habit in sorted(habits.items()):
        if code in EXCLUDED_SUBJECTS or not code:
            continue
        habit.subject_code, habit.lessons_per_week, habit.weeks = code, per_week.get(code, 0.0), weeks
        if habit.found:
            found.append(habit)
    return found


def facts(habit: Habit) -> list[str]:
    """What justifies the rule, in French, without a surname (it is shown in the PWA)."""
    lines = []
    for day, text in sorted(habit.every_lesson)[:2]:
        lines.append(f"Le {fr_day(day)}, le cahier de texte dit : « {text[:110]} »")
    seen = set()
    for day, text in sorted(habit.mentions, reverse=True):
        if day not in seen and len(seen) < 3:
            seen.add(day)
            lines.append(f"Le {fr_day(day)}, la séance dit : « {text[:110]} »")
    if habit.graded:
        dates = ", ".join(fr_day(d) for d, _ in sorted(habit.graded)[:4])
        lines.append(f"{len(habit.graded)} interrogation{'s' if len(habit.graded) > 1 else ''} "
                     f"notée{'s' if len(habit.graded) > 1 else ''} en {round(habit.weeks)} semaines ({dates}).")
    if habit.lessons_per_week:
        lines.append(f"Environ {round(habit.lessons_per_week)} cours {_of(label(habit.subject_code))} par semaine.")
    return lines


def _of(name: str) -> str:
    """« de mathématiques », « d'anglais », « de SVT »."""
    if not name.isupper():
        name = name[0].lower() + name[1:]
    return ("d'" if _plain(name[0]) in "aeiouyh" else "de ") + name


def draft(habit: Habit, today: date) -> Rule:
    """draft_rule: the evening before each class, a quick quiz on the previous lesson, to do together."""
    return Rule(id=f"{habit.subject_code.lower()}-before-quick-quiz", subject=habit.subject_code,
                trigger="before_each_lesson", scope="previous_lesson", format="quick_quiz",
                style="oral_quiz" if habit.oral else "", suggested_mode="together", target_minutes=5,
                origin=Origin(text=sentence(habit), author="agent", on=today))


def sentence(habit: Habit) -> str:
    return (f"En {label(habit.subject_code)}, le prof semble interroger sur la leçon en début de cours : "
            "une interro flash la veille de chaque cours y préparerait.")


def propose(conn: psycopg.Connection, student_id: str, today: date) -> list[str]:
    """New proposals: a habit never proposed before, for a subject without a rule before each
    lesson. Each one becomes an instruction of the agent, waiting for the parent."""
    known = {row[0] for row in conn.execute("SELECT key FROM agent.rule_proposals").fetchall()}
    covered = {r.subject for r in rules_store.active(conn) if r.trigger == "before_each_lesson"}
    names = privacy.teachers(conn) + cases.private_names(conn)
    made = []
    for habit in detect(conn, student_id, today):
        if habit.key in known or habit.subject_code in covered:
            continue
        rule = draft(habit, today)
        lines = [privacy.public_text(line, names) for line in facts(habit)]
        instruction = store.create(conn, text=sentence(habit), student_id=student_id, origin="agent",
                                   agent_rule=rule.model_dump(mode="json"), facts=lines, proposal_key=habit.key)
        conn.execute("INSERT INTO agent.rule_proposals (key, subject_code, instruction_id, facts, rule) "
                     "VALUES (%s, %s, %s, %s, %s)",
                     (habit.key, habit.subject_code, instruction, Jsonb(lines), Jsonb(rule.model_dump(mode="json"))))
        with tracing.observe("detect_patterns", tracing.trace_id("instruction", instruction),
                             trace_name=f"proposal {habit.key}", tags=["proposal", f"subject:{habit.subject_code}"],
                             metadata={"proposal": habit.key}, input={"facts": lines}) as span:
            if span is not None:
                span.update(output={"rule": rule.model_dump(mode="json"), "summary": describe(rule)})
        made.append(habit.key)
    tracing.flush()
    return made


def decided(conn: psycopg.Connection, key: str, status: str, *, final: dict | None = None, reason: str = "") -> None:
    """The parent's decision: kept (a refused habit is never proposed again), a Langfuse score on
    the proposal's trace, and, when the rule was changed or refused with a reason, a case of the
    « consignes » set: an interpretation the agent got wrong."""
    row = conn.execute(
        "UPDATE agent.rule_proposals SET status = %s, final = %s, reason = %s, decided_at = now() WHERE key = %s "
        "RETURNING instruction_id, facts, rule, subject_code",
        (status, Jsonb(final) if final else None, reason[:600], key),
    ).fetchone()
    if row is None:
        return
    instruction, lines, rule, code = row
    tracing.score(tracing.trace_id("instruction", instruction), "proposal.decision", status, reason[:300] or None)
    if status == "edited" or (status == "rejected" and reason):
        cases.save(conn, cases.Case(f"proposal-{key}", "instructions", "proposal",
                                    {"facts": lines, "subject": code, "proposed": rule},
                                    {"decision": status, "rule": final, "reason": reason}, {"key": key}))


def acceptance(conn: psycopg.Connection) -> dict:
    """For the weekly review: proposals decided, accepted (as they were or changed), refused, waiting."""
    rows = dict(conn.execute("SELECT status, count(*) FROM agent.rule_proposals GROUP BY status").fetchall())
    decided_ = rows.get("accepted", 0) + rows.get("edited", 0) + rows.get("rejected", 0)
    return {"decided": decided_, "accepted": rows.get("accepted", 0) + rows.get("edited", 0),
            "edited": rows.get("edited", 0), "rejected": rows.get("rejected", 0), "waiting": rows.get("proposed", 0),
            "rate": (rows.get("accepted", 0) + rows.get("edited", 0)) / decided_ if decided_ else None}

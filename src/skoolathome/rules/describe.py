"""A rule in one French sentence, for the parent: « J'ai compris : … » and the list of rules.

Written by the code from the rule itself, never by the model: the parent validates exactly what
will be saved.
"""

from __future__ import annotations

from skoolathome.rules.privacy import masked
from skoolathome.schema.rule import Rule
from skoolathome.subjects import label

TRIGGERS = {
    "after_each_lesson": "le soir de chaque cours",
    "before_each_lesson": "la veille de chaque cours",
    "before_test": "avant chaque contrôle",
    "after_feedback": "le lendemain de questions ratées",
    "weekly": "chaque semaine",
    "on_homework": "pour chaque devoir",
}
SCOPES = {
    "this_lesson": "sur la séance du jour",
    "previous_lesson": "sur le cours précédent",
    "last_n_lessons": "sur les {n} derniers cours",
    "since_last_test": "sur ce qui a été vu depuis le dernier contrôle",
    "missed_items": "sur les notions ratées",
}
FORMATS = {
    "exercises": "des exercices",
    "lesson_recap": "un cours en clair",
    "quick_quiz": "une interro flash",
    "test_revision": "une révision",
}
NOT_PLANNED = {"weekly", "on_homework"}  # the evening plan does not handle them yet


def describe(rule: Rule, teachers: list[str] | None = None) -> str:
    """`teachers`: the subject's teachers known in École Directe, shown when the rule has none."""
    if rule.trigger == "load_limit":
        return f"Au plus {rule.max_minutes} minutes de fiches par soir, toutes matières confondues."
    where = f"En {label(rule.subject)}" if rule.subject else "Dans toutes les matières"
    if rule.teacher:  # never a surname: this sentence goes online (§17)
        where += f" (avec {masked(rule.teacher)})"
    elif teachers:
        where += f" (cours de {' et '.join(masked(t) for t in teachers[:3])})"
    when = TRIGGERS.get(rule.trigger, rule.trigger)
    if rule.trigger == "before_test" and rule.offsets_days:
        days = sorted(set(rule.offsets_days), reverse=True)
        when += " : " + ", ".join(f"J-{d}" for d in days[:-1]) + (" et " if len(days) > 1 else "") + f"J-{days[-1]}"
    what = FORMATS.get(rule.format, rule.format)
    count = rule.items if rule.format not in ("quick_quiz", "lesson_recap") else (5 if rule.format == "quick_quiz" else None)
    if count:
        what += f" de {count} questions"
    scope = SCOPES.get(rule.scope, "").format(n=rule.n_lessons)
    parts = [f"{where}, {when}, {what}" + (f" {scope}" if scope else "")]
    if rule.style == "oral_quiz":
        parts.append("comme une interrogation orale")
    if rule.suggested_mode == "together":
        parts.append("à faire ensemble")
    parts.append(f"environ {rule.target_minutes} min")
    text = ", ".join(parts) + "."
    if rule.replaces_default and rule.subject:
        text += " Elle remplace l'exercice quotidien de cette matière."
    if rule.trigger in NOT_PLANNED:
        text += " (Pas encore pris en charge par le plan du soir.)"
    if rule.status == "archived":
        text = "Archivée. " + text
    return text

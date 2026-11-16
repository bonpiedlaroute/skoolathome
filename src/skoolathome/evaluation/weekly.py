"""The weekly review (ARCHITECTURE.md §14.4, T24): in two minutes, is the agent still right?

Computed on the laptop from what it made and what came back; sent with the evening's batch from
Friday on, once per week; pushed to the parent with a link to the detail. It proposes 5 sheets to
annotate: the parent's verdicts, question by question, measure the agreement between the judge
and the parent. An indicator past its alert threshold comes first, highlighted.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

import psycopg
from psycopg.types.json import Jsonb

from skoolathome.agent.judge import CRITERIA
from skoolathome.evaluation import cases
from skoolathome.schema.review import Indicator, SheetToAnnotate, WeeklyReview
from skoolathome.subjects import label

DUE_WEEKDAY = 4          # from Friday: the first evening of the end of the week sends it
TO_ANNOTATE = 5
TO_ANNOTATE_DRIFT = 10   # the week after the judge drifted: twice as many sheets to annotate (T33)
AGREEMENT_WINDOW = 30    # the last annotated sheets (§14.2): 5 a week are too few on their own
MIN_ANSWERS = 10         # below that, a success rate says nothing
# Alert thresholds: past them, the indicator is put first in the review.
BLOCKED_MAX, ABSTAINED_MAX, REGENERATED_MAX = 0.30, 0.30, 0.50
SUCCESS_RANGE = (0.60, 0.85)  # the calibration band (§14.2)
AGREEMENT_MIN = 0.80
GUARD_NAMES = {"format": "format", "citation": "citation", "answer_in_quote": "réponse dans la citation",
               "maths": "calcul", "correction": "correction", "duration": "durée", "structure": "structure",
               "focus": "notions ratées", "judge": "correcteur"}
REPORT_NAMES = {"wrong_answer": "réponse fausse", "not_covered": "pas vu en cours", "unclear": "pas claire",
                "too_hard": "trop difficile", "too_easy": "trop facile"}


@dataclass
class Review:
    payload: WeeklyReview
    metrics: dict


def week_of(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def _percent(value: float) -> str:
    return f"{round(value * 100)} %"


def _share(part: int, whole: int) -> str:
    return f"{part} sur {whole} ({_percent(part / whole)})" if whole else "aucune"


def agreement(conn: psycopg.Connection, window: int = AGREEMENT_WINDOW) -> dict:
    """Question by question, over the last annotated sheets: does the judge say what the parent
    says? A question is right for the judge when it passed its four criteria."""
    sheets = conn.execute(
        "SELECT a.sheet_id, s.judge FROM feedback.annotations a JOIN agent.sheets s ON s.id = a.sheet_id "
        "GROUP BY a.sheet_id, s.judge ORDER BY max(a.created_at) DESC LIMIT %s", (window,),
    ).fetchall()
    compared = agreed = 0
    disagreements = []
    for sheet_id, judged in sheets:
        if not judged or not judged.get("available"):
            continue  # the judge could not answer: nothing to compare
        verdicts = {v["id"]: all(v[c] for c in CRITERIA) for v in judged["verdict"]["items"]}
        for item_id, ok in conn.execute("SELECT item_id, ok FROM feedback.annotations WHERE sheet_id = %s",
                                        (sheet_id,)).fetchall():
            compared += 1
            same = verdicts.get(item_id, True) == ok
            agreed += same
            if not same:
                disagreements.append(f"{sheet_id} {item_id}")
    return {"items": compared, "agreed": agreed, "rate": agreed / compared if compared else None,
            "sheets": len(sheets), "disagreements": disagreements[:10]}


def to_annotate(conn: psycopg.Connection, student_id: str, start: date, end: date, week: str,
                count: int = TO_ANNOTATE) -> list[SheetToAnnotate]:
    """Sheets published this week and judged, not annotated yet, drawn at random (the same draw if
    the review is computed again)."""
    rows = conn.execute(
        "SELECT s.id, s.subject_code, s.body->>'title' FROM agent.sheets s WHERE s.student_id = %s "
        "AND s.status = 'published' AND s.run_date BETWEEN %s AND %s AND (s.judge->>'available')::boolean "
        "AND NOT EXISTS (SELECT 1 FROM feedback.annotations a WHERE a.sheet_id = s.id) ORDER BY s.id",
        (student_id, start, end),
    ).fetchall()
    drawn = random.Random(week).sample(rows, min(count, len(rows)))
    return [SheetToAnnotate(id=r[0], subject=label(r[1]), title=r[2] or "") for r in sorted(drawn)]


def compute(conn: psycopg.Connection, student_id: str, day: date) -> Review:
    start, end, week = day - timedelta(days=6), day, week_of(day)
    sheets = conn.execute(
        "SELECT id, status, attempts, guards, rule_id FROM agent.sheets WHERE student_id = %s "
        "AND run_date BETWEEN %s AND %s AND rule_id NOT IN ('', 'forced')", (student_id, start, end),
    ).fetchall()
    made = len(sheets)
    abstained = [s for s in sheets if s[1] == "abstained"]
    blocked = [s for s in abstained if s[2] > 0 and any(not g["ok"] and g["name"] != "judge" for g in s[3])]
    regenerated = [s for s in sheets if s[2] > 1]
    failures = Counter(g["name"] for s in sheets for g in s[3] if not g["ok"])
    reports = conn.execute(
        "SELECT r.reason, coalesce(s.rule_id, '') FROM feedback.reports r LEFT JOIN agent.sheets s ON s.id = r.sheet_id "
        "WHERE r.created_at::date BETWEEN %s AND %s", (start, end),
    ).fetchall()
    solo = [r[0] for r in conn.execute(
        "SELECT correct FROM feedback.attempts WHERE mode = 'solo' AND created_at::date BETWEEN %s AND %s",
        (start, end)).fetchall()]
    finishes = conn.execute(
        "SELECT score, total FROM feedback.finishes WHERE finished_at::date BETWEEN %s AND %s AND total > 0",
        (start, end)).fetchall()
    tests = cases.observed_recall(conn, student_id, start, end)
    asked = conn.execute(  # « Je n'ai pas compris » (T29): what the sheets explain badly
        "SELECT e.helped, s.subject_code, item->>'concept' FROM agent.explanations e "
        "LEFT JOIN agent.sheets s ON s.id = e.sheet_id "
        "LEFT JOIN LATERAL jsonb_array_elements(s.body->'items') AS item ON item->>'id' = e.item_id "
        "WHERE coalesce(e.asked_at, e.answered_at)::date BETWEEN %s AND %s", (start, end),
    ).fetchall()
    agreed = agreement(conn)
    drifted = _drifted_last_week(conn, week)
    chosen = to_annotate(conn, student_id, start, end, week, TO_ANNOTATE_DRIFT if drifted else TO_ANNOTATE)
    from skoolathome.evaluation import changes  # T32
    from skoolathome.rules import calibration  # T33

    watched = changes.recent(conn, end)
    levels = calibration.levels(conn, student_id)
    from skoolathome.agent.proposals import acceptance  # T28

    proposed = acceptance(conn)

    by_reason = Counter(reason for reason, _ in reports)
    by_rule = Counter(rule or "fiche écrite à la main" for _, rule in reports)
    success = sum(solo) / len(solo) if solo else None
    indicators = [
        Indicator(name="blocked", label="Fiches bloquées par les garde-fous", value=_share(len(blocked), made),
                  alert=bool(made) and len(blocked) / made > BLOCKED_MAX,
                  detail="Motifs : " + ", ".join(f"{GUARD_NAMES.get(n, n)} {c}" for n, c in failures.most_common(4))
                  if failures else ""),
        Indicator(name="regenerated", label="Fiches réécrites au moins une fois", value=_share(len(regenerated), made),
                  alert=bool(made) and len(regenerated) / made > REGENERATED_MAX),
        Indicator(name="abstained", label="Abstentions", value=_share(len(abstained), made),
                  alert=bool(made) and len(abstained) / made > ABSTAINED_MAX),
        Indicator(name="reports", label="Signalements", value=str(len(reports)),
                  alert=bool(by_reason["wrong_answer"] or by_reason["not_covered"]),
                  detail=("Motifs : " + ", ".join(f"{REPORT_NAMES[r]} {c}" for r, c in by_reason.most_common())
                          + ". Règles : " + ", ".join(f"{r} {c}" for r, c in by_rule.most_common(4))) if reports else ""),
        Indicator(name="success", label="Réussite en autonomie",
                  value=f"{_percent(success)} sur {len(solo)} réponses" if success is not None else "aucune réponse seul",
                  alert=success is not None and len(solo) >= MIN_ANSWERS and not SUCCESS_RANGE[0] <= success <= SUCCESS_RANGE[1],
                  detail=(f"Fiches finies : {sum(s for s, _ in finishes)}/{sum(t for _, t in finishes)} en moyenne "
                          f"sur {len(finishes)} fiche(s). Visé : entre 60 et 85 %.") if finishes else "Visé : entre 60 et 85 %."),
        Indicator(name="not_understood", label="« Je n'ai pas compris »",
                  value=f"{len(asked)} demande{'s' if len(asked) > 1 else ''}" if asked else "aucune demande",
                  alert=any(helped is False for helped, _, _ in asked),
                  detail=_not_understood(asked)),
        Indicator(name="proposals", label="Règles proposées par l'agent",
                  value=(f"{proposed['accepted']} acceptée{'s' if proposed['accepted'] > 1 else ''} sur "
                         f"{proposed['decided']} ({_percent(proposed['rate'])})") if proposed["decided"]
                  else "aucune décision encore",
                  detail=(f"Dont {proposed['edited']} modifiée(s). " if proposed["edited"] else "")
                  + (f"{proposed['waiting']} en attente de ta réponse." if proposed["waiting"] else "")),
        Indicator(name="tests", label="Contrôles détectés avant le jour",
                  value=f"{tests['detected']} sur {tests['observed']}" if tests["observed"] else "aucun contrôle noté",
                  alert=bool(tests["missed"]),
                  detail="Ratés : " + ", ".join(f"{m['subject']} le {m['day']}" for m in tests["missed"]) if tests["missed"] else ""),
        Indicator(name="agreement", label="Accord entre le correcteur et toi",
                  value=f"{_percent(agreed['rate'])} sur {agreed['items']} questions" if agreed["rate"] is not None
                  else "pas encore de fiche notée",
                  alert=_drifts(agreed),
                  detail=((f"Le correcteur dérive : {len(chosen)} fiches à noter cette semaine. " if _drifts(agreed) else "")
                          + f"Sur les {agreed['sheets']} dernières fiches notées. Seuil : 80 %.")
                  if agreed["rate"] is not None else ""),
        Indicator(name="changes", label="Changements de l'agent (deux semaines)",
                  value=f"{len(watched)} changement{'s' if len(watched) > 1 else ''}" if watched else "aucun",
                  alert=any(c["compared"]["after"]["wrong_answers"] for c in watched),
                  detail=_changes(watched)),
        Indicator(name="difficulty", label="Difficulté visée",
                  value=", ".join(f"{label(code)} {c.level}" for code, c in sorted(levels.items()) if c.calibrated)
                  or "pas encore assez de réponses",
                  detail=_difficulty(levels, start)),
    ]
    if made == 0:
        indicators.insert(0, Indicator(name="nothing", label="Fiches faites cette semaine", value="aucune", alert=True,
                                       detail="Le portable est-il resté éteint ?"))
    indicators.sort(key=lambda i: not i.alert)  # the alerts first, the rest in order
    alerts = [i for i in indicators if i.alert]
    head = f"{len(alerts)} point{'s' if len(alerts) > 1 else ''} d'attention : " + ", ".join(
        i.label.lower() for i in alerts) + ". " if alerts else "Rien d'anormal. "
    body = (head + f"{made} fiche{'s' if made > 1 else ''}, {len(abstained)} abstention(s), "
            f"{len(reports)} signalement(s). " + (f"Réussite {_percent(success)}. " if success is not None else "")
            + (f"{len(chosen)} fiches à noter." if chosen else ""))
    payload = WeeklyReview(
        week=week, start=start, end=end,
        title="Révisions : bilan de la semaine" + (f" ({len(alerts)} alerte{'s' if len(alerts) > 1 else ''})" if alerts else ""),
        body=body[:600], indicators=indicators, to_annotate=chosen)
    metrics = {"made": made, "blocked": len(blocked), "regenerated": len(regenerated), "abstained": len(abstained),
               "guard_failures": dict(failures), "reports": dict(by_reason), "reports_by_rule": dict(by_rule),
               "solo_answers": len(solo), "success": success, "tests": tests, "agreement": agreed,
               "proposals": proposed,
               "changes": [{"id": c["id"], "name": c["name"], "before": c["before"], "after": c["after"],
                            **c["compared"]} for c in watched],
               "difficulty": {code: {"level": c.level, "answers": c.answers, "rate": c.rate,
                                     "changes": [(ch.before, ch.after, ch.at.isoformat() if ch.at else None)
                                                 for ch in c.changes]} for code, c in levels.items()},
               "annotate_count": len(chosen),
               "not_understood": {"asked": len(asked), "helped": sum(1 for h, _, _ in asked if h),
                                  "not_helped": sum(1 for h, _, _ in asked if h is False)},
               "alerts": [i.name for i in alerts]}
    return Review(payload, metrics)


def _drifts(agreed: dict) -> bool:
    return agreed["rate"] is not None and agreed["items"] >= MIN_ANSWERS and agreed["rate"] < AGREEMENT_MIN


def _drifted_last_week(conn: psycopg.Connection, week: str) -> bool:
    """The previous review said the judge drifts: this week, 10 sheets to annotate instead of 5."""
    row = conn.execute("SELECT metrics FROM agent.weekly_reviews WHERE week < %s ORDER BY week DESC LIMIT 1",
                       (week,)).fetchone()
    return bool(row) and "agreement" in (row[0] or {}).get("alerts", [])


def _rate(value: float | None) -> str:
    return _percent(value) if value is not None else "—"


def _changes(watched: list[dict]) -> str:
    """Each change of the last two weeks, with the real feedback before and after (T32)."""
    parts = []
    for change in watched[:4]:
        before, after = change["compared"]["before"], change["compared"]["after"]
        parts.append(f"{change['name']} {change['before']} → {change['after']} ({change['at']:%d/%m}) : réussite "
                     f"{_rate(before['success'])} → {_rate(after['success'])}, signalements {before['reports']} → "
                     f"{after['reports']}, réponses fausses {before['wrong_answers']} → {after['wrong_answers']}, "
                     f"{after['sheets']} fiche(s) depuis")
    return ". ".join(parts)[:400]


def _difficulty(levels: dict, start: date) -> str:
    """The changes of the week, per subject (T33)."""
    moved = [f"{label(code)} {ch.before} → {ch.after} ({_percent(ch.rate)} sur les 10 dernières)"
             for code, c in sorted(levels.items()) for ch in c.changes if ch.at and ch.at.date() >= start]
    text = ("Cette semaine : " + ", ".join(moved) + ". " if moved else "") + \
        "Monte au-dessus de 85 % de réussite seul, descend sous 60 %, au moins 10 réponses entre deux changements."
    return text[:400]


def _not_understood(asked: list[tuple]) -> str:
    """By subject and by concept, and whether the explanations helped."""
    if not asked:
        return ""
    subjects = Counter(label(code) for _, code, _ in asked if code)
    concepts = Counter(concept for _, _, concept in asked if concept)
    rated = [helped for helped, _, _ in asked if helped is not None]
    parts = ["Matières : " + ", ".join(f"{s} {n}" for s, n in subjects.most_common(4))] if subjects else []
    if concepts:
        parts.append("Notions : " + ", ".join(f"{c} {n}" for c, n in concepts.most_common(4)))
    if rated:
        parts.append(f"Ça a aidé : {sum(rated)} sur {len(rated)}")
    return ". ".join(parts) + "."


# ------------------------------------------------------------------------------ storage


def due(conn: psycopg.Connection, day: date) -> bool:
    """From Friday on, once per ISO week."""
    if day.weekday() < DUE_WEEKDAY:
        return False
    return conn.execute("SELECT 1 FROM agent.weekly_reviews WHERE week = %s", (week_of(day),)).fetchone() is None


def save(conn: psycopg.Connection, review: Review) -> None:
    payload = review.payload
    conn.execute(
        "INSERT INTO agent.weekly_reviews (week, start_day, end_day, body, metrics) VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (week) DO UPDATE SET start_day = EXCLUDED.start_day, end_day = EXCLUDED.end_day, "
        "body = EXCLUDED.body, metrics = EXCLUDED.metrics, created_at = now(), sent_at = NULL",
        (payload.week, payload.start, payload.end, Jsonb(payload.model_dump(mode="json")), Jsonb(review.metrics)),
    )


def unsent(conn: psycopg.Connection) -> WeeklyReview | None:
    row = conn.execute("SELECT body FROM agent.weekly_reviews WHERE sent_at IS NULL ORDER BY week DESC LIMIT 1").fetchone()
    return WeeklyReview.model_validate(row[0]) if row else None


def sent(conn: psycopg.Connection, week: str) -> None:
    conn.execute("UPDATE agent.weekly_reviews SET sent_at = now() WHERE week = %s", (week,))

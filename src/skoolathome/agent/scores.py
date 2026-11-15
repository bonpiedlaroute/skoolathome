"""Real feedback as Langfuse scores, on the trace of the generation that made the question
(ARCHITECTURE.md §14.1 layer 3 and §14.5, T22).

Each answer, report, finished sheet and parent annotation kept on the laptop becomes a score on
its sheet's trace, with the rule, the format and the model in its metadata. Rows received before
this existed are sent too: they waited with `sent_to_langfuse_at` empty. Each score has a stable
id: sent twice after a crash, it is counted once.

    feedback.correct         solo answer, right or wrong (BOOLEAN)
    feedback.parent_rating   together: the parent's ✓ ~ ✗ (good, partial, bad)
    feedback.report          « Signaler »: wrong_answer, not_covered, unclear, too_hard, too_easy
    feedback.solo_score      a sheet finished alone: share of questions right
    parent.item_ok           the weekly annotation (T24): the question is right and useful, for the parent
    judge.agreement          the judge said the same thing as the parent about that question (T24, T33)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from skoolathome.agent import tracing

# What every score carries, from the sheet the laptop made (agent.sheets).
_CONTEXT = ("s.rule_id, s.rule_version, s.subject_code, s.kind, s.model, s.prompt_version, s.release")
VIEWS = (
    ("Par règle", "Traces", "Tags : rule:<règle>, par exemple rule:hi-ge-before-quick-quiz. Colonnes : les notes "
                            "feedback.correct, feedback.parent_rating, feedback.report."),
    ("Par modèle", "Traces", "Tags : model:<modèle>, par exemple model:ministral-3:8b-instruct-2512-q4_K_M."),
    ("Signalements", "Scores", "Name = feedback.report. La valeur est le motif : wrong_answer, not_covered…"),
    ("Réponses ratées", "Scores", "Name = feedback.correct, Value = 0."),
    ("Annotations du parent", "Scores", "Name = parent.item_ok (bilan hebdomadaire, T24)."),
)


@dataclass
class Score:
    table: str     # feedback table the row comes from
    row_id: str
    trace_id: str
    name: str
    value: float | bool | str
    comment: str
    metadata: dict[str, str] = field(default_factory=dict)
    suffix: str = ""  # a second score from the same row

    @property
    def score_id(self) -> str:
        return f"skoolathome-{self.table}-{self.row_id}{self.suffix}"


def _context(row: tuple) -> dict[str, str]:
    rule, version, subject, kind, model, prompt_version, release = row
    found = {"rule": rule, "rule_version": str(version) if rule else "", "subject": subject, "format": kind,
             "model": model, "prompt_version": prompt_version, "release": release}
    return {key: str(value) for key, value in found.items() if value}


def pending(conn: psycopg.Connection, limit: int = 5000) -> tuple[list[Score], int]:
    """Scores waiting to be sent, and the count of rows whose sheet has no trace (written by hand)."""
    found: list[Score] = []
    for row in conn.execute(
        f"SELECT a.id, coalesce(a.trace_id, s.trace_id), a.item_id, a.role, a.mode, a.correct, a.rating, a.hints_used, "
        f"{_CONTEXT} FROM feedback.attempts a LEFT JOIN agent.sheets s ON s.id = a.sheet_id "
        "WHERE a.sent_to_langfuse_at IS NULL ORDER BY a.id LIMIT %s", (limit,),
    ).fetchall():
        row_id, trace, item, role, mode, correct, rating, hints = row[:8]
        meta = {**_context(row[8:]), "item": item, "mode": mode, "role": role, "hints_used": str(hints)}
        if rating:
            found.append(Score("attempts", str(row_id), trace, "feedback.parent_rating", rating,
                               f"{item}, ensemble", meta))
        else:
            how = "seul" if mode == "solo" else "ensemble, réponse tapée"
            found.append(Score("attempts", str(row_id), trace, "feedback.correct", bool(correct),
                               f"{item}, {how}" + (f", {hints} indice(s)" if hints else ""), meta))
    for row in conn.execute(
        f"SELECT r.id, coalesce(r.trace_id, s.trace_id), r.item_id, r.role, r.reason, r.comment, {_CONTEXT} "
        "FROM feedback.reports r LEFT JOIN agent.sheets s ON s.id = r.sheet_id "
        "WHERE r.sent_to_langfuse_at IS NULL ORDER BY r.id LIMIT %s", (limit,),
    ).fetchall():
        row_id, trace, item, role, reason, comment = row[:6]
        meta = {**_context(row[6:]), "item": item or "", "role": role}
        found.append(Score("reports", str(row_id), trace, "feedback.report", reason,
                           f"{item or 'fiche'}" + (f" : {comment}" if comment else ""), meta))
    for row in conn.execute(
        f"SELECT f.id, coalesce(f.trace_id, s.trace_id), f.role, f.score, f.total, f.to_review, {_CONTEXT} "
        "FROM feedback.finishes f LEFT JOIN agent.sheets s ON s.id = f.sheet_id "
        "WHERE f.sent_to_langfuse_at IS NULL ORDER BY f.finished_at LIMIT %s", (limit,),
    ).fetchall():
        row_id, trace, role, score, total, to_review = row[:6]
        if total:
            found.append(Score("finishes", row_id, trace, "feedback.solo_score", round(score / total, 3),
                               f"{score}/{total}, {len(to_review or [])} à voir ensemble",
                               {**_context(row[6:]), "role": role}))
    found += _annotations(conn, limit)
    untraced = [s for s in found if not s.trace_id]
    return [s for s in found if s.trace_id], len(untraced)


def _annotations(conn: psycopg.Connection, limit: int) -> list[Score]:
    if conn.execute("SELECT to_regclass('feedback.annotations')").fetchone()[0] is None:
        return []  # before T24's migration
    rows = conn.execute(
        f"SELECT n.id, s.trace_id, n.item_id, n.ok, n.problem, n.comment, s.judge, {_CONTEXT} "
        "FROM feedback.annotations n LEFT JOIN agent.sheets s ON s.id = n.sheet_id "
        "WHERE n.sent_to_langfuse_at IS NULL ORDER BY n.id LIMIT %s", (limit,),
    ).fetchall()
    found = []
    for row_id, trace, item, ok, problem, comment, judged, *context in rows:
        meta = {**_context(tuple(context)), "item": item, "problem": problem or ""}
        found.append(Score("annotations", str(row_id), trace, "parent.item_ok", bool(ok),
                           item + (f", {problem}" if problem else "") + (f" : {comment}" if comment else ""), meta))
        if judged and judged.get("available"):  # does the judge say what the parent says? (T24, T33)
            verdict = next((v for v in judged["verdict"]["items"] if v["id"] == item), None)
            judge_ok = verdict is None or all(verdict[c] for c in ("faithful", "answer_correct", "level_ok", "useful"))
            found.append(Score("annotations", str(row_id), trace, "judge.agreement", judge_ok == bool(ok),
                               f"{item} : correcteur {'OK' if judge_ok else 'KO'}, parent {'OK' if ok else 'KO'}", meta,
                               "-agreement"))
    return found


def send(conn: psycopg.Connection) -> dict:
    """Send what is waiting, then mark it sent. Without Langfuse, nothing is marked: it waits."""
    if tracing.client() is None:
        return {"skipped": "Langfuse non configuré"}
    scores, untraced = pending(conn)
    sent: dict[str, set[str]] = {}
    for score in scores:
        tracing.score(score.trace_id, score.name, score.value, score.comment[:500], score_id=score.score_id,
                      metadata=score.metadata)
        sent.setdefault(score.table, set()).add(score.row_id)
    tracing.flush()
    for table, ids in sent.items():
        key = "id::text" if table != "finishes" else "id"
        conn.execute(f"UPDATE feedback.{table} SET sent_to_langfuse_at = now() WHERE {key} = ANY(%s)", (list(ids),))
    return {**{table: len(ids) for table, ids in sent.items()}, "untraced": untraced}

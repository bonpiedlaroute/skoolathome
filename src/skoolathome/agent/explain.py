"""« Je n'ai pas compris » (T29, ARCHITECTURE.md §12 and §14.2).

The student asks on the tablet; the request waits online. While the laptop is on, a timer polls
the queue every few minutes (one NIP-98 signature), and for each request:

    read what the model read for that sheet ─► write ─► guards ─┬─► the explanation, sent back
                       ▲                                        │
                       └──────── failed, fewer than 3 ──────────┤
                                                                └─► abstention, with its reason

Same rules as a sheet: from the lesson only, the quote copied word for word and found in the
lesson, the expected answer written in the explanation. « Ça m'a aidé ? » comes back with the
feedback, as a score on the explanation's trace; a « Toujours pas » becomes a case of the
reference set.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from pydantic import Field, ValidationError
from rapidfuzz import fuzz

from skoolathome.agent import config, draft, guards, making, prompts, tracing
from skoolathome.agent.draft import Draft
from skoolathome.agent.llm import ModelError
from skoolathome.evaluation import cases
from skoolathome.schema.explanation import ExplanationAnswer, ExplanationBody
from skoolathome.schema.sheet import Item, Quote

PROMPT = "explanation"
MAX_GENERATIONS = 3
MAX_CHARS = 1200
NO_SOURCE = "Cette fiche a été écrite à la main : le portable n'a pas son cours pour l'expliquer."


class ExplanationDraft(Draft):
    explanation: str = Field(description="3 à 6 phrases courtes, jusqu'à la bonne réponse ; vide si le cours ne suffit pas")
    quote: str = Field(description="le passage du cours qui justifie la réponse, mot pour mot")
    example: str = Field(description="un exemple court résolu, ou une chaîne vide")


@dataclass
class Made:
    status: str  # done | abstained
    body: ExplanationBody | None = None
    reason: str = ""
    attempts: int = 0
    guards: list[dict] = field(default_factory=list)
    prompt_version: str = ""

    def answer(self) -> ExplanationAnswer:
        return ExplanationAnswer(status=self.status, explanation=self.body, reason=self.reason[:600])


def _answer_text(item: Item) -> str:
    return " ; ".join(item.answer) if isinstance(item.answer, list) else item.answer


def check(written: ExplanationDraft, item: Item, sources: dict[str, str]) -> list[str]:
    """The guards of a sheet, for an explanation: the quote is in the lesson, the expected answer
    is in the explanation, the length stays short."""
    failures = []
    excerpt = guards.flat(written.quote)
    if len(excerpt) < guards.MIN_QUOTE_CHARS:
        failures.append("citation absente ou trop courte : recopie une phrase entière du cours.")
    elif max((fuzz.partial_ratio(excerpt, guards.flat(text)) for text in sources.values()), default=0) \
            < guards.CITATION_THRESHOLD:
        failures.append(f"la citation « {written.quote[:80]} » n'est pas dans le cours : recopie-la mot pour mot.")
    said = f"{written.explanation} {written.example}"
    if item.type != "open":
        parts = item.answer if isinstance(item.answer, list) else [item.answer]
        accepted = [parts] + [v if isinstance(v, list) else [v] for v in item.variants]
        if not any(all(guards._found(part, said) for part in answer) for answer in accepted):
            failures.append(f"l'explication doit arriver à la réponse attendue, « {_answer_text(item)} », et l'écrire.")
    if len(written.explanation) > MAX_CHARS:
        failures.append(f"explication trop longue ({len(written.explanation)} caractères) : 6 phrases courtes au plus.")
    return failures


def make(conn: psycopg.Connection, models: Any, request: dict, trace: str = "") -> Made:
    """An explanation of one question, from what the model read for its sheet."""
    try:
        read, body = cases.model_input(conn, request["sheet_id"])
    except LookupError:
        return Made("abstained", reason=NO_SOURCE)
    raw = next((i for i in body.get("items", []) if i["id"] == request["item_id"]), None)
    if raw is None:
        return Made("abstained", reason="Question introuvable dans la fiche gardée sur le portable.")
    item = Item.model_validate(raw)
    sources, labels = read.get("sources") or {}, read.get("labels") or {}
    if making.chars(sources) < making.MIN_SOURCE_CHARS:
        return Made("abstained", reason="Le cours de cette fiche est trop court pour l'expliquer : "
                                        "demande-lui son cahier, ou explique-la avec lui.")
    prompt = prompts.get(PROMPT)
    failures: list[str] = []
    log: list[dict] = []
    for attempt in range(1, MAX_GENERATIONS + 1):
        feedback = ("\nLa version précédente a été refusée. Corrige ces points :\n"
                    + "\n".join(f"- {f}" for f in failures)) if failures else ""
        messages = prompt.compile(
            subject=read["task"].get("subject", ""), question=item.prompt, answer=_answer_text(item) or "(question ouverte)",
            solution=item.solution or "(aucune)", asked=request.get("question") or "(rien de plus précis)",
            feedback=feedback, sources="\n\n".join(f"[{key}]\n{text}" for key, text in sources.items()))
        try:
            written = models.structured("generator", messages, ExplanationDraft, tracing.callbacks())
        except (ModelError, ValidationError) as error:
            failures = [making.format_reason(error)]
            log.append({"attempt": attempt, "name": "format", "ok": False, "reasons": failures})
            continue
        if not written.explanation.strip():
            return Made("abstained", reason="Le cours ne permet pas d'expliquer cette question : vois-la avec un parent.",
                        attempts=attempt, guards=log, prompt_version=prompt.label)
        failures = check(written, item, sources)
        log.append({"attempt": attempt, "name": "explanation", "ok": not failures, "reasons": failures})
        if trace:
            tracing.score(trace, "guard.explanation", not failures, f"essai {attempt}"
                          + (" : " + " ; ".join(failures)[:400] if failures else ""))
        if not failures:
            source = draft.locate(written.quote, sources)
            return Made("done", ExplanationBody(text=written.explanation.strip(), example=written.example.strip(),
                                                quote=Quote(source_id=source, excerpt=written.quote.strip()),
                                                source_label=labels.get(source, "")),
                        attempts=attempt, guards=log, prompt_version=prompt.label)
    return Made("abstained", reason=f"Explication refusée après {MAX_GENERATIONS} essais : " + "; ".join(failures)[:400],
                attempts=MAX_GENERATIONS, guards=log, prompt_version=prompt.label)


def _save(conn: psycopg.Connection, request: dict, made: Made, trace: str, model: str) -> None:
    conn.execute(
        "INSERT INTO agent.explanations (id, sheet_id, item_id, question, status, explanation, reason, attempts, "
        "guards, trace_id, model, prompt_version, asked_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, explanation = EXCLUDED.explanation, "
        "reason = EXCLUDED.reason, attempts = EXCLUDED.attempts, guards = EXCLUDED.guards, answered_at = now()",
        (request["id"], request["sheet_id"], request["item_id"], request.get("question", ""), made.status,
         Jsonb(made.body.model_dump(mode="json")) if made.body else None, made.reason, made.attempts,
         Jsonb(made.guards), trace, model, made.prompt_version, request.get("created_at")),
    )


def process(connect: Callable[[], psycopg.Connection], client: Any, models: Any,
            progress: Callable[[str], None] | None = None, conf: config.Config | None = None,
            waiting: list[dict] | None = None) -> list[dict]:
    """Every request waiting online (`waiting`, or asked for here): explained, saved here, sent
    back (the API notifies)."""
    say = progress or (lambda message: None)
    if waiting is not None and not waiting:
        return []
    conf = conf or config.load()
    generator = conf.model("generator")
    done = []
    for request in client.pending_explanations() if waiting is None else waiting:
        trace = tracing.trace_id("explanation", request["id"])
        say(f"Explication {request['id']} : question {request['item_id']} de {request['sheet_id']}…")
        with connect() as conn:
            sheet_trace = conn.execute("SELECT trace_id FROM agent.sheets WHERE id = %s",
                                       (request["sheet_id"],)).fetchone()
            with tracing.observe("explain", trace, trace_name=f"explanation {request['id']}", tags=["explanation"],
                                 metadata={"sheet": request["sheet_id"], "item": request["item_id"],
                                           "sheet_trace": sheet_trace[0] if sheet_trace else "",
                                           **generator.metadata(conf.engine)},
                                 input={"question": request.get("question", "")}) as span:
                made = make(conn, models, request, trace)
                if span is not None:
                    span.update(output=made.body.model_dump() if made.body else made.reason)
            _save(conn, request, made, trace, generator.model)
        client.answer_explanation(request["id"], made.answer())
        say(f"Explication {request['id']} : " + ("envoyée" if made.status == "done" else f"abstention ({made.reason[:100]})"))
        done.append({"id": request["id"], "status": made.status})
    tracing.flush()
    return done


def unhelpful_cases(conn: psycopg.Connection) -> int:
    """Each « Toujours pas » becomes a case of the reference set (family « explanations »): the
    question, what the model read, and the explanation that did not help. Anonymized."""
    rows = conn.execute(
        "SELECT e.id, e.sheet_id, e.item_id, e.question, e.explanation FROM agent.explanations e "
        "WHERE e.helped = false AND NOT EXISTS (SELECT 1 FROM agent.eval_cases c WHERE c.id = 'explanation-' || e.id)"
    ).fetchall()
    names = cases.private_names(conn) if rows else []
    for explanation_id, sheet_id, item_id, question, explanation in rows:
        try:
            read, body = cases.model_input(conn, sheet_id)
        except LookupError:
            continue
        item = next((i for i in body.get("items", []) if i["id"] == item_id), None)
        cases.save(conn, cases.Case(
            f"explanation-{explanation_id}", "explanations", "explanation",
            cases.anonymize({"item": item, "question": question, "sources": read.get("sources") or {},
                             "labels": read.get("labels") or {}, "subject": read["task"].get("subject", "")}, names),
            cases.anonymize({"helped": False, "previous": (explanation or {}).get("text", "")}, names),
            {"sheet": sheet_id, "item": item_id, "concept": (item or {}).get("concept", "")}))
    return len(rows)

"""The evening graph (ARCHITECTURE.md §9.1).

    collect ─► index_documents ─► sync_feedback ─► summarize_lessons ─► detect_tests ─► plan ─► retrieve
      ─► [Send: one branch per task] ─► judging ─► [Send: the judge reads each sheet] ─► regenerating
      ─► [Send: the sheets the judge sent back return to their branch] … ─► publish ─► report

    a branch:  gather_sources ─► generate ─► guards ─┬─► done (the judge reads it next)
                                     ▲               │
                                     └── ko, < 3 ────┤
                                                     └─► abstain(reason)

All the generations come first, then all the judgments, then the regenerations the judge asked
for: the laptop loads each model once per round instead of once per sheet.

The model only does text work, in an imposed format. Everything else is code: the plan, the
sources, the guards, the decision to publish or to abstain. Checkpoints go to Postgres with the
run date as thread id: a run stopped half-way resumes where it was.
"""

from __future__ import annotations

import operator
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated, Any, TypedDict

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from skoolathome.agent import config, draft, formats, guards, judge, lessons, prompts, report, tracing
from skoolathome.agent.embeddings import EmbeddingError
from skoolathome.agent.llm import ModelError
from skoolathome.material import index, search
from skoolathome.rules import assessments
from skoolathome.rules import inputs as plan_inputs
from skoolathome.rules import plan as planning
from skoolathome.rules import store as rules_store
from skoolathome.schema.sheet import Sheet
from skoolathome.store import feedback as feedback_store
from skoolathome.subjects import EXCLUDED_SUBJECTS, fr_day, label

MAX_GENERATIONS = 3      # the first one, then up to two regenerations (ARCHITECTURE.md §9.5)
MAX_SOURCE_CHARS = 9000  # what the model reads: about 2 500 tokens, within an 8 192-token context
MIN_SOURCE_CHARS = 120   # below that, no sheet: the model would invent, the guards refuse, three times
PAGE_CHARS = 3000
MIN_QUERY_CHARS = 40     # a shorter text finds pages by chance: no search
MAX_TEST_CHECKS = 10     # ambiguous homework asked to the model per evening
BATCH_MAX = 50           # sheets per signed batch (the API's limit)


def merge(left: dict, right: dict) -> dict:
    return {**left, **right}


class EveningState(TypedDict, total=False):
    run_date: str
    student_id: str | None
    lesson_ids: list[str]  # `evening --lesson`: these lessons, outside the rules
    forced_format: str
    publish: bool
    collect: dict
    documents: dict
    feedback: dict
    detected: dict
    plan: dict           # skipped tasks with their reason, tasks already made
    tests: list[dict]    # tests of the coming week, for the report
    tasks: list[dict]
    outcomes: Annotated[dict[str, dict], merge]  # task id -> where its sheet stands
    memory: Annotated[list[dict], operator.add]  # RAM and swap after each model call
    published: dict
    report: dict


class TaskState(TypedDict, total=False):
    run_date: str
    student_id: str
    task: dict
    trace_id: str
    sources: dict[str, str]  # source key -> text given to the model
    labels: dict[str, str]   # source key -> how the family reads it
    sheet: dict | None       # the last generated sheet, before the guards
    failures: list[str]      # why the last attempt failed: fed back to the model
    guard_log: Annotated[list[dict], operator.add]  # every guard and judge result of every attempt
    attempts: int
    prompt_version: str
    outcomes: Annotated[dict[str, dict], merge]
    memory: Annotated[list[dict], operator.add]


class TaskOutput(TypedDict):
    """What a branch hands back to the evening. The branches run side by side, and must not
    write the evening's other keys."""
    outcomes: Annotated[dict[str, dict], merge]
    memory: Annotated[list[dict], operator.add]


class Outcome(TypedDict, total=False):
    """Where a task stands: guarded (waiting for the judge), rejected (back to the generator),
    ready, or abstained. Also the judge's input."""
    run_date: str
    student_id: str
    task: dict
    status: str
    reason: str
    sheet: dict | None
    attempts: int
    failures: list[str]
    guard_log: list[dict]
    prompt_version: str
    trace_id: str
    sources: dict[str, str]
    labels: dict[str, str]
    judge: dict | None


@dataclass
class Services:
    """What the graph uses outside itself. Tests give fakes."""
    models: Any                                    # text() and structured(): agent.llm.Models
    collector: Callable[[], dict] | None = None    # one École Directe collection; None: --no-collect
    api: Callable[[], AbstractContextManager] | None = None  # an ApiClient in a bunker session
    embedder: Any = None                           # agent.embeddings.LocalEmbedder
    material: Any = None                           # collectors.manual.ManualSource
    progress: Callable[[str], None] | None = None  # what the evening is doing, as it goes (CLI)


Connect = Callable[[], psycopg.Connection]


def build(services: Services, connect: Connect, conf: config.Config | None = None) -> StateGraph:
    conf = conf or config.load()
    generator, judge_model = conf.model("generator"), conf.model("judge")
    models = services.models

    def say(message: str) -> None:
        if services.progress is not None:
            services.progress(message)

    # ---------------------------------------------------------------- tracing helpers

    @contextmanager
    def step(name: str, state: EveningState, **kwargs: Any) -> Iterator[Any]:
        """A span in the evening's own trace: collection, plan, publication, report."""
        with tracing.observe(name, tracing.trace_id(state["run_date"], "evening"),
                             trace_name=f"evening {state['run_date']}", metadata={"run_date": state["run_date"]},
                             tags=["evening"], **kwargs) as span:
            yield span

    def trace_metadata(task: dict, run_date: str) -> dict[str, str]:
        return {"run_date": run_date, "task": task["id"], "subject": task["subject_code"], "format": task["kind"],
                "rule": task["rule"], "rule_version": str(task["rule_version"]), "stage": task.get("stage", ""),
                **generator.metadata(conf.engine)}

    @contextmanager
    def node(name: str, state: TaskState | Outcome, **kwargs: Any) -> Iterator[Any]:
        """A span in the sheet's trace."""
        task = state["task"]
        with tracing.observe(name, state["trace_id"], trace_name=f"sheet {task['id']}",
                             metadata=trace_metadata(task, state["run_date"]),
                             tags=[task["kind"], task["subject_code"], task["rule"]], **kwargs) as span:
            yield span

    def output(span: Any, value: Any, warning: bool = False) -> None:
        if span is not None:
            span.update(output=value, level="WARNING" if warning else "DEFAULT")

    # ------------------------------------------------------------------ evening level

    def collect(state: EveningState) -> dict:
        with step("collect", state) as span:
            if services.collector is None:
                result = {"status": "skipped"}
            else:
                try:
                    result = services.collector()
                except Exception as error:  # noqa: BLE001  (the evening goes on with what is already known)
                    result = {"status": "failed", "errors": [{"kind": "run", "ref": "", "error": str(error)[:300]}]}
            output(span, result, warning=result["status"] not in ("ok", "skipped"))
        say("Collecte École Directe : " + ("sautée" if result["status"] == "skipped" else result["status"]))
        update: dict = {"collect": result}
        if result.get("student_id") and not state.get("student_id"):
            update["student_id"] = result["student_id"]
        return update

    def index_documents(state: EveningState) -> dict:
        """New or changed files of the drop folder are indexed (T18)."""
        if services.material is None or services.embedder is None:
            return {"documents": {"skipped": True}}
        with step("index_documents", state) as span, connect() as conn:
            say("Documents du dépôt : indexation…")
            result = index.sync(conn, services.material, services.embedder).as_json()
            output(span, result, warning=bool(result["failed"]))
        say(f"Documents : {len(result['indexed'])} indexé(s), {result['unchanged']} inchangé(s)"
            + (f", {len(result['failed'])} en échec" if result["failed"] else ""))
        return {"documents": result}

    def sync_feedback(state: EveningState) -> dict:
        """Answers, reports and finished sheets since the last call, kept on the laptop (T17)."""
        if services.api is None:
            return {"feedback": {"skipped": "API non configurée"}}
        with step("sync_feedback", state) as span:
            try:
                with connect() as conn:
                    since = feedback_store.cursor(conn)
                with services.api() as client:
                    data = client.feedback(since)
                with connect() as conn:
                    result = {**feedback_store.save(conn, data), "since": since, "until": data["until"]}
            except Exception as error:  # noqa: BLE001  (the feedback waits for tomorrow, the sheets do not)
                result = {"error": str(error)[:300]}
            output(span, result, warning="error" in result)
        say(f"Retours de la tablette : non récupérés ({result['error'][:100]})" if "error" in result else
            f"Retours de la tablette : {result['attempts']} réponse(s), {result['reports']} signalement(s), "
            f"{result['finishes']} fiche(s) terminée(s)")
        return {"feedback": result}

    def summarize_lessons(state: EveningState) -> dict:
        """Each lesson is summarized once, when it arrives; the summary is kept in the database."""
        if not state.get("student_id"):
            return {}
        with connect() as conn:
            pending = lessons.without_summary(conn, state["student_id"])
            prompt = prompts.get(formats.SUMMARY_PROMPT) if any(len(l.content) >= lessons.SUMMARY_MIN_CHARS for l in pending) else None
            for lesson in pending:
                if len(lesson.content) < lessons.SUMMARY_MIN_CHARS:
                    lessons.save_summary(conn, lesson, lesson.content, model="none")
                    continue
                trace = tracing.trace_id("summary", lesson.source_id, lesson.fingerprint)
                metadata = {"lesson": lesson.key, "prompt.lesson-summary": prompt.label, **generator.metadata(conf.engine)}
                with tracing.observe("summarize_lesson", trace, trace_name=f"summary {lesson.key}",
                                     metadata=metadata, prompt=prompt.client, input={"content": lesson.content}) as span:
                    messages = prompt.compile(subject=lesson.label, day=lesson.day.strftime("%d/%m/%Y"), content=lesson.content)
                    say(f"Résumé de la séance {lesson.key}…")
                    try:
                        text = models.text("generator", messages, tracing.callbacks())
                    except ModelError as error:  # tried again next evening; the sheets read the lesson itself
                        output(span, str(error)[:500], warning=True)
                        continue
                    output(span, text)
                lessons.save_summary(conn, lesson, text, model=generator.model, prompt_version=prompt.label, trace_id=trace)
        return {}

    def detect_tests(state: EveningState) -> dict:
        """Homework that may announce a test without the usual words: the model reads it, once
        per version of its text, as a last resort (ARCHITECTURE.md §8)."""
        student = state.get("student_id")
        if not student:
            return {}
        today = date.fromisoformat(state["run_date"])
        with connect() as conn:
            rows = conn.execute(
                "SELECT source, source_id, fingerprint, subject_code, subject, given_on, due_on, text "
                "FROM school.homework h WHERE student_id = %s AND is_assessment <> 'yes' AND due_on > %s "
                "AND due_on <= %s AND text <> '' AND NOT EXISTS (SELECT 1 FROM agent.assessment_checks c "
                "WHERE c.source = h.source AND c.student_id = h.student_id AND c.source_id = h.source_id "
                "AND c.fingerprint = h.fingerprint) ORDER BY due_on, subject_code",
                (student, today, today + timedelta(days=planning.HORIZON_DAYS)),
            ).fetchall()
            pending = [r for r in rows if r[3] not in EXCLUDED_SUBJECTS and assessments.is_ambiguous(r[7])]
            pending = pending[:MAX_TEST_CHECKS]
            if not pending:
                return {"detected": {"asked": 0, "tests": 0}}
            prompt = prompts.get(formats.ASSESSMENT_PROMPT)
            asked, found = 0, []
            with step("detect_tests", state, prompt=prompt.client) as span:
                for source, source_id, fingerprint, code, subject, given_on, due_on, text in pending:
                    messages = prompt.compile(
                        subject=label(code, subject), today=fr_day(today), due_on=f"{fr_day(due_on)} {due_on.year}",
                        given_on=fr_day(given_on) if given_on else "inconnu", text=text[:1500])
                    say(f"Lecture d'un devoir de {label(code, subject)} pour le {fr_day(due_on)} : est-ce un contrôle ?")
                    try:
                        verdict = models.structured("generator", messages, draft.AssessmentVerdict, tracing.callbacks())
                    except (ModelError, ValidationError):
                        continue  # asked again tomorrow
                    asked += 1
                    day = _test_day(verdict.date, today, due_on)
                    conn.execute(
                        "INSERT INTO agent.assessment_checks (source, student_id, source_id, fingerprint, is_assessment, "
                        "day, scope, confidence, model, trace_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (source, student, source_id, fingerprint, verdict.is_assessment, day, verdict.scope[:200],
                         verdict.confidence, generator.model, tracing.trace_id(state["run_date"], "evening")),
                    )
                    if verdict.is_assessment and verdict.confidence >= plan_inputs.MODEL_CONFIDENCE:
                        found.append({"subject": code, "day": (day or due_on).isoformat(), "confidence": verdict.confidence})
                output(span, {"asked": asked, "tests": found})
        say(f"Devoirs ambigus : {len(found)} contrôle(s) trouvé(s) sur {asked} devoir(s) lu(s)")
        return {"detected": {"asked": asked, "tests": len(found)}}

    def plan(state: EveningState) -> dict:
        """The evening plan (T19): tasks kept, and tasks skipped with their reason, in the trace."""
        student = state.get("student_id")
        if not student:
            skipped = [{"task": "soirée", "reason": "aucun élève connu : lance d'abord une collecte"}]
            return {"tasks": [], "tests": [], "plan": {"skipped": skipped, "done": [], "rules": []}}
        today = date.fromisoformat(state["run_date"])
        with step("plan", state) as span, connect() as conn:
            rules_store.ensure_defaults(conn)
            rules = rules_store.active(conn)
            known = plan_inputs.load(conn, student, today)
            if state.get("lesson_ids"):
                result = planning.forced(known, state["lesson_ids"], state.get("forced_format") or "exercises")
            else:
                result = planning.plan(known, rules)
            week = today + timedelta(days=7)
            tests = [{"subject": t.subject, "subject_code": t.subject_code, "day": t.day.isoformat(), "reason": t.reason}
                     for t in planning.unique_tests(known.tests) if t.day <= week]
            used = [f"{rule.id} v{rule.version}" for rule in rules]
            output(span, {
                "rules": used,
                "tasks": [{"id": t.id, "rule": t.rule_id, "format": t.kind, "visible_from": t.visible_from.isoformat(),
                           "minutes": t.target_minutes, "sources": [l.key for l in t.lessons]} for t in result.tasks],
                "skipped": result.skipped, "done": result.done, "tests": tests,
            })
        say(f"Plan : {len(result.tasks)} fiche(s) à faire ce soir, {len(result.skipped)} tâche(s) sautée(s), "
            f"{len(result.done)} déjà faite(s)")
        for task in result.tasks:
            say(f"  - {task.id} ({task.kind}, visible le {fr_day(task.visible_from)})")
        return {"tasks": [t.as_json() for t in result.tasks], "tests": tests,
                "plan": {"skipped": result.skipped, "done": result.done, "rules": used}}

    def retrieve(state: EveningState) -> dict:
        """The document pages closest to each task's lessons (T18). Done for every task at once:
        the embedding model is loaded once."""
        tasks = state.get("tasks") or []
        if not tasks or services.embedder is None:
            return {}
        with connect() as conn:
            if not index.has_chunks(conn):
                return {}
            with step("retrieve", state) as span:
                say("Recherche des pages de documents pour chaque fiche…")
                queries = [_query(conn, state["student_id"], task) for task in tasks]
                wanted = [i for i, query in enumerate(queries) if len(query) >= MIN_QUERY_CHARS]
                try:
                    vectors = services.embedder.embed([queries[i] for i in wanted]) if wanted else []
                except EmbeddingError as error:
                    output(span, {"error": str(error)}, warning=True)
                    return {}
                updated = [dict(task) for task in tasks]
                for i, vector in zip(wanted, vectors, strict=True):
                    hits = search.similar_pages(conn, vector, updated[i]["subject_code"], min_score=search.MIN_SCORE)
                    updated[i]["pages"] = [hit.as_json() for hit in hits]
                output(span, {task["id"]: [(p["label"], p["score"]) for p in task["pages"]] for task in updated})
        return {"tasks": updated}

    def to_branches(state: EveningState) -> list[Send] | str:
        tasks = state.get("tasks") or []
        if not tasks:
            return "publish"
        return [
            Send("task", {
                "run_date": state["run_date"], "student_id": state["student_id"], "task": task,
                "trace_id": tracing.trace_id(state["run_date"], task["id"]),
                "attempts": 0, "failures": [], "sources": {}, "labels": {}, "sheet": None,
            })
            for task in tasks
        ]

    # --------------------------------------------------------------------- task level

    def entry(state: TaskState) -> str:
        """Back from the judge, the sources are already gathered."""
        return "generate" if state.get("sources") else "gather_sources"

    def gather_sources(state: TaskState) -> dict:
        task = state["task"]
        with node("gather_sources", state) as span, connect() as conn:
            sources, labels = _sources(conn, state["student_id"], task)
            pages = {p["key"]: p["score"] for p in task.get("pages", [])}
            output(span, {key: {"chars": len(text), "label": labels.get(key, ""), **({"score": pages[key]} if key in pages else {})}
                          for key, text in sources.items()})
        return {"sources": sources, "labels": labels}

    def after_sources(state: TaskState) -> str:
        return "generate" if _chars(state.get("sources") or {}) >= MIN_SOURCE_CHARS else "abstain"

    def generate(state: TaskState) -> dict:
        task = state["task"]
        shape = formats.FORMATS[task["kind"]]
        prompt = prompts.get(shape.prompt)
        feedback = ""
        if state.get("failures"):
            feedback = ("\nLa version précédente a été refusée par les vérifications. "
                        "Corrige ces points :\n" + "\n".join(f"- {r}" for r in state["failures"]))
        items = shape.item_count(task["target_minutes"], _chars(state["sources"]))
        messages = prompt.compile(
            subject=task["subject"],
            item_count=items,
            target_minutes=task["target_minutes"],
            sources="\n\n".join(f"[{key}]\n{text}" for key, text in state["sources"].items()),
            feedback=feedback,
            context=_context(task, state["sources"]),
        )
        attempt = state.get("attempts", 0) + 1
        say(f"{task['id']} : écriture, essai {attempt}" + (f", {items} questions" if shape.scales else "") + "…")
        started = time.monotonic()
        with node("generate", state, prompt=prompt.client, input={"attempt": attempt}) as span:
            try:
                written = models.structured("generator", messages, shape.shape, tracing.callbacks())
                sheet = draft.to_sheet(written, task=task, sources=state["sources"], labels=state.get("labels") or {},
                                       trace_id=state["trace_id"])
            except (ModelError, ValidationError, ValueError) as error:
                reasons = [_format_reason(error)]
                output(span, reasons[0][:500], warning=True)
                say(f"{task['id']} : sortie refusée ({reasons[0][:120]})")
                result = guards.GuardResult("format", False, reasons)
                tracing.score(state["trace_id"], "guard.format", False, f"essai {attempt} : {reasons[0][:400]}")
                return {"sheet": None, "failures": reasons, "attempts": attempt, "prompt_version": prompt.label,
                        "guard_log": [{"attempt": attempt, **result.as_json()}], "memory": [report.memory_sample()]}
            output(span, sheet.model_dump(mode="json"))
        say(f"{task['id']} : fiche écrite en {round(time.monotonic() - started)} s")
        tracing.score(state["trace_id"], "guard.format", True, f"essai {attempt}")
        return {"sheet": sheet.model_dump(mode="json"), "failures": [], "attempts": attempt,
                "prompt_version": prompt.label, "memory": [report.memory_sample()],
                "guard_log": [{"attempt": attempt, "name": "format", "ok": True, "reasons": []}]}

    def check(state: TaskState) -> dict:
        if state.get("sheet") is None:
            return {}  # the format guard already failed in generate
        task = state["task"]
        sheet = Sheet.model_validate(state["sheet"])
        with node("guards", state) as span:
            results = guards.run_all(sheet, state["sources"], task["target_minutes"], task["computational"],
                                     concepts=task.get("focus") or None)
            for result in results:
                tracing.score(state["trace_id"], f"guard.{result.name}", result.ok,
                              f"essai {state['attempts']}" + (" : " + " ; ".join(result.reasons)[:400] if result.reasons else ""))
            failures = [reason for result in results for reason in result.reasons]
            output(span, {r.name: r.ok for r in results}, warning=bool(failures))
        say(f"{task['id']} : garde-fous OK" if not failures else
            f"{task['id']} : refusée par les garde-fous ({len(failures)} motif(s), dont : {failures[0][:110]})")
        return {"failures": failures,
                "guard_log": [{"attempt": state["attempts"], **result.as_json()} for result in results]}

    def after_guards(state: TaskState) -> str:
        if not state.get("failures"):
            return "done"
        return "generate" if state["attempts"] < MAX_GENERATIONS else "abstain"

    def done(state: TaskState) -> dict:
        return {"outcomes": {state["task"]["id"]: _outcome(state, "guarded")}}

    def abstain(state: TaskState) -> dict:
        task = state["task"]
        chars = _chars(state.get("sources") or {})
        if not state.get("sources"):
            reason = task.get("missing") or "Rien d'exploitable dans les sources de cette fiche."
        elif chars < MIN_SOURCE_CHARS:
            reason = f"Trop peu de texte pour une fiche ({chars} caractères) : {planning.ASK_FOR_NOTEBOOK}"
        else:
            reason = (f"Fiche non publiée après {state['attempts']} essais. Dernier motif : "
                      + "; ".join(state.get("failures") or ["inconnu"])[:600])
        outcome = _outcome(state, "abstained", reason)
        with node("abstain", state, input={"reason": reason}), connect() as conn:
            _store(conn, outcome, model=generator.model)
        tracing.score(state["trace_id"], "abstained", True, reason[:400])
        tracing.score(state["trace_id"], "generations", float(state.get("attempts", 0)))
        say(f"{task['id']} : abstention. {reason[:160]}")
        return {"outcomes": {task["id"]: outcome}}

    # ------------------------------------------------------------------------- judge

    def to_judges(state: EveningState) -> list[Send] | str:
        waiting = [o for o in (state.get("outcomes") or {}).values() if o["status"] == "guarded"]
        return [Send("judge", o) for o in waiting] if waiting else "publish"

    def judge_sheet(outcome: Outcome) -> dict:
        """The judge reads a sheet the guards accepted (T17). Its problems send the sheet back to
        the generator, like a guard; after the last attempt, the agent abstains."""
        task, attempt = outcome["task"], outcome["attempts"]
        sheet = Sheet.model_validate(outcome["sheet"])
        prompt = prompts.get(formats.JUDGE_PROMPT)
        messages = prompt.compile(subject=task["subject"], sheet=judge.render(sheet),
                                  sources="\n\n".join(f"[{key}]\n{text}" for key, text in outcome["sources"].items()))
        verdict, error = None, ""
        say(f"{task['id']} : relecture par le correcteur…")
        with node("judge", outcome, prompt=prompt.client, input={"attempt": attempt}) as span:
            try:
                verdict = models.structured("judge", messages, judge.Verdict, tracing.callbacks())
            except (ModelError, ValidationError) as failure:
                error = str(failure)[:300]
            output(span, verdict.model_dump() if verdict else error, warning=verdict is None)
        record = {"model": judge_model.model, "prompt_version": prompt.label, "attempt": attempt}
        if verdict is None:
            # The guards passed: a judge that cannot answer does not block the sheet. Counted.
            record |= {"available": False, "error": error}
            failures: list[str] = []
            tracing.score(outcome["trace_id"], "judge.available", False, error)
            status = "ready"
        else:
            failures = judge.reasons(verdict, sheet)
            record |= {"available": True, "ok": not failures, "reasons": failures, "verdict": verdict.model_dump()}
            for name, rate in judge.rates(verdict, sheet).items():
                tracing.score(outcome["trace_id"], f"judge.{name}", rate)
            tracing.score(outcome["trace_id"], "judge.ok", not failures,
                          f"essai {attempt}" + (" : " + " ; ".join(failures)[:400] if failures else ""))
            status = "ready" if not failures else "rejected" if attempt < MAX_GENERATIONS else "abstained"
        updated: Outcome = {**outcome, "status": status, "judge": record, "failures": failures,
                            "guard_log": outcome["guard_log"] + [{"attempt": attempt, "name": "judge",
                                                                  "ok": not failures, "reasons": failures}]}
        say(f"{task['id']} : " + {"ready": "acceptée" + (" (correcteur indisponible)" if verdict is None else ""),
                                  "rejected": "renvoyée par le correcteur",
                                  "abstained": "abstention après le correcteur"}[status]
            + (f" ({failures[0][:110]})" if failures else ""))
        if status == "abstained":
            updated["reason"] = f"Fiche non publiée après {attempt} essais. Dernier motif : " + "; ".join(failures)[:600]
        if status in ("ready", "abstained"):
            with connect() as conn:
                _store(conn, updated, model=generator.model)
            tracing.score(outcome["trace_id"], "abstained", status == "abstained",
                          updated.get("reason", "")[:400] or None)
            tracing.score(outcome["trace_id"], "generations", float(attempt))
        return {"outcomes": {task["id"]: updated}, "memory": [report.memory_sample()]}

    def to_regenerations(state: EveningState) -> list[Send] | str:
        rejected = [o for o in (state.get("outcomes") or {}).values() if o["status"] == "rejected"]
        if not rejected:
            return "publish"
        return [
            Send("task", {
                "run_date": o["run_date"], "student_id": o["student_id"], "task": o["task"], "trace_id": o["trace_id"],
                "attempts": o["attempts"], "failures": o["failures"], "sources": o["sources"], "labels": o["labels"],
                "sheet": None, "guard_log": o["guard_log"], "prompt_version": o["prompt_version"],
            })
            for o in rejected
        ]

    # ----------------------------------------------------------------- end of evening

    def publish(state: EveningState) -> dict:
        """One signed batch with every sheet ready, including those an earlier evening could not
        send. Never the sheets made to try a format (`--lesson`), nor those past their due date."""
        with step("publish", state) as span:
            if not state.get("publish"):
                result: dict = {"skipped": "--no-publish"}
            elif services.api is None:
                result = {"error": "adresse de l'API ou jeton du bunker absents", "count": 0}
            else:
                with connect() as conn:
                    rows = conn.execute(
                        "SELECT body FROM agent.sheets WHERE student_id = %s AND status = 'ready' AND body IS NOT NULL "
                        "AND rule_id NOT IN ('', 'forced') AND (due_by IS NULL OR due_by >= %s) ORDER BY id",
                        (state.get("student_id"), state["run_date"]),
                    ).fetchall()
                sheets = [Sheet.model_validate(row[0]) for row in rows]
                if not sheets:
                    result = {"count": 0, "ids": []}
                else:
                    try:
                        ids: list[str] = []
                        with services.api() as client:
                            for start in range(0, len(sheets), BATCH_MAX):
                                ids += client.publish(sheets[start:start + BATCH_MAX])
                        with connect() as conn:
                            conn.execute("UPDATE agent.sheets SET status = 'published', published_at = now() "
                                         "WHERE id = ANY(%s)", (ids,))
                        result = {"count": len(ids), "ids": ids}
                    except Exception as error:  # noqa: BLE001  (sent tomorrow with the next batch)
                        result = {"error": str(error)[:300], "count": 0}
            output(span, result, warning="error" in result)
        if "error" in result:
            say(f"Publication en échec : {result['error'][:150]}")
        elif "count" in result:
            say(f"Publication : {result['count']} fiche(s) envoyée(s)")
        return {"published": result}

    def end_report(state: EveningState) -> dict:
        """« OK : 3 fiches, contrôle jeudi » or « KO : motif », with RAM and swap, to the parent (§15)."""
        with step("report", state) as span:
            built = report.build(date.fromisoformat(state["run_date"]),
                                 {**state, "memory": (state.get("memory") or []) + [report.memory_sample()]})
            with connect() as conn:
                report.save(conn, built)
            if state.get("publish") and services.api is not None:
                try:
                    with services.api() as client:
                        client.report(built)
                    built["sent"] = True
                except Exception as error:  # noqa: BLE001  (the desktop notification still shows it)
                    built |= {"sent": False, "send_error": str(error)[:200]}
            output(span, built, warning=built["status"] == "ko")
        return {"report": built}

    # ------------------------------------------------------------------------ wiring

    task_graph = StateGraph(TaskState, output_schema=TaskOutput)
    task_graph.add_node("gather_sources", gather_sources)
    task_graph.add_node("generate", generate)
    task_graph.add_node("guards", check)
    task_graph.add_node("done", done)
    task_graph.add_node("abstain", abstain)
    task_graph.add_conditional_edges(START, entry, ["gather_sources", "generate"])
    task_graph.add_conditional_edges("gather_sources", after_sources, ["generate", "abstain"])
    task_graph.add_edge("generate", "guards")
    task_graph.add_conditional_edges("guards", after_guards, ["done", "generate", "abstain"])
    task_graph.add_edge("done", END)
    task_graph.add_edge("abstain", END)

    evening = StateGraph(EveningState)
    for name, function in [
        ("collect", collect), ("index_documents", index_documents), ("sync_feedback", sync_feedback),
        ("summarize_lessons", summarize_lessons), ("detect_tests", detect_tests), ("plan", plan),
        ("retrieve", retrieve), ("judge", judge_sheet), ("publish", publish), ("report", end_report),
    ]:
        evening.add_node(name, function)
    evening.add_node("task", task_graph.compile())
    evening.add_node("judging", lambda state: {})       # waits for every branch
    evening.add_node("regenerating", lambda state: {})  # waits for every judgment
    for first, then in [("collect", "index_documents"), ("index_documents", "sync_feedback"),
                        ("sync_feedback", "summarize_lessons"), ("summarize_lessons", "detect_tests"),
                        ("detect_tests", "plan"), ("plan", "retrieve"), ("task", "judging"),
                        ("judge", "regenerating"), ("publish", "report")]:
        evening.add_edge(first, then)
    evening.add_edge(START, "collect")
    evening.add_conditional_edges("retrieve", to_branches, ["task", "publish"])
    evening.add_conditional_edges("judging", to_judges, ["judge", "publish"])
    evening.add_conditional_edges("regenerating", to_regenerations, ["task", "publish"])
    evening.add_edge("report", END)
    return evening


# ----------------------------------------------------------------------------- helpers


def _test_day(written: str | None, today: date, due_on: date) -> date | None:
    """The date the model read, if it is a plausible one."""
    try:
        day = date.fromisoformat(written) if written else None
    except ValueError:
        return None
    if day is None or not today < day <= due_on + timedelta(days=planning.HORIZON_DAYS):
        return None
    return day


def _chars(sources: dict[str, str]) -> int:
    return sum(len(text.strip()) for text in sources.values())


def _query(conn: psycopg.Connection, student_id: str, task: dict) -> str:
    """What the search looks for: the lessons' text; for an empty lesson, the homework given that
    day (« apprendre la leçon p. 86 ») and what the teacher wrote about the test."""
    texts = []
    for ref in task["lessons"]:
        lesson = lessons.get(conn, student_id, ref["source"], ref["source_id"])
        if lesson and lesson.content.strip():
            texts.append(lesson.content)
    if not texts:
        for day in task.get("scope_days", []):
            texts += lessons.homework_of(conn, student_id, task["subject_code"], date.fromisoformat(day))
    if task.get("test") and task["test"].get("text"):
        texts.append(task["test"]["text"])
    return "\n".join(texts)[:4000]


def _sources(conn: psycopg.Connection, student_id: str, task: dict) -> tuple[dict[str, str], dict[str, str]]:
    """The lessons first, most recent first within the budget, then the pages found. Each text
    is given as it is: the quotes must be copied from what the teacher or the book wrote."""
    found = []
    for ref in task["lessons"]:
        lesson = lessons.get(conn, student_id, ref["source"], ref["source_id"])
        if lesson and lesson.content.strip():
            found.append(lesson)
    budget, kept = MAX_SOURCE_CHARS, {}
    for lesson in sorted(found, key=lambda l: l.day, reverse=True):
        if budget < 200:
            break
        kept[lesson.key] = lesson.content[:budget]
        budget -= len(kept[lesson.key])
    sources = {lesson.key: kept[lesson.key] for lesson in sorted(found, key=lambda l: l.day) if lesson.key in kept}
    labels = {lesson.key: f"ton cours du {fr_day(lesson.day)}" for lesson in found if lesson.key in kept}
    for page in task.get("pages", []):
        if budget < 300:
            break
        text = search.page_text(conn, page["document_id"], page["page"])[:min(PAGE_CHARS, budget)]
        if text.strip():
            sources[page["key"]], labels[page["key"]] = text, page["label"]
            budget -= len(text)
    return sources, labels


def _context(task: dict, sources: dict[str, str]) -> str:
    """What the code tells the model about this task, beyond the format's own prompt."""
    lines = []
    test = task.get("test")
    if task["kind"] == "test_revision" and test:
        lines.append(f"C'est une révision pour le contrôle de {task['subject']} du "
                     f"{fr_day(date.fromisoformat(test['day']))} : {task['stage_text']}.")
        if test.get("text"):
            lines.append(f"Ce que le prof a écrit : « {test['text'][:300]} »")
    elif task.get("stage") == "missed":
        lines.append("Cette fiche reprend les notions ratées dans les dernières fiches.")
    elif task.get("stage_text"):
        lines.append(f"Type de révision : {task['stage_text']}.")
    if task.get("focus"):
        lines.append("Notions ratées récemment, à reprendre en priorité avec au moins une question sur chacune : "
                     + " ; ".join(task["focus"]) + ". Écris le nom de la notion dans `concept`.")
    if task.get("style") == "oral_quiz":
        lines.append("Style : comme une interrogation orale en début de cours, questions directes, "
                     "réponses de quelques mots.")
    if any(key.startswith("doc:") for key in sources):
        lines.append("Des pages de documents sont jointes (sources [doc:…]) : elles complètent la séance."
                     + (" En mathématiques, n'en reprends pas les nombres : seulement la notion et la méthode."
                        if task["computational"] else ""))
    return "\n".join(lines)


def _outcome(state: TaskState, status: str, reason: str = "") -> Outcome:
    return {
        "run_date": state["run_date"], "student_id": state["student_id"], "task": state["task"],
        "status": status, "reason": reason, "sheet": state.get("sheet"), "attempts": state.get("attempts", 0),
        "failures": state.get("failures") or [], "guard_log": state.get("guard_log") or [],
        "prompt_version": state.get("prompt_version", ""), "trace_id": state["trace_id"],
        "sources": state.get("sources") or {}, "labels": state.get("labels") or {}, "judge": None,
    }


def _format_reason(error: Exception) -> str:
    if isinstance(error, ValidationError):
        details = "; ".join(f"{'.'.join(str(p) for p in e['loc'])} : {e['msg']}" for e in error.errors()[:5])
        return f"fiche mal formée : {details}"
    return f"fiche mal formée : {error}"[:600]


def _store(conn: psycopg.Connection, outcome: Outcome, *, model: str) -> None:
    task, status = outcome["task"], outcome["status"]
    ready = status == "ready"
    sheet = outcome.get("sheet") if ready else None
    conn.execute(
        "INSERT INTO agent.sheets (id, run_date, task_id, student_id, subject_code, kind, status, body, reason, "
        "attempts, guards, trace_id, model, prompt_version, release, judge, rule_id, rule_version, visible_from, "
        "due_by, minutes, inputs) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (id) DO UPDATE SET run_date = EXCLUDED.run_date, status = EXCLUDED.status, "
        "body = EXCLUDED.body, reason = EXCLUDED.reason, attempts = EXCLUDED.attempts, guards = EXCLUDED.guards, "
        "trace_id = EXCLUDED.trace_id, model = EXCLUDED.model, prompt_version = EXCLUDED.prompt_version, "
        "release = EXCLUDED.release, judge = EXCLUDED.judge, rule_id = EXCLUDED.rule_id, "
        "rule_version = EXCLUDED.rule_version, visible_from = EXCLUDED.visible_from, due_by = EXCLUDED.due_by, "
        "minutes = EXCLUDED.minutes, inputs = EXCLUDED.inputs, updated_at = now(), published_at = NULL",
        (task["id"], outcome["run_date"], task["id"], outcome["student_id"], task["subject_code"], task["kind"], status,
         Jsonb(sheet) if sheet else None, outcome.get("reason", ""), outcome.get("attempts", 0),
         Jsonb(outcome.get("guard_log") or []), outcome["trace_id"], model, outcome.get("prompt_version", ""),
         tracing.release(), Jsonb(outcome.get("judge")) if outcome.get("judge") else None, task["rule"],
         task["rule_version"], task["visible_from"], task.get("due_by"),
         sheet["estimated_minutes"] if sheet else 0, task.get("inputs", "")),
    )


# ------------------------------------------------------------------------------ running


@contextmanager
def checkpointer(database_url: str) -> Iterator[PostgresSaver]:
    """LangGraph's tables live in the `langgraph` schema of the application database."""
    conninfo = make_conninfo(database_url, options="-c search_path=langgraph")
    with PostgresSaver.from_conn_string(conninfo) as saver:
        saver.setup()
        yield saver


@dataclass
class Evening:
    results: list[dict]  # one per task: ready, published or abstained
    report: dict
    plan: dict
    resumed: bool


def run(
    run_date: date,
    student_id: str | None,
    services: Services,
    database_url: str,
    *,
    publish: bool = False,
    lesson_ids: list[str] | None = None,
    forced_format: str | None = None,
    restart: bool = False,
) -> Evening:
    """Run the evening, or resume it if a run of the same date stopped half-way."""
    def connect() -> psycopg.Connection:
        return psycopg.connect(database_url, autocommit=True)

    thread = run_date.isoformat()
    config_ = {"configurable": {"thread_id": thread}, "max_concurrency": 1, "recursion_limit": 200}
    with checkpointer(database_url) as saver:
        graph = build(services, connect).compile(checkpointer=saver)
        interrupted = bool(graph.get_state(config_).next)
        if interrupted and not restart:
            if services.progress is not None:
                services.progress("Reprise du traitement interrompu, là où il s'était arrêté")
            final = graph.invoke(None, config_)
        else:
            saver.delete_thread(thread)  # a finished run of the same day: start again from scratch
            final = graph.invoke({
                "run_date": thread, "student_id": student_id, "lesson_ids": lesson_ids or [],
                "forced_format": forced_format or "", "publish": publish,
            }, config_)
    tracing.flush()
    published = set((final.get("published") or {}).get("ids", []))
    results = [
        {"task_id": o["task"]["id"], "kind": o["task"]["kind"], "rule": o["task"]["rule"],
         "status": "published" if o["task"]["id"] in published else o["status"], "reason": o.get("reason", ""),
         "attempts": o.get("attempts", 0), "trace_id": o["trace_id"], "visible_from": o["task"]["visible_from"]}
        for o in sorted((final.get("outcomes") or {}).values(), key=lambda o: o["task"]["id"])
    ]
    return Evening(results=results, report=final.get("report") or {}, plan=final.get("plan") or {},
                   resumed=interrupted and not restart)

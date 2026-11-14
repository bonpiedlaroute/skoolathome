"""The evening graph (ARCHITECTURE.md §9.1), first version.

    summarize_lessons ─► plan ─► [Send: one branch per task] ─► end
                                        │
        gather_sources ─► generate ─► guards ─┬─► save
                              ▲               │
                              └─ ko, < 3 tries┤
                                              └─► abstain(reason)

The model only does text work, in an imposed format. Everything else is code: the plan, the
sources, the guards, the decision to publish or to abstain. Checkpoints go to Postgres with
the run date as thread id: a run stopped half-way resumes where it was.
"""

from __future__ import annotations

import operator
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from typing import Annotated, Any, TypedDict

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from skoolathome.agent import config, draft, guards, lessons, prompts, tracing
from skoolathome.agent.llm import ModelError
from skoolathome.schema.sheet import Sheet

MAX_GENERATIONS = 3  # the first one, then up to two regenerations (ARCHITECTURE.md §9.5)
GENERATOR_PROMPT = "sheet-generator"
SUMMARY_PROMPT = "lesson-summary"


class EveningState(TypedDict, total=False):
    run_date: str
    student_id: str
    lesson_ids: list[str]  # lessons forced from the command line; empty: the fixed choice
    tasks: list[dict]
    results: Annotated[list[dict], operator.add]


class TaskState(TypedDict, total=False):
    run_date: str
    student_id: str
    task: dict
    trace_id: str
    sources: dict[str, str]  # source key -> text given to the model
    sheet: dict | None  # the last generated sheet, before the guards
    failures: list[str]  # why the last attempt failed: fed back to the model
    guard_log: Annotated[list[dict], operator.add]  # every guard result of every attempt
    attempts: int
    prompt_version: str
    results: Annotated[list[dict], operator.add]


class TaskOutput(TypedDict):
    """What a branch hands back to the evening: its result only. The branches run side by side,
    and must not write the evening's other keys."""
    results: Annotated[list[dict], operator.add]


Connect = Callable[[], psycopg.Connection]


def build(models: Any, connect: Connect, conf: config.Config | None = None) -> StateGraph:
    """`models` has text() and structured() (agent.llm.Models, or a fake in tests)."""
    conf = conf or config.load()
    generator = conf.model("generator")

    def trace_metadata(state: TaskState, **extra: str) -> dict[str, str]:
        task = state["task"]
        return {"run_date": state["run_date"], "task": task["id"], "subject": task["subject_code"],
                "format": task["kind"], "rule": task["rule"], **generator.metadata(conf.engine), **extra}

    @contextmanager
    def node(name: str, state: TaskState, **kwargs: Any) -> Iterator[Any]:
        task = state["task"]
        with tracing.observe(name, state["trace_id"], trace_name=f"sheet {task['id']}",
                             metadata=trace_metadata(state), tags=[task["kind"], task["subject_code"]],
                             **kwargs) as span:
            yield span

    # ------------------------------------------------------------- evening level

    def summarize_lessons(state: EveningState) -> dict:
        """Each lesson is summarized once, when it arrives; the summary is kept in the database."""
        with connect() as conn:
            pending = lessons.without_summary(conn, state["student_id"])
            prompt = prompts.get(SUMMARY_PROMPT) if any(len(l.content) >= lessons.SUMMARY_MIN_CHARS for l in pending) else None
            for lesson in pending:
                if len(lesson.content) < lessons.SUMMARY_MIN_CHARS:
                    lessons.save_summary(conn, lesson, lesson.content, model="none")
                    continue
                trace = tracing.trace_id("summary", lesson.source_id, lesson.fingerprint)
                metadata = {"lesson": lesson.key, "prompt.lesson-summary": prompt.label, **generator.metadata(conf.engine)}
                with tracing.observe("summarize_lesson", trace, trace_name=f"summary {lesson.key}",
                                     metadata=metadata, prompt=prompt.client, input={"content": lesson.content}) as span:
                    messages = prompt.compile(subject=lesson.label, day=lesson.day.strftime("%d/%m/%Y"), content=lesson.content)
                    try:
                        text = models.text("generator", messages, tracing.callbacks())
                    except ModelError as error:  # tried again next evening; the sheets read the lesson itself
                        if span is not None:
                            span.update(level="WARNING", status_message=str(error)[:500])
                        continue
                    if span is not None:
                        span.update(output=text)
                lessons.save_summary(conn, lesson, text, model=generator.model, prompt_version=prompt.label, trace_id=trace)
        return {}

    def plan(state: EveningState) -> dict:
        with connect() as conn:
            tasks = lessons.fixed_tasks(conn, state["student_id"], date.fromisoformat(state["run_date"]),
                                        state.get("lesson_ids") or None)
        return {"tasks": tasks}

    def one_branch_per_task(state: EveningState) -> list[Send] | str:
        if not state.get("tasks"):
            return END
        return [
            Send("task", {
                "run_date": state["run_date"], "student_id": state["student_id"], "task": task,
                "trace_id": tracing.trace_id(state["run_date"], task["id"]),
                "attempts": 0, "failures": [], "sources": {}, "sheet": None,
            })
            for task in state["tasks"]
        ]

    # --------------------------------------------------------------- task level

    def gather_sources(state: TaskState) -> dict:
        task = state["task"]
        with node("gather_sources", state) as span, connect() as conn:
            lesson = lessons.get(conn, state["student_id"], task["source"], task["source_id"])
            sources = {lesson.key: lesson.content} if lesson and lesson.content.strip() else {}
            if span is not None:
                span.update(output={key: {"chars": len(text), "summary": lessons.summary(conn, lesson)}
                                    for key, text in sources.items()})
        return {"sources": sources}

    def generate(state: TaskState) -> dict:
        task = state["task"]
        prompt = prompts.get(GENERATOR_PROMPT)
        feedback = ""
        if state.get("failures"):
            feedback = ("\nLa version précédente a été refusée par les vérifications automatiques. "
                        "Corrige ces points :\n" + "\n".join(f"- {r}" for r in state["failures"]))
        messages = prompt.compile(
            subject=task["subject"],
            item_count=draft.item_count(task["target_minutes"]),
            target_minutes=task["target_minutes"],
            sources="\n\n".join(f"[{key}]\n{text}" for key, text in state["sources"].items()),
            feedback=feedback,
        )
        attempt = state.get("attempts", 0) + 1
        with node("generate", state, prompt=prompt.client, input={"attempt": attempt}) as span:
            try:
                written = models.structured("generator", messages, draft.DraftSheet, tracing.callbacks())
                sheet = draft.to_sheet(
                    written, sheet_id=task["id"], subject=task["subject"], source_id=task["source_key"],
                    kind=task["kind"], visible_from=date.fromisoformat(task["visible_from"]),
                    trace_id=state["trace_id"],
                )
            except (ModelError, ValidationError, ValueError) as error:
                reasons = [_format_reason(error)]
                if span is not None:
                    span.update(level="WARNING", status_message=reasons[0][:500])
                result = guards.GuardResult("format", False, reasons)
                tracing.score(state["trace_id"], "guard.format", False, f"essai {attempt} : {reasons[0][:400]}")
                return {"sheet": None, "failures": reasons, "attempts": attempt, "prompt_version": prompt.label,
                        "guard_log": [{"attempt": attempt, **result.as_json()}]}
            if span is not None:
                span.update(output=sheet.model_dump(mode="json"))
        tracing.score(state["trace_id"], "guard.format", True, f"essai {attempt}")
        return {"sheet": sheet.model_dump(mode="json"), "failures": [], "attempts": attempt,
                "prompt_version": prompt.label, "guard_log": [{"attempt": attempt, "name": "format", "ok": True, "reasons": []}]}

    def check(state: TaskState) -> dict:
        if state.get("sheet") is None:
            return {}  # the format guard already failed in generate
        task = state["task"]
        sheet = Sheet.model_validate(state["sheet"])
        with node("guards", state) as span:
            results = guards.run_all(sheet, state["sources"], task["target_minutes"], task["computational"])
            for result in results:
                tracing.score(state["trace_id"], f"guard.{result.name}", result.ok,
                              f"essai {state['attempts']}" + (" : " + " ; ".join(result.reasons)[:400] if result.reasons else ""))
            failures = [reason for result in results for reason in result.reasons]
            if span is not None:
                span.update(output={r.name: r.ok for r in results}, level="WARNING" if failures else "DEFAULT")
        return {"failures": failures,
                "guard_log": [{"attempt": state["attempts"], **result.as_json()} for result in results]}

    def after_guards(state: TaskState) -> str:
        if not state.get("failures"):
            return "save"
        return "generate" if state["attempts"] < MAX_GENERATIONS else "abstain"

    def after_sources(state: TaskState) -> str:
        return "generate" if state.get("sources") else "abstain"

    def save(state: TaskState) -> dict:
        with node("save", state), connect() as conn:
            _store(conn, state, "ready", reason="", model=generator.model)
        tracing.score(state["trace_id"], "abstained", False)
        tracing.score(state["trace_id"], "generations", float(state["attempts"]))
        return {"results": [_result(state, "ready")]}

    def abstain(state: TaskState) -> dict:
        if not state.get("sources"):
            reason = "Rien d'exploitable dans la séance : demande-lui son cahier, ou dépose une photo."
        else:
            reason = (f"Fiche non publiée après {state['attempts']} essais. Dernier motif : "
                      + "; ".join(state.get("failures") or ["inconnu"])[:600])
        with node("abstain", state, input={"reason": reason}), connect() as conn:
            _store(conn, state, "abstained", reason=reason, model=generator.model)
        tracing.score(state["trace_id"], "abstained", True, reason[:400])
        tracing.score(state["trace_id"], "generations", float(state.get("attempts", 0)))
        return {"results": [_result(state, "abstained", reason)]}

    task_graph = StateGraph(TaskState, output_schema=TaskOutput)
    task_graph.add_node("gather_sources", gather_sources)
    task_graph.add_node("generate", generate)
    task_graph.add_node("guards", check)
    task_graph.add_node("save", save)
    task_graph.add_node("abstain", abstain)
    task_graph.add_edge(START, "gather_sources")
    task_graph.add_conditional_edges("gather_sources", after_sources, ["generate", "abstain"])
    task_graph.add_edge("generate", "guards")
    task_graph.add_conditional_edges("guards", after_guards, ["save", "generate", "abstain"])
    task_graph.add_edge("save", END)
    task_graph.add_edge("abstain", END)

    evening = StateGraph(EveningState)
    evening.add_node("summarize_lessons", summarize_lessons)
    evening.add_node("plan", plan)
    evening.add_node("task", task_graph.compile())
    evening.add_edge(START, "summarize_lessons")
    evening.add_edge("summarize_lessons", "plan")
    evening.add_conditional_edges("plan", one_branch_per_task, ["task", END])
    evening.add_edge("task", END)
    return evening


def _format_reason(error: Exception) -> str:
    if isinstance(error, ValidationError):
        details = "; ".join(f"{'.'.join(str(p) for p in e['loc'])} : {e['msg']}" for e in error.errors()[:5])
        return f"fiche mal formée : {details}"
    return f"fiche mal formée : {error}"[:600]


def _result(state: TaskState, status: str, reason: str = "") -> dict:
    return {"task_id": state["task"]["id"], "status": status, "reason": reason,
            "attempts": state.get("attempts", 0), "trace_id": state["trace_id"]}


def _store(conn: psycopg.Connection, state: TaskState, status: str, *, reason: str, model: str) -> None:
    task = state["task"]
    conn.execute(
        "INSERT INTO agent.sheets (id, run_date, task_id, student_id, subject_code, kind, status, body, reason, "
        "attempts, guards, trace_id, model, prompt_version, release) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (id) DO UPDATE SET run_date = EXCLUDED.run_date, status = EXCLUDED.status, "
        "body = EXCLUDED.body, reason = EXCLUDED.reason, attempts = EXCLUDED.attempts, guards = EXCLUDED.guards, "
        "trace_id = EXCLUDED.trace_id, model = EXCLUDED.model, prompt_version = EXCLUDED.prompt_version, "
        "release = EXCLUDED.release, updated_at = now(), published_at = NULL",
        (task["id"], state["run_date"], task["id"], state["student_id"], task["subject_code"], task["kind"], status,
         Jsonb(state["sheet"]) if status == "ready" else None, reason, state.get("attempts", 0),
         Jsonb(state.get("guard_log", [])), state["trace_id"], model, state.get("prompt_version", ""),
         tracing.release()),
    )


# ------------------------------------------------------------------------- running


@contextmanager
def checkpointer(database_url: str) -> Iterator[PostgresSaver]:
    """LangGraph's tables live in the `langgraph` schema of the application database."""
    conninfo = make_conninfo(database_url, options="-c search_path=langgraph")
    with PostgresSaver.from_conn_string(conninfo) as saver:
        saver.setup()
        yield saver


def run(
    run_date: date,
    student_id: str,
    models: Any,
    database_url: str,
    *,
    lesson_ids: list[str] | None = None,
    restart: bool = False,
) -> tuple[list[dict], bool]:
    """Run the evening, or resume it if a run of the same date stopped half-way.
    Returns (one result per task, resumed?)."""
    def connect() -> psycopg.Connection:
        return psycopg.connect(database_url, autocommit=True)

    thread = run_date.isoformat()
    config_ = {"configurable": {"thread_id": thread}, "max_concurrency": 1, "recursion_limit": 100}
    with checkpointer(database_url) as saver:
        graph = build(models, connect).compile(checkpointer=saver)
        interrupted = bool(graph.get_state(config_).next)
        if interrupted and not restart:
            final = graph.invoke(None, config_)
        else:
            saver.delete_thread(thread)  # a finished run of the same day: start again from scratch
            final = graph.invoke({"run_date": thread, "student_id": student_id, "lesson_ids": lesson_ids or [],
                                  "results": []}, config_)
    tracing.flush()
    return final.get("results", []), interrupted and not restart

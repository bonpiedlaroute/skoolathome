"""The graph of instructions (ARCHITECTURE.md §7.4 and §9.2, T21).

    interpret ─┬─► clarify ─► interrupt(question) ─────────────────────► interpret …
               ├─► propose ─► interrupt(« J'ai compris : … ») ─┬─► save_rule   [Valider] or [Modifier]
               │                                                ├─► discard     [Annuler]
               │                                                └─► interpret   a correction in words
               ├─► save_rule   a rule written in the form: no model
               └─► end         still unclear after two questions

The model only fills a rule in an imposed format. The code links « le prof d'histoire » to the
subject and the teacher known in École Directe, asks instead of guessing, writes the sentence the
parent validates, and saves nothing without that validation. Each pause is a checkpoint in
Postgres (thread `instruction-<id>`): the graph resumes when the parent's reply comes back
through /api/feedback, maybe days later.
"""

from __future__ import annotations

import operator
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any, Literal, TypedDict

import psycopg
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import Field, ValidationError
from rapidfuzz import fuzz

from skoolathome.agent import config, prompts, tracing
from skoolathome.agent.checkpoints import checkpointer
from skoolathome.agent.draft import Draft
from skoolathome.agent.llm import ModelError
from skoolathome.rules import privacy
from skoolathome.rules import store as rules_store
from skoolathome.rules.describe import describe
from skoolathome.rules.plan import DEFAULT_TARGETS
from skoolathome.schema.rule import DAILY_RULE_ID, Origin, Rule
from skoolathome.store import instructions as store
from skoolathome.subjects import EXCLUDED_SUBJECTS, SUBJECT_LABELS, code_of, fr_day, label

INTERPRET_PROMPT = "instruction-interpret"
MAX_QUESTIONS = 2  # then the parent is sent to the form: two questions without a rule is enough
UNCLEAR = "Consigne toujours pas claire après deux questions : écris la règle avec le formulaire."
DEFAULT_OFFSETS = [4, 2, 1]
SHORT_TRIGGER = {"before_each_lesson": "before", "after_each_lesson": "after", "before_test": "test",
                 "after_feedback": "missed", "weekly": "weekly", "on_homework": "homework"}
# A rule without a subject on these triggers is the default rule itself, changed by the parent.
DEFAULT_IDS = {"after_each_lesson": DAILY_RULE_ID, "before_test": "before_test", "after_feedback": "missed_concepts",
               "load_limit": "max_evening_load"}
TITLES = {"m", "mr", "mme", "mlle", "monsieur", "madame", "mademoiselle", "prof", "professeur", "le", "la"}


# ------------------------------------------------------------------ what the model fills


class RuleDraft(Draft):
    subject: str = Field(description="code de la matière (HI-GE…), ou vide pour toutes les matières")
    teacher: str = Field(description="nom du professeur s'il est donné, sinon vide")
    trigger: Literal["before_each_lesson", "after_each_lesson", "before_test", "after_feedback", "load_limit", "weekly"]
    scope: Literal["previous_lesson", "this_lesson", "last_n_lessons", "since_last_test", "missed_items"]
    format: Literal["quick_quiz", "exercises", "lesson_recap", "test_revision"]
    oral: bool
    together: bool
    target_minutes: int
    items: int
    replaces_default: bool
    offsets_days: list[int]
    n_lessons: int
    max_minutes: int


class Interpretation(Draft):
    clear: bool
    question: str = Field(description="si clear est false : une seule question, courte ; sinon vide")
    rule: RuleDraft


# ------------------------------------------------------------- what École Directe knows


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _name_parts(name: str) -> set[str]:
    words = _plain(name).replace(".", " ").replace("-", " ").split()
    return {w for w in words if len(w) >= 3 and w not in TITLES}


@dataclass
class Known:
    """The student's subjects and teachers, as the timetable writes them."""

    subjects: dict[str, str] = field(default_factory=dict)       # code -> name
    teachers: dict[str, list[str]] = field(default_factory=dict)  # code -> teachers

    def listing(self) -> str:
        if not self.subjects:
            return "\n".join(f"- {code} : {name}" for code, name in SUBJECT_LABELS.items())
        return "\n".join(f"- {code} : {name}" + (f", {', '.join(self.teachers[code])}" if self.teachers.get(code) else "")
                         for code, name in sorted(self.subjects.items()))

    def names(self) -> str:
        return ", ".join(sorted(self.subjects.values())[:12])

    def by_teacher(self, written: str) -> list[tuple[str, str]]:
        """(subject, teacher) pairs whose teacher matches what the parent wrote (« Durand », « M. Durand »)."""
        wanted = _name_parts(written)
        return [(code, teacher) for code, teachers in self.teachers.items() for teacher in teachers
                if wanted and wanted & _name_parts(teacher)]


def known(conn: psycopg.Connection, student_id: str | None) -> Known:
    rows = conn.execute(
        "SELECT subject_code, subject, teacher FROM school.timetable_slots WHERE kind = 'COURS' "
        "AND (%(s)s::text IS NULL OR student_id = %(s)s) "
        "UNION SELECT subject_code, subject, teacher FROM school.lessons WHERE (%(s)s::text IS NULL OR student_id = %(s)s)",
        {"s": student_id},
    ).fetchall()
    found = Known()
    for code, subject, teacher in rows:
        if code in EXCLUDED_SUBJECTS or not code:
            continue
        found.subjects.setdefault(code, label(code, subject))
        if teacher.strip() and teacher.strip() not in found.teachers.setdefault(code, []):
            found.teachers[code].append(teacher.strip())
    for teachers in found.teachers.values():
        teachers.sort()
    return found


def _subject_from_name(written: str, known_: Known) -> str | None:
    code = code_of(written)
    if code:
        return code
    best = max(((fuzz.WRatio(_plain(written), _plain(name)), code) for code, name in known_.subjects.items()), default=None)
    return best[1] if best and best[0] >= 85 else None


# ------------------------------------------------------------------- rule from the draft


@dataclass
class Resolution:
    rule: Rule | None = None
    question: str = ""
    teachers: list[str] = field(default_factory=list)  # shown in « J'ai compris »: the link to École Directe


def resolve(reading: Interpretation, known_: Known, *, sentence: str, today: date,
            existing: dict[str, Rule] | None = None) -> Resolution:
    """The code decides: the subject and the teacher are looked up in École Directe, the rule is
    completed and validated. Anything missing becomes a question to the parent."""
    existing = existing or {}
    if not reading.clear:
        return Resolution(question=reading.question.strip() or "Peux-tu préciser : pour quelle matière, et à quel moment ?")
    draft = reading.rule
    code = None
    if draft.subject.strip():
        code = _subject_from_name(draft.subject, known_)
        if code is None:
            return Resolution(question=f"Je ne connais pas la matière « {draft.subject.strip()} ». Laquelle est-ce ? "
                                       f"({known_.names()})")
        if known_.subjects and code not in known_.subjects:
            return Resolution(question=f"Je ne vois pas de cours de {label(code)} dans l'emploi du temps. "
                                       f"Quelle matière est-ce ? ({known_.names()})")
    teacher = None
    if draft.teacher.strip():
        matches = [(c, t) for c, t in known_.by_teacher(draft.teacher) if code is None or c == code]
        if not matches:
            where = f" de {label(code)}" if code else ""
            names = ", ".join(known_.teachers.get(code, [])) if code else ""
            return Resolution(question=f"Je ne trouve pas « {draft.teacher.strip()} » parmi les profs{where} de "
                                       f"l'emploi du temps{f' ({names})' if names else ''}. De qui s'agit-il ?")
        if code is None and len({c for c, _ in matches}) > 1:
            return Resolution(question=f"« {draft.teacher.strip()} » enseigne plusieurs matières : "
                                       f"{', '.join(label(c) for c, _ in matches)}. Laquelle ?")
        code = code or matches[0][0]
        # A filter on the teacher only matters when several teach the subject: otherwise a
        # substitute teacher would stop the rule.
        if len(known_.teachers.get(code, [])) > 1:
            teacher = matches[0][1]
    trigger = draft.trigger
    if trigger == "before_each_lesson" and code is None:
        return Resolution(question="Pour quelle matière ?")

    base = existing.get(_rule_id(trigger, code, draft.format))
    origin = Origin(text=sentence[:600], author="parent", on=today)
    if trigger == "load_limit":
        minutes = draft.max_minutes or draft.target_minutes
        if minutes <= 0:
            return Resolution(question="Combien de minutes de fiches au plus par soir ?")
        return Resolution(Rule(id=DEFAULT_IDS["load_limit"], trigger="load_limit", max_minutes=min(minutes, 180),
                               origin=origin))
    data: dict[str, Any] = {
        "id": _rule_id(trigger, code, draft.format), "subject": code, "teacher": teacher, "trigger": trigger,
        "scope": _scope(trigger, draft.scope), "format": draft.format,
        "style": "oral_quiz" if draft.oral else "", "suggested_mode": "together" if draft.together or draft.oral else "solo",
        "target_minutes": _target(draft, base), "items": _items(draft, base),
        "replaces_default": bool(draft.replaces_default and code), "origin": origin,
        "n_lessons": min(max(draft.n_lessons, 1), 10) if draft.scope == "last_n_lessons" else 3,
    }
    if trigger == "before_test":
        offsets = sorted({d for d in draft.offsets_days if 1 <= d <= 14}, reverse=True)
        data["offsets_days"] = offsets or (base.offsets_days if base and base.offsets_days else DEFAULT_OFFSETS)
    try:
        rule = Rule.model_validate(data)
    except ValidationError as error:
        return Resolution(question=f"Je n'ai pas su en faire une règle ({error.errors()[0]['msg']}). "
                                   "Peux-tu la dire autrement ?")
    return Resolution(rule, teachers=[] if teacher else known_.teachers.get(code or "", []))


def _rule_id(trigger: str, code: str | None, fmt: str) -> str:
    if code is None and trigger in DEFAULT_IDS:
        return DEFAULT_IDS[trigger]
    return f"{(code or 'all').lower()}-{SHORT_TRIGGER.get(trigger, trigger)}-{fmt.replace('_', '-')}"


def _scope(trigger: str, scope: str) -> str:
    """The scopes that make sense for each trigger: a sheet before a lesson cannot be on it."""
    if trigger == "before_each_lesson":
        return scope if scope in ("previous_lesson", "last_n_lessons") else "previous_lesson"
    if trigger == "after_each_lesson":
        return scope if scope in ("this_lesson", "last_n_lessons") else "this_lesson"
    return {"before_test": "since_last_test", "after_feedback": "missed_items"}.get(trigger, scope)


def _target(draft: RuleDraft, base: Rule | None) -> int:
    if draft.target_minutes > 0:
        return min(draft.target_minutes, 60)
    if base is not None and base.format == draft.format:
        return base.target_minutes
    return DEFAULT_TARGETS[draft.format]


def _items(draft: RuleDraft, base: Rule | None) -> int | None:
    if draft.format in ("quick_quiz", "lesson_recap"):
        return None  # fixed by the format: 5 questions, or 2 after the « cours en clair »
    if draft.items > 0:
        return min(draft.items, 15)
    return base.items if base is not None and base.format == draft.format else None


def read(models: Any, prompt: prompts.Prompt, known_: Known, text: str, exchanges: str, today: date) -> Interpretation:
    """The model reads the sentence (and the answers to its questions) and fills a rule."""
    messages = prompt.compile(today=f"{fr_day(today)} {today.year}", subjects=known_.listing(), sentence=text,
                              exchanges=exchanges)
    return models.structured("generator", messages, Interpretation, tracing.callbacks())


# --------------------------------------------------------------------------- the graph


class InstructionState(TypedDict, total=False):
    id: str
    text: str
    form_rule: dict | None
    student_id: str | None
    today: str
    exchanges: Annotated[list[dict], operator.add]  # questions asked and the parent's answers
    question: str
    proposal: dict | None
    teachers: list[str]
    summary: str
    decision: dict | None   # the parent's reply to the proposal: approve, edit (with the rule), reject
    status: str             # once finished: saved, rejected or failed
    rule_id: str
    rule_version: int
    reason: str
    agent_rule: dict | None  # a rule the agent proposes from what it saw (T28): no model, straight to the pause
    facts: list[str]
    proposal_key: str


Connect = Callable[[], psycopg.Connection]


def _sentence(state: InstructionState) -> str:
    """The sentence the rule comes from, with what the parent added when asked."""
    answers = [e["answer"] for e in state.get("exchanges") or [] if e.get("answer")]
    return " — ".join([state.get("text", "")] + answers).strip(" —")


def _exchanges(state: InstructionState) -> str:
    lines = []
    for exchange in state.get("exchanges") or []:
        lines += [f"Question posée : {exchange['question']}", f"Réponse du parent : {exchange['answer']}"]
    return "\n".join(lines)


def build(models: Any, connect: Connect, conf: config.Config | None = None) -> StateGraph:
    conf = conf or config.load()
    generator = conf.model("generator")

    def interpret(state: InstructionState) -> dict:
        if state.get("form_rule"):
            return {"proposal": state["form_rule"], "decision": {"kind": "approve"}, "question": ""}
        if state.get("agent_rule") and not state.get("exchanges"):
            # The graph of proposals (§9.3): drafted by the code, from facts. A correction in words
            # (« plutôt à l'écrit ») goes through the model below, like an instruction.
            rule = Rule.model_validate(state["agent_rule"])
            return {"proposal": rule.model_dump(mode="json"), "question": "", "decision": None,
                    "summary": ("L'agent propose : " + describe(rule))[:800]}
        today = date.fromisoformat(state["today"])
        with connect() as conn:
            known_ = known(conn, state.get("student_id"))
            existing = {rule.id: rule for rule in rules_store.latest(conn)}
        prompt = prompts.get(INTERPRET_PROMPT)
        trace = tracing.trace_id("instruction", state["id"])
        metadata = {"instruction": state["id"], f"prompt.{INTERPRET_PROMPT}": prompt.label,
                    **generator.metadata(conf.engine)}
        with tracing.observe("interpret", trace, trace_name=f"instruction {state['id']}", metadata=metadata,
                             tags=["instruction"], prompt=prompt.client,
                             input={"sentence": state["text"], "exchanges": state.get("exchanges") or []}) as span:
            reading = read(models, prompt, known_, state["text"], _exchanges(state), today)
            found = resolve(reading, known_, sentence=_sentence(state), today=today, existing=existing)
            if span is not None:
                span.update(output={"question": found.question} if found.question else found.rule.model_dump(mode="json"))
        if found.question:
            if len(state.get("exchanges") or []) >= MAX_QUESTIONS:
                tracing.score(trace, "instruction.accepted", False, "pas comprise")
                return {"status": "failed", "reason": UNCLEAR, "question": found.question, "decision": None}
            return {"question": found.question, "proposal": None, "decision": None}
        rule = found.rule
        replaced = existing.get(rule.id)
        summary = "J'ai compris : " + describe(rule, found.teachers)
        if replaced is not None:
            summary += f" Elle remplace la règle actuelle : {describe(replaced)}"
        return {"question": "", "proposal": rule.model_dump(mode="json"), "teachers": found.teachers,
                "summary": summary[:800], "decision": None}

    def after_interpret(state: InstructionState) -> str:
        if state.get("status") == "failed":
            return END
        if state.get("decision"):
            return "save_rule"
        return "clarify" if state.get("question") else "propose"

    def clarify(state: InstructionState) -> dict:
        reply = interrupt({"kind": "question", "question": state["question"]})
        return {"exchanges": [{"question": state["question"], "answer": str(reply.get("text", "")).strip()}]}

    def propose(state: InstructionState) -> dict:
        reply = interrupt({"kind": "proposal", "summary": state["summary"], "proposal": state["proposal"]})
        if reply["kind"] == "answer":  # « non, c'est le prof d'anglais » : read again, with that
            return {"decision": None, "exchanges": [{"question": state["summary"], "answer": reply["text"].strip()}]}
        return {"decision": reply}

    def after_propose(state: InstructionState) -> str:
        decision = state.get("decision")
        if decision is None:
            return "interpret"
        return "discard" if decision["kind"] == "reject" else "save_rule"

    def save_rule(state: InstructionState) -> dict:
        decision = state["decision"]
        edited = decision["kind"] == "edit"
        chosen = decision["rule"] if edited else state["proposal"]
        with connect() as conn:  # back from the PWA, a teacher is « M. D. »: the timetable's name again
            chosen = privacy.restore(chosen, privacy.teachers(conn))
        today = date.fromisoformat(state["today"])
        written = _sentence(state) or (chosen.get("origin") or {}).get("text", "")
        trace = tracing.trace_id("instruction", state["id"])
        author = "agent" if state.get("agent_rule") else "parent"
        try:
            rule = Rule.model_validate({**chosen, "status": chosen.get("status", "active"),
                                        "origin": {"text": written[:600], "author": author, "on": today.isoformat()}})
        except ValidationError as error:
            return {"status": "failed", "reason": f"Règle invalide : {error.errors()[0]['msg']}"[:600]}
        with connect() as conn:
            saved = rules_store.save(conn, rule)
            if edited and state.get("proposal") and not state.get("form_rule") and not state.get("agent_rule"):
                store.correction(conn, state["id"], written, state["proposal"], saved.model_dump(mode="json"))
            if state.get("proposal_key"):
                from skoolathome.agent import proposals  # the decision on a proposal (T28)

                proposals.decided(conn, state["proposal_key"], "edited" if edited else "accepted",
                                  final=saved.model_dump(mode="json"))
        if not state.get("form_rule") and not state.get("agent_rule"):
            tracing.score(trace, "instruction.accepted", not edited, "modifiée par le parent" if edited else "validée")
            tracing.score(trace, "instruction.questions", float(len(state.get("exchanges") or [])))
        return {"status": "saved", "rule_id": saved.id, "rule_version": saved.version,
                "summary": ("Règle enregistrée : " + describe(saved, state.get("teachers")))[:800]}

    def discard(state: InstructionState) -> dict:
        reason = ((state.get("decision") or {}).get("text") or "").strip()
        if state.get("proposal_key"):
            from skoolathome.agent import proposals

            with connect() as conn:  # never proposed again
                proposals.decided(conn, state["proposal_key"], "rejected", reason=reason)
        else:
            tracing.score(tracing.trace_id("instruction", state["id"]), "instruction.accepted", False, "annulée")
        return {"status": "rejected", "reason": reason[:600]}

    graph = StateGraph(InstructionState)
    for name, function in [("interpret", interpret), ("clarify", clarify), ("propose", propose),
                           ("save_rule", save_rule), ("discard", discard)]:
        graph.add_node(name, function)
    graph.add_edge(START, "interpret")
    graph.add_conditional_edges("interpret", after_interpret, ["clarify", "propose", "save_rule", END])
    graph.add_edge("clarify", "interpret")
    graph.add_conditional_edges("propose", after_propose, ["interpret", "save_rule", "discard"])
    graph.add_edge("save_rule", END)
    graph.add_edge("discard", END)
    return graph


# --------------------------------------------------------------------------- running


@dataclass
class Step:
    """Where an instruction stands after its graph ran: paused on a question or a proposal, or finished."""

    status: str
    question: str = ""
    summary: str = ""
    proposal: dict | None = None
    rule_id: str | None = None
    rule_version: int | None = None
    reason: str = ""

    def save(self, conn: psycopg.Connection, instruction_id: str) -> None:
        store.set_state(conn, instruction_id, status=self.status, question=self.question, summary=self.summary,
                        proposal=self.proposal, rule_id=self.rule_id, rule_version=self.rule_version,
                        reason=self.reason, trace_id=tracing.trace_id("instruction", instruction_id))


class Runner:
    """Runs or resumes the graph of each instruction, with its checkpoints in Postgres."""

    def __init__(self, graph: Any) -> None:
        self.graph = graph

    @staticmethod
    def config(instruction_id: str) -> dict:
        return {"configurable": {"thread_id": f"instruction-{instruction_id}"}}

    def step(self, instruction_id: str) -> Step:
        snapshot = self.graph.get_state(self.config(instruction_id))
        if snapshot.interrupts:
            value = snapshot.interrupts[0].value
            if value["kind"] == "question":
                return Step("question", question=value["question"])
            return Step("proposed", summary=value["summary"], proposal=value["proposal"])
        values = snapshot.values or {}
        if snapshot.next or not values.get("status"):
            return Step("pending")  # stopped by an error: tried again at the next run
        return Step(values["status"], summary=values.get("summary", ""), proposal=values.get("proposal"),
                    question=values.get("question", ""), rule_id=values.get("rule_id"),
                    rule_version=values.get("rule_version"), reason=values.get("reason", ""))

    def start(self, instruction: dict, student_id: str | None, today: date) -> Step:
        config_ = self.config(instruction["id"])
        snapshot = self.graph.get_state(config_)
        if snapshot.next and not snapshot.interrupts:
            self.graph.invoke(None, config_)  # an earlier run stopped half-way
        elif not snapshot.values:
            self.graph.invoke({"id": instruction["id"], "text": instruction["text"], "form_rule": instruction["form_rule"],
                               "agent_rule": instruction.get("agent_rule"), "facts": instruction.get("facts") or [],
                               "proposal_key": instruction.get("proposal_key") or "",
                               "student_id": student_id, "today": today.isoformat(), "exchanges": []}, config_)
        return self.step(instruction["id"])

    def waiting(self, instruction_id: str) -> bool:
        return bool(self.graph.get_state(self.config(instruction_id)).interrupts)

    def resume(self, instruction_id: str, reply: dict) -> Step:
        self.graph.invoke(Command(resume={"kind": reply["kind"], "text": reply.get("text") or "",
                                          "rule": reply.get("rule")}), self.config(instruction_id))
        return self.step(instruction_id)


@contextmanager
def runner(database_url: str, models: Any, conf: config.Config | None = None) -> Iterator[Runner]:
    def connect() -> psycopg.Connection:
        return psycopg.connect(database_url, autocommit=True)

    with checkpointer(database_url) as saver:
        yield Runner(build(models, connect, conf).compile(checkpointer=saver))


def process(database_url: str, models: Any, student_id: str | None, today: date,
            conf: config.Config | None = None, progress: Callable[[str], None] | None = None,
            only_agent: bool = False) -> dict:
    """Every new instruction, and every reply of the parent: the graph runs until its next pause.
    An instruction that fails (model stopped) stays where it was, for the next run. `only_agent`:
    the agent's new proposals only, which need no model (`proposals run`)."""
    say = progress or (lambda message: None)
    done: list[dict] = []
    errors: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as conn, runner(database_url, models, conf) as run_:
        for instruction in store.new(conn):
            if only_agent and instruction["origin"] != "agent":
                continue
            say(f"Consigne {instruction['id']} : lecture…")
            try:
                step = run_.start(instruction, student_id, today)
            except (ModelError, ValidationError, psycopg.Error) as error:
                errors.append(f"{instruction['id']} : {str(error)[:200]}")
                continue
            step.save(conn, instruction["id"])
            done.append({"id": instruction["id"], "status": step.status})
        for instruction in [] if only_agent else store.with_replies(conn):
            while (reply := store.next_reply(conn, instruction["id"])) is not None:
                if not run_.waiting(instruction["id"]):
                    store.handled(conn, reply["id"])  # nothing to answer: a duplicate, or already finished
                    continue
                say(f"Consigne {instruction['id']} : réponse du parent ({reply['kind']})…")
                try:
                    step = run_.resume(instruction["id"], reply)
                except (ModelError, ValidationError, psycopg.Error) as error:
                    errors.append(f"{instruction['id']} : {str(error)[:200]}")
                    break
                step.save(conn, instruction["id"])
                store.handled(conn, reply["id"])
                done.append({"id": instruction["id"], "status": step.status})
    tracing.flush()
    final = {d["id"]: d["status"] for d in done}  # where each one stands now
    return {"handled": len(final), "saved": sum(1 for s in final.values() if s == "saved"),
            "waiting": sum(1 for s in final.values() if s in store.WAITING), "errors": errors, "steps": done}

"""One sheet from its sources: write it, check it, have it judged (ARCHITECTURE.md §9.1 and §9.5).

The evening graph runs these steps in its own order: every generation, then every judgment, so
each model is loaded once per round. The reference set (T23) runs them one sheet at a time with
`make`. Same prompts, same guards, same judge: what `make eval` measures is what the evening does.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from skoolathome.agent import draft, formats, guards, judge, tracing
from skoolathome.agent.llm import ModelError
from skoolathome.agent.prompts import Prompt
from skoolathome.schema.sheet import Sheet

MAX_GENERATIONS = 3  # the first one, then up to two regenerations, guards and judge together
MIN_SOURCE_CHARS = 120  # below that, no sheet: the model would invent, the guards refuse, three times


def chars(sources: dict[str, str]) -> int:
    return sum(len(text.strip()) for text in sources.values())


def format_reason(error: Exception) -> str:
    if isinstance(error, ValidationError):
        details = "; ".join(f"{'.'.join(str(p) for p in e['loc'])} : {e['msg']}" for e in error.errors()[:5])
        return f"fiche mal formée : {details}"
    return f"fiche mal formée : {error}"[:600]


@dataclass
class Written:
    sheet: Sheet | None
    failures: list[str]  # the format guard: why the output could not become a sheet
    items: int           # questions asked for


def write(models: Any, prompt: Prompt, task: dict, sources: dict[str, str], labels: dict[str, str], context: str,
          failures: list[str], trace_id: str) -> Written:
    """The generator writes the sheet in its format's imposed shape; the code makes it a Sheet."""
    shape = formats.FORMATS[task["kind"]]
    feedback = ""
    if failures:
        feedback = ("\nLa version précédente a été refusée par les vérifications. "
                    "Corrige ces points :\n" + "\n".join(f"- {r}" for r in failures))
    items = shape.item_count(task["target_minutes"], chars(sources), task.get("items"))
    messages = prompt.compile(
        subject=task["subject"],
        item_count=items,
        target_minutes=task["target_minutes"],
        sources="\n\n".join(f"[{key}]\n{text}" for key, text in sources.items()),
        feedback=feedback,
        context=context,
    )
    try:
        written = models.structured("generator", messages, shape.shape, tracing.callbacks())
        sheet = draft.to_sheet(written, task=task, sources=sources, labels=labels, trace_id=trace_id)
    except (ModelError, ValidationError, ValueError) as error:
        return Written(None, [format_reason(error)], items)
    return Written(sheet, [], items)


def check(sheet: Sheet, sources: dict[str, str], task: dict) -> list[guards.GuardResult]:
    return guards.run_all(sheet, sources, task["target_minutes"], task["computational"],
                          concepts=task.get("focus") or None)


@dataclass
class Judged:
    verdict: judge.Verdict | None  # None: the judge could not answer
    failures: list[str] = field(default_factory=list)
    error: str = ""


def judge_once(models: Any, prompt: Prompt, task: dict, sheet: Sheet, sources: dict[str, str]) -> Judged:
    messages = prompt.compile(subject=task["subject"], sheet=judge.render(sheet),
                              sources="\n\n".join(f"[{key}]\n{text}" for key, text in sources.items()))
    try:
        verdict = models.structured("judge", messages, judge.Verdict, tracing.callbacks())
    except (ModelError, ValidationError) as failure:
        return Judged(None, error=str(failure)[:300])
    return Judged(verdict, judge.reasons(verdict, sheet))


def make(models: Any, prompt_for: Callable[[str], Prompt], task: dict, sources: dict[str, str],
         labels: dict[str, str] | None = None, context: str = "") -> dict:
    """One sheet, start to end, one step after the other: what the evening does for a task. Returns
    {status: ready | abstained, reason, attempts, sheet, judge, guards}."""
    labels = labels or {}
    log: list[dict] = []
    if chars(sources) < MIN_SOURCE_CHARS:
        return {"status": "abstained", "reason": f"Trop peu de texte pour une fiche ({chars(sources)} caractères)",
                "attempts": 0, "sheet": None, "judge": None, "guards": log}
    failures: list[str] = []
    record: dict | None = None
    for attempt in range(1, MAX_GENERATIONS + 1):
        written = write(models, prompt_for(formats.FORMATS[task["kind"]].prompt), task, sources, labels, context,
                        failures, "")
        if written.sheet is None:
            failures = written.failures
            log.append({"attempt": attempt, "name": "format", "ok": False, "reasons": failures})
            continue
        results = check(written.sheet, sources, task)
        log += [{"attempt": attempt, **result.as_json()} for result in results]
        failures = [reason for result in results for reason in result.reasons]
        if failures:
            continue
        judged = judge_once(models, prompt_for(formats.JUDGE_PROMPT), task, written.sheet, sources)
        record = ({"available": False, "error": judged.error} if judged.verdict is None else
                  {"available": True, "ok": not judged.failures, "reasons": judged.failures,
                   "verdict": judged.verdict.model_dump()})
        log.append({"attempt": attempt, "name": "judge", "ok": not judged.failures, "reasons": judged.failures})
        failures = judged.failures
        if not failures:
            return {"status": "ready", "reason": "", "attempts": attempt, "sheet": written.sheet.model_dump(mode="json"),
                    "judge": record, "guards": log}
    return {"status": "abstained", "attempts": MAX_GENERATIONS, "sheet": None, "judge": record, "guards": log,
            "reason": f"Fiche non publiée après {MAX_GENERATIONS} essais. Dernier motif : " + "; ".join(failures)[:600]}

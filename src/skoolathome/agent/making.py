"""One sheet from its sources: write it, check it, have it judged (ARCHITECTURE.md §9.1 and §9.5).

The evening graph runs these steps in its own order: every generation, then every judgment, so
each model is loaded once per round. The reference set (T23) runs them one sheet at a time with
`make`. Same prompts, same guards, same judge: what `make eval` measures is what the evening does.
"""

from __future__ import annotations

import math
import re

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from skoolathome.agent import draft, formats, guards, judge, tracing
from skoolathome.agent.llm import ModelError
from skoolathome.agent.prompts import Prompt
from skoolathome.schema.sheet import Item, Sheet

MAX_GENERATIONS = 3  # the first one, then up to two regenerations, guards and judge together
MIN_SOURCE_CHARS = 120  # below that, no sheet: the model would invent, the guards refuse, three times


def chars(sources: dict[str, str]) -> int:
    return sum(len(text.strip()) for text in sources.values())


def format_reason(error: Exception) -> str:
    if isinstance(error, ValidationError):
        details = "; ".join(f"{'.'.join(str(p) for p in e['loc'])} : {e['msg']}" for e in error.errors()[:5])
        return f"fiche mal formée : {details}"
    return f"fiche mal formée : {error}"[:600]


PART_ITEMS = 6      # questions asked in one call: a long sheet is written in several parts
KEEP_SHARE = 0.7    # a sheet keeps going with 70 % of the questions asked, the others left out
MAX_LISTED = 30     # questions already written, listed so that the next part asks others
ITEM_REASON = re.compile(r"^(q\d+) : ")  # a check about one question, not the whole sheet


@dataclass
class Written:
    sheet: Sheet | None
    failures: list[str]  # the format guard: why the output could not become a sheet
    items: int           # questions asked for, in all
    dropped: list[str] = field(default_factory=list)  # questions left out as badly built, parts lost


def _shares(total: int, weights: list[int]) -> list[int]:
    """`total` split along the weights, each part at least one (largest remainder)."""
    weights = [max(1, w) for w in weights]
    exact = [total * w / sum(weights) for w in weights]
    shares = [max(1, int(x)) for x in exact]
    for index in sorted(range(len(exact)), key=lambda i: exact[i] - int(exact[i]), reverse=True):
        if sum(shares) >= total:
            break
        shares[index] += 1
    while sum(shares) > total and max(shares) > 1:
        shares[shares.index(max(shares))] -= 1
    return shares


def parts(wanted: int, sources: dict[str, str]) -> list[tuple[int, dict[str, str]]]:
    """A long sheet in parts of about six questions, one call each: a small model writes six
    good questions more surely than eighteen, and one part lost is not the whole sheet. With
    several lessons, each part has its own, so that the questions cover them all."""
    if wanted <= 0:
        return []
    count = math.ceil(wanted / PART_ITEMS)
    keys = list(sources)
    if count == 1:
        return [(wanted, sources)]
    if len(keys) >= count:
        groups = [keys[index::count] for index in range(count)]
    else:
        groups = [keys] * count
    chosen = [{key: sources[key] for key in group} for group in groups]
    return list(zip(_shares(wanted, [chars(group) for group in chosen]), chosen))


def _feedback(failures: list[str], completing: bool) -> str:
    if not failures:
        return ""
    if completing:
        # The questions refused are gone: their numbers mean nothing to the next call.
        lessons = list(dict.fromkeys(ITEM_REASON.sub("", reason) for reason in failures))
        return ("\nDes questions ont été refusées par les vérifications, évite ces erreurs :\n"
                + "\n".join(f"- {r}" for r in lessons[:12]))
    return ("\nLa version précédente a été refusée par les vérifications. "
            "Corrige ces points :\n" + "\n".join(f"- {r}" for r in failures))


def _part_note(count: int, index: int, total: int, written: list[Item]) -> str:
    lines = []
    if total > 1:
        lines.append(f"Cette fiche est écrite en {total} parties. Partie {index} : {count} questions, "
                     "sur les cours donnés dans <cours>. Va du plus simple au plus difficile : questions "
                     "de cours, puis exercices, et au moins une question à rédiger (type `open`).")
    if written:
        lines.append("Questions déjà dans la fiche : n'en répète aucune, pose d'autres questions.\n"
                     + "\n".join(f"- {item.prompt[:120]}" for item in written[-MAX_LISTED:]))
    return ("\n" + "\n".join(lines)) if lines else ""


def _sources_text(sources: dict[str, str]) -> str:
    return "\n\n".join(f"[{key}]\n{text}" for key, text in sources.items())


def write(models: Any, prompt: Prompt, task: dict, sources: dict[str, str], labels: dict[str, str], context: str,
          failures: list[str], trace_id: str, kept: dict | None = None) -> Written:
    """The generator writes the sheet in its format's imposed shape; the code makes it a Sheet.

    Exercises and test revisions: the questions badly built are left out one by one, a long
    sheet is written in parts, and `kept` (the questions that passed an earlier attempt) is
    completed instead of thrown away."""
    shape = formats.FORMATS[task["kind"]]
    asked = shape.item_count(task["target_minutes"], chars(sources), task.get("items"))
    if not shape.scales:
        messages = prompt.compile(subject=task["subject"], item_count=asked, target_minutes=task["target_minutes"],
                                  sources=_sources_text(sources), feedback=_feedback(failures, False),
                                  context=context)
        try:
            written = models.structured("generator", messages, shape.shape, tracing.callbacks())
            sheet = draft.to_sheet(written, task=task, sources=sources, labels=labels, trace_id=trace_id)
        except (ModelError, ValidationError, ValueError) as error:
            return Written(None, [format_reason(error)], asked)
        return Written(sheet, [], asked)

    previous = [Item.model_validate(item) for item in (kept or {}).get("items", [])]
    title = (kept or {}).get("title", "")
    feedback = _feedback(failures, completing=bool(previous))
    new: list[Item] = []
    dropped: list[str] = []
    planned = parts(asked - len(previous), sources)
    for index, (count, part) in enumerate(planned, start=1):
        messages = prompt.compile(
            subject=task["subject"],
            item_count=count,
            target_minutes=max(1, round(task["target_minutes"] * count / asked)),
            sources=_sources_text(part),
            feedback=feedback + _part_note(count, index, len(planned), previous + new),
            context=context,
        )
        try:
            written = models.structured("generator", messages, shape.shape, tracing.callbacks())
        except (ModelError, ValidationError, ValueError) as error:
            dropped.append(format_reason(error))
            continue
        items, reasons = draft.to_items(written.items, sources)
        new += items
        dropped += reasons
        title = title or written.title
    if previous:
        new = new[:max(0, asked - len(previous))]  # completing: no more than the questions missing
    items = draft.numbered(previous + new)
    if not items:
        return Written(None, dropped or ["fiche mal formée : aucune question"], asked)
    try:
        sheet = draft.to_sheet(None, task=task, sources=sources, labels=labels, trace_id=trace_id, items=items,
                               title=title or f"Révisions de {task['subject']}")
    except (ValidationError, ValueError) as error:
        return Written(None, [format_reason(error)], asked)
    return Written(sheet, [], asked, dropped)


@dataclass
class Settled:
    sheet: Sheet | None          # accepted, maybe with fewer questions; None: try again or abstain
    failures: list[str]          # fed back to the model
    kept: dict | None = None     # the questions that passed, for the next attempt to complete
    dropped: list[str] = field(default_factory=list)  # ids left out


def enough(asked: int, written: int, *, long: bool, last: bool) -> int:
    """Questions a sheet needs to be kept, once the refused ones are left out: 70 % of those
    asked for a long sheet (its length is its point), of those written otherwise (a short lesson
    gives a short sheet, as before). At the last attempt, three are enough: a shorter sheet
    rather than none."""
    share = max(1, math.ceil((asked if long else written) * KEEP_SHARE))
    return min(formats.MIN_ITEMS, share) if last else share


def settle(sheet: Sheet, results: list[guards.GuardResult], task: dict, asked: int, *, last: bool,
           notes: list[str] | tuple[str, ...] = ()) -> Settled:
    """What the checks (guards or judge) mean for the sheet. A quiz or a « cours en clair » has a
    fixed shape: any problem sends it back. Exercises and revisions leave the refused questions
    out, and go on with the others when enough remain; else the next attempt completes them."""
    reasons = [reason for result in results for reason in result.reasons]
    if not formats.FORMATS[task["kind"]].scales:
        return Settled(None, reasons) if reasons else Settled(sheet, [])
    bad = {match.group(1) for reason in reasons if (match := ITEM_REASON.match(reason))}
    # Duration and missed concepts are measured again on what remains.
    general = [reason for result in results if result.name not in ("duration", "focus")
               for reason in result.reasons if not ITEM_REASON.match(reason)]
    items = [item for item in sheet.items if item.id not in bad]
    shorter = draft.with_items(sheet, items) if items and bad else sheet
    too_long = False
    if items:
        again = [guards.duration(shorter, task["target_minutes"])]
        if task.get("focus"):
            again.append(guards.focus(shorter, task["focus"]))
        too_long = not again[0].ok
        general += [reason for result in again for reason in result.reasons]
    need = enough(asked, len(sheet.items), long=task["target_minutes"] >= draft.LONG_MINUTES, last=last)
    if items and not general and len(items) >= need:
        return Settled(shorter, [], dropped=sorted(bad, key=lambda i: int(i[1:])))
    failures = [r for r in reasons if ITEM_REASON.match(r)] + general + list(notes)
    if len(items) < need:
        failures.append(f"il manque des questions : {len(items)} retenues sur {asked}, "
                        f"écris-en {asked - len(items)} autres.")
    kept = None if too_long or not items else {"title": sheet.title,
                                               "items": [item.model_dump(mode="json") for item in items]}
    return Settled(None, failures, kept, sorted(bad, key=lambda i: int(i[1:])))


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
    kept: dict | None = None
    record: dict | None = None
    for attempt in range(1, MAX_GENERATIONS + 1):
        last = attempt == MAX_GENERATIONS
        written = write(models, prompt_for(formats.FORMATS[task["kind"]].prompt), task, sources, labels, context,
                        failures, "", kept)
        if written.sheet is None:
            failures, kept = written.failures, None
            log.append({"attempt": attempt, "name": "format", "ok": False, "reasons": failures})
            continue
        results = check(written.sheet, sources, task)
        log += [{"attempt": attempt, **result.as_json()} for result in results]
        settled = settle(written.sheet, results, task, written.items, last=last, notes=written.dropped)
        if settled.sheet is None:
            failures, kept = settled.failures, settled.kept
            continue
        judged = judge_once(models, prompt_for(formats.JUDGE_PROMPT), task, settled.sheet, sources)
        record = ({"available": False, "error": judged.error} if judged.verdict is None else
                  {"available": True, "ok": not judged.failures, "reasons": judged.failures,
                   "verdict": judged.verdict.model_dump()})
        log.append({"attempt": attempt, "name": "judge", "ok": not judged.failures, "reasons": judged.failures})
        final = settle(settled.sheet, [guards.GuardResult("judge", not judged.failures, judged.failures)], task,
                       written.items, last=last)
        if final.sheet is not None:
            return {"status": "ready", "reason": "", "attempts": attempt, "sheet": final.sheet.model_dump(mode="json"),
                    "judge": record, "guards": log}
        failures, kept = final.failures, final.kept
    return {"status": "abstained", "attempts": MAX_GENERATIONS, "sheet": None, "judge": record, "guards": log,
            "reason": f"Fiche non publiée après {MAX_GENERATIONS} essais. Dernier motif : " + "; ".join(failures)[:600]}


MODEL_TROUBLE = ("le serveur du modèle", "le moteur du modèle", "le modèle s'est mis", "pas de réponse du modèle")


def for_the_parent(reason: str) -> str:
    """The reason the parent reads in « fiche demandée impossible »: what happened, in plain words.
    The validation errors (in English, from the format checks) stay on the laptop: agent.sheets
    and Langfuse keep the whole reason."""
    head, found, last = reason.partition("Dernier motif : ")
    if not found:
        return reason
    tries = re.search(r"après (\d+) essais", head)
    count = tries.group(1) if tries else "plusieurs"
    first = last.split("; ")[0]
    if first.startswith("fiche mal formée"):
        detail = first.removeprefix("fiche mal formée").lstrip(" :")
        if detail.startswith(MODEL_TROUBLE):
            return f"Fiche non faite : {detail[:200]}"
        return f"Fiche non faite : en {count} essais, le modèle n'a pas réussi à écrire des questions bien construites"
    return f"Fiche non faite : en {count} essais, aucune version n'a passé les contrôles (le dernier : {first[:160]})"

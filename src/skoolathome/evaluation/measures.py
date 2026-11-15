"""What `make eval` measures on each case, and the thresholds (ARCHITECTURE.md §14.2, T23).

Blocking, at 100 %: a candidate below one of them is not promoted.
    answers_correct  corrigés justes: the judge finds every answer right, and a reported wrong
                     answer does not come back
    grounded         ancrage: every question rests on its quote, no question on a page out of the
                     subject, a question reported « pas vu en cours » does not come back
    traps            abstention correcte: the traps (empty lesson, instructions only, page out of the
                     subject) give no sheet, or no question on the trap
    test_recall      contrôles détectés: every homework announcing a test is detected
Tracked, not blocking: sheets produced on real cases, reports fixed, false alarms, generations.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from rapidfuzz import fuzz

from skoolathome.agent import formats, guards, making, tracing
from skoolathome.agent.draft import AssessmentVerdict
from skoolathome.agent.llm import ModelError
from skoolathome.agent.prompts import Prompt
from skoolathome.rules import assessments
from skoolathome.rules.inputs import MODEL_CONFIDENCE
from skoolathome.subjects import fr_day

THRESHOLDS = {"answers_correct": 1.0, "grounded": 1.0, "traps": 1.0, "test_recall": 1.0}
NAMES = {
    "answers_correct": "Corrigés justes",
    "grounded": "Ancrage dans le cours",
    "traps": "Abstention correcte (pièges)",
    "test_recall": "Contrôles détectés (rappel)",
    "produced": "Fiches produites (cas réels)",
    "reports_fixed": "Signalements corrigés",
    "false_alarms": "Fausses alertes de contrôle",
    "attempts": "Générations par fiche",
}
SIMILAR = 85  # a question this close to the reported one is the same question


# ------------------------------------------------------------------------------- sheets


def make_sheet(models: Any, prompt_for: Callable[[str], Prompt], case_input: dict) -> dict:
    return making.make(models, prompt_for, case_input["task"], case_input["sources"], case_input.get("labels") or {},
                       case_input.get("context", ""))


def _answer(item: dict) -> str:
    answer = item.get("answer", "")
    return guards.flat(" ".join(answer) if isinstance(answer, list) else str(answer))


def _same_question(sheet: dict, reported: dict) -> list[dict]:
    wanted = guards.flat(reported.get("prompt", ""))
    return [item for item in sheet["items"] if wanted and fuzz.token_set_ratio(wanted, guards.flat(item["prompt"])) >= SIMILAR]


def measure_sheet(output: dict, expected: dict, origin: str) -> dict[str, float | None]:
    """1 good, 0 bad, None not measured (an abstention says nothing of the answers)."""
    ready = output["status"] == "ready"
    sheet = output.get("sheet") or {}
    found: dict[str, float | None] = {}
    if origin == "trap":
        cited = {(item.get("quote") or {}).get("source_id") for item in sheet.get("items", [])}
        avoided = not ready or not cited & set(expected.get("must_not_cite", []))
        found["traps"] = float(avoided if not expected.get("abstain") else not ready)
        if not ready or expected.get("abstain"):
            return found
    else:
        found["produced"] = float(ready)
    if not ready:
        return found
    found["attempts"] = float(output["attempts"])
    judged = (output.get("judge") or {}).get("verdict")
    ids = {item["id"] for item in sheet["items"]}
    verdicts = [v for v in (judged or {}).get("items", []) if v["id"] in ids]
    correct = None if judged is None else all(v["answer_correct"] for v in verdicts)
    faithful = None if judged is None else all(v["faithful"] for v in verdicts)
    cited = {(item.get("quote") or {}).get("source_id") for item in sheet["items"]}
    if cited & set(expected.get("must_not_cite", [])):
        faithful = False
    report = expected.get("report")
    if report:
        again = _same_question(sheet, report)
        if report["reason"] == "wrong_answer":
            wrong = guards.flat(str(report.get("answer", "")))
            right = guards.flat(report.get("correct_answer", ""))
            if any(_answer(item) == wrong or (right and right not in _answer(item)) for item in again):
                correct = False
        elif report["reason"] == "not_covered" and again:
            faithful = False
        found["reports_fixed"] = float(not any(fuzz.ratio(guards.flat(i["prompt"]), guards.flat(report["prompt"])) >= 95
                                               and _answer(i) == guards.flat(str(report.get("answer", "")))
                                               for i in again))
    found["answers_correct"] = None if correct is None else float(correct)
    found["grounded"] = None if faithful is None else float(faithful)
    return found


# ---------------------------------------------------------------------- test detection


def detect_test(models: Any, prompt: Prompt, case_input: dict) -> dict:
    """What the evening does with a homework (§8): the flag, the words, then the model for an
    ambiguous one."""
    text = case_input["text"]
    if case_input.get("flagged"):
        return {"detected": True, "by": "flag"}
    if assessments.has_keywords(text):
        return {"detected": True, "by": "keywords"}
    if not assessments.is_ambiguous(text):
        return {"detected": False, "by": "rules"}
    due_on = date.fromisoformat(case_input["due_on"])
    given_on = date.fromisoformat(case_input["given_on"]) if case_input.get("given_on") else None
    today = given_on or due_on - timedelta(days=3)
    messages = prompt.compile(subject=case_input["subject"], today=fr_day(today), due_on=f"{fr_day(due_on)} {due_on.year}",
                              given_on=fr_day(given_on) if given_on else "inconnu", text=text[:1500])
    try:
        verdict = models.structured("generator", messages, AssessmentVerdict, tracing.callbacks())
    except ModelError as error:
        return {"detected": False, "by": "model", "error": str(error)[:200]}
    return {"detected": verdict.is_assessment and verdict.confidence >= MODEL_CONFIDENCE, "by": "model",
            "confidence": verdict.confidence}


def measure_test(output: dict, expected: dict) -> dict[str, float | None]:
    if expected.get("is_assessment"):
        return {"test_recall": float(output["detected"])}
    return {"false_alarms": float(output["detected"])}


# ------------------------------------------------------------------------- the run


def aggregate(per_case: list[dict[str, float | None]]) -> dict[str, dict]:
    """Per measure: the mean over the cases it applies to, and how many."""
    totals: dict[str, list[float]] = {}
    for measures in per_case:
        for name, value in measures.items():
            if value is not None:
                totals.setdefault(name, []).append(value)
    return {name: {"value": round(sum(values) / len(values), 4), "cases": len(values)} for name, values in totals.items()}


def failed_thresholds(metrics: dict[str, dict]) -> list[str]:
    return [name for name, minimum in THRESHOLDS.items() if name in metrics and metrics[name]["value"] < minimum]


def prompts_of(family: str) -> list[str]:
    if family == "tests":
        return [formats.ASSESSMENT_PROMPT]
    return sorted({shape.prompt for shape in formats.FORMATS.values()} | {formats.JUDGE_PROMPT})

"""The test sentences of the graph of instructions (T21): each one read by the model, resolved by
the code, and compared with the rule expected, field by field (ARCHITECTURE.md §14.2).

A case: {"sentence": "…", "expected": {"subject": "HI-GE", "trigger": "before_each_lesson", …}}.
Only the fields written in `expected` are compared. {"question": true}: the agent must ask
instead of guessing. "offsets_contains": 7 checks one day of a revision before a test.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import psycopg

from skoolathome.agent import prompts
from skoolathome.agent.instructions import INTERPRET_PROMPT, Resolution, known, read, resolve
from skoolathome.agent.llm import ModelError
from skoolathome.rules import store as rules_store


def differences(expected: dict, found: Resolution) -> list[str]:
    if expected.get("question"):
        return [] if found.question else [f"une question était attendue, pas la règle {found.rule.id}"]
    if found.question:
        return [f"question au lieu d'une règle : {found.question}"]
    rule = found.rule.model_dump(mode="json")
    problems = []
    for key, wanted in expected.items():
        if key == "offsets_contains":
            if wanted not in rule["offsets_days"]:
                problems.append(f"offsets_days : {rule['offsets_days']}, sans {wanted}")
        elif rule.get(key) != wanted:
            problems.append(f"{key} : {rule.get(key)!r} au lieu de {wanted!r}")
    return problems


def run(conn: psycopg.Connection, cases: list[dict], student_id: str | None, today: date, models: Any = None,
        progress: Callable[[str], None] | None = None) -> list[dict]:
    if models is None:
        from skoolathome.agent.llm import Models

        models = Models()
    say = progress or (lambda message: None)
    known_ = known(conn, student_id)
    existing = {rule.id: rule for rule in rules_store.latest(conn)}
    prompt = prompts.get(INTERPRET_PROMPT)
    results = []
    for case in cases:
        try:
            found = resolve(read(models, prompt, known_, case["sentence"], "", today), known_,
                            sentence=case["sentence"], today=today, existing=existing)
            problems = differences(case["expected"], found)
        except ModelError as error:
            found, problems = Resolution(question=""), [f"le modèle n'a pas répondu : {str(error)[:150]}"]
        results.append({"sentence": case["sentence"], "ok": not problems, "problems": problems,
                        "rule": found.rule.model_dump(mode="json") if found.rule else None, "question": found.question})
        say(f"{'✓' if not problems else '✗'} {case['sentence'][:80]}"
            + "".join(f"\n    {p}" for p in problems))
    return results


# ------------------------------------------------------------------- in make eval (T31)

COMPARED = ("subject", "trigger", "scope", "format", "n_lessons", "max_minutes", "items", "offsets_days",
            "style", "suggested_mode", "target_minutes", "replaces_default")


def interpret(models: Any, prompt: prompts.Prompt, known_: Any, existing: dict, sentence: str, today: date) -> dict:
    """One sentence, read by the model and resolved by the code: {rule} or {question}, or {error}."""
    try:
        found = resolve(read(models, prompt, known_, sentence, "", today), known_, sentence=sentence, today=today,
                        existing=existing)
    except ModelError as error:
        return {"rule": None, "question": "", "error": f"le modèle n'a pas répondu : {str(error)[:150]}"}
    return {"rule": found.rule.model_dump(mode="json") if found.rule else None, "question": found.question}


def problems(expected: dict, output: dict) -> list[str]:
    """What differs, field by field: only the fields the case gives."""
    if output.get("error"):
        return [output["error"]]
    if expected.get("question"):
        return [] if output.get("question") else [f"une question était attendue, pas la règle {output['rule']['id']}"]
    if output.get("rule") is None:
        return [f"question au lieu d'une règle : {output.get('question', '')}"]
    rule, found = output["rule"], []
    for key, wanted in expected.items():
        if key == "offsets_contains":
            if wanted not in rule["offsets_days"]:
                found.append(f"offsets_days : {rule['offsets_days']}, sans {wanted}")
        elif rule.get(key) != wanted:
            found.append(f"{key} : {rule.get(key)!r} au lieu de {wanted!r}")
    return found


def expected_from(proposed: dict, final: dict) -> dict:
    """A [Modifier]: the rule the parent saved, on the fields that say what the rule does, plus
    every field the parent changed."""
    keys = {"subject", "trigger", "scope", "format"} | {k for k in COMPARED if proposed.get(k) != final.get(k)}
    if final.get("scope") == "last_n_lessons":
        keys.add("n_lessons")
    if final.get("trigger") == "load_limit":
        keys.add("max_minutes")
    return {k: final.get(k) for k in COMPARED if k in keys}

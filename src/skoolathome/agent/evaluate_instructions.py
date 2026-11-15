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

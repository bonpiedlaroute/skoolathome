"""The judge (ARCHITECTURE.md §14.1, layer 2): a model of another family checks what the code
cannot — is the question faithful to its excerpt, is the answer right, is it 5e level?

The verdict comes in an imposed format. The code decides: a criterion marked false, with its
problem written down, sends the sheet back to the generator with that problem, like a guard.
"""

from __future__ import annotations

from pydantic import Field

from skoolathome.agent.draft import Draft
from skoolathome.schema.sheet import Item, Sheet


class ItemVerdict(Draft):
    id: str
    faithful: bool = Field(description="the question and its answer rest on the quoted passage")
    answer_correct: bool = Field(description="the expected answer and the correction are right")
    level_ok: bool = Field(description="clear wording, 5e level")
    useful: bool = Field(description="it checks something to know or to do from the lesson, not how the class "
                                     "is organised (duration, list of skills, instructions, material)")
    problem: str = Field(description="when one of the four is false: the problem, in one sentence; else empty")


class Verdict(Draft):
    items: list[ItemVerdict]
    recap_faithful: bool = Field(description="the summary, the example and the points say what the lesson says; true without a recap")
    recap_problem: str = ""


CRITERIA = ("faithful", "answer_correct", "level_ok", "useful")


def _answer(item: Item) -> str:
    return ", ".join(item.answer) if isinstance(item.answer, list) else item.answer


def render(sheet: Sheet) -> str:
    """The sheet as the judge reads it: compact, without what it does not judge."""
    lines = [f"Titre : {sheet.title}"]
    if sheet.recap is not None:
        recap = sheet.recap
        lines += [f"Résumé : {recap.summary}", f"Exemple : {recap.example.statement}",
                  f"Solution de l'exemple : {recap.example.solution}",
                  "À retenir : " + " / ".join(recap.key_points)]
    for item in sheet.items:
        lines.append(f"\n{item.id} ({item.type}) : {item.prompt}")
        if item.quote:
            lines.append(f"  citation : « {item.quote.excerpt} »")
        if item.choices:
            lines.append("  propositions : " + " | ".join(item.choices))
        if item.type != "open" or item.answer:
            lines.append(f"  réponse attendue : {_answer(item)}")
        if item.expression:
            lines.append(f"  calcul : {item.expression}")
        if item.solution:
            lines.append(f"  correction : {item.solution}")
    return "\n".join(lines)


def reasons(verdict: Verdict, sheet: Sheet) -> list[str]:
    """Problems the generator must fix. A criterion marked false without a stated problem is
    ignored: a small judge that cannot say what is wrong does not block a sheet."""
    ids = {item.id for item in sheet.items}
    found = []
    for item in verdict.items:
        failed = [name for name in CRITERIA if not getattr(item, name)]
        if failed and item.problem.strip() and item.id in ids:
            found.append(f"{item.id} : (correcteur) {item.problem.strip()[:300]}")
    if sheet.recap is not None and not verdict.recap_faithful and verdict.recap_problem.strip():
        found.append(f"cours en clair : (correcteur) {verdict.recap_problem.strip()[:300]}")
    return found


def rates(verdict: Verdict, sheet: Sheet) -> dict[str, float]:
    """Share of questions passing each criterion: one score per criterion in Langfuse."""
    ids = {item.id for item in sheet.items}
    judged = [item for item in verdict.items if item.id in ids]
    if not judged:
        return {}
    return {name: sum(1 for item in judged if getattr(item, name)) / len(judged) for name in CRITERIA}

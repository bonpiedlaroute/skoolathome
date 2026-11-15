"""An instruction from the parent, in plain French, and what the agent made of it (ARCHITECTURE.md §7.4).

Written in the PWA (stored online, picked up by the laptop's next run) or on the laptop
(`skoolathome instructions add`). The agent proposes a rule, and saves it only once the parent
validated it. The rule form « fait foi »: a rule written in the form is saved as it is, without
the model. Shared with the API.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from skoolathome.schema.rule import Rule

InstructionStatus = Literal["pending", "question", "proposed", "saved", "rejected", "failed"]
ReplyKind = Literal["answer", "approve", "edit", "reject"]
# What the parent can send back, depending on where the instruction stands.
ALLOWED_REPLIES: dict[str, set[str]] = {
    "question": {"answer"},
    "proposed": {"approve", "edit", "reject", "answer"},  # answer: a correction in their own words
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_rule(rule: dict) -> None:
    try:
        Rule.model_validate(rule)
    except ValidationError as error:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'règle'} : {e['msg']}" for e in error.errors()[:3])
        raise ValueError(f"règle invalide : {problems}") from None


class NewInstruction(Strict):
    text: str = Field(default="", max_length=600)  # « Le prof d'histoire interroge à chaque cours… »
    rule: dict | None = None                        # the rule form: saved as it is, no model

    @model_validator(mode="after")
    def _one_of_them(self) -> NewInstruction:
        if bool(self.text.strip()) == (self.rule is not None):
            raise ValueError("a sentence or a rule, one of them")
        if self.rule is not None:
            _check_rule(self.rule)
        return self


class InstructionReply(Strict):
    kind: ReplyKind
    text: str = Field(default="", max_length=600)  # answer: to the question, or a correction
    rule: dict | None = None                        # edit: the rule as the parent changed it

    @model_validator(mode="after")
    def _is_complete(self) -> InstructionReply:
        if self.kind == "answer" and not self.text.strip():
            raise ValueError("an answer needs a text")
        if self.kind == "edit":
            if self.rule is None:
                raise ValueError("an edit needs the rule")
            _check_rule(self.rule)
        return self


class InstructionUpdate(Strict):
    """Sent back with the laptop's batch: where an instruction made online stands."""

    id: str = Field(min_length=1, max_length=64)
    status: Literal["question", "proposed", "saved", "rejected", "failed"]
    origin: Literal["parent", "agent"] = "parent"  # agent: a rule it proposes (T28), created by this update
    text: str = Field(default="", max_length=600)    # agent: what it saw, in one sentence
    facts: list[str] = Field(default=[], max_length=10)
    question: str = Field(default="", max_length=600)
    summary: str = Field(default="", max_length=800)  # « J'ai compris : … »
    proposal: dict | None = None                       # the rule proposed, for the form
    rule_id: str | None = None
    rule_version: int | None = None
    reason: str = Field(default="", max_length=600)


class PublishedRule(Strict):
    """A rule as the parent view shows it: the laptop sends every rule with its batch."""

    id: str
    version: int
    status: str
    summary: str = Field(max_length=800)
    body: dict

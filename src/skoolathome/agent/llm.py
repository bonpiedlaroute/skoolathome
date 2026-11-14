"""Calls to the local model, through the OpenAI-compatible API (Ollama today)."""

from __future__ import annotations

from typing import TypeVar

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from skoolathome.agent import config

Schema = TypeVar("Schema", bound=BaseModel)


class ModelError(Exception):
    """The model did not answer, or not in the imposed format."""


def chat_model(role: str, conf: config.Config | None = None) -> ChatOpenAI:
    conf = conf or config.load()
    model = conf.model(role)
    options: dict = {}
    if model.reasoning_effort:
        options["reasoning_effort"] = model.reasoning_effort
    return ChatOpenAI(
        model=model.model,
        base_url=conf.engine.base_url,
        api_key="local",  # required by the client, ignored by Ollama
        temperature=model.temperature,
        max_tokens=model.max_tokens,
        timeout=conf.engine.timeout_seconds,
        max_retries=0,  # a failure is handled by the graph (regeneration), not retried blindly
        **options,
    )


class Models:
    """What the graph asks of the model: free text, or an object in an imposed format."""

    def __init__(self, conf: config.Config | None = None) -> None:
        self.conf = conf or config.load()

    def text(self, role: str, messages: list[dict], callbacks: list) -> str:
        from openai import APIConnectionError

        try:
            reply = chat_model(role, self.conf).invoke(messages, config={"callbacks": callbacks})
        except APIConnectionError as error:  # includes the timeout
            raise ModelError(f"pas de réponse du modèle : {error}") from error
        return str(reply.content).strip()

    def structured(self, role: str, messages: list[dict], schema: type[Schema], callbacks: list) -> Schema:
        """The JSON schema of `schema` goes to Ollama as its `format`: the output can only be valid JSON
        of that shape. Pydantic then checks the rest."""
        from openai import APIConnectionError, LengthFinishReasonError

        model = chat_model(role, self.conf).with_structured_output(schema, method="json_schema", include_raw=True)
        try:
            result = model.invoke(messages, config={"callbacks": callbacks})
        except LengthFinishReasonError as error:
            raise ModelError("sortie coupée : trop longue pour max_tokens") from error
        except APIConnectionError as error:  # includes the timeout: the graph regenerates, then abstains
            raise ModelError(f"pas de réponse du modèle : {error}") from error
        if result.get("parsed") is None:
            raw = result.get("raw")
            content = raw.content if isinstance(raw, AIMessage) else raw
            raise ModelError(f"sortie hors format : {result.get('parsing_error')} — {str(content)[:300]}")
        return result["parsed"]

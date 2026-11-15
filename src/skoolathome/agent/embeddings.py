"""Vectors from the local embedding model, through the OpenAI-compatible API (T18)."""

from __future__ import annotations

from typing import Protocol

import httpx

from skoolathome.agent import config

BATCH = 16


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingError(Exception):
    pass


class LocalEmbedder:
    """The `embedder` of models.toml (bge-m3 in Ollama)."""

    def __init__(self, conf: config.Config | None = None) -> None:
        conf = conf or config.load()
        model = conf.model("embedder")
        self.model = model.model
        self.dimensions = model.dimensions
        self.http = httpx.Client(base_url=conf.engine.base_url, timeout=conf.engine.timeout_seconds)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            try:
                response = self.http.post("/embeddings", json={"model": self.model, "input": texts[start:start + BATCH]})
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise EmbeddingError(f"modèle d'embedding {self.model} : {error}") from error
            data = sorted(response.json()["data"], key=lambda d: d["index"])
            vectors += [d["embedding"] for d in data]
        if self.dimensions and any(len(v) != self.dimensions for v in vectors):
            raise EmbeddingError(f"{self.model} ne donne pas des vecteurs de {self.dimensions} dimensions")
        return vectors

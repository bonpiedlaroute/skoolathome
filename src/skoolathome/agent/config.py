"""Engine and model configuration, read from models.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).parent / "models.toml"


@dataclass(frozen=True)
class Engine:
    name: str
    base_url: str
    native_url: str
    timeout_seconds: int


@dataclass(frozen=True)
class Model:
    role: str
    model: str
    family: str
    quantization: str
    temperature: float = 0.0
    max_tokens: int = 0
    reasoning_effort: str | None = None
    dimensions: int | None = None  # embedding models only
    base_url: str | None = None    # its own server (llama.cpp: one model per server); else the engine's
    engine: str | None = None      # its own engine's name, e.g. the embedder left on Ollama (T30)

    def url(self, engine: Engine) -> str:
        return self.base_url or engine.base_url

    def engine_name(self, engine: Engine) -> str:
        return self.engine or engine.name

    def metadata(self, engine: Engine) -> dict[str, str]:
        """What every trace carries (ARCHITECTURE.md §14.5): filter the scores by model or engine."""
        return {"model": self.model, "family": self.family, "quantization": self.quantization,
                "engine": self.engine_name(engine)}


@dataclass(frozen=True)
class Config:
    engine: Engine
    models: dict[str, Model]

    def model(self, role: str) -> Model:
        return self.models[role]


def load(path: Path | None = None) -> Config:
    path = path or Path(os.environ.get("SKOOLATHOME_MODELS", DEFAULT_PATH))
    data = tomllib.loads(path.read_text())
    engine = Engine(**data.pop("engine"))
    return Config(engine=engine, models={role: Model(role=role, **values) for role, values in data.items()})

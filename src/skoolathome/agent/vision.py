"""Read a photo of the notebook: the course as written in class, a printed handout completed by
hand, which École Directe does not have. The text becomes the first source of the sheet asked for,
once the parent has checked it.

Ollama's own API, not the OpenAI-compatible one: only it takes `num_gpu`. The image is read on the
processor (models.toml, [vision]): on this laptop's graphics card, encoding an image crashes the
driver and leaves Ollama unable to answer until the model is unloaded.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass

import httpx

from skoolathome.agent import config
from skoolathome.agent.llm import ModelError

ROLE = "vision"
TIMEOUT_SECONDS = 900  # about 3 minutes measured for a photo; much more means a stuck engine

PROMPT = (
    "Voici la photo d'une page de cours d'un élève de 5e : un polycopié imprimé ou une page de cahier, "
    "souvent complété à la main par l'élève. Recopie mot pour mot tout le texte de la page, imprimé et "
    "manuscrit, dans l'ordre de lecture. Garde les titres et la numérotation. Écris le texte manuscrit à sa "
    "place, sans les pointillés. Ne décris pas les figures, n'ajoute rien, ne corrige rien, ne résume rien. "
    "Si un mot est illisible, écris [illisible]. Si la photo ne montre pas de cours, écris seulement : "
    "PAS DE COURS."
)
NOT_A_COURSE = "PAS DE COURS"


@dataclass(frozen=True)
class Read:
    text: str
    problem: str  # why the photo gives no text, for the parent; "" when read
    seconds: float


def _model(conf: config.Config) -> tuple[config.Model, str]:
    """The [vision] model, and Ollama's own address. An engine file without it (llama.cpp,
    OpenVINO: T30) borrows the one of the default models.toml, still served by Ollama."""
    if ROLE in conf.models and conf.engine.name == "ollama":
        return conf.model(ROLE), conf.engine.native_url
    default = config.load(config.DEFAULT_PATH)
    if ROLE not in default.models:
        raise ModelError("aucun modèle pour lire les photos : ajoute [vision] dans models.toml")
    return default.model(ROLE), default.engine.native_url


def transcribe(image: bytes, conf: config.Config | None = None, http: httpx.Client | None = None) -> Read:
    """The text of one photo (JPEG). Raises ModelError when the model does not answer; a photo
    that shows no course is a Read with its problem, not an error."""
    model, url = _model(conf or config.load())
    options: dict = {"temperature": model.temperature}
    if model.num_gpu is not None:
        options["num_gpu"] = model.num_gpu
    if model.context:
        options["num_ctx"] = model.context
    if model.max_tokens:
        options["num_predict"] = model.max_tokens
    body = {
        "model": model.model, "stream": False, "options": options,
        # Unloaded at once: loaded on the processor, it would serve the next sheet there too.
        "keep_alive": 0,
        "messages": [{"role": "user", "content": PROMPT, "images": [base64.b64encode(image).decode()]}],
    }
    client = http or httpx.Client(timeout=TIMEOUT_SECONDS)
    started = time.monotonic()
    try:
        response = client.post(f"{url}/api/chat", json=body)
    except httpx.HTTPError as error:
        raise ModelError(f"pas de réponse du modèle pour lire la photo : {error}") from error
    if response.status_code >= 400:
        raise ModelError(f"le modèle n'a pas pu lire la photo ({response.status_code} : {response.text[:200]})")
    reply = response.json()
    text = (reply.get("message") or {}).get("content", "").strip()
    seconds = time.monotonic() - started
    if not text or text.upper().startswith(NOT_A_COURSE):
        return Read("", "pas de cours lisible sur cette photo", seconds)
    if reply.get("done_reason") == "length":
        return Read(text, "texte coupé : la page est très longue, vérifie la fin", seconds)
    return Read(text, "", seconds)

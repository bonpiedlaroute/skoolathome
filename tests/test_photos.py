"""Photos of the notebook sent with a request: read by the model on the processor, the text sent
back for the parent to check, then the first source of every part of the sheet."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from skoolathome.agent import making, photos, vision
from skoolathome.agent.llm import ModelError
from skoolathome.schema.request import Transcription

JPEG = b"\xff\xd8\xff\xe0photo"


def ollama(reply: dict | None = None, status: int = 200, seen: list | None = None) -> httpx.Client:
    def answer(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(status, json=reply or {})

    return httpx.Client(transport=httpx.MockTransport(answer))


def test_a_photo_is_read_on_the_processor_then_unloaded():
    seen: list[dict] = []
    read = vision.transcribe(JPEG, http=ollama({"message": {"content": " Chapitre 4\nLES SYMETRIES "},
                                                "done_reason": "stop"}, seen=seen))
    assert (read.text, read.problem) == ("Chapitre 4\nLES SYMETRIES", "")
    [body] = seen
    assert body["options"]["num_gpu"] == 0 and body["keep_alive"] == 0  # the graphics card crashes on images
    assert body["messages"][0]["images"] == [base64.b64encode(JPEG).decode()]
    assert "mot pour mot" in body["messages"][0]["content"]


def test_a_photo_without_a_course_or_cut_short_is_said_so():
    nothing = vision.transcribe(JPEG, http=ollama({"message": {"content": "PAS DE COURS."}}))
    assert nothing.text == "" and nothing.problem == "pas de cours lisible sur cette photo"
    cut = vision.transcribe(JPEG, http=ollama({"message": {"content": "1. Définitions"}, "done_reason": "length"}))
    assert cut.text == "1. Définitions" and cut.problem.startswith("texte coupé")
    with pytest.raises(ModelError, match="n'a pas pu lire la photo"):
        vision.transcribe(JPEG, http=ollama({"error": "failed to process mtmd chunk"}, status=500))


class Api:
    def __init__(self) -> None:
        self.asked: list[tuple[str, int]] = []
        self.sent: dict[str, Transcription] = {}

    def request_photo(self, request_id: str, position: int) -> bytes:
        self.asked.append((request_id, position))
        return JPEG + bytes([position])

    def request_pages(self, request_id: str, transcription: Transcription) -> dict:
        self.sent[request_id] = transcription
        return {"ok": True}


def test_each_photo_is_kept_on_the_laptop_read_and_sent_back(tmp_path):
    def read(image: bytes) -> vision.Read:
        if image.endswith(b"\x02"):
            raise ModelError("pas de réponse du modèle pour lire la photo")
        return vision.Read(f"page {image[-1]}", "", 120.0)

    api, said = Api(), []
    assert photos.process(api, [{"id": "pwa-1", "photos": 2}], read=read, root=tmp_path, progress=said.append) == 1
    assert api.asked == [("pwa-1", 1), ("pwa-1", 2)]
    assert (tmp_path / "pwa-1" / "2.jpg").read_bytes() == JPEG + b"\x02"
    first, second = api.sent["pwa-1"].pages
    assert (first.text, first.problem) == ("page 1", "")
    # Not read: said on its page, for the parent to type it, never tried again in a loop.
    assert second.text == "" and second.problem.startswith("photo non lue")
    assert said[0] == "pwa-1 : photo 1/2 lue en 120 s, 6 caractères"


def test_every_part_of_a_long_sheet_reads_the_photos():
    sources = {"photo:pwa-1:1": "Deux figures sont symétriques par rapport à un point. " * 10,
               **{f"lesson:2026-11-{day}:MATHS": "Correction des exercices. " * 10 for day in (10, 12, 13, 16)}}
    planned = making.parts(18, sources)
    assert len(planned) == 3
    assert all("photo:pwa-1:1" in part for _, part in planned)
    assert sorted(key for _, part in planned for key in part if key.startswith("lesson")) == sorted(
        key for key in sources if key.startswith("lesson"))  # the lessons still shared out

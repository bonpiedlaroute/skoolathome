"""The photos of the notebook sent with a request, read by the laptop (the explain timer): each one
is downloaded, kept in data/photos/ (never committed), read by the model, and the text goes back
online for the parent to check. The API then deletes the photos.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from skoolathome.agent import vision
from skoolathome.agent.llm import ModelError
from skoolathome.schema.request import PhotoPage, Transcription

DEFAULT_ROOT = Path("data/photos")


def process(client: Any, waiting: list[dict], *, read: Callable[[bytes], vision.Read] = vision.transcribe,
            root: Path = DEFAULT_ROOT, progress: Callable[[str], None] = print) -> int:
    """Each request waiting for its photos to be read: [{"id", "photos"}]. A photo the model cannot
    read is said so on its page, for the parent to type it: never a loop of tries. Returns the
    requests answered."""
    answered = 0
    for request in waiting:
        request_id, count = request["id"], request["photos"]
        folder = root / request_id
        folder.mkdir(parents=True, exist_ok=True)
        pages = []
        for position in range(1, count + 1):
            image = client.request_photo(request_id, position)
            (folder / f"{position}.jpg").write_bytes(image)
            try:
                found = read(image)
            except ModelError as error:
                pages.append(PhotoPage(problem=f"photo non lue : {str(error)[:200]}"))
                progress(f"{request_id} : photo {position}/{count} non lue ({error})")
                continue
            pages.append(PhotoPage(text=found.text, problem=found.problem))
            progress(f"{request_id} : photo {position}/{count} lue en {found.seconds:.0f} s, "
                     f"{len(found.text)} caractères" + (f" ({found.problem})" if found.problem else ""))
        client.request_pages(request_id, Transcription(pages=pages))
        answered += 1
    return answered

"""The laptop's side of the online API: publish a batch, fetch the feedback. Every call is signed (NIP-98)."""

from __future__ import annotations

import json
import os
from secrets import token_hex
from typing import Protocol
from urllib.parse import urlencode

import httpx
from nostr_sdk import Event

from skoolathome import secrets
from skoolathome.publish.nip46 import NIP98_KIND, authorization_header, nip98_tags
from skoolathome.schema.explanation import ExplanationAnswer
from skoolathome.schema.home import Home
from skoolathome.schema.instruction import InstructionUpdate, PublishedRule
from skoolathome.schema.request import RequestUpdate, Transcription
from skoolathome.schema.review import WeeklyReview
from skoolathome.schema.sheet import Sheet

API_SECRET_NAME = "api"  # ~/.config/skoolathome/api.json: {"url": "https://….vercel.app"}


class Signer(Protocol):
    def sign(self, kind: int, content: str, tags: list[list[str]]) -> Event: ...


NOT_MIGRATED = ("la base en ligne n'a pas les dernières migrations : lance "
                "« DATABASE_URL='<adresse Neon>' uv run skoolathome api migrate »")


class ApiError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        # The API's 503 when Neon lags behind the deployed code: every log that prints the error
        # (evening, explain timer, CLI) then says what to run.
        said = NOT_MIGRATED if status == 503 and "not migrated" in detail else detail
        super().__init__(f"{status} {said}")
        self.status = status
        self.detail = detail


def api_url() -> str:
    """SKOOLATHOME_API_URL, or the address saved by `skoolathome api set-url`."""
    url = os.environ.get("SKOOLATHOME_API_URL") or (secrets.read(API_SECRET_NAME) or {}).get("url")
    if not url:
        raise FileNotFoundError("adresse de l'API inconnue : lance « skoolathome api set-url https://… »")
    return url.rstrip("/")


class ApiClient:
    def __init__(self, base_url: str, signer: Signer, http: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.signer = signer
        self.http = http or httpx.Client(timeout=60)

    def _call(self, method: str, path: str, payload: dict | None = None, params: dict | None = None,
              *, content: bytes | None = None, content_type: str = "application/json") -> dict:
        return self._send(method, path, payload, params, content=content, content_type=content_type).json()

    def _send(self, method: str, path: str, payload: dict | None = None, params: dict | None = None,
              *, content: bytes | None = None, content_type: str = "application/json") -> httpx.Response:
        # The signed URL and body must be exactly the ones sent: build them once, use them twice.
        url = self.base_url + path + (f"?{urlencode(params)}" if params else "")
        if content is not None:
            body = content
        else:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode() if payload is not None else b""
        # Two identical calls in the same second would give the same event id, and the second
        # would be refused as a replay: a random tag makes each request unique.
        tags = nip98_tags(url, method, body) + [["nonce", token_hex(8)]]
        event = self.signer.sign(NIP98_KIND, "", tags)
        headers = {"Authorization": authorization_header(event)}
        if body:
            headers["Content-Type"] = content_type
        response = self.http.request(method, url, content=body or None, headers=headers)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ApiError(response.status_code, str(detail)[:500])
        return response

    def publish(self, sheets: list[Sheet], requests: list[RequestUpdate] | None = None, *,
                instructions: list[InstructionUpdate] | None = None,
                rules: list[PublishedRule] | None = None, review: WeeklyReview | None = None) -> list[str]:
        """The sheets, what became of the requests and instructions made from the PWA, the rules
        for the parent view, and the weekly review: one signature."""
        payload: dict = {"sheets": [sheet.model_dump(mode="json") for sheet in sheets]}
        if requests:
            payload["requests"] = [r.model_dump(mode="json") for r in requests]
        if instructions:
            payload["instructions"] = [i.model_dump(mode="json") for i in instructions]
        if rules is not None:
            payload["rules"] = [r.model_dump(mode="json") for r in rules]
        if review is not None:
            payload["review"] = review.model_dump(mode="json")
        return self._call("POST", "/api/batches", payload)["published"]

    def feedback(self, since: int = 0) -> dict:
        return self._call("GET", "/api/feedback", params={"since": since})

    def pairing_code(self, role: str, label: str) -> dict:
        """A code to pair a new device: {code, url, expires_in_minutes, qr_svg}."""
        return self._call("POST", "/api/agent/pairing-tokens", {"role": role, "label": label})

    def pending_explanations(self) -> list[dict]:
        """« Je n'ai pas compris » waiting for this laptop (T29)."""
        return self._call("GET", "/api/agent/explanations")

    def answer_explanation(self, explanation_id: str, answer: ExplanationAnswer) -> dict:
        return self._call("POST", f"/api/agent/explanations/{explanation_id}", answer.model_dump(mode="json"))

    def queue(self) -> dict:
        """What waits online: {"explanations": [...], "requests": [ids], "photos": [{id, photos}]}.
        An API deployed before the queue only has the explanations; before the photos, none."""
        try:
            return {"photos": [], **self._call("GET", "/api/agent/queue")}
        except ApiError as error:
            if error.status != 404:
                raise
            return {"explanations": self.pending_explanations(), "requests": [], "photos": []}

    def request_photo(self, request_id: str, position: int) -> bytes:
        """A photo of the notebook sent with a request, in JPEG."""
        return self._send("GET", f"/api/agent/requests/{request_id}/photos/{position}").content

    def request_pages(self, request_id: str, transcription: Transcription) -> dict:
        """What was read on the photos: the API deletes them and asks the parent to check the text."""
        return self._call("POST", f"/api/agent/requests/{request_id}/pages", transcription.model_dump(mode="json"))

    def missing_documents(self, sha256: list[str]) -> list[str]:
        return self._call("POST", "/api/agent/documents/missing", {"sha256": sha256})["missing"]

    def upload_document(self, sha256: str, name: str, media_type: str, content: bytes) -> dict:
        """A teacher's file of the home page, as it is (home.upload_files)."""
        return self._call("PUT", f"/api/agent/documents/{sha256}", params={"name": name},
                          content=content, content_type=media_type)

    def home(self, page: Home) -> dict:
        """The home page of the PWA (what comes next, grades, sheets to do): replaces the previous one."""
        return self._call("PUT", "/api/agent/home", page.model_dump(mode="json"))

    def notify(self, title: str, body: str, url: str = "/", tag: str = "agent") -> dict:
        """A notification to the parent devices (T32: a wrong answer on a version just promoted)."""
        return self._call("POST", "/api/agent/notify", {"title": title, "body": body, "url": url, "tag": tag})

    def report(self, report: dict) -> dict:
        """The end-of-evening report: stored online and pushed to the parent devices."""
        return self._call("POST", "/api/evening-reports", report)

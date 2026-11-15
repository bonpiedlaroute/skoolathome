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
from skoolathome.schema.instruction import InstructionUpdate, PublishedRule
from skoolathome.schema.request import RequestUpdate
from skoolathome.schema.review import WeeklyReview
from skoolathome.schema.sheet import Sheet

API_SECRET_NAME = "api"  # ~/.config/skoolathome/api.json: {"url": "https://….vercel.app"}


class Signer(Protocol):
    def sign(self, kind: int, content: str, tags: list[list[str]]) -> Event: ...


class ApiError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status} {detail}")
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

    def _call(self, method: str, path: str, payload: dict | None = None, params: dict | None = None) -> dict:
        # The signed URL and body must be exactly the ones sent: build them once, use them twice.
        url = self.base_url + path + (f"?{urlencode(params)}" if params else "")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode() if payload is not None else b""
        # Two identical calls in the same second would give the same event id, and the second
        # would be refused as a replay: a random tag makes each request unique.
        tags = nip98_tags(url, method, body) + [["nonce", token_hex(8)]]
        event = self.signer.sign(NIP98_KIND, "", tags)
        headers = {"Authorization": authorization_header(event)}
        if body:
            headers["Content-Type"] = "application/json"
        response = self.http.request(method, url, content=body or None, headers=headers)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ApiError(response.status_code, str(detail)[:500])
        return response.json()

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

    def report(self, report: dict) -> dict:
        """The end-of-evening report: stored online and pushed to the parent devices."""
        return self._call("POST", "/api/evening-reports", report)

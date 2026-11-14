"""NIP-98 HTTP authentication: the laptop's calls are signed by the bunker, checked here.

The laptop holds no key. Each call carries a kind 27235 event signed by the agent's identity,
bound to one URL, one method and one body, valid for 60 seconds, accepted once.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import time
from dataclasses import dataclass

import psycopg
from fastapi import Depends, Header, HTTPException, Request
from nostr_sdk import Event, PublicKey

from skoolathome.api.auth import get_conn

KIND = 27235
MAX_SKEW_SECONDS = 60
MAX_HEADER_BYTES = 16_384
SEEN_RETENTION = "1 day"


class Refused(Exception):
    """Why a request is refused. The reason goes back in the 401 response: it helps debugging and reveals nothing."""


@dataclass(frozen=True)
class Agent:
    pubkey: str  # hex
    event_id: str


def expected_pubkey() -> str | None:
    """The agent's identity (npub or hex), as printed by bunkerd. Set in the Vercel variables."""
    value = os.environ.get("SKOOLATHOME_AGENT_PUBKEY", "").strip()
    return PublicKey.parse(value).to_hex() if value else None


def request_url(request: Request) -> str:
    """The URL as the laptop called it. Behind Vercel's proxy, the scheme and host come from headers."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    query = request.url.query
    return f"{scheme}://{host}{request.url.path}" + (f"?{query}" if query else "")


def _decode(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Nostr "):
        raise Refused("missing")
    encoded = authorization.removeprefix("Nostr ").strip()
    if len(encoded) > MAX_HEADER_BYTES:
        raise Refused("too large")
    try:
        data = json.loads(base64.b64decode(encoded, validate=True))
    except (binascii.Error, ValueError):
        raise Refused("malformed") from None
    if not isinstance(data, dict):
        raise Refused("malformed")
    return data


def _tag(data: dict, name: str) -> str | None:
    for tag in data.get("tags") or []:
        if isinstance(tag, list) and len(tag) >= 2 and tag[0] == name:
            return tag[1]
    return None


def check_event(authorization: str | None, method: str, url: str, body: bytes, expected: str, now: float) -> str:
    """Every check except replay, which needs the database. Returns the event id."""
    data = _decode(authorization)
    try:
        event = Event.from_json(json.dumps(data))
    except Exception:  # the bindings raise one error type for every parsing failure
        raise Refused("malformed") from None
    if not event.verify():
        raise Refused("bad signature")
    if event.kind().as_u16() != KIND:
        raise Refused("wrong kind")
    if event.author().to_hex() != expected:
        raise Refused("unknown key")
    if abs(now - event.created_at().as_secs()) > MAX_SKEW_SECONDS:
        raise Refused("expired")
    if _tag(data, "u") != url:
        raise Refused("wrong url")
    if (_tag(data, "method") or "").upper() != method.upper():
        raise Refused("wrong method")
    payload = _tag(data, "payload")
    if (body or payload is not None) and payload != hashlib.sha256(body).hexdigest():
        raise Refused("wrong payload")
    return event.id().to_hex()


def remember(conn: psycopg.Connection, event_id: str) -> None:
    """Accept each event once. A replayed request, even within the 60 seconds, is refused."""
    stored = conn.execute(
        "INSERT INTO nip98_seen (event_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING event_id", (event_id,)
    ).fetchone()
    if stored is None:
        raise Refused("replayed")
    conn.execute(f"DELETE FROM nip98_seen WHERE seen_at < now() - interval '{SEEN_RETENTION}'")


async def require_agent(
    request: Request,
    authorization: str | None = Header(default=None),
    conn: psycopg.Connection = Depends(get_conn),
) -> Agent:
    """FastAPI dependency for the routes the laptop calls."""
    expected = expected_pubkey()
    if expected is None:
        raise HTTPException(503, "agent key not configured")
    body = await request.body()  # already read by FastAPI for the route's body: served from cache
    try:
        event_id = check_event(authorization, request.method, request_url(request), body, expected, time.time())
        remember(conn, event_id)
    except Refused as refusal:
        raise HTTPException(401, f"nip98: {refusal}") from None
    return Agent(pubkey=expected, event_id=event_id)

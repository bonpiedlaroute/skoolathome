"""Remote signing through the bunker (NIP-46): this laptop holds no key, only a revocable token.

The bunker (nostr-remote-signer on Railway) signs with the agent's identity and applies its
own policy: here, NIP-98 HTTP authentication events (kind 27235) only.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets as random
from datetime import timedelta

from nostr_sdk import Event, EventBuilder, Keys, Kind, NostrConnect, NostrConnectUri, Tag

from skoolathome import secrets

NIP98_KIND = 27235
TIMEOUT = timedelta(seconds=30)  # per request, room for a cold relay
SECRET_NAME = "bunker"  # ~/.config/skoolathome/bunker.json: {"uri": "bunker://…", "token": "…"}


class SigningRefused(Exception):
    """The bunker refused, or did not answer in time."""


def new_token() -> tuple[str, str]:
    """(token, its sha256): the token stays on this laptop, the vault stores only the hash."""
    token = random.token_hex(32)
    return token, hashlib.sha256(token.encode("ascii")).hexdigest()


def load_conf() -> dict:
    conf = secrets.read(SECRET_NAME)
    if not conf or not conf.get("uri") or not conf.get("token"):
        raise FileNotFoundError("bunker non configuré : lance « skoolathome signer new-token »")
    return conf


def nip98_tags(url: str, method: str, body: bytes | None = None) -> list[list[str]]:
    """Tags of an HTTP authentication event (NIP-98): the exact URL, the method, the body's hash."""
    tags = [["u", url], ["method", method.upper()]]
    if body:
        tags.append(["payload", hashlib.sha256(body).hexdigest()])
    return tags


def authorization_header(event: Event) -> str:
    return "Nostr " + base64.b64encode(event.as_json().encode("utf-8")).decode("ascii")


async def sign_async(kind: int, content: str, tags: list[list[str]], conf: dict | None = None) -> Event:
    """Ask the bunker to sign. The result is checked here: never trust the signer on its word."""
    conf = conf or load_conf()
    separator = "&" if "?" in conf["uri"] else "?"
    uri = NostrConnectUri.parse(f"{conf['uri']}{separator}secret={conf['token']}")
    # A new transport key each time: it only signs the NIP-46 envelopes.
    connect = NostrConnect(uri, Keys.generate(), TIMEOUT, None)
    try:
        author = await connect.get_public_key_async()
        unsigned = EventBuilder(Kind(kind), content).tags([Tag.parse(t) for t in tags]).finalize_unsigned(author)
        event = await connect.sign_event_async(unsigned)
    except Exception as error:  # the bindings raise one error type for every failure
        raise SigningRefused(str(error)) from error
    if event is None:
        raise SigningRefused("no event returned")
    if event.author().to_hex() != author.to_hex() or not event.verify():
        raise SigningRefused("the bunker returned an invalid event")
    return event


def sign(kind: int, content: str, tags: list[list[str]], conf: dict | None = None) -> Event:
    return asyncio.run(sign_async(kind, content, tags, conf))

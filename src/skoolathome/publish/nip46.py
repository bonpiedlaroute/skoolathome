"""Remote signing through the bunker (NIP-46): this laptop holds no key, only a revocable token.

The bunker (nostr-remote-signer on Railway) signs with the agent's identity and applies its
own policy: here, NIP-98 HTTP authentication events (kind 27235) only.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets as random
import time
from datetime import timedelta

from nostr_sdk import Event, EventBuilder, Keys, Kind, NostrConnect, NostrConnectUri, PublicKey, Tag

from skoolathome import secrets

NIP98_KIND = 27235
TIMEOUT = timedelta(seconds=30)  # per request, room for a cold relay
SECRET_NAME = "bunker"  # ~/.config/skoolathome/bunker.json: {"uri": "bunker://…", "token": "…"}


class SigningRefused(Exception):
    """The bunker refused, or did not answer in time."""


class Pace:
    """The vault's rate policy (deploy/railway/entrypoint.sh): per session, 5 requests at once then
    one every 10 seconds; for the whole bunker, 10 at once then one every 5 seconds. Beyond, the
    answer is « Rejected », and a refused request still costs its token. A signature is one
    request, opening a session two (connect, get_public_key). Paced here, a little slower than the
    vault, so that the teachers' files go online one by one instead of being refused in a row."""

    def __init__(self, burst: int = 8, every: float = 5.5, clock=time.monotonic, sleep=time.sleep) -> None:
        self.burst, self.every, self.clock, self.sleep = burst, every, clock, sleep
        self.tokens, self.at = float(burst), clock()

    def take(self, cost: int = 1) -> None:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.at) / self.every)
        self.at = now
        if self.tokens < cost:
            self.sleep((cost - self.tokens) * self.every)
            self.tokens, self.at = float(cost), self.clock()
        self.tokens -= cost


PACE = Pace(burst=8, every=5.5)  # the bunker as a whole: shared by every session of this process
SESSION_BURST, SESSION_EVERY = 4, 10.5


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


class BunkerSession:
    """One NIP-46 session for one step of the evening.

    The bunker sees `connect` and `get_public_key` once, then one `sign_event` per call. The
    evening graph opens one at the start (feedback) and one per call at the end (batch, report):
    an hour of generation in between, and no connection left idle that long.
    """

    def __init__(self, conf: dict | None = None) -> None:
        self._conf = conf or load_conf()
        self._loop = asyncio.new_event_loop()
        self._connect: NostrConnect | None = None
        self._author: PublicKey | None = None
        self.signatures = 0
        self._pace = Pace(burst=SESSION_BURST, every=SESSION_EVERY)  # the vault counts per session too

    def __enter__(self) -> BunkerSession:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if not self._loop.is_closed():
            self._loop.close()

    def _run(self, coroutine):
        try:
            return self._loop.run_until_complete(coroutine)
        except Exception as error:  # the bindings raise one error type for every failure
            raise SigningRefused(str(error)) from error

    async def _open(self) -> NostrConnect:
        separator = "&" if "?" in self._conf["uri"] else "?"
        uri = NostrConnectUri.parse(f"{self._conf['uri']}{separator}secret={self._conf['token']}")
        # A new transport key per session: it only signs the NIP-46 envelopes.
        return NostrConnect(uri, Keys.generate(), TIMEOUT, None)

    def public_key(self) -> PublicKey:
        if self._author is None:
            PACE.take(2)
            self._pace.take(2)
            self._connect = self._run(self._open())
            self._author = self._run(self._connect.get_public_key_async())
        return self._author

    def sign(self, kind: int, content: str, tags: list[list[str]]) -> Event:
        """Ask the bunker to sign. The result is checked here: never trust the signer on its word."""
        author = self.public_key()
        PACE.take(1)  # before the event is dated: a paced event is as fresh as any
        self._pace.take(1)
        unsigned = EventBuilder(Kind(kind), content).tags([Tag.parse(t) for t in tags]).finalize_unsigned(author)
        event = self._run(self._connect.sign_event_async(unsigned))
        if event is None:
            raise SigningRefused("no event returned")
        if event.author().to_hex() != author.to_hex() or not event.verify():
            raise SigningRefused("the bunker returned an invalid event")
        self.signatures += 1
        return event


def sign(kind: int, content: str, tags: list[list[str]], conf: dict | None = None) -> Event:
    """One signature in its own session: for `skoolathome signer test`."""
    with BunkerSession(conf) as session:
        return session.sign(kind, content, tags)

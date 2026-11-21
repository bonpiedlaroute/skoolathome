"""The NIP-46 client helpers, without a bunker: token, NIP-98 tags, Authorization header."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json

from nostr_sdk import Event, EventBuilder, Keys, Kind, Tag

from skoolathome.publish.nip46 import NIP98_KIND, authorization_header, new_token, nip98_tags


def test_token_and_its_hash():
    token, digest = new_token()
    assert len(token) == 64 and digest == hashlib.sha256(token.encode()).hexdigest()
    assert new_token()[0] != token


def test_nip98_tags():
    body = b'{"sheets": []}'
    assert nip98_tags("https://x.vercel.app/api/batches", "post", body) == [
        ["u", "https://x.vercel.app/api/batches"],
        ["method", "POST"],
        ["payload", hashlib.sha256(body).hexdigest()],
    ]
    assert nip98_tags("https://x.vercel.app/api/feedback?since=1", "GET") == [
        ["u", "https://x.vercel.app/api/feedback?since=1"], ["method", "GET"]]


def test_authorization_header_carries_the_signed_event():
    keys = Keys.generate()
    tags = [Tag.parse(t) for t in nip98_tags("https://x/api", "GET")]
    unsigned = EventBuilder(Kind(NIP98_KIND), "").tags(tags).finalize_unsigned(keys.public_key())
    event = asyncio.run(unsigned.sign_async(keys))
    header = authorization_header(event)
    assert header.startswith("Nostr ")
    decoded = json.loads(base64.b64decode(header.removeprefix("Nostr ")))
    assert decoded["kind"] == NIP98_KIND and ["method", "GET"] in decoded["tags"]
    assert Event.from_json(json.dumps(decoded)).verify()


def test_signatures_are_paced_to_the_vaults_policy():
    """The vault takes 10 requests at once, then one every 5 seconds, and answers « Rejected »
    beyond: 20 teachers' files sent in a row were refused after the eighth."""
    from skoolathome.publish.nip46 import Pace

    now, slept = [0.0], []

    def sleep(seconds):
        slept.append(round(seconds, 2))
        now[0] += seconds

    pace = Pace(burst=8, every=5.5, clock=lambda: now[0], sleep=sleep)
    pace.take(2)                      # the session opens
    for _ in range(6):
        pace.take()                   # within the burst: no wait
    assert slept == []
    pace.take()
    assert slept == [5.5]             # then one every 5.5 seconds
    now[0] += 60                      # a quiet minute refills the burst
    for _ in range(8):
        pace.take()
    assert slept == [5.5]

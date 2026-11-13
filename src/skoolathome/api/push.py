"""Web Push: notifications to the paired devices that accepted them (VAPID, pywebpush)."""

from __future__ import annotations

import base64
import json
import logging
import os

import psycopg
from pywebpush import WebPushException, webpush

log = logging.getLogger(__name__)

TTL_SECONDS = 12 * 3600  # a notification older than that is useless
GONE = (404, 410)  # the browser dropped the subscription


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_vapid_keys() -> tuple[str, str]:
    """(public key for the browser, private key for the server), both base64url."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private = ec.generate_private_key(ec.SECP256R1())
    raw_private = private.private_numbers().private_value.to_bytes(32, "big")
    raw_public = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64url(raw_public), _b64url(raw_private)


def public_key() -> str:
    return os.environ.get("SKOOLATHOME_VAPID_PUBLIC_KEY", "")


def _private_key() -> str:
    return os.environ.get("SKOOLATHOME_VAPID_PRIVATE_KEY", "")


def _subject() -> str:
    """The VAPID contact: a URL of the site itself, so no e-mail address is needed."""
    explicit = os.environ.get("SKOOLATHOME_VAPID_SUBJECT")
    if explicit:
        return explicit
    host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")  # set by Vercel
    return f"https://{host}" if host else "https://localhost"


def enabled() -> bool:
    return bool(public_key() and _private_key())


def send(
    conn: psycopg.Connection,
    payload: dict,
    *,
    roles: tuple[str, ...] = ("parent", "child"),
    device_id: str | None = None,
) -> dict[str, int]:
    """Send `payload` ({title, body, url}) to every subscription of the matching devices.

    Revoked devices are skipped. A subscription the push service says is gone is deleted.
    """
    counts = {"sent": 0, "failed": 0, "removed": 0}
    if not enabled():
        return counts
    rows = conn.execute(
        "SELECT s.endpoint, s.p256dh, s.auth FROM push_subscriptions s "
        "JOIN devices d ON d.id = s.device_id "
        "WHERE d.revoked_at IS NULL AND d.role = ANY(%s) AND (%s::text IS NULL OR d.id = %s)",
        (list(roles), device_id, device_id),
    ).fetchall()
    data = json.dumps(payload, ensure_ascii=False)
    for endpoint, p256dh, auth in rows:
        try:
            webpush(
                subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
                data=data,
                vapid_private_key=_private_key(),
                vapid_claims={"sub": _subject()},
                ttl=TTL_SECONDS,
                timeout=10,
            )
            counts["sent"] += 1
        except WebPushException as error:
            status = error.response.status_code if error.response is not None else None
            if status in GONE:
                conn.execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,))
                counts["removed"] += 1
            else:
                log.warning("push failed (%s): %s", status, error)
                counts["failed"] += 1
    return counts

"""Devices and sessions: no password to remember, each paired device has a role."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterator

import psycopg
from fastapi import Cookie, Depends, HTTPException, Response

from skoolathome.api import settings

COOKIE_NAME = "skoolathome_session"
COOKIE_MAX_AGE = int(timedelta(days=400).total_seconds())  # the longest lifetime browsers accept
PAIRING_TOKEN_LIFETIME = timedelta(hours=1)  # time to send the link by message and open it
MIN_BOOTSTRAP_CODE_LENGTH = 16
# A pairing code is typed by hand on a computer: no 0/O nor 1/I. 8 characters out of 32 are 40 bits,
# for one hour and a single use: far out of reach of guessing.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


@dataclass(frozen=True)
class Device:
    id: str
    role: str
    label: str


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def get_conn() -> Iterator[psycopg.Connection]:
    """One short connection per request: what a serverless function needs."""
    with psycopg.connect(settings.database_url(), autocommit=True) as conn:
        yield conn


def set_session_cookie(response: Response, value: str) -> None:
    response.set_cookie(
        COOKIE_NAME, value, max_age=COOKIE_MAX_AGE, path="/",
        httponly=True, secure=settings.cookie_secure(), samesite="lax",
    )


def create_device(conn: psycopg.Connection, role: str, label: str) -> tuple[Device, str]:
    """Returns the device and its session value, to put in the cookie. Only the hash is stored."""
    session = secrets.token_urlsafe(32)
    device = Device(id=secrets.token_hex(8), role=role, label=label)
    conn.execute(
        "INSERT INTO devices (id, role, label, session_hash) VALUES (%s, %s, %s, %s)",
        (device.id, device.role, device.label, sha256(session)),
    )
    return device, session


def pair_with_bootstrap_code(conn: psycopg.Connection, code: str, label: str) -> tuple[Device, str]:
    """The very first parent device. Works once: refused as soon as a parent device exists. Typed
    on a phone: case, spaces and dashes do not count."""
    expected = normalize_code(settings.bootstrap_code())
    if len(expected) < MIN_BOOTSTRAP_CODE_LENGTH or not hmac.compare_digest(normalize_code(code).encode(), expected.encode()):
        raise HTTPException(403, "invalid code")
    with conn.transaction():
        conn.execute("LOCK TABLE devices IN EXCLUSIVE MODE")
        exists = conn.execute(
            "SELECT 1 FROM devices WHERE role = 'parent' AND revoked_at IS NULL LIMIT 1"
        ).fetchone()
        if exists:
            raise HTTPException(403, "a parent device is already paired: pair from its QR code")
        return create_device(conn, "parent", label)


def normalize_code(text: str) -> str:
    """« k7pm 4qxn », « K7PM-4QXN »: the same code."""
    return "".join(c for c in text.upper() if c.isalnum())


def new_code(length: int = CODE_LENGTH) -> str:
    """K7PM-4QXN: groups of four, easy to read out and to type."""
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))
    return "-".join(raw[i:i + 4] for i in range(0, length, 4))


def create_pairing_token(conn: psycopg.Connection, creator: Device | None, role: str, label: str) -> str:
    """A short code, shown as K7PM-4QXN: typed on a computer, or read from the QR code's link.
    `creator` is None when the laptop asks for it (`skoolathome pair`, signed NIP-98)."""
    code = new_code()
    conn.execute(
        "INSERT INTO pairing_tokens (token_hash, role, label, created_by, expires_at) "
        "VALUES (%s, %s, %s, %s, now() + %s)",
        (sha256(normalize_code(code)), role, label, creator.id if creator else None, PAIRING_TOKEN_LIFETIME),
    )
    return code


def pair_with_token(conn: psycopg.Connection, token: str) -> tuple[Device, str]:
    """A token works once, within ten minutes. The UPDATE is the single-use guarantee."""
    with conn.transaction():
        row = conn.execute(
            "UPDATE pairing_tokens SET used_at = now() "
            "WHERE token_hash = %s AND used_at IS NULL AND expires_at > now() "
            "RETURNING role, label",
            (sha256(normalize_code(token)),),
        ).fetchone()
        if row is None:
            raise HTTPException(403, "invalid, expired or already used token")
        return create_device(conn, row[0], row[1])


def current_device(
    conn: psycopg.Connection = Depends(get_conn),
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> Device:
    """The device behind the session cookie. A revoked device loses access at its next request."""
    if not session:
        raise HTTPException(401, "not paired")
    row = conn.execute(
        "UPDATE devices SET last_seen_at = now() "
        "WHERE session_hash = %s AND revoked_at IS NULL RETURNING id, role, label",
        (sha256(session),),
    ).fetchone()
    if row is None:
        raise HTTPException(401, "not paired")
    return Device(*row)


def parent_device(device: Device = Depends(current_device)) -> Device:
    if device.role != "parent":
        raise HTTPException(403, "parent only")
    return device

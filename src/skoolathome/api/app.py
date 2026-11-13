"""The FastAPI application. Every route is under /api; the PWA is served from web/dist."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import psycopg
import segno
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from skoolathome.api import auth
from skoolathome.api.auth import Device, current_device, get_conn, parent_device
from skoolathome.correction import check
from skoolathome.schema.sheet import Sheet

SCHOOL_TIMEZONE = ZoneInfo("Europe/Paris")

app = FastAPI(title="skoolathome", docs_url=None, redoc_url=None, openapi_url=None)


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PairRequest(Body):
    code: str | None = Field(default=None, max_length=200)   # bootstrap code, first parent device only
    token: str | None = Field(default=None, max_length=200)  # one-time token from a QR code
    label: str = Field(default="", max_length=60)


class PairingTokenRequest(Body):
    role: Literal["parent", "child"]
    label: str = Field(min_length=1, max_length=60)


class AttemptRequest(Body):
    sheet_id: str
    item_id: str
    mode: Literal["solo", "together"] = "solo"
    response: str | list[str] | bool
    hints_used: int = Field(default=0, ge=0, le=20)


class ReportRequest(Body):
    sheet_id: str
    item_id: str | None = None
    reason: Literal["wrong_answer", "not_covered", "unclear", "too_hard", "too_easy"]
    comment: str = Field(default="", max_length=500)


def today():
    return datetime.now(SCHOOL_TIMEZONE).date()


def _device_json(device: Device) -> dict:
    return {"id": device.id, "role": device.role, "label": device.label}


# ------------------------------------------------------------------ devices


@app.post("/api/pair")
def pair(body: PairRequest, response: Response, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    if body.token:
        device, session = auth.pair_with_token(conn, body.token)
    elif body.code:
        device, session = auth.pair_with_bootstrap_code(conn, body.code, body.label or "Parent")
    else:
        raise HTTPException(422, "code or token required")
    auth.set_session_cookie(response, session)
    return _device_json(device)


@app.get("/api/me")
def me(device: Device = Depends(current_device)) -> dict:
    return _device_json(device)


@app.post("/api/pairing-tokens")
def create_pairing_token(
    body: PairingTokenRequest,
    request: Request,
    device: Device = Depends(parent_device),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    token = auth.create_pairing_token(conn, device, body.role, body.label)
    # The token travels in the fragment (#…): browsers never send it to a server or write it in logs.
    origin = str(request.base_url).rstrip("/")
    url = f"{origin}/#/pair/{token}"
    qr = segno.make(url, error="m").svg_inline(scale=6, border=2, dark="#111", light="#fff")
    minutes = int(auth.PAIRING_TOKEN_LIFETIME.total_seconds() // 60)
    return {"url": url, "qr_svg": qr, "expires_in_minutes": minutes}


@app.get("/api/devices")
def list_devices(device: Device = Depends(parent_device), conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    rows = conn.execute(
        "SELECT id, role, label, created_at, last_seen_at FROM devices "
        "WHERE revoked_at IS NULL ORDER BY created_at"
    ).fetchall()
    return [
        {"id": r[0], "role": r[1], "label": r[2], "created_at": r[3], "last_seen_at": r[4], "is_this_device": r[0] == device.id}
        for r in rows
    ]


@app.delete("/api/devices/{device_id}", status_code=204)
def revoke_device(
    device_id: str, device: Device = Depends(parent_device), conn: psycopg.Connection = Depends(get_conn)
) -> None:
    if device_id == device.id:
        raise HTTPException(409, "this device cannot revoke itself")
    done = conn.execute(
        "UPDATE devices SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL RETURNING id", (device_id,)
    ).fetchone()
    if done is None:
        raise HTTPException(404, "unknown device")


# ------------------------------------------------------------------- sheets


def _for_role(sheet: dict, role: str) -> dict:
    """The parent guide is for the parent: a child device never receives it."""
    if role != "parent":
        for item in sheet["items"]:
            item.pop("parent_guide", None)
    return sheet


@app.get("/api/sheets")
def list_sheets(device: Device = Depends(current_device), conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    """Sheets whose display date has come, most recent first, with the progress made on each."""
    rows = conn.execute(
        "SELECT s.id, s.subject, s.title, s.type, s.visible_from, s.due_by, "
        "       (s.body->>'estimated_minutes')::int, jsonb_array_length(s.body->'items'), "
        "       (SELECT count(DISTINCT a.item_id) FROM attempts a WHERE a.sheet_id = s.id), "
        "       (SELECT count(DISTINCT a.item_id) FROM attempts a WHERE a.sheet_id = s.id AND a.correct) "
        "FROM sheets s WHERE s.visible_from <= %s ORDER BY s.visible_from DESC, s.id LIMIT 60",
        (today(),),
    ).fetchall()
    keys = ("id", "subject", "title", "type", "visible_from", "due_by", "estimated_minutes", "items", "answered", "correct")
    return [dict(zip(keys, row)) for row in rows]


def _load_sheet(conn: psycopg.Connection, sheet_id: str) -> dict:
    row = conn.execute(
        "SELECT body FROM sheets WHERE id = %s AND visible_from <= %s", (sheet_id, today())
    ).fetchone()
    if row is None:
        raise HTTPException(404, "unknown sheet")
    return row[0]


@app.get("/api/sheets/{sheet_id}")
def get_sheet(
    sheet_id: str, device: Device = Depends(current_device), conn: psycopg.Connection = Depends(get_conn)
) -> dict:
    return _for_role(_load_sheet(conn, sheet_id), device.role)


@app.post("/api/attempts", status_code=201)
def create_attempt(
    body: AttemptRequest, device: Device = Depends(current_device), conn: psycopg.Connection = Depends(get_conn)
) -> dict:
    """Stores an answer. The server corrects it again: it never trusts the browser's verdict."""
    sheet = _load_sheet(conn, body.sheet_id)
    item = next((i for i in sheet["items"] if i["id"] == body.item_id), None)
    if item is None:
        raise HTTPException(404, "unknown item")
    correct = check(
        item["check"]["mode"], item["answer"], body.response,
        variants=item.get("variants"), tolerance=item["check"].get("tolerance", "0"),
    )
    conn.execute(
        "INSERT INTO attempts (sheet_id, item_id, device_id, mode, response, correct, hints_used) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (body.sheet_id, body.item_id, device.id, body.mode, Jsonb(body.response), correct, body.hints_used),
    )
    return {"correct": correct}


@app.post("/api/reports", status_code=201)
def create_report(
    body: ReportRequest, device: Device = Depends(current_device), conn: psycopg.Connection = Depends(get_conn)
) -> dict:
    _load_sheet(conn, body.sheet_id)
    conn.execute(
        "INSERT INTO reports (sheet_id, item_id, device_id, reason, comment) VALUES (%s, %s, %s, %s, %s)",
        (body.sheet_id, body.item_id, device.id, body.reason, body.comment),
    )
    return {"ok": True}


def save_sheet(conn: psycopg.Connection, sheet: Sheet) -> None:
    """Insert or replace a sheet. Used by the seeding command now, by POST /batches in T13."""
    conn.execute(
        "INSERT INTO sheets (id, student, subject, title, type, visible_from, due_by, body) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (id) DO UPDATE SET student = EXCLUDED.student, subject = EXCLUDED.subject, "
        "title = EXCLUDED.title, type = EXCLUDED.type, visible_from = EXCLUDED.visible_from, "
        "due_by = EXCLUDED.due_by, body = EXCLUDED.body, published_at = now()",
        (sheet.id, sheet.student, sheet.subject, sheet.title, sheet.type, sheet.visible_from, sheet.due_by,
         Jsonb(sheet.model_dump(mode="json"))),
    )


# The PWA. Declared last: every /api route above wins over the files.
app.frontend("/", directory="web/dist", check_dir=False)

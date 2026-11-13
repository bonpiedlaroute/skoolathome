"""The FastAPI application. Every route is under /api; the PWA is served from web/dist."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import hmac
import os
from datetime import timedelta

import psycopg
import segno
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from skoolathome.api import auth, push
from skoolathome.api.auth import Device, current_device, get_conn, parent_device
from skoolathome.correction import check
from skoolathome.progress import ItemProgress
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
    response: str | list[str] | bool | None = None
    rating: Literal["good", "partial", "bad"] | None = None  # together mode: the parent's verdict
    hints_used: int = Field(default=0, ge=0, le=20)
    client_id: str | None = Field(default=None, max_length=64)  # set by the browser, for retries
    answered_at: datetime | None = None

    @model_validator(mode="after")
    def _has_an_answer(self) -> AttemptRequest:
        if self.response is None and self.rating is None:
            raise ValueError("response or rating required")
        if self.rating is not None and self.mode != "together":
            raise ValueError("a rating is given in together mode only")
        return self


class ReportRequest(Body):
    sheet_id: str
    item_id: str | None = None
    reason: Literal["wrong_answer", "not_covered", "unclear", "too_hard", "too_easy"]
    comment: str = Field(default="", max_length=500)
    client_id: str | None = Field(default=None, max_length=64)


class FinishRequest(Body):
    client_id: str = Field(min_length=8, max_length=64)


class PushKeys(Body):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class PushSubscription(Body):
    model_config = ConfigDict(extra="ignore")  # browsers add expirationTime
    endpoint: str = Field(max_length=1000, pattern=r"^https://")
    keys: PushKeys


SCHOOL_DAYS = range(0, 5)  # Monday to Friday; school holidays are not known online
DEFAULT_ABSENCE_HOURS = 36


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
    conn.execute("DELETE FROM push_subscriptions WHERE device_id = %s", (device_id,))


# ------------------------------------------------------------------- sheets


def item_progress(conn: psycopg.Connection, sheet_ids: list[str]) -> dict[tuple[str, str], ItemProgress]:
    """Counters per (sheet, item), over every device: the sheet can be started on one, finished on another."""
    rows = conn.execute(
        "SELECT sheet_id, item_id, "
        "  count(*) FILTER (WHERE mode = 'solo' AND correct), "
        "  count(*) FILTER (WHERE mode = 'solo' AND NOT correct), "
        "  (array_agg(rating ORDER BY id DESC) FILTER (WHERE rating IS NOT NULL))[1] "
        "FROM attempts WHERE sheet_id = ANY(%s) GROUP BY sheet_id, item_id",
        (sheet_ids,),
    ).fetchall()
    return {(r[0], r[1]): ItemProgress(solo_right=r[2], solo_wrong=r[3], rating=r[4]) for r in rows}


def sheet_progress(sheet: dict, counters: dict[tuple[str, str], ItemProgress]) -> dict[str, dict]:
    return {
        item["id"]: counters.get((sheet["id"], item["id"]), ItemProgress()).as_json(item["type"])
        for item in sheet["items"]
    }


@app.get("/api/sheets")
def list_sheets(device: Device = Depends(current_device), conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    """Sheets whose display date has come, most recent first, with the progress made on each."""
    rows = conn.execute(
        "SELECT id, subject, title, type, visible_from, due_by, body FROM sheets "
        "WHERE visible_from <= %s ORDER BY visible_from DESC, id LIMIT 60",
        (today(),),
    ).fetchall()
    counters = item_progress(conn, [r[0] for r in rows])
    listed = []
    for sheet_id, subject, title, kind, visible_from, due_by, body in rows:
        progress = sheet_progress(body, counters).values()
        listed.append({
            "id": sheet_id, "subject": subject, "title": title, "type": kind,
            "visible_from": visible_from, "due_by": due_by,
            "estimated_minutes": body["estimated_minutes"], "items": len(body["items"]),
            "answered": sum(1 for p in progress if p["solo_done"] or p["rating"]),
            "correct": sum(1 for p in progress if p["solo_right"] or p["rating"] == "good"),
            "to_review": sum(1 for p in progress if p["to_review"]),
        })
    return listed


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
    # Every device receives the parent guide: in together mode the parent reads it on the
    # tablet. The page shows it only after a tap.
    return _load_sheet(conn, sheet_id)


@app.get("/api/sheets/{sheet_id}/progress")
def get_progress(
    sheet_id: str, device: Device = Depends(current_device), conn: psycopg.Connection = Depends(get_conn)
) -> dict:
    sheet = _load_sheet(conn, sheet_id)
    return sheet_progress(sheet, item_progress(conn, [sheet_id]))


@app.post("/api/attempts", status_code=201)
def create_attempt(
    body: AttemptRequest, device: Device = Depends(current_device), conn: psycopg.Connection = Depends(get_conn)
) -> dict:
    """Stores an answer. The server corrects it again: it never trusts the browser's verdict."""
    sheet = _load_sheet(conn, body.sheet_id)
    item = next((i for i in sheet["items"] if i["id"] == body.item_id), None)
    if item is None:
        raise HTTPException(404, "unknown item")
    if body.rating is not None:
        correct = body.rating == "good"  # the parent's verdict wins over the typed answer
    else:
        correct = check(
            item["check"]["mode"], item["answer"], body.response,
            variants=item.get("variants"), tolerance=item["check"].get("tolerance", "0"),
        )
    conn.execute(
        "INSERT INTO attempts (sheet_id, item_id, device_id, mode, response, correct, rating, hints_used, "
        "client_id, answered_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (client_id) WHERE client_id IS NOT NULL DO NOTHING",
        (body.sheet_id, body.item_id, device.id, body.mode,
         None if body.response is None else Jsonb(body.response),
         correct, body.rating, body.hints_used, body.client_id, body.answered_at),
    )
    return {"correct": correct}


@app.post("/api/reports", status_code=201)
def create_report(
    body: ReportRequest, device: Device = Depends(current_device), conn: psycopg.Connection = Depends(get_conn)
) -> dict:
    _load_sheet(conn, body.sheet_id)
    conn.execute(
        "INSERT INTO reports (sheet_id, item_id, device_id, reason, comment, client_id) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (client_id) WHERE client_id IS NOT NULL DO NOTHING",
        (body.sheet_id, body.item_id, device.id, body.reason, body.comment, body.client_id),
    )
    return {"ok": True}


@app.post("/api/sheets/{sheet_id}/finish")
def finish_sheet(
    sheet_id: str,
    body: FinishRequest,
    device: Device = Depends(current_device),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    """The student finished the sheet alone: tell the parent, with a link to the first point to review."""
    sheet = _load_sheet(conn, sheet_id)
    progress = sheet_progress(sheet, item_progress(conn, [sheet_id]))
    score = sum(1 for p in progress.values() if p["solo_right"])
    review = [item["id"] for item in sheet["items"] if progress[item["id"]]["to_review"]]
    total = len(sheet["items"])
    inserted = conn.execute(
        "INSERT INTO sheet_finishes (client_id, sheet_id, device_id, score, total, to_review) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (client_id) DO NOTHING RETURNING client_id",
        (body.client_id, sheet_id, device.id, score, total, Jsonb(review)),
    ).fetchone()
    if inserted is not None:  # first time this run is reported: notify once
        points = f", {len(review)} point{'s' if len(review) > 1 else ''} à voir ensemble" if review else ""
        push.send(conn, {
            "title": f"{sheet['subject']} : fiche terminée",
            "body": f"Fini : {score}/{total}{points}",
            "url": f"/#/sheet/{sheet_id}/review" if review else "/",
            "tag": f"finish-{sheet_id}",
        }, roles=("parent",))
    return {"score": score, "total": total, "to_review": review}


# --------------------------------------------------------------- notifications


@app.get("/api/push/key")
def push_key(device: Device = Depends(current_device)) -> dict:
    return {"public_key": push.public_key()}


@app.post("/api/push/subscription", status_code=201)
def subscribe(
    body: PushSubscription, device: Device = Depends(current_device), conn: psycopg.Connection = Depends(get_conn)
) -> dict:
    conn.execute(
        "INSERT INTO push_subscriptions (endpoint, device_id, p256dh, auth) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (endpoint) DO UPDATE SET device_id = EXCLUDED.device_id, "
        "p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth",
        (body.endpoint, device.id, body.keys.p256dh, body.keys.auth),
    )
    return {"ok": True}


@app.post("/api/push/test")
def push_test(device: Device = Depends(current_device), conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """A notification to this device only, to check that it arrives."""
    return push.send(
        conn, {"title": "Révisions", "body": "Les notifications marchent sur cet appareil.", "url": "/"},
        device_id=device.id,
    )


def _check_cron_secret(authorization: str | None) -> None:
    """Vercel calls the scheduled task with `Authorization: Bearer <CRON_SECRET>`."""
    secret = os.environ.get("CRON_SECRET", "")
    expected = f"Bearer {secret}"
    if not secret or not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(401, "not the scheduled task")


@app.get("/api/cron/daily")
def daily(
    authorization: str | None = Header(default=None),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    """Once a day: announce today's sheets, and alert the parent when nothing was published lately."""
    _check_cron_secret(authorization)
    day = today()
    claimed = conn.execute(
        "INSERT INTO daily_runs (day) VALUES (%s) ON CONFLICT (day) DO NOTHING RETURNING day", (day,)
    ).fetchone()
    if claimed is None:
        return {"day": day, "skipped": "already done today"}

    details: dict = {}
    subjects = [r[0] for r in conn.execute(
        "SELECT subject FROM sheets WHERE visible_from = %s ORDER BY subject", (day,)
    ).fetchall()]
    if subjects:
        count = len(subjects)
        details["sheets"] = push.send(conn, {
            "title": "Révisions",
            "body": f"{count} fiche{'s' if count > 1 else ''} t'attend{'ent' if count > 1 else ''} : "
                    + ", ".join(dict.fromkeys(subjects)),
            "url": "/",
            "tag": f"sheets-{day}",
        })

    hours = int(os.environ.get("SKOOLATHOME_ABSENCE_HOURS", DEFAULT_ABSENCE_HOURS))
    last = conn.execute("SELECT max(published_at) FROM sheets").fetchone()[0]
    if hours > 0 and last is not None and day.weekday() in SCHOOL_DAYS:
        silence = datetime.now(SCHOOL_TIMEZONE) - last
        if silence > timedelta(hours=hours):
            details["absence"] = push.send(conn, {
                "title": "Révisions : rien de nouveau",
                "body": f"Aucune fiche publiée depuis {int(silence.total_seconds() // 3600)} h. "
                        "Le portable est-il éteint, ou le traitement du soir en échec ?",
                "url": "/",
                "tag": "absence",
            }, roles=("parent",))

    conn.execute("UPDATE daily_runs SET details = %s WHERE day = %s", (Jsonb(details), day))
    return {"day": day, **details}


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

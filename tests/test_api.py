"""The online API, with FastAPI's TestClient and a throw-away local database."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from skoolathome.api import MIGRATIONS_DIR
from skoolathome.api.app import app, save_sheet
from skoolathome.schema.sheet import Sheet
from skoolathome.store import db
from skoolathome.store.migrate import migrate

BOOTSTRAP = "bootstrap-code-for-tests-only"
SAMPLE = json.loads((Path(__file__).parent.parent / "examples" / "sheet-sample.json").read_text())


@pytest.fixture
def api_db(monkeypatch):
    name = f"skoolathome_api_test_{os.getpid()}"
    try:
        admin = psycopg.connect(db.database_url("postgres"), autocommit=True, connect_timeout=3)
    except (psycopg.OperationalError, FileNotFoundError) as error:
        pytest.skip(f"local Postgres unavailable: {error}")
    admin.execute(f"DROP DATABASE IF EXISTS {name}")
    admin.execute(f"CREATE DATABASE {name}")
    monkeypatch.setenv("SKOOLATHOME_API_DATABASE_URL", db.database_url(name))
    monkeypatch.setenv("SKOOLATHOME_BOOTSTRAP_CODE", BOOTSTRAP)
    try:
        with psycopg.connect(db.database_url(name), autocommit=True) as conn:
            migrate(conn, MIGRATIONS_DIR)
            yield conn
    finally:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()


def browser() -> TestClient:
    """A separate browser: its own cookie jar. https, so Secure cookies are sent back."""
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def parent(api_db) -> TestClient:
    client = browser()
    assert client.post("/api/pair", json={"code": BOOTSTRAP, "label": "Téléphone"}).status_code == 200
    return client


def pair_child(parent: TestClient) -> TestClient:
    created = parent.post("/api/pairing-tokens", json={"role": "child", "label": "iPad"}).json()
    token = created["url"].split("#/pair/")[1]
    child = browser()
    assert child.post("/api/pair", json={"token": token}).status_code == 200
    return child


def publish(conn, visible_from: date | None = None, sheet_id: str = "s1") -> None:
    data = dict(SAMPLE, id=sheet_id, visible_from=(visible_from or date.today() - timedelta(days=1)).isoformat())
    save_sheet(conn, Sheet.model_validate(data))


def test_without_session_everything_is_401(api_db):
    client = browser()
    for path in ("/api/me", "/api/sheets", "/api/sheets/s1", "/api/devices"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/attempts", json={"sheet_id": "s1", "item_id": "q1", "response": "x"}).status_code == 401


def test_bootstrap_pairs_the_first_parent_only_once(api_db):
    first = browser()
    assert first.post("/api/pair", json={"code": "wrong-code-wrong-code"}).status_code == 403
    response = first.post("/api/pair", json={"code": BOOTSTRAP})
    assert response.status_code == 200 and response.json()["role"] == "parent"
    assert first.get("/api/me").json()["role"] == "parent"
    assert browser().post("/api/pair", json={"code": BOOTSTRAP}).status_code == 403


def test_session_cookie_is_not_readable_by_scripts(api_db):
    response = browser().post("/api/pair", json={"code": BOOTSTRAP})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    stored = api_db.execute("SELECT session_hash FROM devices").fetchone()[0]
    assert stored not in response.headers["set-cookie"]  # only a hash is stored


def test_pairing_token_works_once(parent):
    created = parent.post("/api/pairing-tokens", json={"role": "child", "label": "iPad"}).json()
    assert created["qr_svg"].startswith("<svg") and created["expires_in_minutes"] == 10
    token = created["url"].split("#/pair/")[1]
    child = browser()
    assert child.post("/api/pair", json={"token": token}).json() == {
        "id": child.get("/api/me").json()["id"], "role": "child", "label": "iPad"}
    assert browser().post("/api/pair", json={"token": token}).status_code == 403


def test_the_pairing_code_can_be_typed_by_hand(parent):
    created = parent.post("/api/pairing-tokens", json={"role": "parent", "label": "Chrome du portable"}).json()
    code = created["code"]
    assert len(code) == 9 and code[4] == "-" and created["url"].endswith(f"#/pair/{code}")
    assert not set(code.replace("-", "")) & set("01OI")
    typed = f" {code[:4].lower()} {code[5:].lower()} "  # as someone types it
    laptop = browser()
    assert laptop.post("/api/pair", json={"token": typed}).json()["role"] == "parent"
    assert laptop.get("/api/me").json()["label"] == "Chrome du portable"


def test_expired_pairing_token_is_refused(parent, api_db):
    created = parent.post("/api/pairing-tokens", json={"role": "child", "label": "iPad"}).json()
    api_db.execute("UPDATE pairing_tokens SET expires_at = now() - interval '1 second'")
    assert browser().post("/api/pair", json={"token": created["url"].split("#/pair/")[1]}).status_code == 403


def test_child_cannot_use_parent_routes(parent):
    child = pair_child(parent)
    assert child.get("/api/devices").status_code == 403
    assert child.post("/api/pairing-tokens", json={"role": "parent", "label": "x"}).status_code == 403
    assert child.delete("/api/devices/anything").status_code == 403


def test_revoked_device_loses_access_at_once(parent):
    child = pair_child(parent)
    assert child.get("/api/sheets").status_code == 200
    devices = parent.get("/api/devices").json()
    assert [d["label"] for d in devices] == ["Téléphone", "iPad"]
    child_id = next(d["id"] for d in devices if d["role"] == "child")
    assert parent.delete(f"/api/devices/{child_id}").status_code == 204
    assert child.get("/api/sheets").status_code == 401
    own_id = next(d["id"] for d in devices if d["is_this_device"])
    assert parent.delete(f"/api/devices/{own_id}").status_code == 409


def test_sheet_is_listed_only_from_its_display_date(parent, api_db):
    publish(api_db, sheet_id="today")
    publish(api_db, visible_from=date.today() + timedelta(days=3), sheet_id="later")
    listed = parent.get("/api/sheets").json()
    assert [s["id"] for s in listed] == ["today"]
    assert listed[0]["items"] == 7 and listed[0]["answered"] == 0
    assert parent.get("/api/sheets/later").status_code == 404


def test_every_device_receives_the_parent_guide(parent, api_db):
    """Together mode happens on the tablet: the page hides the guide behind a tap."""
    publish(api_db)
    child = pair_child(parent)
    assert any(item.get("parent_guide") for item in child.get("/api/sheets/s1").json()["items"])


def test_attempt_is_corrected_by_the_server_and_stored(parent, api_db):
    publish(api_db)
    child = pair_child(parent)
    send = lambda item, response: child.post(
        "/api/attempts", json={"sheet_id": "s1", "item_id": item, "response": response}).json()["correct"]
    assert send("q3", "22/24") is True          # equivalent fraction
    assert send("q2", "3") is False             # -3 expected
    assert send("q4", "  Le Denominateur ") is True
    assert send("q5", ["5", "8"]) is True
    assert send("q6", ["0,5", "0,75", "0,25"]) is False
    assert send("q7", True) is True             # open question: the student's own verdict
    assert api_db.execute("SELECT count(*), count(*) FILTER (WHERE correct) FROM attempts").fetchone() == (6, 4)
    listed = child.get("/api/sheets").json()[0]
    # q2 and q6 were missed once: they can still be tried again, so they are not done yet
    assert (listed["answered"], listed["correct"], listed["to_review"]) == (4, 4, 1)
    assert child.post("/api/attempts", json={"sheet_id": "s1", "item_id": "nope", "response": "x"}).status_code == 404


def test_report_is_stored(parent, api_db):
    publish(api_db)
    child = pair_child(parent)
    ok = child.post("/api/reports", json={"sheet_id": "s1", "item_id": "q1", "reason": "not_covered"})
    assert ok.status_code == 201
    assert child.post("/api/reports", json={"sheet_id": "s1", "reason": "boring"}).status_code == 422
    assert api_db.execute("SELECT reason FROM reports").fetchall() == [("not_covered",)]


def test_sample_sheet_has_every_item_type():
    sheet = Sheet.model_validate(SAMPLE)
    assert {item.type for item in sheet.items} == {
        "choice", "number", "fraction", "short_answer", "fill_blank", "matching", "open"}


@pytest.mark.parametrize(
    "change",
    [
        {"check": {"mode": "number"}},                  # wrong mode for a choice item
        {"answer": "9/9"},                              # answer not among the choices
        {"choices": ["3/6"]},                           # a single choice
    ],
)
def test_inconsistent_item_is_refused(change):
    data = json.loads(json.dumps(SAMPLE))
    data["items"][0].update(change)
    with pytest.raises(ValueError):
        Sheet.model_validate(data)

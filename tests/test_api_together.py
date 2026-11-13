"""T10 and T11 on the API: together mode, progress, offline retries, notifications, daily task."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from skoolathome.api import app as app_module
from skoolathome.api import push
from skoolathome.progress import ItemProgress, solo_done, to_review
from test_api import BOOTSTRAP, browser, pair_child, publish  # noqa: F401  (fixtures below)
from test_api import api_db, parent  # noqa: F401


def attempt(client, item, **fields):
    body = {"sheet_id": "s1", "item_id": item, **fields}
    return client.post("/api/attempts", json=body)


# ------------------------------------------------------------- progress rules


@pytest.mark.parametrize(
    ("progress", "item_type", "done", "review"),
    [
        (ItemProgress(), "number", False, False),
        (ItemProgress(solo_wrong=1), "number", False, False),          # one more try allowed
        (ItemProgress(solo_wrong=1, solo_right=1), "number", True, False),
        (ItemProgress(solo_wrong=2), "number", True, True),            # missed twice
        (ItemProgress(solo_wrong=2, rating="partial"), "number", True, False),  # seen together
        (ItemProgress(solo_right=1), "open", True, True),              # open: always seen together
        (ItemProgress(solo_right=1, rating="good"), "open", True, False),
        (ItemProgress(rating="bad"), "choice", False, False),          # done together only
    ],
)
def test_progress_rules(progress, item_type, done, review):
    assert solo_done(progress, item_type) is done
    assert to_review(progress, item_type) is review


# -------------------------------------------------------------- together mode


def test_together_rating_is_the_verdict(parent, api_db):
    publish(api_db)
    assert attempt(parent, "q2", mode="together", rating="good").json() == {"correct": True}
    # a typed answer is corrected, but the parent's rating wins
    assert attempt(parent, "q3", mode="together", response="1/2", rating="good").json() == {"correct": True}
    assert attempt(parent, "q4", mode="together", rating="partial").json() == {"correct": False}
    assert attempt(parent, "q4", mode="solo", rating="good").status_code == 422
    assert attempt(parent, "q4", mode="together").status_code == 422  # nothing to store


def test_a_sheet_started_alone_continues_together(parent, api_db):
    publish(api_db)
    child = pair_child(parent)
    attempt(child, "q2", response="3")
    attempt(child, "q2", response="4")       # missed twice
    attempt(child, "q7", response=True)       # open question
    attempt(child, "q1", response="3/6")
    progress = parent.get("/api/sheets/s1/progress").json()
    assert progress["q1"] == {"solo_right": 1, "solo_wrong": 0, "rating": None, "solo_done": True, "to_review": False}
    assert progress["q2"]["to_review"] and progress["q7"]["to_review"]
    # the parent picks up on another device, in together mode
    attempt(parent, "q2", mode="together", rating="partial")
    progress = child.get("/api/sheets/s1/progress").json()
    assert not progress["q2"]["to_review"] and progress["q2"]["rating"] == "partial"
    assert progress["q7"]["to_review"]


def test_answer_sent_twice_is_stored_once(parent, api_db):
    publish(api_db)
    for _ in range(2):
        response = attempt(parent, "q1", response="3/6", client_id="c-123", answered_at="2026-11-13T17:02:00+01:00")
        assert response.status_code == 201
    rows = api_db.execute("SELECT answered_at::text FROM attempts").fetchall()
    assert rows == [("2026-11-13 16:02:00+00",)]
    for _ in range(2):
        parent.post("/api/reports", json={"sheet_id": "s1", "item_id": "q1", "reason": "unclear", "client_id": "r-1"})
    assert api_db.execute("SELECT count(*) FROM reports").fetchone()[0] == 1


# --------------------------------------------------------------- notifications


@pytest.fixture
def sent(monkeypatch):
    """Capture what would be sent to the push services."""
    monkeypatch.setenv("SKOOLATHOME_VAPID_PUBLIC_KEY", "public")
    monkeypatch.setenv("SKOOLATHOME_VAPID_PRIVATE_KEY", "private")
    calls: list[dict] = []

    def fake_webpush(subscription_info, data, **kwargs):
        calls.append({"endpoint": subscription_info["endpoint"], "data": data, **kwargs})

    monkeypatch.setattr(push, "webpush", fake_webpush)
    return calls


def subscribe(client, name):
    body = {"endpoint": f"https://push.example/{name}", "keys": {"p256dh": "p", "auth": "a"}, "expirationTime": None}
    assert client.post("/api/push/subscription", json=body).status_code == 201


def test_vapid_keys_have_the_browser_format():
    public, private = push.generate_vapid_keys()
    assert len(public) == 87 and len(private) == 43  # 65 and 32 bytes, base64url without padding


def test_finishing_alone_notifies_the_parent_once(parent, api_db, sent):
    publish(api_db)
    child = pair_child(parent)
    subscribe(parent, "phone")
    subscribe(child, "ipad")
    attempt(child, "q1", response="3/6")
    attempt(child, "q2", response="3")
    attempt(child, "q2", response="4")
    for _ in range(2):  # the browser may send it again after a network failure
        result = child.post("/api/sheets/s1/finish", json={"client_id": "finish-0001"}).json()
    assert result == {"score": 1, "total": 7, "to_review": ["q2"]}
    assert len(sent) == 1 and sent[0]["endpoint"] == "https://push.example/phone"
    assert '"Fini : 1/7, 1 point à voir ensemble"' in sent[0]["data"]
    assert '"/#/sheet/s1/review"' in sent[0]["data"]


def test_revoked_device_gets_nothing_and_dead_subscriptions_are_removed(parent, api_db, sent, monkeypatch):
    child = pair_child(parent)
    subscribe(child, "ipad")
    child_id = child.get("/api/me").json()["id"]
    parent.delete(f"/api/devices/{child_id}")
    assert push.send(api_db, {"title": "t"}) == {"sent": 0, "failed": 0, "removed": 0}

    subscribe(parent, "gone")

    class Gone:
        status_code = 410

    def gone(**kwargs):
        raise push.WebPushException("gone", response=Gone())

    monkeypatch.setattr(push, "webpush", gone)
    assert push.send(api_db, {"title": "t"})["removed"] == 1
    assert api_db.execute("SELECT count(*) FROM push_subscriptions").fetchone()[0] == 0


def test_test_notification_goes_to_this_device_only(parent, api_db, sent):
    child = pair_child(parent)
    subscribe(parent, "phone")
    subscribe(child, "ipad")
    assert child.post("/api/push/test").json()["sent"] == 1
    assert sent[0]["endpoint"] == "https://push.example/ipad"


# ----------------------------------------------------------------- daily task


MONDAY = date(2026, 11, 16)


@pytest.fixture
def cron(monkeypatch, sent):
    monkeypatch.setenv("CRON_SECRET", "cron-secret")
    monkeypatch.setattr(app_module, "today", lambda: MONDAY)
    return {"authorization": "Bearer cron-secret"}


def test_daily_task_needs_the_cron_secret(api_db, cron):
    client = browser()
    assert client.get("/api/cron/daily").status_code == 401
    assert client.get("/api/cron/daily", headers={"authorization": "Bearer wrong"}).status_code == 401


def test_daily_task_announces_today_sheets_once(parent, api_db, cron, sent):
    subscribe(parent, "phone")
    publish(api_db, visible_from=MONDAY, sheet_id="today")
    publish(api_db, visible_from=MONDAY - timedelta(days=1), sheet_id="yesterday")
    client = browser()
    first = client.get("/api/cron/daily", headers=cron).json()
    assert first["sheets"]["sent"] == 1 and "absence" not in first
    assert '1 fiche t\'attend : Mathématiques' in sent[0]["data"]
    assert client.get("/api/cron/daily", headers=cron).json()["skipped"] == "already done today"
    assert len(sent) == 1


def test_absence_alert_after_36_hours_on_a_school_day(parent, api_db, cron, sent):
    subscribe(parent, "phone")
    child = pair_child(parent)
    subscribe(child, "ipad")
    publish(api_db, visible_from=MONDAY - timedelta(days=3))
    api_db.execute("UPDATE sheets SET published_at = now() - interval '40 hours'")
    result = browser().get("/api/cron/daily", headers=cron).json()
    assert result["absence"]["sent"] == 1  # the parent only
    assert "Aucune fiche publiée depuis 40 h" in sent[0]["data"]


def test_no_absence_alert_on_saturday(parent, api_db, cron, sent, monkeypatch):
    monkeypatch.setattr(app_module, "today", lambda: MONDAY - timedelta(days=2))
    subscribe(parent, "phone")
    publish(api_db, visible_from=MONDAY - timedelta(days=5))
    api_db.execute("UPDATE sheets SET published_at = now() - interval '72 hours'")
    assert "absence" not in browser().get("/api/cron/daily", headers=cron).json()

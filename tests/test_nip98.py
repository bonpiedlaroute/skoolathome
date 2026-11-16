"""T13: NIP-98 on the API. A signed batch is published; every forged or replayed request is refused."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from nostr_sdk import Event, EventBuilder, Keys, Kind, Tag, Timestamp

from skoolathome.api import app as app_module
from skoolathome.api.app import app
from skoolathome.publish.client import ApiClient, ApiError
from skoolathome.publish.nip46 import authorization_header, nip98_tags
from skoolathome.schema.explanation import ExplanationAnswer
from skoolathome.schema.instruction import InstructionUpdate, PublishedRule
from skoolathome.schema.request import RequestUpdate
from skoolathome.schema.review import WeeklyReview
from skoolathome.schema.sheet import Sheet
from test_api import SAMPLE, api_db, browser, pair_child, parent  # noqa: F401  (fixtures)

BASE = "https://testserver"
AGENT = Keys.generate()


class LocalSigner:
    """Stands in for the bunker: same interface, a key in memory."""

    def __init__(self, keys: Keys = AGENT, created_at: int | None = None, kind: int = 27235) -> None:
        self.keys, self.created_at, self.kind, self.signatures = keys, created_at, kind, 0

    def sign(self, kind: int, content: str, tags: list[list[str]]) -> Event:
        builder = EventBuilder(Kind(self.kind), content).tags([Tag.parse(t) for t in tags])
        if self.created_at is not None:
            builder = builder.custom_created_at(Timestamp.from_secs(self.created_at))
        self.signatures += 1
        return builder.finalize(self.keys)


@pytest.fixture
def agent(api_db, monkeypatch):
    monkeypatch.setenv("SKOOLATHOME_AGENT_PUBKEY", AGENT.public_key().to_bech32())
    monkeypatch.setattr(app_module, "FEEDBACK_SETTLE", timedelta(0))
    return ApiClient(BASE, LocalSigner(), http=TestClient(app, base_url=BASE))


def sheet(sheet_id: str = "b1", days: int = -1) -> Sheet:
    return Sheet.model_validate(dict(SAMPLE, id=sheet_id, visible_from=(date.today() + timedelta(days=days)).isoformat()))


def signed_post(body: bytes, *, url: str = f"{BASE}/api/batches", method: str = "POST", signer=None,
                sent_body: bytes | None = None, header: str | None = None):
    """A raw signed request, to tamper with one part at a time."""
    event = (signer or LocalSigner()).sign(27235, "", nip98_tags(url, method, body))
    client = TestClient(app, base_url=BASE)
    return client.post("/api/batches", content=sent_body if sent_body is not None else body,
                       headers={"Authorization": header or authorization_header(event),
                                "Content-Type": "application/json"})


def batch_body(*sheets: Sheet) -> bytes:
    return json.dumps({"sheets": [s.model_dump(mode="json") for s in sheets]}).encode()


# ------------------------------------------------------------------- accepted


def test_a_signed_batch_appears_on_the_tablet(agent, parent):
    assert agent.publish([sheet("b1"), sheet("b2", days=+1)]) == ["b1", "b2"]
    listed = [s["id"] for s in parent.get("/api/sheets").json()]
    assert "b1" in listed and "b2" not in listed  # b2 shows from tomorrow
    assert agent.signer.signatures == 1


def test_feedback_returns_what_came_back_once(agent, parent, api_db):
    agent.publish([sheet("b1")])
    child = pair_child(parent)
    child.post("/api/attempts", json={"sheet_id": "b1", "item_id": "q1", "response": "3/6"})
    child.post("/api/reports", json={"sheet_id": "b1", "item_id": "q2", "reason": "unclear", "comment": "?"})
    child.post("/api/sheets/b1/finish", json={"client_id": "finish-0001"})

    first = agent.feedback(0)
    assert [(a["item_id"], a["role"], a["correct"]) for a in first["attempts"]] == [("q1", "child", True)]
    assert [(r["item_id"], r["reason"]) for r in first["reports"]] == [("q2", "unclear")]
    assert [f["sheet_id"] for f in first["finishes"]] == ["b1"]

    again = agent.feedback(first["until"])
    assert again["attempts"] == again["reports"] == again["finishes"] == []
    parent.post("/api/attempts", json={"sheet_id": "b1", "item_id": "q2", "mode": "together", "rating": "good"})
    latest = agent.feedback(first["until"])
    assert [(a["item_id"], a["role"], a["rating"]) for a in latest["attempts"]] == [("q2", "parent", "good")]


def test_rows_still_settling_wait_for_the_next_call(agent, parent, monkeypatch):
    monkeypatch.setattr(app_module, "FEEDBACK_SETTLE", timedelta(minutes=1))
    agent.publish([sheet("b1")])
    parent.post("/api/attempts", json={"sheet_id": "b1", "item_id": "q1", "response": "3/6"})
    assert agent.feedback(0)["attempts"] == []


# ------------------------------------------------------------------- refused


def test_a_replayed_request_is_refused(agent):
    body = batch_body(sheet())
    event = LocalSigner().sign(27235, "", nip98_tags(f"{BASE}/api/batches", "POST", body))
    headers = {"Authorization": authorization_header(event), "Content-Type": "application/json"}
    client = TestClient(app, base_url=BASE)
    assert client.post("/api/batches", content=body, headers=headers).status_code == 201
    second = client.post("/api/batches", content=body, headers=headers)
    assert second.status_code == 401 and second.json()["detail"] == "nip98: replayed"


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("modified body", "wrong payload"),
        ("other key", "unknown key"),
        ("too old", "expired"),
        ("from the future", "expired"),
        ("other url", "wrong url"),
        ("other method", "wrong method"),
        ("other kind", "wrong kind"),
        ("forged signature", "bad signature"),
        ("no header", "missing"),
        ("bearer token", "missing"),
        ("not base64", "malformed"),
    ],
)
def test_forged_requests_are_refused(agent, api_db, case, reason):
    body = batch_body(sheet())
    now = int(time.time())
    if case == "modified body":
        response = signed_post(body, sent_body=batch_body(sheet("other")))
    elif case == "other key":
        response = signed_post(body, signer=LocalSigner(Keys.generate()))
    elif case == "too old":
        response = signed_post(body, signer=LocalSigner(created_at=now - 120))
    elif case == "from the future":
        response = signed_post(body, signer=LocalSigner(created_at=now + 120))
    elif case == "other url":
        response = signed_post(body, url=f"{BASE}/api/feedback")
    elif case == "other method":
        response = signed_post(body, method="PUT")
    elif case == "other kind":
        response = signed_post(body, signer=LocalSigner(kind=1))
    elif case == "forged signature":
        event = json.loads(LocalSigner().sign(27235, "", nip98_tags(f"{BASE}/api/batches", "POST", body)).as_json())
        event["tags"][0][1] = f"{BASE}/api/batches?x=1"  # the signature no longer matches
        response = signed_post(body, header="Nostr " + base64.b64encode(json.dumps(event).encode()).decode())
    elif case == "no header":
        response = TestClient(app, base_url=BASE).post("/api/batches", content=body)
    elif case == "bearer token":
        response = signed_post(body, header="Bearer something")
    else:
        response = signed_post(body, header="Nostr ###")
    assert response.status_code == 401
    assert response.json()["detail"] == f"nip98: {reason}"
    assert api_db.execute("SELECT count(*) FROM sheets").fetchone()[0] == 0


def test_feedback_needs_a_signature_for_its_own_url(agent):
    client = TestClient(app, base_url=BASE)
    event = LocalSigner().sign(27235, "", nip98_tags(f"{BASE}/api/feedback?since=0", "GET"))
    headers = {"Authorization": authorization_header(event)}
    assert client.get("/api/feedback?since=0", headers=headers).status_code == 200
    event = LocalSigner().sign(27235, "", nip98_tags(f"{BASE}/api/feedback?since=0", "GET"))
    stolen = client.get("/api/feedback?since=5", headers={"Authorization": authorization_header(event)})
    assert stolen.status_code == 401 and stolen.json()["detail"] == "nip98: wrong url"
    assert client.get("/api/feedback").status_code == 401


def test_the_url_seen_behind_the_proxy(agent):
    """Vercel forwards the public scheme and host in headers."""
    url = "https://skoolathome.example/api/batches"
    body = batch_body(sheet())
    event = LocalSigner().sign(27235, "", nip98_tags(url, "POST", body))
    response = TestClient(app, base_url="http://internal").post(
        "/api/batches", content=body,
        headers={"Authorization": authorization_header(event), "Content-Type": "application/json",
                 "x-forwarded-proto": "https", "x-forwarded-host": "skoolathome.example"},
    )
    assert response.status_code == 201


def test_without_the_agent_key_nothing_is_accepted(api_db, monkeypatch):
    monkeypatch.delenv("SKOOLATHOME_AGENT_PUBKEY", raising=False)
    client = ApiClient(BASE, LocalSigner(), http=TestClient(app, base_url=BASE))
    with pytest.raises(ApiError) as refused:
        client.publish([sheet()])
    assert refused.value.status == 503


def test_an_invalid_sheet_is_refused_after_the_signature(agent, api_db):
    data = json.loads(batch_body(sheet()))
    data["sheets"][0]["items"] = []
    body = json.dumps(data).encode()
    response = signed_post(body)
    assert response.status_code == 422
    assert api_db.execute("SELECT count(*) FROM sheets").fetchone()[0] == 0


REPORT = {"run_date": "2026-11-13", "status": "ok", "title": "Révisions : soirée OK",
          "body": "OK : 3 fiches, contrôle jeudi (Mathématiques). RAM max 14,2/30,8 Go, swap 2,1 Go.",
          "details": {"abstentions": []}}


def test_the_evening_report_is_kept_and_pushed_to_the_parents(agent, api_db, monkeypatch):
    sent = []
    monkeypatch.setattr(app_module.push, "send", lambda conn, payload, **kw: sent.append((payload, kw)) or {"sent": 1})
    assert agent.report(REPORT) == {"push": {"sent": 1}}
    agent.report({**REPORT, "status": "ko", "title": "Révisions : soirée KO", "body": "KO : collecte en échec."})
    assert api_db.execute("SELECT status, title FROM evening_reports").fetchall() == [("ko", "Révisions : soirée KO")]
    payload, options = sent[0]
    assert payload["title"] == "Révisions : soirée OK" and payload["tag"] == "evening-2026-11-13"
    assert options == {"roles": ("parent",)}


def test_an_unsigned_report_is_refused(api_db):
    response = TestClient(app, base_url=BASE).post("/api/evening-reports", json=REPORT)
    assert response.status_code in (401, 503)


def test_payload_hash_is_the_sha256_of_the_body():
    body = batch_body(sheet())
    assert nip98_tags("u", "POST", body)[2] == ["payload", hashlib.sha256(body).hexdigest()]


# ------------------------------------------------------------- the parent's requests


def test_a_request_goes_to_the_laptop_and_comes_back_answered(agent, parent, api_db, monkeypatch):
    pushed = []
    monkeypatch.setattr(app_module.push, "send", lambda conn, payload, **kw: pushed.append(payload) or {"sent": 1})
    created = parent.post("/api/requests", json={"subject": "MATHS", "kind": "revision", "focus": "les fractions"})
    assert created.status_code == 201 and created.json()["status"] == "pending"
    request_id = created.json()["id"]
    assert pair_child(parent).post("/api/requests", json={"subject": "MATHS"}).status_code == 403  # parents only

    [picked] = agent.feedback(0)["requests"]
    assert (picked["id"], picked["subject"], picked["kind"], picked["focus"]) == (request_id, "MATHS", "revision", "les fractions")

    answer = RequestUpdate(id=request_id, status="done", sheet_id="b1")
    assert agent.publish([sheet("b1")], [answer]) == ["b1"]
    [listed] = parent.get("/api/requests").json()
    assert (listed["status"], listed["sheet_id"]) == ("done", "b1")
    assert pushed[-1]["title"] == "Révisions : fiche demandée prête" and pushed[-1]["url"] == "/#/sheet/b1"

    other = parent.post("/api/requests", json={"subject": "SVT", "kind": "extra"}).json()["id"]
    agent.publish([], [RequestUpdate(id=other, status="abstained", reason="Rien en SVT dans le cahier de texte.")])
    assert "Rien en SVT" in parent.get("/api/requests").json()[0]["reason"]


def test_an_empty_batch_is_refused(agent):
    with pytest.raises(ApiError) as refused:
        agent.publish([])
    assert refused.value.status == 422


def test_the_laptop_can_make_a_pairing_code(agent, api_db):
    made = agent.pairing_code("parent", "iPhone")
    assert made["expires_in_minutes"] == 60 and made["url"] == f"{BASE}/#/pair/{made['code']}"
    phone = TestClient(app, base_url=BASE)
    assert phone.post("/api/pair", json={"token": made["code"]}).json()["role"] == "parent"
    unsigned = TestClient(app, base_url=BASE).post("/api/agent/pairing-tokens", json={"role": "parent", "label": "x"})
    assert unsigned.status_code in (401, 503)


# ---------------------------------------------------------------- instructions (T21)

PROPOSAL = {"id": "hi-ge-before-quick-quiz", "subject": "HI-GE", "trigger": "before_each_lesson",
            "scope": "previous_lesson", "format": "quick_quiz", "target_minutes": 5}


def test_an_instruction_is_proposed_validated_then_saved(agent, parent, api_db, monkeypatch):
    pushed = []
    monkeypatch.setattr(app_module.push, "send", lambda conn, payload, **kw: pushed.append(payload) or {"sent": 1})
    created = parent.post("/api/instructions", json={"text": "Le prof d'histoire interroge à chaque cours."})
    assert created.status_code == 201 and created.json()["status"] == "pending"
    instruction = created.json()["id"]
    reply = f"/api/instructions/{instruction}/reply"
    assert parent.post(reply, json={"kind": "approve"}).status_code == 409  # nothing proposed yet
    assert pair_child(parent).post("/api/instructions", json={"text": "x"}).status_code == 403  # parents only

    [picked] = agent.feedback(0)["instructions"]
    assert (picked["id"], picked["rule"]) == (instruction, None)
    rules = [PublishedRule(id="daily", version=1, status="active", summary="Dans toutes les matières…",
                           body={"id": "daily", "trigger": "after_each_lesson"})]
    agent.publish([], instructions=[InstructionUpdate(id=instruction, status="proposed", proposal=PROPOSAL,
                                                      summary="J'ai compris : En Histoire-géographie…")], rules=rules)
    [listed] = parent.get("/api/instructions").json()
    assert (listed["status"], listed["waiting"], listed["proposal"]["subject"]) == ("proposed", False, "HI-GE")
    assert pushed[-1]["title"] == "Révisions : consigne à valider" and pushed[-1]["url"] == "/#/instructions"
    assert [r["id"] for r in parent.get("/api/rules").json()] == ["daily"]

    invalid = {**PROPOSAL, "subject": None}  # a rule before each lesson needs its subject
    assert parent.post(reply, json={"kind": "edit", "rule": invalid}).status_code == 422
    assert parent.post(reply, json={"kind": "edit", "rule": {**PROPOSAL, "target_minutes": 8}}).json()["waiting"]
    assert parent.post(reply, json={"kind": "approve"}).status_code == 409  # one reply per pause
    [sent_back] = agent.feedback(0)["instruction_replies"]
    assert (sent_back["instruction_id"], sent_back["kind"], sent_back["rule"]["target_minutes"]) == (instruction, "edit", 8)

    agent.publish([], instructions=[InstructionUpdate(id=instruction, status="saved", rule_id=PROPOSAL["id"],
                                                      rule_version=1, summary="Règle enregistrée : …")])
    [listed] = parent.get("/api/instructions").json()
    assert (listed["status"], listed["waiting"], listed["rule_version"]) == ("saved", False, 1)
    assert [r["id"] for r in parent.get("/api/rules").json()] == ["daily"]  # no list sent: kept


def test_a_rule_written_in_the_form_is_an_instruction_too(parent):
    created = parent.post("/api/instructions", json={"rule": PROPOSAL})
    assert created.status_code == 201 and created.json()["text"] == ""
    assert parent.post("/api/instructions", json={"text": "", "rule": None}).status_code == 422



# ------------------------------------------------------------------ traces (T22)


def test_answers_and_reports_carry_the_trace_of_their_sheet(agent, parent):
    traced = Sheet.model_validate({**sheet("b1").model_dump(mode="json"), "trace_id": "a" * 32})
    agent.publish([traced, sheet("b2")])
    child = pair_child(parent)
    child.post("/api/attempts", json={"sheet_id": "b1", "item_id": "q1", "response": "3/6", "trace_id": "b" * 32})
    child.post("/api/attempts", json={"sheet_id": "b2", "item_id": "q1", "response": "3/6", "trace_id": "c" * 32})
    child.post("/api/reports", json={"sheet_id": "b1", "item_id": "q2", "reason": "unclear"})
    child.post("/api/sheets/b1/finish", json={"client_id": "finish-0002"})
    data = agent.feedback(0)
    # The sheet's own trace wins over the browser's copy; without one, the browser's is kept.
    assert [a["trace_id"] for a in data["attempts"]] == ["a" * 32, "c" * 32]
    assert data["reports"][0]["trace_id"] == "a" * 32 and data["finishes"][0]["trace_id"] == "a" * 32



# ------------------------------------------------------------ weekly review (T24)


def test_the_weekly_review_is_pushed_and_its_sheets_annotated(agent, parent, monkeypatch):
    pushed = []
    monkeypatch.setattr(app_module.push, "send", lambda conn, payload, **kw: pushed.append(payload) or {"sent": 1})
    agent.publish([sheet("b1")])
    review = WeeklyReview(week="2026-W46", start="2026-11-07", end="2026-11-13", title="Révisions : bilan de la semaine",
                          body="Rien d'anormal. 8 fiches.", to_annotate=[{"id": "b1", "subject": "Maths", "title": "Fractions"}],
                          indicators=[{"name": "reports", "label": "Signalements", "value": "0"}])
    agent.publish([], review=review)
    assert pushed[-1]["url"] == "/#/review/2026-W46" and pushed[-1]["title"] == "Révisions : bilan de la semaine"
    shown = parent.get("/api/weekly-reviews/latest").json()
    assert shown["week"] == "2026-W46" and shown["annotated"] == []
    assert [r["week"] for r in parent.get("/api/weekly-reviews").json()] == ["2026-W46"]

    verdict = {"sheet_id": "b1", "items": [{"item_id": "q1", "ok": True}, {"item_id": "q2", "ok": False, "problem": "answer_correct"}]}
    assert pair_child(parent).post("/api/annotations", json=verdict).status_code == 403  # parents only
    assert parent.post("/api/annotations", json={"sheet_id": "b1", "items": [{"item_id": "q9", "ok": True}]}).status_code == 422
    assert parent.post("/api/annotations", json=verdict).json() == {"saved": 2}
    assert parent.get("/api/weekly-reviews/2026-W46").json()["annotated"] == ["b1"]
    changed = {"sheet_id": "b1", "items": [{"item_id": "q2", "ok": True}]}  # the parent changes their mind
    parent.post("/api/annotations", json=changed)
    found = sorted((a["item_id"], a["ok"], a["problem"]) for a in agent.feedback(0)["annotations"])
    assert found == [("q1", True, None), ("q2", True, None)]



# ------------------------------------------------------- « Je n'ai pas compris » (T29)


def test_an_explanation_is_asked_answered_then_rated(agent, parent, monkeypatch):
    pushed = []
    monkeypatch.setattr(app_module.push, "send", lambda conn, payload, **kw: pushed.append((payload, kw)) or {"sent": 1})
    agent.publish([sheet("b1")])
    child = pair_child(parent)
    asked = child.post("/api/explanations", json={"sheet_id": "b1", "item_id": "q1", "question": "pourquoi 1/2 ?"})
    assert asked.status_code == 201 and asked.json()["status"] == "pending"
    again = child.post("/api/explanations", json={"sheet_id": "b1", "item_id": "q1"})
    assert again.json()["id"] == asked.json()["id"]  # asked twice: one request
    assert child.post("/api/explanations", json={"sheet_id": "b1", "item_id": "q9"}).status_code == 404
    explanation_id = asked.json()["id"]
    assert child.post(f"/api/explanations/{explanation_id}/helped", json={"helped": True}).status_code == 409

    [waiting] = agent.pending_explanations()
    assert (waiting["id"], waiting["question"]) == (explanation_id, "pourquoi 1/2 ?")
    agent.answer_explanation(explanation_id, ExplanationAnswer(status="done", explanation={
        "text": "3/6 et 1/2 sont égales.", "quote": {"source_id": "lesson:x", "excerpt": "on divise"},
        "source_label": "ton cours du lundi 9/11"}))
    payload, options = pushed[-1]
    assert payload["url"] == f"/#/explanation/{explanation_id}" and payload["title"] == "Révisions : explication prête"
    assert options == {}  # every device: the child asked
    assert agent.pending_explanations() == []
    shown = child.get(f"/api/explanations/{explanation_id}").json()
    assert shown["status"] == "done" and shown["explanation"]["source_label"] == "ton cours du lundi 9/11"

    assert child.post(f"/api/explanations/{explanation_id}/helped", json={"helped": False}).json()["helped"] is False
    [rated] = agent.feedback(0)["explanations"]
    assert (rated["id"], rated["helped"]) == (explanation_id, False)
    assert [e["id"] for e in child.get("/api/explanations?sheet_id=b1").json()] == [explanation_id]



# ---------------------------------------------------------------- proposals (T28)


def test_a_proposal_of_the_agent_is_created_by_the_batch_and_refused_with_a_reason(agent, parent, monkeypatch):
    pushed = []
    monkeypatch.setattr(app_module.push, "send", lambda conn, payload, **kw: pushed.append(payload) or {"sent": 1})
    proposal = InstructionUpdate(id="agent-1", status="proposed", origin="agent", proposal=PROPOSAL,
                                 text="En Histoire-géographie, le prof semble interroger en début de cours.",
                                 facts=["Le jeudi 12/11, la séance dit : « Interro orale sur la leçon »"],
                                 summary="L'agent propose : En Histoire-géographie…")
    agent.publish([], instructions=[proposal])
    agent.publish([], instructions=[proposal])  # sent again: still one
    [listed] = parent.get("/api/instructions").json()
    assert (listed["origin"], listed["status"], listed["facts"]) == ("agent", "proposed", proposal.facts)
    assert pushed[-1]["title"] == "Révisions : l'agent propose une règle"
    parent.post("/api/instructions/agent-1/reply", json={"kind": "reject", "text": "seulement avant les contrôles"})
    [refused] = agent.feedback(0)["instruction_replies"]
    assert (refused["kind"], refused["text"]) == ("reject", "seulement avant les contrôles")


# ------------------------------------------------------------------------ home page


def test_the_home_page_is_sent_signed_and_read_with_the_progress(agent, parent, monkeypatch):
    from datetime import datetime

    from skoolathome.schema.home import Home

    assert parent.get("/api/home").json() == {"home": None}
    agent.publish([sheet("b1")])
    page = Home.model_validate({
        "generated_at": datetime.now().astimezone().isoformat(),
        "slots": [{"day": "2026-11-16", "starts": "08:20", "ends": "09:15", "subject_code": "MATHS",
                   "subject": "Mathématiques"}],
        "grades": [{"subject_code": "MATHS", "subject": "Mathématiques", "grades": [
            {"day": "2026-11-06", "kind": "Contrôle", "title": "Chapitre 3", "value": "16,75", "score": 16.75,
             "out_of": 20, "class_average": 14.2, "class_min": 6, "class_max": 19.5}]}],
        "suggestions": [{"subject_code": "MATHS", "subject": "Mathématiques", "reason": "lesson",
                         "for_day": "2026-11-16", "sheet_ids": ["b1", "unknown"],
                         "request": {"kind": "extra", "focus": "la séance du jeudi 12/11"}}],
    })
    agent.home(page)
    parent.post("/api/requests", json={"subject": "FRANC", "kind": "revision"})
    child = pair_child(parent)
    shown = child.get("/api/home").json()
    assert shown["home"]["grades"][0]["grades"][0]["class_max"] == 19.5
    assert list(shown["sheets"]) == ["b1"] and shown["sheets"]["b1"]["answered"] == 0
    assert shown["requested"] == ["FRANC"]
    agent.home(page.model_copy(update={"suggestions": []}))  # the next collection replaces it
    assert parent.get("/api/home").json()["home"]["suggestions"] == []


def test_a_database_behind_the_code_says_to_migrate(agent, parent, api_db):
    """Pushed but not migrated (the home table came in 0013): a 503 that says what to run, on the
    tablet and in the laptop's log, instead of a bare 500 and a home page with no blocks."""
    from datetime import datetime

    from skoolathome.schema.home import Home

    api_db.execute("DROP TABLE home")
    answer = parent.get("/api/home")
    assert answer.status_code == 503 and "skoolathome api migrate" in answer.json()["detail"]
    with pytest.raises(ApiError) as refused:
        agent.home(Home.model_validate({"generated_at": datetime.now().astimezone().isoformat()}))
    assert refused.value.status == 503
    assert "uv run skoolathome api migrate" in str(refused.value) and "base en ligne" in str(refused.value)


def test_the_laptop_notifies_the_parents(agent, monkeypatch):
    pushed = []
    monkeypatch.setattr(app_module.push, "send", lambda conn, payload, **kw: pushed.append((payload, kw)) or {"sent": 1})
    agent.notify("Révisions : réponse fausse signalée", "sheet-generator v5", url="/#/review")
    assert pushed == [({"title": "Révisions : réponse fausse signalée", "body": "sheet-generator v5",
                        "url": "/#/review", "tag": "agent"}, {"roles": ("parent",)})]


def test_the_queue_says_what_waits_for_the_laptop(agent, parent):
    assert agent.queue() == {"explanations": [], "requests": []}
    made = parent.post("/api/requests", json={"subject": "MATHS", "kind": "extra", "focus": "symétrie"}).json()
    assert agent.queue()["requests"] == [made["id"]]


def test_an_api_without_the_queue_still_gives_the_explanations(agent, monkeypatch):
    def gone(*args, **kwargs):
        raise ApiError(404, "Not Found")

    monkeypatch.setattr(agent, "_call", lambda method, path, *a, **k: gone() if path == "/api/agent/queue" else [])
    assert agent.queue() == {"explanations": [], "requests": []}


def home_with_file(sha: str, name: str = "cours.pdf"):
    from datetime import datetime

    from skoolathome.schema.home import Home

    return Home.model_validate({
        "generated_at": datetime.now().astimezone().isoformat(),
        "suggestions": [{"subject_code": "MATHS", "subject": "Mathématiques", "reason": "lesson",
                         "for_day": "2026-11-16", "request": {"kind": "extra", "focus": "x"},
                         "lessons": [{"day": "2026-11-12", "documents": [{"name": name, "size": 19, "sha256": sha}],
                                      "links": [{"url": "https://www.geogebra.org/m/abc", "label": "GeoGebra"}]}]}],
    })


def test_a_teachers_file_goes_online_once_and_opens_from_the_link(agent, parent, api_db):
    pdf = b"%PDF-1.7 the lesson"
    sha = hashlib.sha256(pdf).hexdigest()
    assert agent.missing_documents([sha, sha]) == [sha]
    with pytest.raises(ApiError) as wrong:
        agent.upload_document("0" * 64, "cours.pdf", "application/pdf", pdf)
    assert wrong.value.status == 422  # the name is its sha256: checked
    agent.upload_document(sha, "cours.pdf", "application/pdf", pdf)
    assert agent.missing_documents([sha]) == []
    agent.home(home_with_file(sha))
    shown = pair_child(parent).get("/api/home").json()
    link = shown["documents"][sha]
    assert shown["home"]["suggestions"][0]["lessons"][0]["links"][0]["label"] == "GeoGebra"
    # The link opens without a session: on the iPad, Safari does not have the app's cookies.
    opened = browser().get(link)
    assert opened.status_code == 200 and opened.content == pdf
    assert opened.headers["content-type"] == "application/pdf"
    assert opened.headers["content-disposition"] == "inline; filename*=UTF-8''cours.pdf"
    # A forged or expired link: refused (403, never 401: the service worker would forget its data).
    assert browser().get(link.replace("sig=", "sig=0")).status_code == 403
    expired = link.split("?")[0] + "?expires=1&sig=" + link.split("sig=")[1]
    assert browser().get(expired).status_code == 403
    assert parent.get(link.split("?")[0]).status_code == 200  # a paired device needs no link
    # The next page without it: the file is removed.
    agent.home(home_with_file(hashlib.sha256(b"other").hexdigest()))
    assert api_db.execute("SELECT count(*) FROM documents").fetchone()[0] == 0


def test_a_file_that_could_run_a_script_is_downloaded_not_shown(agent, parent):
    page = b"<html><script>alert(1)</script></html>"
    sha = hashlib.sha256(page).hexdigest()
    agent.upload_document(sha, "activite.html", "text/html", page)
    opened = parent.get(f"/api/documents/{sha}")
    assert opened.headers["content-type"] == "application/octet-stream"
    assert opened.headers["content-disposition"].startswith("attachment;")
    assert opened.headers["x-content-type-options"] == "nosniff"

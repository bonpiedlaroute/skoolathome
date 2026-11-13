"""Tests for the École Directe client, without network: a fake server answers instead."""

from __future__ import annotations

import base64
import json
from datetime import date
from urllib.parse import unquote

import httpx
import pytest

from skoolathome import secrets
from skoolathome.collectors.ecoledirecte.client import (
    Device,
    EcoleDirecteClient,
    EcoleDirecteError,
    TwoFactorRequired,
)
from skoolathome.collectors.ecoledirecte.probe import decode_contents, is_allowed


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


PARENT_ACCOUNT = {
    "id": 1,
    "typeCompte": "1",
    "profile": {
        "eleves": [
            {
                "id": 42,
                "prenom": "Léo",
                "classe": {"libelle": "5E B"},
                "modules": [
                    {"code": "EDT", "enable": True},
                    {"code": "NOTES", "enable": True},
                    {"code": "CAHIER_DE_TEXTES", "enable": False},
                ],
            }
        ]
    },
}


class FakeEcoleDirecte:
    """Plays the École Directe server and records the requests it receives."""

    def __init__(self, requires_two_factor: bool = False, login_code: int = 200) -> None:
        self.requires_two_factor = requires_two_factor
        self.login_code = login_code
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/v3/login.awp" and request.method == "GET":
            return httpx.Response(200, headers={"set-cookie": "GTK=gtk-123; Path=/"}, json={})
        payload = json.loads(request.content.decode().removeprefix("data="))
        if path == "/v3/login.awp":
            if self.requires_two_factor and "fa" not in payload:
                return httpx.Response(
                    200,
                    headers={"x-token": "temporary-token", "2FA-Token": "2fa-token-value"},
                    json={"code": 250, "token": "temporary-token", "data": {}},
                )
            if self.login_code != 200:
                return httpx.Response(200, json={"code": self.login_code, "message": "", "data": {}})
            return httpx.Response(
                200,
                headers={"x-token": "final-token"},
                json={"code": 200, "token": "final-token", "data": {"accounts": [PARENT_ACCOUNT]}},
            )
        if path == "/v3/connexion/doubleauth.awp":
            if request.url.params["verbe"] == "get":
                return httpx.Response(
                    200,
                    json={
                        "code": 200,
                        "data": {
                            "question": b64("Quelle est votre ville de naissance ?"),
                            "propositions": [b64("Lyon"), b64("Nantes")],
                        },
                    },
                )
            assert payload == {"choix": b64("Nantes")}
            return httpx.Response(200, json={"code": 200, "data": {"cn": "CN", "cv": "CV"}})
        if path == "/v3/E/42/emploidutemps.awp":
            return httpx.Response(200, json={"code": 200, "data": [{"start_date": "2026-11-09 08:00"}]})
        return httpx.Response(404)


def client(server: FakeEcoleDirecte, device: Device | None = None) -> EcoleDirecteClient:
    return EcoleDirecteClient("parent", "pass&word+1", device, transport=httpx.MockTransport(server))


def test_login_without_two_factor():
    server = FakeEcoleDirecte()
    with client(server) as ed:
        accounts = ed.connect()
    assert accounts == [PARENT_ACCOUNT]
    login = server.requests[1]
    assert login.headers["x-gtk"] == "gtk-123"
    payload = json.loads(login.content.decode().removeprefix("data="))
    assert payload["motdepasse"] == "pass%26word%2B1"
    assert unquote(payload["motdepasse"]) == "pass&word+1"


def test_login_with_two_factor():
    server = FakeEcoleDirecte(requires_two_factor=True)
    with client(server) as ed:
        ed.connect(lambda question, choices: "Nantes")
        assert ed.device == Device("CN", "CV")
    challenge = server.requests[2]
    assert challenge.headers["2fa-token"] == "2fa-token-value"
    assert challenge.headers["x-token"] == "temporary-token"
    second_login = server.requests[-1]
    payload = json.loads(second_login.content.decode().removeprefix("data="))
    assert payload["fa"] == [{"cn": "CN", "cv": "CV"}]


def test_two_factor_without_answerer_does_not_retry():
    server = FakeEcoleDirecte(requires_two_factor=True)
    with client(server) as ed, pytest.raises(TwoFactorRequired):
        ed.connect()
    logins = [r for r in server.requests if r.url.path == "/v3/login.awp" and r.method == "POST"]
    assert len(logins) == 1


def test_known_device_skips_two_factor():
    server = FakeEcoleDirecte(requires_two_factor=True)
    with client(server, Device("CN", "CV")) as ed:
        ed.connect()
    assert not any(r.url.path == "/v3/connexion/doubleauth.awp" for r in server.requests)


def test_rejected_api_version():
    server = FakeEcoleDirecte(login_code=517)
    with client(server) as ed, pytest.raises(EcoleDirecteError, match="API_VERSION"):
        ed.connect()


def test_parent_account_students_and_authenticated_call():
    server = FakeEcoleDirecte()
    with client(server) as ed:
        ed.connect()
        assert ed.students() == [
            {"id": 42, "first_name": "Léo", "class_name": "5E B", "modules": ["EDT", "NOTES"]}
        ]
        response = ed.timetable(42, date(2026, 11, 9), date(2026, 11, 9))
    assert response["data"][0]["start_date"] == "2026-11-09 08:00"
    call = server.requests[-1]
    assert call.headers["x-token"] == "final-token"
    assert call.url.params["verbe"] == "get"


def test_disabled_module_is_never_called():
    student = {"id": 42, "modules": ["EDT", "NOTES"]}
    assert is_allowed(student, "timetable")
    assert is_allowed(student, "grades")
    assert not is_allowed(student, "homework")
    assert not is_allowed(student, "diary")
    assert not is_allowed(student, "textbooks")


def test_decode_contents():
    raw = {"matieres": [{"contenuDeSeance": {"contenu": b64("<p>Les fractions</p>")}}]}
    readable = decode_contents(raw)
    assert readable["matieres"][0]["contenuDeSeance"]["contenu_text"] == "<p>Les fractions</p>"
    assert "contenu_text" not in raw["matieres"][0]["contenuDeSeance"]


def test_secrets_file_mode_600(tmp_path):
    secrets.write("ecoledirecte", {"username": "x"}, directory=tmp_path)
    path = tmp_path / "ecoledirecte.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert secrets.read("ecoledirecte", directory=tmp_path) == {"username": "x"}
    path.chmod(0o644)
    with pytest.raises(PermissionError):
        secrets.read("ecoledirecte", directory=tmp_path)

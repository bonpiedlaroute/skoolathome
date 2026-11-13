"""Minimal client for the unofficial École Directe API.

References: https://github.com/EduWireApps/ecoledirecte-api-docs
and the `ecoledirecte` library used by hass-ecoledirecte.

The JSON keys sent to and received from École Directe stay in French
(`identifiant`, `motdepasse`, `choix`, `verbe`…): they belong to its API.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date
from typing import Callable
from urllib.parse import quote

import httpx

API_URL = "https://api.ecoledirecte.com/v3"
API_VERSION = "4.104.3"
# The token is tied to the User-Agent: keep the same one for the whole session.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)

CODE_OK = 200
CODE_NO_DATA = 210
CODE_TWO_FACTOR = 250

# Messages shown to the parent: kept in French.
ERRORS = {
    403: "accès refusé par École Directe (module désactivé pour cet élève ?)",
    505: "identifiant ou mot de passe refusé",
    517: "version d'API refusée : mets à jour API_VERSION",
    520: "jeton invalide",
    525: "jeton expiré",
    535: "établissement indisponible (vacances ?)",
}


class EcoleDirecteError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


class TwoFactorRequired(EcoleDirecteError):
    """École Directe asks for two-factor authentication (code 250)."""


@dataclass(frozen=True)
class Device:
    """The cn/cv pair: the device École Directe trusts after the security question."""

    cn: str
    cv: str


# (question, choices) -> the chosen choice
Answerer = Callable[[str, list[str]], str]


def _encode(text: str) -> str:
    """Same as encodeURIComponent in JavaScript."""
    return quote(text, safe="!*'()")


def _body(payload: dict) -> str:
    return "data=" + json.dumps(payload)


def _b64_to_text(value: str) -> str:
    return base64.b64decode(value).decode("utf-8")


def _text_to_b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class EcoleDirecteClient:
    def __init__(
        self,
        username: str,
        password: str,
        device: Device | None = None,
        *,
        version: str = API_VERSION,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self.device = device
        self.version = version
        self._http = httpx.Client(
            base_url=API_URL,
            headers={
                "user-agent": USER_AGENT,
                "accept": "application/json, text/plain, */*",
                "content-type": "application/x-www-form-urlencoded",
                "origin": "https://www.ecoledirecte.com",
                "referer": "https://www.ecoledirecte.com/",
            },
            timeout=30.0,
            transport=transport,
        )
        self.accounts: list[dict] = []

    def __enter__(self) -> EcoleDirecteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -------------------------------------------------------------------- login

    def connect(self, answer: Answerer | None = None) -> list[dict]:
        """Log in. One attempt, plus one after the security question: never a loop."""
        body = self._login()
        if body.get("code") == CODE_TWO_FACTOR:
            if answer is None:
                raise TwoFactorRequired(CODE_TWO_FACTOR, "double authentification requise")
            self.device = self._pass_two_factor(answer)
            body = self._login()
        self._check(body)
        self.accounts = body["data"]["accounts"]
        return self.accounts

    def _login(self) -> dict:
        self._fetch_gtk()
        payload: dict = {
            "identifiant": _encode(self._username),
            "motdepasse": _encode(self._password),
            "isReLogin": False,
            "uuid": "",
        }
        if self.device is not None:
            payload["cn"] = self.device.cn
            payload["cv"] = self.device.cv
            payload["fa"] = [{"cn": self.device.cn, "cv": self.device.cv}]
        response = self._http.post(
            "/login.awp", params={"v": self.version}, content=_body(payload)
        )
        self._http.headers.pop("x-gtk", None)
        body = self._json(response)
        self._keep_tokens(response, body)
        return body

    def _fetch_gtk(self) -> None:
        self._http.headers.pop("x-gtk", None)
        response = self._http.get("/login.awp", params={"v": self.version, "gtk": 1})
        gtk = response.cookies.get("GTK") or self._http.cookies.get("GTK")
        if not gtk:
            raise EcoleDirecteError(0, "cookie GTK absent : la connexion a peut-être changé")
        self._http.headers["x-gtk"] = gtk

    def _pass_two_factor(self, answer: Answerer) -> Device:
        challenge = self._call("/connexion/doubleauth.awp", verb="get", payload={})
        question = _b64_to_text(challenge["data"]["question"])
        choices = [_b64_to_text(c) for c in challenge["data"]["propositions"]]
        choice = answer(question, choices)
        if choice not in choices:
            raise ValueError("la réponse doit être l'une des propositions")
        result = self._call(
            "/connexion/doubleauth.awp", verb="post", payload={"choix": _text_to_b64(choice)}
        )
        data = result.get("data") or {}
        if not data.get("cn") or not data.get("cv"):
            raise EcoleDirecteError(result.get("code", 0), "réponse à la question refusée")
        return Device(cn=data["cn"], cv=data["cv"])

    # -------------------------------------------------------------------- calls

    def _call(self, path: str, *, verb: str = "get", payload: dict | None = None) -> dict:
        response = self._http.post(
            path,
            params={"verbe": verb, "v": self.version},
            content=_body(payload or {}),
        )
        body = self._json(response)
        self._keep_tokens(response, body)
        self._check(body)
        return body

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        response.raise_for_status()
        return response.json()

    def _keep_tokens(self, response: httpx.Response, body: dict) -> None:
        token = response.headers.get("x-token") or body.get("token")
        if token:
            self._http.headers["x-token"] = token
        two_factor_token = response.headers.get("2fa-token")
        if two_factor_token:
            self._http.headers["2fa-token"] = two_factor_token

    @staticmethod
    def _check(body: dict) -> None:
        code = body.get("code")
        if code in (CODE_OK, CODE_NO_DATA):
            return
        if code == CODE_TWO_FACTOR:
            raise TwoFactorRequired(code, "double authentification requise")
        message = ERRORS.get(code) or body.get("message") or "erreur inconnue"
        raise EcoleDirecteError(code or 0, message)

    # --------------------------------------------------------------------- data

    def students(self) -> list[dict]:
        """Children of the account: profile.eleves for a parent, the account itself for a student."""
        found = []
        for account in self.accounts:
            profile = account.get("profile") or {}
            if account.get("typeCompte") == "E":
                found.append(self._student(account, profile.get("classe")))
            for child in profile.get("eleves") or []:
                found.append(self._student(child, child.get("classe")))
        return found

    @staticmethod
    def _student(raw: dict, school_class: dict | None) -> dict:
        return {
            "id": raw["id"],
            "first_name": raw.get("prenom", ""),
            "class_name": (school_class or {}).get("libelle", ""),
            # Codes of the modules the school enabled for this student (EDT, NOTES…).
            "modules": sorted(m["code"] for m in raw.get("modules") or [] if m.get("enable")),
        }

    def timetable(self, student_id: int, start: date, end: date) -> dict:
        return self._call(
            f"/E/{student_id}/emploidutemps.awp",
            payload={"dateDebut": start.isoformat(), "dateFin": end.isoformat(), "avecTrous": False},
        )

    def upcoming_homework(self, student_id: int) -> dict:
        return self._call(f"/Eleves/{student_id}/cahierdetexte.awp")

    def day_diary(self, student_id: int, day: date) -> dict:
        return self._call(f"/Eleves/{student_id}/cahierdetexte/{day.isoformat()}.awp")

    def grades(self, student_id: int) -> dict:
        return self._call(f"/eleves/{student_id}/notes.awp", payload={"anneeScolaire": ""})

    def digital_textbooks(self, student_id: int) -> dict:
        return self._call(f"/Eleves/{student_id}/manuelsNumeriques.awp")

"""Probe: log in with the parent account and save the raw École Directe responses."""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from skoolathome import secrets
from skoolathome.collectors.ecoledirecte.client import (
    Device,
    EcoleDirecteClient,
    EcoleDirecteError,
    TwoFactorRequired,
)

PAUSE_BETWEEN_CALLS = 1.0  # seconds: stay discreet
CODE_FORBIDDEN = 403

# The École Directe module each call needs. A call to a disabled module is
# answered with 403 and a warning that the IP address was recorded: never make it.
REQUIRED_MODULE = {
    "timetable": "EDT",
    "homework": "CAHIER_DE_TEXTES",
    "diary": "CAHIER_DE_TEXTES",
    "grades": "NOTES",
    "textbooks": "MANUELS_SCOLAIRES",
}


def is_allowed(student: dict, call_name: str) -> bool:
    return REQUIRED_MODULE[call_name] in student["modules"]


def answer_in_terminal(question: str, choices: list[str]) -> str:
    print(f"\nDouble authentification École Directe :\n  {question}")
    for number, choice in enumerate(choices, start=1):
        print(f"  {number}. {choice}")
    while True:
        typed = input("Numéro de ta réponse : ").strip()
        if typed.isdigit() and 1 <= int(typed) <= len(choices):
            return choices[int(typed) - 1]


def decode_contents(obj: object) -> object:
    """Copy of the response where each `contenu` field (base64) is also given as plain text."""
    if isinstance(obj, list):
        return [decode_contents(item) for item in obj]
    if not isinstance(obj, dict):
        return obj
    copy = {key: decode_contents(value) for key, value in obj.items()}
    encoded = obj.get("contenu")
    if isinstance(encoded, str) and encoded:
        try:
            copy["contenu_text"] = base64.b64decode(encoded).decode("utf-8")
        except ValueError:
            pass
    return copy


def load_credentials() -> dict:
    conf = secrets.read("ecoledirecte")
    if conf is None:
        conf = {
            "username": input("Identifiant École Directe (compte parent) : ").strip(),
            "password": getpass.getpass("Mot de passe : "),
        }
        secrets.write("ecoledirecte", conf)
    return conf


def school_days(timetable: dict, homework: dict) -> list[date]:
    days = {lesson["start_date"][:10] for lesson in timetable.get("data") or []}
    days |= set((homework.get("data") or {}).keys())
    return sorted(date.fromisoformat(day) for day in days)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days-back", type=int, default=14)
    parser.add_argument("--days-ahead", type=int, default=14)
    parser.add_argument("--out", type=Path, default=Path("data/raw/ecoledirecte"))
    parser.add_argument("--student", type=int, help="only probe this student id")
    parser.add_argument(
        "--interactive", action="store_true", help="allow answering the security question in the terminal"
    )
    args = parser.parse_args()

    conf = load_credentials()
    device = Device(conf["cn"], conf["cv"]) if conf.get("cn") and conf.get("cv") else None

    with EcoleDirecteClient(conf["username"], conf["password"], device) as ed:
        try:
            ed.connect(answer_in_terminal if args.interactive else None)
        except TwoFactorRequired:
            sys.exit("Double authentification requise : relance avec --interactive.")
        except EcoleDirecteError as error:
            sys.exit(f"Connexion refusée : {error}")

        if ed.device is not None and ed.device != device:
            conf.update(cn=ed.device.cn, cv=ed.device.cv)
            secrets.write("ecoledirecte", conf)
            print("Appareil enregistré : plus de question aux prochaines connexions.")

        out_dir = args.out / datetime.now().strftime("%Y-%m-%dT%H%M%S")
        out_dir.mkdir(parents=True, mode=0o700)

        def save(name: str, response: object) -> None:
            for suffix, content in (("", response), (".readable", decode_contents(response))):
                path = out_dir / f"{name}{suffix}.json"
                path.write_text(json.dumps(content, ensure_ascii=False, indent=2))

        save("accounts", ed.accounts)
        today = date.today()
        start = today - timedelta(days=args.days_back)
        end = today + timedelta(days=args.days_ahead)

        for student in ed.students():
            sid = student["id"]
            if args.student is not None and sid != args.student:
                continue
            print(f"Élève {student['first_name']} ({student['class_name']}), id {sid}")
            calls = {
                "timetable": lambda: ed.timetable(sid, start, end),
                "homework": lambda: ed.upcoming_homework(sid),
                "grades": lambda: ed.grades(sid),
                "textbooks": lambda: ed.digital_textbooks(sid),
            }
            responses: dict[str, dict] = {name: {} for name in calls}
            forbidden = False
            for name, call in calls.items():
                if not is_allowed(student, name):
                    print(f"  {name} : module {REQUIRED_MODULE[name]} désactivé, appel ignoré")
                    continue
                try:
                    responses[name] = call()
                    save(f"{sid}-{name}", responses[name])
                except (EcoleDirecteError, httpx.HTTPError) as error:
                    print(f"  {name} : {error}")
                    if getattr(error, "code", None) == CODE_FORBIDDEN:
                        forbidden = True
                        break
                time.sleep(PAUSE_BETWEEN_CALLS)

            if forbidden or not is_allowed(student, "diary"):
                print("  diary : ignoré pour cet élève")
                continue
            for day in school_days(responses["timetable"], responses["homework"]):
                try:
                    save(f"{sid}-diary-{day.isoformat()}", ed.day_diary(sid, day))
                except (EcoleDirecteError, httpx.HTTPError) as error:
                    print(f"  diary {day} : {error}")
                    if getattr(error, "code", None) == CODE_FORBIDDEN:
                        break  # never insist on a refused call
                time.sleep(PAUSE_BETWEEN_CALLS)

        print(f"Réponses enregistrées dans {out_dir}")


if __name__ == "__main__":
    main()

"""Command line: skoolathome db migrate | collect | summary | evening | ed …"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from pydantic import ValidationError

SUMMARY_DIR = Path("data/summaries")


def _today(args: argparse.Namespace) -> date:
    return date.fromisoformat(args.today) if getattr(args, "today", None) else date.today()


def _connect():
    from skoolathome.store import db

    conn = db.connect()
    conn.autocommit = True
    return conn


def cmd_db_migrate(args: argparse.Namespace) -> int:
    from skoolathome.store.migrate import migrate

    with _connect() as conn:
        applied = migrate(conn)
    print("Migrations appliquées : " + (", ".join(applied) if applied else "aucune, tout est à jour"))
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    from skoolathome import secrets
    from skoolathome.collect import collect
    from skoolathome.collectors.ecoledirecte.client import Device, EcoleDirecteClient
    from skoolathome.collectors.ecoledirecte.source import ReplayClient

    if args.replay:
        client, pause = ReplayClient(Path(args.replay)), 0.0
    else:
        conf = secrets.read("ecoledirecte")
        if conf is None:
            sys.exit("Identifiants École Directe absents : lance d'abord skoolathome-ed-probe --interactive.")
        device = Device(conf["cn"], conf["cv"]) if conf.get("cn") and conf.get("cv") else None
        client, pause = EcoleDirecteClient(conf["username"], conf["password"], device), 1.0
    try:
        with _connect() as conn:
            result = collect(conn, client, student_id=args.student, today=_today(args), pause=pause)
    finally:
        client.close()
    print(f"Collecte n° {result.run_id} : {result.status}")
    for table, counts in result.counts.items():
        print(f"  {table} : " + ", ".join(f"{n} {what}" for what, n in sorted(counts.items())) if counts else f"  {table} : rien")
    for error in result.errors:
        print(f"  erreur {error['kind']} {error['ref']} : {error['error']}")
    if result.status == "two_factor_required":
        print("École Directe demande la double authentification : lance skoolathome-ed-probe --interactive, une seule fois.")
    return 0 if result.status in ("ok", "partial") else 1


def _notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "--app-name=skoolathome", title, body], check=False)


def cmd_summary(args: argparse.Namespace) -> int:
    from skoolathome import summary

    today = _today(args)
    with _connect() as conn:
        student_id = str(args.student) if args.student else summary.latest_student(conn)
        if student_id is None:
            sys.exit("Aucune collecte en base : lance d'abord skoolathome collect.")
        text = summary.build(conn, student_id, today)
    if args.save:
        SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
        path = SUMMARY_DIR / f"{today.isoformat()}.md"
        path.write_text(text)
        print(f"Résumé enregistré dans {path}")
    else:
        print(text, end="")
    if args.notify:
        tests = sum(1 for line in text.split("## ")[1].splitlines() if line.startswith("- "))
        _notify("skoolathome", f"{tests} contrôle(s) dans les 14 jours. Résumé : {SUMMARY_DIR}/{today.isoformat()}.md")
    return 0


def cmd_evening(args: argparse.Namespace) -> int:
    """What the timer runs: collect, then save the summary and notify. The summary is
    written even when the collection failed, so the failure is visible."""
    args.replay, args.save, args.notify = None, True, True
    code = cmd_collect(args)
    if code != 0:
        _notify("skoolathome : collecte en échec", "Voir : skoolathome summary")
    try:
        cmd_summary(args)
    except SystemExit as stop:
        print(stop)
    return code


def cmd_ed_validate(args: argparse.Namespace) -> int:
    """Check that saved responses still match what the mapping expects."""
    from skoolathome.collectors.ecoledirecte import mapping

    translators = {"timetable": mapping.map_timetable, "homework": mapping.map_upcoming_homework,
                   "grades": mapping.map_grades, "diary": mapping.map_diary}
    checked = failed = 0
    for path in sorted(Path(args.directory).glob("*.json")):
        if ".readable" in path.name:
            continue
        kind = next((k for k in translators if k in path.stem), None)
        if kind is None:
            continue
        checked += 1
        try:
            translators[kind](json.loads(path.read_text()), "0")
        except (ValidationError, ValueError, KeyError) as error:
            failed += 1
            detail = error.errors(include_input=False)[:3] if isinstance(error, ValidationError) else error
            print(f"KO {path.name} : {detail}")
    print(f"{checked} réponses vérifiées, {failed} en erreur")
    return 1 if failed else 0


def cmd_ed_anonymize(args: argparse.Namespace) -> int:
    from skoolathome.collectors.ecoledirecte.anonymize import anonymize_directory

    written = anonymize_directory(Path(args.source), Path(args.target), args.student)
    print(f"{len(written)} fichiers écrits dans {args.target} : " + ", ".join(written))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="skoolathome", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    db = commands.add_parser("db", help="local database").add_subparsers(dest="action", required=True)
    db.add_parser("migrate", help="apply pending migrations").set_defaults(run=cmd_db_migrate)

    collect = commands.add_parser("collect", help="collect École Directe into the local database")
    collect.add_argument("--student", type=int, help="student id (default: the only one with a homework diary)")
    collect.add_argument("--replay", metavar="DIR", help="read responses saved by the probe instead of calling École Directe")
    collect.add_argument("--today", help="pretend today is this date (YYYY-MM-DD), for replays and tests")
    collect.set_defaults(run=cmd_collect)

    summary = commands.add_parser("summary", help="upcoming tests and lessons without content")
    summary.add_argument("--student", type=int)
    summary.add_argument("--today")
    summary.add_argument("--save", action="store_true", help=f"write to {SUMMARY_DIR}/ instead of printing")
    summary.add_argument("--notify", action="store_true", help="desktop notification")
    summary.set_defaults(run=cmd_summary)

    evening = commands.add_parser("evening", help="what the timer runs: collect, then summary")
    evening.add_argument("--student", type=int)
    evening.add_argument("--today")
    evening.set_defaults(run=cmd_evening)

    ed = commands.add_parser("ed", help="École Directe tools").add_subparsers(dest="action", required=True)
    validate = ed.add_parser("validate", help="validate saved raw responses")
    validate.add_argument("directory")
    validate.set_defaults(run=cmd_ed_validate)
    anonymize = ed.add_parser("anonymize", help="make test fixtures from saved raw responses")
    anonymize.add_argument("source")
    anonymize.add_argument("target")
    anonymize.add_argument("--student", type=int, required=True)
    anonymize.set_defaults(run=cmd_ed_anonymize)

    args = parser.parse_args()
    sys.exit(args.run(args))


if __name__ == "__main__":
    main()

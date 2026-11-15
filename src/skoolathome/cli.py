"""Command line: skoolathome db migrate | collect | summary | evening | ed … | api …"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date, datetime
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
    """What the timer runs (T17): the whole evening graph. Collection (one login, never a retry),
    feedback, plan, sheets, judge, signed publication, then the report to the parent."""
    from skoolathome import summary
    from skoolathome.agent import graph, llm
    from skoolathome.collectors.manual.source import ManualSource
    from skoolathome.store import db

    today, publish = _today(args), not args.no_publish
    problem = _preflight()
    if problem:
        print(problem)
        _send_failure(today, problem, publish)
        return 1
    with _connect() as conn:
        student = str(args.student) if args.student else summary.latest_student(conn)
    services = graph.Services(
        models=llm.Models(),
        collector=None if args.no_collect or args.only_requests else _collector(args),
        api=_api_sessions(),
        embedder=_embedder(),
        material=ManualSource(),
        progress=lambda message: print(f"{datetime.now():%H:%M}  {message}", flush=True),
    )
    what = f" (séances choisies : {', '.join(args.lesson)}, format {args.format})" if args.lesson else ""
    what = " : les fiches demandées seulement" if args.only_requests else what
    print(f"Soirée du {today.isoformat()}{what}" + ("" if publish else ", sans publication")
          + ". Compter 7 à 10 minutes par fiche.", flush=True)
    try:
        evening = graph.run(today, student, services, db.database_url(), publish=publish,
                            lesson_ids=args.lesson, forced_format=args.format,
                            only_requests=args.only_requests, restart=args.restart)
    except Exception as error:
        _send_failure(today, f"traitement interrompu ({type(error).__name__} : {str(error)[:150]})", publish)
        raise
    print()
    _print_evening(evening)
    if not args.no_collect and not args.only_requests:
        args.save, args.notify = True, False
        try:
            cmd_summary(args)  # the T06 summary, kept in data/summaries/
        except SystemExit as stop:
            print(stop)
    _notify(evening.report.get("title", "skoolathome"), evening.report.get("body", ""))
    return 0 if evening.report.get("status") == "ok" else 1


def _preflight() -> str | None:
    """Why the graph cannot run at all, or None."""
    import httpx

    from skoolathome.agent import config

    conf = config.load()
    with _connect() as conn:
        if conn.execute("SELECT to_regclass('agent.rules')").fetchone()[0] is None:
            return "Base locale pas à jour : lance skoolathome db migrate."
    try:
        httpx.get(f"{conf.engine.native_url}/api/version", timeout=3).raise_for_status()
    except httpx.HTTPError:
        return f"Ollama ne répond pas sur {conf.engine.native_url} : sudo systemctl start ollama"
    for role in ("generator", "judge"):
        model = conf.model(role)
        if not _ollama_has(conf, model.model):
            return f"Modèle absent : ollama pull {model.model}"
    return None


def _ollama_has(conf, model: str) -> bool:
    import httpx

    try:
        return httpx.post(f"{conf.engine.native_url}/api/show", json={"model": model}, timeout=10).status_code == 200
    except httpx.HTTPError:
        return False


def _embedder():
    """The embedding model, if pulled: without it, no document is indexed or searched (T18)."""
    from skoolathome.agent import config
    from skoolathome.agent.embeddings import LocalEmbedder

    conf = config.load()
    if not _ollama_has(conf, conf.model("embedder").model):
        print(f"Modèle d'embedding absent (ollama pull {conf.model('embedder').model}) : documents ignorés ce soir.")
        return None
    return LocalEmbedder(conf)


def _collector(args: argparse.Namespace):
    """One École Directe collection, as the graph's `collect` node runs it."""
    def collect_once() -> dict:
        from skoolathome import secrets
        from skoolathome.collect import collect
        from skoolathome.collectors.ecoledirecte.client import Device, EcoleDirecteClient

        conf = secrets.read("ecoledirecte")
        if conf is None:
            return {"status": "failed", "errors": [{"kind": "run", "ref": "", "error": "identifiants École Directe absents"}]}
        device = Device(conf["cn"], conf["cv"]) if conf.get("cn") and conf.get("cv") else None
        client = EcoleDirecteClient(conf["username"], conf["password"], device)
        try:
            with _connect() as conn:
                result = collect(conn, client, student_id=args.student, today=_today(args), pause=1.0)
        finally:
            client.close()
        return {"status": result.status, "run_id": result.run_id, "student_id": result.student_id,
                "counts": result.counts, "errors": result.errors[:10]}

    return collect_once


def _api_sessions():
    """A factory of signed API clients, one bunker session each; None when the API address or the
    bunker token is missing."""
    from contextlib import contextmanager

    from skoolathome.publish.client import ApiClient, api_url
    from skoolathome.publish.nip46 import BunkerSession, load_conf

    try:
        url, conf = api_url(), load_conf()
    except FileNotFoundError as error:
        print(f"Pas de publication ni de retours : {error}")
        return None

    @contextmanager
    def session():
        with BunkerSession(conf) as bunker:
            yield ApiClient(url, bunker)

    return session


def _send_failure(today: date, reason: str, publish: bool) -> None:
    """The evening could not run: say it to the parent, online if possible, and on this laptop."""
    from skoolathome.agent import report

    built = report.failure(today, reason)
    try:
        with _connect() as conn:
            report.save(conn, built)
    except Exception as error:  # noqa: BLE001  (the database may be what failed)
        print(f"Rapport non enregistré : {error}")
    sessions = _api_sessions() if publish else None
    if sessions is not None:
        try:
            with sessions() as client:
                client.report(built)
        except Exception as error:  # noqa: BLE001
            print(f"Rapport non envoyé : {error}")
    _notify(built["title"], built["body"])


def _print_evening(evening) -> None:
    from skoolathome.agent import tracing

    states = {"ready": "prête", "published": "publiée", "abstained": "abstention"}
    if not evening.results:
        print("Aucune fiche ce soir.")
    for result in evening.results:
        print(f"  {result['task_id']} ({result['rule']}) : {states.get(result['status'], result['status'])}, "
              f"{result['attempts']} génération(s), visible le {result['visible_from']}"
              + (f"\n    {result['reason']}" if result["reason"] else ""))
        url = tracing.trace_url(result["trace_id"])
        if url:
            print(f"    trace : {url}")
    skipped = evening.plan.get("skipped") or []
    if skipped:
        print("Tâches sautées :")
        for skip in skipped[:20]:
            print(f"  {skip['task']} : {skip['reason']}")
    if evening.plan.get("done"):
        print(f"Déjà faites un autre soir : {len(evening.plan['done'])}")
    if evening.report:
        print(f"\n{evening.report['title']}\n{evening.report['body']}")


def cmd_plan(args: argparse.Namespace) -> int:
    """The evening plan, without any model: the tasks, and the expected tasks skipped with their reason (T19)."""
    from skoolathome import summary
    from skoolathome.rules import inputs, plan, store
    from skoolathome.subjects import fr_day

    today = _today(args)
    with _connect() as conn:
        student = str(args.student) if args.student else summary.latest_student(conn)
        if student is None:
            sys.exit("Aucune collecte en base : lance d'abord skoolathome collect.")
        store.ensure_defaults(conn)
        rules = store.active(conn)
        result = plan.plan(inputs.load(conn, student, today), rules)
    print(f"Plan du soir du {fr_day(today)} {today.year} (règles : {', '.join(f'{r.id} v{r.version}' for r in rules)})")
    for task in result.tasks:
        sources = ", ".join(l.key for l in task.lessons) or "aucune séance avec du texte"
        print(f"  {task.id} : {task.kind} [{task.rule_id}], visible le {fr_day(task.visible_from)}, "
              f"{task.target_minutes} min" + (f", notions : {', '.join(task.focus)}" if task.focus else "")
              + f"\n      sources : {sources}")
    if not result.tasks:
        print("  aucune tâche ce soir")
    if result.skipped:
        print("Sautées :")
        for skip in result.skipped:
            print(f"  {skip['task']} : {skip['reason']}")
    if result.done:
        print("Déjà faites, sources inchangées : " + ", ".join(result.done))
    return 0


def cmd_rules_list(args: argparse.Namespace) -> int:
    from skoolathome.rules import store

    with _connect() as conn:
        store.ensure_defaults(conn)
        rules = store.latest(conn)
    for rule in sorted(rules, key=lambda r: (r.priority, r.id)):
        what = f"{rule.trigger}" + (f" {rule.subject}" if rule.subject else "") + (f" → {rule.format}" if rule.trigger != "load_limit" else f" {rule.max_minutes} min")
        if rule.trigger != "load_limit":
            what += f", {rule.items} questions" if rule.items else ", questions selon la durée"
            what += f", {rule.target_minutes} min"
        print(f"{rule.id:<20} v{rule.version:<3} {rule.status:<9} {what}")
        if rule.origin.text:
            print(f"{'':<20} « {rule.origin.text} » ({rule.origin.author})")
    return 0


def cmd_rules_show(args: argparse.Namespace) -> int:
    import yaml

    from skoolathome.rules import store

    with _connect() as conn:
        rule = next((r for r in store.latest(conn) if r.id == args.id), None)
    if rule is None:
        sys.exit(f"Règle inconnue : {args.id}")
    print(yaml.safe_dump(rule.model_dump(mode="json"), allow_unicode=True, sort_keys=False), end="")
    return 0


def cmd_rules_add(args: argparse.Namespace) -> int:
    """A rule written by hand in YAML (ARCHITECTURE.md §7.1): the form « fait foi », no model involved."""
    import yaml

    from skoolathome.rules import store
    from skoolathome.schema.rule import Origin, Rule

    data = yaml.safe_load(Path(args.file).read_text())
    data.setdefault("origin", Origin().model_dump(mode="json"))
    try:
        rule = store.by_parent(Rule.model_validate(data))  # yours from now on: the code no longer updates it
    except ValidationError as error:
        sys.exit(f"Règle invalide :\n{error}")
    with _connect() as conn:
        saved = store.save(conn, rule)
    print(f"Règle {saved.id} enregistrée, version {saved.version}, statut {saved.status}.")
    return 0


def cmd_rules_set(args: argparse.Namespace) -> int:
    """`skoolathome rules set before_test items=4 target_minutes=8`: a new version of the rule."""
    import yaml

    from skoolathome.rules import store

    changes = {}
    for pair in args.changes:
        key, sep, value = pair.partition("=")
        if not sep:
            sys.exit(f"Attendu champ=valeur, pas « {pair} »")
        changes[key.strip()] = yaml.safe_load(value) if value.strip() else None
    with _connect() as conn:
        try:
            saved = store.set_fields(conn, args.id, changes)
        except (LookupError, ValueError) as error:  # ValidationError is a ValueError
            sys.exit(f"Règle non modifiée : {error}")
    print(f"Règle {saved.id} : version {saved.version}, "
          + ", ".join(f"{key} = {getattr(saved, key)}" for key in changes) + ".")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    """A sheet asked for by the parent: a revision on a precise point, or an extra exercise. With
    --now, made at once (no collection), then published unless --no-publish."""
    from skoolathome import summary
    from skoolathome.schema.request import NewRequest
    from skoolathome.store import requests
    from skoolathome.subjects import code_of, label

    code = code_of(args.subject)
    if code is None:
        sys.exit(f"Matière inconnue : {args.subject}. Par exemple : maths, francais, histoire, anglais, svt, physique.")
    focus = " ".join(args.focus).strip()
    try:
        wanted = NewRequest(subject=code, kind="revision" if args.revision else "extra", focus=focus, items=args.items)
    except ValidationError as error:
        sys.exit(f"Demande invalide :\n{error}")
    with _connect() as conn:
        request_id = requests.create(conn, wanted, student_id=summary.latest_student(conn))
    kind = "révision" if args.revision else "exercice en plus"
    print(f"Demande {request_id} : {kind} de {label(code)}" + (f" sur « {focus} »" if focus else "") + ".")
    if not args.now:
        print("Elle sera faite au prochain passage : la soirée, ou skoolathome evening --only-requests.")
        return 0
    args.only_requests, args.no_collect, args.lesson, args.format, args.restart = True, True, None, "exercises", False
    args.student, args.today = None, None
    return cmd_evening(args)


def cmd_requests(args: argparse.Namespace) -> int:
    from skoolathome.store import requests
    from skoolathome.subjects import label

    states = {"pending": "en attente", "done": "faite", "abstained": "impossible"}
    with _connect() as conn:
        rows = requests.recent(conn)
    if not rows:
        print("Aucune demande. Par exemple : skoolathome ask maths les fractions égales --revision --now")
    for r in rows:
        kind = "révision" if r["kind"] == "revision" else "exercice en plus"
        print(f"{r['created_at']:%d/%m %H:%M}  {label(r['subject_code']):<20} {kind:<17} {states[r['status']]:<11}"
              f" {r['focus'][:40]}" + (f"  → {r['sheet_id']}" if r["sheet_id"] else "")
              + (f"\n{'':<16}{r['reason'][:150]}" if r["status"] == "abstained" else ""))
    return 0


def _edit_rule(rule: dict) -> dict | None:
    """The rule in YAML in $EDITOR, read back and validated; None when given up."""
    import os
    import tempfile

    import yaml

    from skoolathome.schema.rule import Rule

    editor = os.environ.get("EDITOR") or shutil.which("nano") or "vi"
    text = yaml.safe_dump({k: v for k, v in rule.items() if k not in ("version", "origin")}, allow_unicode=True,
                          sort_keys=False)
    while True:
        with tempfile.NamedTemporaryFile("w+", suffix=".yaml", delete=False) as handle:
            handle.write(text)
        subprocess.run([editor, handle.name], check=False)
        text = Path(handle.name).read_text()
        Path(handle.name).unlink()
        try:
            data = yaml.safe_load(text) or {}
            return Rule.model_validate({**data, "origin": rule.get("origin") or {}}).model_dump(mode="json")
        except (yaml.YAMLError, ValidationError) as error:
            print(f"Règle invalide : {str(error)[:300]}")
            if input("Corriger encore ? [O/n] ").strip().lower() == "n":
                return None


def cmd_instructions_add(args: argparse.Namespace) -> int:
    """An instruction in plain French, from the laptop (T21): the agent asks if it must, says what
    it understood, and saves the rule only once you validated it."""
    import yaml

    from skoolathome import summary
    from skoolathome.agent import instructions, llm
    from skoolathome.store import db
    from skoolathome.store import instructions as store

    text, today = " ".join(args.text).strip(), _today(args)
    if not text:
        sys.exit("Écris la consigne, par exemple : skoolathome instructions add \"Le prof d'histoire interroge à "
                 "chaque cours sur le cours précédent\"")
    problem = _preflight()
    if problem:
        sys.exit(problem)
    with _connect() as conn:
        student = summary.latest_student(conn)
        instruction_id = store.create(conn, text=text, student_id=student)
        instruction = store.get(conn, instruction_id)
    print("Lecture de la consigne par le modèle (une à deux minutes)…", flush=True)
    with instructions.runner(db.database_url(), llm.Models()) as run:
        step = run.start(instruction, student, today)
        while True:
            with _connect() as conn:
                step.save(conn, instruction_id)
            if step.status == "question":
                answer = ""
                while not answer:
                    answer = input(f"\nQuestion : {step.question}\n> ").strip()
                step = run.resume(instruction_id, {"kind": "answer", "text": answer})
            elif step.status == "proposed":
                print(f"\n{step.summary}\n")
                print(yaml.safe_dump({k: v for k, v in (step.proposal or {}).items() if v not in (None, "", [])
                                      and k != "origin"}, allow_unicode=True, sort_keys=False))
                choice = input("[v]alider, [m]odifier, [a]nnuler, ou corrige avec tes mots : ").strip()
                if choice.lower() in ("v", "valider"):
                    reply = {"kind": "approve"}
                elif choice.lower() in ("m", "modifier"):
                    edited = _edit_rule(step.proposal or {})
                    reply = {"kind": "edit", "rule": edited} if edited else {"kind": "reject"}
                elif choice.lower() in ("a", "annuler", ""):
                    reply = {"kind": "reject"}
                else:
                    reply = {"kind": "answer", "text": choice}
                print("…", flush=True)
                step = run.resume(instruction_id, reply)
            else:
                break
    if step.status == "saved":
        print(f"\n{step.summary}\nVersion {step.rule_version} de la règle {step.rule_id} : elle compte dès la "
              "prochaine soirée.")
    elif step.status == "rejected":
        print("\nConsigne annulée : aucune règle enregistrée.")
    else:
        print(f"\nPas de règle : {step.reason or step.status}")
    return 0 if step.status in ("saved", "rejected") else 1


def cmd_instructions_list(args: argparse.Namespace) -> int:
    from skoolathome.store import instructions as store

    states = {"pending": "à lire", "question": "question posée", "proposed": "à valider", "saved": "enregistrée",
              "rejected": "annulée", "failed": "pas comprise"}
    with _connect() as conn:
        rows = store.recent(conn)
    if not rows:
        print("Aucune consigne. Par exemple : skoolathome instructions add \"Le prof d'histoire interroge à chaque "
              "cours sur le cours précédent\"")
    for r in rows:
        what = f"« {r['text'][:70]} »" if r["text"] else "règle écrite avec le formulaire"
        print(f"{r['created_at']:%d/%m %H:%M}  {r['id']:<16} {states[r['status']]:<15} {what}")
        detail = r["question"] if r["status"] == "question" else r["reason"] if r["status"] == "failed" else r["summary"]
        if detail:
            print(f"{'':<17}{detail[:200]}")
    return 0


def cmd_instructions_corrections(args: argparse.Namespace) -> int:
    """Every [Modifier]: the sentence, the rule understood, the rule saved (future test cases, T31)."""
    from skoolathome.store import instructions as store

    with _connect() as conn:
        rows = store.corrections(conn)
    if not rows:
        print("Aucune correction : toutes les règles proposées ont été validées telles quelles.")
    for row in rows:
        changed = {k: (row["proposed"].get(k), v) for k, v in row["final"].items()
                   if k not in ("origin", "version") and row["proposed"].get(k) != v}
        print(f"« {row['text'][:100]} »")
        for key, (before, after) in changed.items():
            print(f"    {key} : {before} → {after}")
    return 0


def cmd_instructions_sync(args: argparse.Namespace) -> int:
    """Now, without waiting for the evening: fetch the parent's instructions and replies, run the
    graph of instructions, send back the questions and proposals. Two signatures."""
    from skoolathome import summary
    from skoolathome.agent import instructions, llm
    from skoolathome.rules import store as rules_store
    from skoolathome.store import db
    from skoolathome.store import instructions as store

    problem = _preflight()
    if problem:
        sys.exit(problem)
    sessions = _api_sessions()
    if sessions is None:
        return 1
    with _connect() as conn:
        student = summary.latest_student(conn)
    counts = _sync_feedback(sessions, student)
    print(f"Retours : {counts['instructions']} consigne(s), {counts['replies']} réponse(s) aux consignes")
    result = instructions.process(db.database_url(), llm.Models(), student, _today(args), progress=print)
    for error in result["errors"]:
        print(f"  erreur : {error}")
    with _connect() as conn:
        rules_store.ensure_defaults(conn)
        updates, rules = store.to_report(conn), rules_store.published(conn)
    with sessions() as client:
        client.publish([], instructions=updates, rules=rules)
    with _connect() as conn:
        store.reported(conn, [u.id for u in updates])
    print(f"{len(updates)} consigne(s) mise(s) à jour en ligne, {len(rules)} règles envoyées.")
    return 0


def _sync_feedback(sessions, student: str | None) -> dict:
    """GET /api/feedback since the cursor, stored on the laptop (one signature)."""
    from skoolathome.store import feedback as feedback_store

    with _connect() as conn:
        since = feedback_store.cursor(conn)
    with sessions() as client:
        data = client.feedback(since)
    with _connect() as conn:
        return feedback_store.save(conn, data, student)


def cmd_instructions_eval(args: argparse.Namespace) -> int:
    """The test sentences of evals/instructions/cases.json, read by the real model: is each one
    understood as expected? (T21; T31 puts it in make eval.)"""
    from skoolathome import summary
    from skoolathome.agent import evaluate_instructions

    problem = _preflight()
    if problem:
        sys.exit(problem)
    cases = json.loads(Path(args.cases).read_text())["cases"]
    with _connect() as conn:
        student = summary.latest_student(conn)
        results = evaluate_instructions.run(conn, cases, student, _today(args), progress=print)
    good = sum(1 for r in results if r["ok"])
    print(f"\nBien interprétées : {good}/{len(results)} ({round(100 * good / len(results))} %), seuil visé 90 %")
    return 0 if good / len(results) >= 0.9 else 1


def cmd_scores_send(args: argparse.Namespace) -> int:
    """The feedback kept on this laptop, sent to Langfuse as scores on each sheet's trace (T22).
    The evening does it by itself; this is the catch-up, e.g. after Langfuse was stopped."""
    from skoolathome import summary
    from skoolathome.agent import scores

    if args.fetch:  # what came back since the last evening, first (one signature)
        sessions = _api_sessions()
        if sessions is None:
            return 1
        with _connect() as conn:
            student = summary.latest_student(conn)
        counts = _sync_feedback(sessions, student)
        print("Retours récupérés : " + (", ".join(f"{n} {kind}" for kind, n in counts.items() if n) or "rien de nouveau"))
    with _connect() as conn:
        waiting, untraced = scores.pending(conn)
        if args.dry_run:
            for score in waiting[:30]:
                print(f"  {score.name:<22} {str(score.value):<12} {score.trace_id[:12]}…  {score.comment[:60]}")
            print(f"{len(waiting)} note(s) à envoyer, {untraced} retour(s) sans trace (fiches écrites à la main)")
            return 0
        result = scores.send(conn)
    if "skipped" in result:
        sys.exit(f"Rien envoyé : {result['skipped']} (deploy/langfuse/langfuse.sh start).")
    sent = sum(v for k, v in result.items() if k != "untraced")
    print(f"{sent} note(s) envoyée(s) à Langfuse : " + ", ".join(f"{k} {v}" for k, v in result.items() if k != "untraced")
          + (f". {result['untraced']} retour(s) sans trace, ignoré(s)." if result["untraced"] else ""))
    return 0


def cmd_scores_views(args: argparse.Namespace) -> int:
    """The saved views to create once in Langfuse (T22): filter the scores by rule, model, reason."""
    from skoolathome.agent import scores, tracing

    project = tracing.project_url()
    print("Dans Langfuse, ouvre la page indiquée, pose les filtres, puis « Save view » (en haut du tableau) :\n")
    for name, page, filters in scores.VIEWS:
        where = f"{project}/{page.lower()}" if project else page
        print(f"  {name}\n    page    : {where}\n    filtres : {filters}\n")
    if project is None:
        print("(Langfuse injoignable : les adresses complètes s'afficheront quand il tournera.)")
    return 0


def _names(args: argparse.Namespace) -> list[str]:
    """The names to take out of a case: the teachers, the family's names (kept with --name)."""
    from skoolathome.evaluation import cases

    if getattr(args, "name", None):
        cases.remember_names(args.name)
    with _connect() as conn:
        return cases.private_names(conn)


def cmd_eval_add_sheet(args: argparse.Namespace) -> int:
    """A real sheet becomes a case of the reference set: what the model read, anonymized (T23)."""
    from skoolathome.evaluation import cases

    names = _names(args)
    with _connect() as conn:
        try:
            case = cases.from_sheet(conn, args.sheet, names)
        except LookupError as error:
            sys.exit(str(error))
        if not any(text.strip() for text in case.input["sources"].values()):
            sys.exit("Cette fiche n'a plus de source lisible : rien à rejouer.")
        cases.save(conn, case)
    print(f"Cas {case.id} ajouté au jeu « fiches » ({len(case.input['sources'])} source(s), noms remplacés : "
          f"{len(names)}). Relis-le avec : skoolathome eval show {case.id}")
    return 0


def cmd_eval_add_report(args: argparse.Namespace) -> int:
    """A report of the tablet becomes a case, with the right answer (T23): one command."""
    from skoolathome.evaluation import cases

    names = _names(args)
    with _connect() as conn:
        try:
            case = cases.from_report(conn, args.report, names, args.answer or "")
        except (LookupError, ValueError) as error:
            sys.exit(str(error))
        cases.save(conn, case)
    report = case.expected["report"]
    print(f"Cas {case.id} ajouté : {report['reason']} sur {report['item']} « {report['prompt'][:80]} »"
          + (f", bonne réponse « {report['correct_answer']} »" if report.get("correct_answer") else ""))
    return 0


def cmd_eval_add_tests(args: argparse.Namespace) -> int:
    """The homework that announced each test seen in the grades becomes a case (jeu « contrôles »)."""
    from skoolathome import summary
    from skoolathome.evaluation import cases

    with _connect() as conn:
        student = summary.latest_student(conn)
        found = cases.from_grades(conn, student, args.days) if student else []
        for case in found:
            cases.save(conn, case)
    print(f"{len(found)} devoir(s) annonçant un contrôle ajouté(s) au jeu « contrôles ».")
    return 0


def cmd_eval_reports(args: argparse.Namespace) -> int:
    """The reports received from the tablet, and whether each one is a case already."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT r.id, r.created_at, r.reason, r.sheet_id, r.item_id, r.comment, "
            "EXISTS (SELECT 1 FROM agent.eval_cases c WHERE c.id = 'report-' || r.id) "
            "FROM feedback.reports r ORDER BY r.created_at DESC LIMIT 40").fetchall()
    if not rows:
        print("Aucun signalement reçu.")
    for report_id, created, reason, sheet, item, comment, kept in rows:
        print(f"{report_id:>5}  {created:%d/%m}  {reason:<13} {sheet} {item or ''}  {comment[:50]}"
              + ("  (dans le jeu)" if kept else ""))
    return 0


def cmd_eval_list(args: argparse.Namespace) -> int:
    from skoolathome.evaluation import cases

    with _connect() as conn:
        cases.load_traps(conn)
        found = cases.load(conn, args.family)
    for case in found:
        what = case.metadata.get("note") or case.metadata.get("reason") or case.metadata.get("sheet") or ""
        print(f"{case.family:<7} {case.origin:<7} {case.id:<45} {what[:70]}")
    print(f"{len(found)} cas")
    return 0


def cmd_eval_show(args: argparse.Namespace) -> int:
    from skoolathome.evaluation import cases

    with _connect() as conn:
        found = next((c for c in cases.load(conn) if c.id == args.id), None)
    if found is None:
        sys.exit(f"Cas inconnu : {args.id}")
    print(json.dumps({"input": found.input, "expected": found.expected, "metadata": found.metadata},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_eval_sync(args: argparse.Namespace) -> int:
    from skoolathome.agent import tracing
    from skoolathome.evaluation import cases

    with _connect() as conn:
        traps = cases.load_traps(conn)
        sent = cases.sync(conn)
    print(f"{traps} piège(s) chargé(s) depuis evals/traps.json. "
          + (f"{sent} cas envoyé(s) aux jeux de données Langfuse." if tracing.client() else
             "Langfuse non configuré : jeu gardé sur ce portable seulement."))
    return 0


def cmd_eval_run(args: argparse.Namespace) -> int:
    """`make eval`: the candidate (prompts labelled staging) on the reference set, compared to the
    production run; exit code 1 when a blocking threshold is not held (T23)."""
    from skoolathome.agent import llm
    from skoolathome.evaluation import cases, runner

    problem = _preflight()
    if problem:
        sys.exit(problem)
    families = runner.FAMILIES if args.family == "all" else (args.family,)
    passed = True
    with _connect() as conn:
        cases.load_traps(conn)
        for family in families:
            print(f"\nJeu « {runner.FAMILY_NAMES[family]} », consignes {args.label}… (une fiche prend 7 à 10 minutes)",
                  flush=True)
            try:
                run = runner.evaluate(conn, family, args.label, llm.Models(), limit=args.limit, only=args.case,
                                      progress=lambda message: print(message, flush=True))
            except LookupError as error:
                print(error)
                continue
            baseline = None if args.label == "production" else runner.latest(conn, family, "production")
            print("\n" + runner.report(run, baseline))
            if run.experiment:
                print(f"Expérience Langfuse : « {run.experiment} » (Datasets → {cases.DATASETS[family]} → Runs)")
            passed &= run.passed
    return 0 if passed else 1


def cmd_eval_promote(args: argparse.Namespace) -> int:
    """`make promote`: the staging prompts go to production, only those whose make eval passed."""
    from skoolathome.agent import tracing
    from skoolathome.evaluation import runner

    if tracing.client() is None:
        sys.exit("Langfuse non configuré : les étiquettes des consignes sont dans Langfuse.")
    with _connect() as conn:
        result = runner.promote(conn, dry_run=args.dry_run)
    for name, (before, version) in result.moved.items():
        print(f"{name} : version {version} {'serait' if args.dry_run else ''} en production (avant : {before})")
    for name, why in result.refused.items():
        print(f"{name} : refusé, {why}")
    if not result.moved and not result.refused:
        print("Aucune consigne en staging différente de production.")
    return 1 if result.refused else 0


def cmd_eval_recall(args: argparse.Namespace) -> int:
    """Tests seen in the grades, and how many were detected before their day (§14.2)."""
    from datetime import timedelta

    from skoolathome import summary
    from skoolathome.evaluation import cases

    until = _today(args)
    with _connect() as conn:
        student = summary.latest_student(conn)
        found = cases.observed_recall(conn, student, until - timedelta(days=args.days), until)
    if not found["observed"]:
        print("Aucun contrôle dans les notes sur la période, depuis la première collecte.")
        return 0
    print(f"Contrôles constatés dans les notes : {found['observed']}, détectés avant : {found['detected']} "
          f"({round(100 * found['detected'] / found['observed'])} %, seuil 100 %)")
    for miss in found["missed"]:
        print(f"  raté : {miss['subject']} le {miss['day']} ({miss['title']})")
    return 0 if not found["missed"] else 1


def cmd_review(args: argparse.Namespace) -> int:
    """The weekly review (T24): the evening sends it by itself from Friday on. Here: to look at it
    any day, and with --send to send it now (one signature)."""
    from skoolathome import summary
    from skoolathome.evaluation import weekly

    day = _today(args)
    with _connect() as conn:
        student = summary.latest_student(conn)
        if student is None:
            sys.exit("Aucune collecte en base : lance d'abord skoolathome collect.")
        review = weekly.compute(conn, student, day)
    payload = review.payload
    print(f"{payload.title} — du {payload.start:%d/%m} au {payload.end:%d/%m}\n{payload.body}\n")
    for indicator in payload.indicators:
        print(f"{'⚠ ' if indicator.alert else '  '}{indicator.label} : {indicator.value}"
              + (f"\n      {indicator.detail}" if indicator.detail else ""))
    if payload.to_annotate:
        print("\nÀ noter dans la vue parent (Bilan) :")
        for sheet in payload.to_annotate:
            print(f"  {sheet.id} — {sheet.subject} : {sheet.title}")
    if args.send:
        sessions = _api_sessions()
        if sessions is None:
            return 1
        with _connect() as conn:
            weekly.save(conn, review)
        with sessions() as client:
            client.publish([], review=payload)
        with _connect() as conn:
            weekly.sent(conn, payload.week)
        print("\nBilan envoyé aux appareils parents.")
    return 0


def cmd_rules_status(args: argparse.Namespace) -> int:
    from skoolathome.rules import store

    with _connect() as conn:
        try:
            saved = store.set_status(conn, args.id, args.status)
        except LookupError as error:
            sys.exit(str(error))
    print(f"Règle {saved.id} : version {saved.version}, {saved.status}.")
    return 0


def _material_query(conn, lesson_id: str | None, text: str | None) -> tuple[str, str | None]:
    """(text to search with, subject) for a lesson id, or a free text."""
    if text:
        return text, None
    row = conn.execute("SELECT content, subject_code, day FROM school.lessons WHERE source_id = %s", (lesson_id,)).fetchone()
    if row is None:
        sys.exit(f"Séance inconnue : {lesson_id}")
    content, subject, day = row
    if not content.strip():
        homework = conn.execute("SELECT string_agg(text, ' ') FROM school.homework WHERE subject_code = %s AND given_on = %s",
                                (subject, day)).fetchone()[0]
        content = homework or ""
    return content, subject


def cmd_material_index(args: argparse.Namespace) -> int:
    """Index the drop folder now, instead of waiting for the evening (T18)."""
    from skoolathome.collectors.manual.source import ManualSource
    from skoolathome.material import index

    embedder = _embedder()
    if embedder is None:
        return 1
    with _connect() as conn:
        result = index.sync(conn, ManualSource(Path(args.root)), embedder)
    print(f"{len(result.indexed)} indexé(s), {result.unchanged} inchangé(s), {len(result.removed)} retiré(s)")
    for name in result.indexed:
        print(f"  + {name}")
    for name in result.unsupported:
        print(f"  ? {name} : format non traité")
    for failure in result.failed:
        print(f"  ✗ {failure['document']} : {failure['error']}")
    return 1 if result.failed else 0


def cmd_material_list(args: argparse.Namespace) -> int:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT d.id, d.source_ref, d.subject_code, d.first_page, d.page_count, d.ocr, d.status, d.error, "
            "(SELECT count(*) FROM material.chunks c WHERE c.document_id = d.id) FROM material.documents d ORDER BY d.source_ref"
        ).fetchall()
    if not rows:
        print("Aucun document. Dépose des PDF dans data/manual/<matière>/, puis skoolathome material index.")
    for doc_id, ref, subject, first, pages, ocr, status, error, pieces in rows:
        span = f"p. {first} à {first + pages - 1}" if pages else "pas de page"
        print(f"{doc_id:>3}  {ref:<45} {subject or 'toutes':<7} {span:<14} {'OCR ' if ocr else ''}{status}"
              + (f" ({pieces} morceaux)" if status == "indexed" else f" : {error}"))
    return 0


def cmd_material_search(args: argparse.Namespace) -> int:
    """The pages found for a lesson, with their score: what gather_sources would add."""
    from skoolathome.material import search

    embedder = _embedder()
    if embedder is None:
        return 1
    with _connect() as conn:
        query, subject = _material_query(conn, args.lesson, args.text)
        if len(query.strip()) < 10:
            sys.exit("Rien à chercher : la séance est vide et aucun devoir n'a été donné ce jour-là.")
        hits = search.similar_pages(conn, embedder.embed([query])[0], subject, limit=args.limit)
    for hit in hits:
        kept = "gardée" if hit.score >= search.MIN_SCORE else "sous le seuil"
        print(f"  {hit.score:.3f}  {hit.label:<30} ({hit.title}, {kept})")
    if not hits:
        print("Aucune page indexée pour cette matière.")
    return 0


def cmd_material_eval(args: argparse.Namespace) -> int:
    """Recall@3 on the cases of evals/retrieval/cases.json: the right page among the first three (T18, T31)."""
    from skoolathome.material import search

    cases = json.loads(Path(args.cases).read_text()).get("cases", [])
    if not cases:
        sys.exit(f"Aucun cas dans {args.cases} : ajoute-en (voir tmp/T18-depot-manuel-index.md).")
    embedder = _embedder()
    if embedder is None:
        return 1
    found = 0
    with _connect() as conn:
        for case in cases:
            query, subject = _material_query(conn, case.get("lesson"), case.get("query"))
            hits = search.similar_pages(conn, embedder.embed([query])[0], subject, limit=search.TOP_PAGES)
            documents = {row[0]: (row[1].lower(), row[2].lower()) for row in conn.execute(
                "SELECT id, source_ref, title FROM material.documents").fetchall()}
            expected = [(e["document"].lower(), e["page"]) for e in case["expected"]]
            ok = any(page == hit.page and name in documents.get(hit.document_id, ())
                     for name, page in expected for hit in hits)
            found += ok
            print(f"{'✓' if ok else '✗'} {case.get('note') or case.get('lesson') or case.get('query')[:40]} : "
                  + ", ".join(f"{h.label} ({h.score:.2f})" for h in hits))
    print(f"\nRappel@3 : {found}/{len(cases)} ({found / len(cases):.0%}), seuil visé 90 %")
    return 0


def cmd_sheets_list(args: argparse.Namespace) -> int:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT run_date, id, status, attempts, reason FROM agent.sheets "
            + ("WHERE run_date = %s " if args.date else "") + "ORDER BY run_date DESC, id LIMIT 50",
            (date.fromisoformat(args.date),) if args.date else (),
        ).fetchall()
    for run_date, sheet_id, status, attempts, reason in rows:
        print(f"{run_date}  {sheet_id:<36} {status:<10} {attempts} gén." + (f"  {reason[:100]}" if reason else ""))
    return 0


def cmd_sheets_show(args: argparse.Namespace) -> int:
    with _connect() as conn:
        row = conn.execute("SELECT body, status, reason FROM agent.sheets WHERE id = %s", (args.id,)).fetchone()
    if row is None:
        sys.exit(f"Fiche inconnue : {args.id}")
    if row[0] is None:
        sys.exit(f"Abstention : {row[2]}")
    print(json.dumps(row[0], ensure_ascii=False, indent=2))
    return 0


def cmd_sheets_stats(args: argparse.Namespace) -> int:
    """Blocking rate and reasons (T16): to measure on 20 real generations."""
    from collections import Counter

    since = date.fromisoformat(args.since) if args.since else date(2000, 1, 1)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, attempts, guards FROM agent.sheets WHERE run_date >= %s", (since,)
        ).fetchall()
    if not rows:
        print("Aucune fiche générée sur la période.")
        return 0
    total = len(rows)
    abstained = sum(1 for status, _, _ in rows if status == "abstained")
    regenerated = sum(1 for _, attempts, _ in rows if attempts > 1)
    generations = sum(attempts for _, attempts, _ in rows)
    failures = Counter(g["name"] for _, _, log in rows for g in log if not g["ok"])
    checks = Counter(g["name"] for _, _, log in rows for g in log)
    print(f"{total} tâches, {generations} générations")
    print(f"  abstentions (bloquées)  : {abstained} ({abstained / total:.0%})")
    print(f"  régénérées au moins 1×  : {regenerated} ({regenerated / total:.0%})")
    print("  échecs par garde-fou (sur toutes les générations) :")
    for name in ("format", "citation", "answer_in_quote", "maths", "correction", "duration", "structure",
                 "focus", "judge"):
        if checks[name]:
            print(f"    {name:<16} {failures[name]:>3} / {checks[name]}")
    reasons = Counter(r.split(" : ", 1)[-1][:70] for _, _, log in rows for g in log for r in g["reasons"])
    if reasons:
        print("  motifs les plus fréquents :")
        for reason, count in reasons.most_common(8):
            print(f"    {count:>3} × {reason}")
    return 0


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


def _api_connect():
    import psycopg

    from skoolathome.api import settings

    return psycopg.connect(settings.database_url(), autocommit=True)


def cmd_api_migrate(args: argparse.Namespace) -> int:
    """Migrate the online database (Neon), named by DATABASE_URL."""
    from skoolathome.api import MIGRATIONS_DIR
    from skoolathome.store.migrate import migrate

    with _api_connect() as conn:
        applied = migrate(conn, MIGRATIONS_DIR)
    print("Migrations appliquées : " + (", ".join(applied) if applied else "aucune, tout est à jour"))
    return 0


def cmd_api_seed(args: argparse.Namespace) -> int:
    """Publish a hand-written sheet straight into the online database."""
    from skoolathome.api.app import save_sheet
    from skoolathome.schema.sheet import Sheet

    try:
        sheet = Sheet.model_validate_json(Path(args.file).read_text())
    except ValidationError as error:
        sys.exit(f"Fiche invalide :\n{error}")
    if args.visible_from:
        sheet = sheet.model_copy(update={"visible_from": date.fromisoformat(args.visible_from)})
    with _api_connect() as conn:
        save_sheet(conn, sheet)
    print(f"Fiche {sheet.id} publiée, visible à partir du {sheet.visible_from}.")
    return 0


def cmd_api_vapid_keys(args: argparse.Namespace) -> int:
    """Print a new VAPID key pair, to put in the Vercel environment variables."""
    from skoolathome.api.push import generate_vapid_keys

    public, private = generate_vapid_keys()
    print(f"SKOOLATHOME_VAPID_PUBLIC_KEY={public}")
    print(f"SKOOLATHOME_VAPID_PRIVATE_KEY={private}")
    return 0


def cmd_api_serve(args: argparse.Namespace) -> int:
    """Run the API and the built PWA on this machine, for tests."""
    import uvicorn

    uvicorn.run("skoolathome.api.app:app", host=args.host, port=args.port)
    return 0


def cmd_signer_new_token(args: argparse.Namespace) -> int:
    """Create the agent token, keep it here, and print what the vault needs: its hash."""
    from skoolathome import secrets
    from skoolathome.publish.nip46 import SECRET_NAME, new_token

    if not args.uri.startswith("bunker://"):
        sys.exit("L'adresse doit commencer par bunker:// (elle est affichée par bunkerd au démarrage).")
    if secrets.read(SECRET_NAME) and not args.replace:
        sys.exit("Un jeton existe déjà. Ajoute --replace pour en créer un nouveau (l'ancien ne servira plus).")
    token, digest = new_token()
    secrets.write(SECRET_NAME, {"uri": args.uri, "token": token})
    agents = {"agents": [{"name": "skoolathome", "identity": args.identity, "token_sha256": digest}]}
    print("Jeton enregistré dans ~/.config/skoolathome/bunker.json (mode 600).")
    print("Variable BUNKER_AGENTS_JSON du service bunkerd sur Railway :")
    print(json.dumps(agents))
    return 0


def cmd_signer_test(args: argparse.Namespace) -> int:
    """Sign one event through the bunker. Kind 27235 must pass, kind 1 must be refused."""
    from skoolathome.publish.nip46 import NIP98_KIND, SigningRefused, nip98_tags, sign

    tags = nip98_tags(args.url, "GET") if args.kind == NIP98_KIND else []
    try:
        event = sign(args.kind, "" if args.kind == NIP98_KIND else "test skoolathome", tags)
    except SigningRefused as error:
        print(f"Refusé (type {args.kind}) : {error}")
        return 1
    except FileNotFoundError as error:
        sys.exit(str(error))
    print(f"Signé à distance (type {args.kind})")
    print(f"  identifiant : {event.id().to_hex()}")
    print(f"  auteur      : {event.author().to_bech32()}")
    return 0


def cmd_system_memory(args: argparse.Namespace) -> int:
    """What fills the RAM and the swap (T14), to free memory before running the model."""
    from skoolathome import sysinfo

    memory = sysinfo.memory()
    print(memory.describe())
    users = sysinfo.swap_users(limit=10_000)
    if not users:
        print("Rien dans le swap.")
        return 0
    print(f"\nEn swap ({sysinfo.gib(sum(u.swap for u in users))} visibles, tes processus seulement) :")
    for name, count, swap in sysinfo.by_program(users)[:12]:
        print(f"  {swap / 2**20:>6.0f} Mo  {name}" + (f" ({count} processus)" if count > 1 else ""))
    hidden = memory.swap_used - sum(u.swap for u in users)
    if hidden > 0:
        print(f"  {hidden / 2**20:>6.0f} Mo  processus système, invisibles sans root (sudo pour le détail)")
    print("\nPour vider le swap une fois de la RAM libérée (il faut plus de RAM disponible que de swap utilisé) :")
    print("  sudo swapoff -a && sudo swapon -a")
    return 0


def cmd_llm_bench(args: argparse.Namespace) -> int:
    """Measure each model on a fixed lesson: reading and writing speed, load time, memory."""
    from skoolathome.agent import bench, config

    conf = config.load()
    models = args.model or [m.model for m in conf.models.values() if m.max_tokens]  # not the embedder
    ollama = bench.Ollama(conf.engine.native_url)
    print(bench.HEADER)
    for model in models:
        for run in range(1, args.runs + 1):
            try:
                print(bench.measure(ollama, model, run).row(), flush=True)
            except Exception as error:  # noqa: BLE001  (show the failure and go on with the next model)
                print(f"| `{model}` | échec : {error} |||||| ")
    print("\nCopie ce tableau dans tmp/T14-ollama-langfuse.md.")
    return 0


def cmd_llm_try(args: argparse.Namespace) -> int:
    """The first traced call: summarize a lesson with the prompt managed in Langfuse."""
    from datetime import datetime

    from skoolathome.agent import bench, config, llm, prompts, tracing

    conf = config.load()
    model = conf.model("generator")
    prompt = prompts.get("lesson-summary")
    content = Path(args.file).read_text() if args.file else bench.SAMPLE_LESSON
    messages = prompt.compile(subject="Mathématiques", day="lundi 10 novembre", content=content)
    trace = tracing.trace_id("llm-try", datetime.now().isoformat())
    metadata = {**model.metadata(conf.engine), "prompt.lesson-summary": prompt.label}
    with tracing.observe("lesson-summary", trace, trace_name="llm try", metadata=metadata,
                         prompt=prompt.client, input={"content": content}) as span:
        text = llm.Models(conf).text("generator", messages, tracing.callbacks())
        if span is not None:
            span.update(output=text)
    tracing.flush()
    print(text)
    print(f"\nModèle {model.model}, consigne lesson-summary {prompt.label}, version du code {tracing.release()}")
    url = tracing.trace_url(trace)
    print(f"Trace : {url}" if url else "Langfuse non configuré : pas de trace.")
    return 0


def cmd_prompt_push(args: argparse.Namespace) -> int:
    """Send the local prompts (src/skoolathome/agent/prompts/*.md) to Langfuse, with a label."""
    import logging

    from skoolathome.agent import prompts

    logging.getLogger("langfuse").setLevel(logging.ERROR)  # "not found" is expected for a first version
    for name in args.names or prompts.names():
        try:
            version, created = prompts.push(name, args.label)
        except RuntimeError as error:
            sys.exit(str(error))
        state = "nouvelle version" if created else "inchangée"
        print(f"{name} : version {version} ({state}), étiquette {args.label}")
    return 0


def cmd_api_set_url(args: argparse.Namespace) -> int:
    from skoolathome import secrets
    from skoolathome.publish.client import API_SECRET_NAME

    if not args.url.startswith("https://"):
        sys.exit("L'adresse doit commencer par https://")
    secrets.write(API_SECRET_NAME, {"url": args.url.rstrip("/")})
    print(f"Adresse de l'API enregistrée : {args.url.rstrip('/')}")
    return 0


def _signed_api(action):
    """Run `action(client)` in one bunker session, and say how many signatures it took."""
    from skoolathome.publish.client import ApiClient, ApiError, api_url
    from skoolathome.publish.nip46 import BunkerSession, SigningRefused

    try:
        with BunkerSession() as session:
            result = action(ApiClient(api_url(), session))
            print(f"({session.signatures} signature{'s' if session.signatures > 1 else ''} demandée"
                  f"{'s' if session.signatures > 1 else ''} au bunker)")
            return result
    except FileNotFoundError as error:
        sys.exit(str(error))
    except SigningRefused as error:
        sys.exit(f"Le bunker n'a pas signé : {error}")
    except ApiError as error:
        sys.exit(f"L'API a refusé : {error}")


def cmd_pair(args: argparse.Namespace) -> int:
    """A code to pair a phone, a tablet or a computer: scan the QR code, open the link, or type the code."""
    import segno

    label = args.label or ("Parent" if args.role == "parent" else "Enfant")
    data = _signed_api(lambda client: client.pairing_code(args.role, label))
    site = data["url"].split("/#/")[0]
    print()
    segno.make(data["url"], error="m").terminal(compact=True)
    print(f"\n  Code : {data['code']}    ({'parent' if args.role == 'parent' else 'enfant'}, "
          f"valable {data['expires_in_minutes']} min, une seule fois)\n")
    print("  Téléphone ou tablette : scanne ce QR code avec l'appareil photo, puis touche le lien.")
    print(f"  Ordinateur : ouvre {site} et tape le code.")
    print(f"  Ou envoie ce lien par message : {data['url']}")
    return 0


def cmd_api_bootstrap_code(args: argparse.Namespace) -> int:
    """A bootstrap code easy to type on a phone, for SKOOLATHOME_BOOTSTRAP_CODE."""
    from skoolathome.api.auth import new_code

    print(new_code(16))
    print("\nÀ mettre dans Vercel (SKOOLATHOME_BOOTSTRAP_CODE), puis redéploie. Il n'associe que le tout premier "
          "appareil parent. En le tapant, majuscules, espaces et tirets ne comptent pas.\n"
          "Plus simple encore, sans lui : skoolathome pair")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    """Publish hand-written or generated sheets in one signed batch."""
    from skoolathome.schema.sheet import Sheet

    sheets = []
    for name in args.files:
        try:
            sheet = Sheet.model_validate_json(Path(name).read_text())
        except ValidationError as error:
            sys.exit(f"Fiche invalide ({name}) :\n{error}")
        if args.visible_from:
            sheet = sheet.model_copy(update={"visible_from": date.fromisoformat(args.visible_from)})
        sheets.append(sheet)
    published = _signed_api(lambda client: client.publish(sheets))
    for sheet in sheets:
        print(f"Fiche {sheet.id} publiée, visible à partir du {sheet.visible_from}.")
    return 0 if len(published) == len(sheets) else 1


def cmd_feedback(args: argparse.Namespace) -> int:
    """What came back from the tablet since the cursor: answers, reports, finished sheets."""
    data = _signed_api(lambda client: client.feedback(args.since))
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"{len(data['attempts'])} réponse(s), {len(data['reports'])} signalement(s), "
          f"{len(data['finishes'])} fiche(s) terminée(s)")
    for report in data["reports"]:
        print(f"  signalement {report['reason']} : {report['sheet_id']} {report['item_id'] or ''} {report['comment']}")
    print(f"Prochain appel : skoolathome feedback --since {data['until']}")
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

    evening = commands.add_parser("evening", help="what the timer runs: collect, summary, then the sheets")
    evening.add_argument("--student", type=int)
    evening.add_argument("--today")
    evening.add_argument("--no-collect", action="store_true", help="work on what is already in the database")
    evening.add_argument("--no-publish", action="store_true", help="keep the sheets on this laptop")
    evening.add_argument("--lesson", action="append",
                         help="a lesson id (school.lessons.source_id) to work on, outside the rules")
    evening.add_argument("--format", default="exercises",
                         choices=["exercises", "quick_quiz", "lesson_recap", "test_revision"],
                         help="with --lesson: the sheet format to make")
    evening.add_argument("--restart", action="store_true", help="start again even if an interrupted run exists")
    evening.add_argument("--only-requests", action="store_true",
                         help="only the sheets the parent asked for (skoolathome ask, or the PWA); no collection")
    evening.set_defaults(run=cmd_evening)

    sheets = commands.add_parser("sheets", help="sheets generated on this laptop").add_subparsers(dest="action", required=True)
    listing = sheets.add_parser("list", help="the latest sheets and abstentions")
    listing.add_argument("--date", help="run date (YYYY-MM-DD)")
    listing.set_defaults(run=cmd_sheets_list)
    show = sheets.add_parser("show", help="print a sheet as JSON")
    show.add_argument("id")
    show.set_defaults(run=cmd_sheets_show)
    stats = sheets.add_parser("stats", help="blocking rate and reasons of the guards")
    stats.add_argument("--since", help="first run date (YYYY-MM-DD)")
    stats.set_defaults(run=cmd_sheets_stats)

    ed = commands.add_parser("ed", help="École Directe tools").add_subparsers(dest="action", required=True)
    validate = ed.add_parser("validate", help="validate saved raw responses")
    validate.add_argument("directory")
    validate.set_defaults(run=cmd_ed_validate)
    anonymize = ed.add_parser("anonymize", help="make test fixtures from saved raw responses")
    anonymize.add_argument("source")
    anonymize.add_argument("target")
    anonymize.add_argument("--student", type=int, required=True)
    anonymize.set_defaults(run=cmd_ed_anonymize)

    api = commands.add_parser("api", help="online API tools").add_subparsers(dest="action", required=True)
    api.add_parser("migrate", help="migrate the online database (DATABASE_URL)").set_defaults(run=cmd_api_migrate)
    seed = api.add_parser("seed", help="publish a hand-written sheet")
    seed.add_argument("file")
    seed.add_argument("--visible-from", help="override the display date (YYYY-MM-DD)")
    seed.set_defaults(run=cmd_api_seed)
    api.add_parser("vapid-keys", help="print a new key pair for Web Push").set_defaults(run=cmd_api_vapid_keys)
    api.add_parser("bootstrap-code", help="print a new bootstrap code, easy to type").set_defaults(run=cmd_api_bootstrap_code)
    set_url = api.add_parser("set-url", help="save the API address used by publish and feedback")
    set_url.add_argument("url")
    set_url.set_defaults(run=cmd_api_set_url)
    serve = api.add_parser("serve", help="run the API locally")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(run=cmd_api_serve)

    signer = commands.add_parser("signer", help="remote signer (bunker)").add_subparsers(dest="action", required=True)
    token = signer.add_parser("new-token", help="create the agent token for the bunker")
    token.add_argument("--uri", required=True, help="bunker://… as printed by bunkerd")
    token.add_argument("--identity", required=True, help="npub of the identity, as printed by bunkerd")
    token.add_argument("--replace", action="store_true")
    token.set_defaults(run=cmd_signer_new_token)
    test = signer.add_parser("test", help="sign one event through the bunker")
    test.add_argument("--kind", type=int, default=27235)
    test.add_argument("--url", default="https://example.invalid/api/test")
    test.set_defaults(run=cmd_signer_test)

    system = commands.add_parser("system", help="this laptop").add_subparsers(dest="action", required=True)
    system.add_parser("memory", help="RAM, swap, and what fills the swap").set_defaults(run=cmd_system_memory)

    llm = commands.add_parser("llm", help="local model").add_subparsers(dest="action", required=True)
    bench = llm.add_parser("bench", help="measure the models (Ollama): tokens/s, load time, memory")
    bench.add_argument("--model", action="append", help="a model name (default: those of models.toml)")
    bench.add_argument("--runs", type=int, default=2)
    bench.set_defaults(run=cmd_llm_bench)
    try_ = llm.add_parser("try", help="one call traced in Langfuse: summarize a lesson")
    try_.add_argument("--file", help="a text file to summarize (default: a sample lesson)")
    try_.set_defaults(run=cmd_llm_try)

    prompt = commands.add_parser("prompt", help="model prompts in Langfuse").add_subparsers(dest="action", required=True)
    push_ = prompt.add_parser("push", help="send the local copies to Langfuse")
    push_.add_argument("names", nargs="*", help="default: every prompt")
    push_.add_argument("--label", default="production", choices=["production", "staging"])
    push_.set_defaults(run=cmd_prompt_push)

    publish = commands.add_parser("publish", help="publish sheets in one batch signed by the bunker (NIP-98)")
    publish.add_argument("files", nargs="+")
    publish.add_argument("--visible-from", help="override the display date of every sheet (YYYY-MM-DD)")
    publish.set_defaults(run=cmd_publish)
    feedback = commands.add_parser("feedback", help="answers and reports received by the API (NIP-98)")
    feedback.add_argument("--since", type=int, default=0, help="cursor printed by the previous call")
    feedback.add_argument("--json", action="store_true")
    feedback.set_defaults(run=cmd_feedback)

    review = commands.add_parser("review", help="the weekly review: indicators, alerts, sheets to annotate (T24)")
    review.add_argument("--today")
    review.add_argument("--send", action="store_true", help="send it now to the parent devices")
    review.set_defaults(run=cmd_review)

    evals = commands.add_parser("eval", help="reference set and non-regression: make eval (T23)").add_subparsers(
        dest="action", required=True)
    run_eval = evals.add_parser("run", help="the graphs on the reference set; exit 1 below a blocking threshold")
    run_eval.add_argument("--label", default="staging", choices=["staging", "production"],
                          help="which prompts: the candidate (staging) or the baseline (production)")
    run_eval.add_argument("--family", default="all", choices=["all", "sheets", "tests"])
    run_eval.add_argument("--limit", type=int, help="the first N cases only (a quick look, not a decision)")
    run_eval.add_argument("--case", action="append", help="this case only (repeatable)")
    run_eval.set_defaults(run=cmd_eval_run)
    promote_ = evals.add_parser("promote", help="staging prompts to production, if their make eval passed")
    promote_.add_argument("--dry-run", action="store_true")
    promote_.set_defaults(run=cmd_eval_promote)
    add_sheet = evals.add_parser("add-sheet", help="a sheet of this laptop becomes a real case, anonymized")
    add_sheet.add_argument("sheet")
    add_sheet.add_argument("--name", action="append", help="a family name to take out (kept for next time)")
    add_sheet.set_defaults(run=cmd_eval_add_sheet)
    add_report = evals.add_parser("add-report", help="a report of the tablet becomes a case")
    add_report.add_argument("report", type=int, help="its id (skoolathome eval reports lists them)")
    add_report.add_argument("--answer", help="the right answer, for a wrong_answer report")
    add_report.add_argument("--name", action="append")
    add_report.set_defaults(run=cmd_eval_add_report)
    add_tests = evals.add_parser("add-tests", help="homework announcing the tests seen in the grades")
    add_tests.add_argument("--days", type=int, default=120)
    add_tests.set_defaults(run=cmd_eval_add_tests)
    evals.add_parser("reports", help="the reports received, to turn into cases").set_defaults(run=cmd_eval_reports)
    list_eval = evals.add_parser("list", help="the cases of the reference set")
    list_eval.add_argument("--family", choices=["sheets", "tests"])
    list_eval.set_defaults(run=cmd_eval_list)
    show_eval = evals.add_parser("show", help="a case, as the model will read it")
    show_eval.add_argument("id")
    show_eval.set_defaults(run=cmd_eval_show)
    evals.add_parser("sync", help="load the traps and send the cases to Langfuse").set_defaults(run=cmd_eval_sync)
    recall = evals.add_parser("recall", help="tests seen in the grades, detected beforehand or not")
    recall.add_argument("--days", type=int, default=60)
    recall.add_argument("--today")
    recall.set_defaults(run=cmd_eval_recall)

    scores_ = commands.add_parser("scores", help="real feedback as Langfuse scores (T22)").add_subparsers(
        dest="action", required=True)
    send_scores = scores_.add_parser("send", help="send the feedback not sent yet, on each sheet's trace")
    send_scores.add_argument("--dry-run", action="store_true", help="show what would be sent")
    send_scores.add_argument("--fetch", action="store_true", help="first fetch the feedback from the API (NIP-98)")
    send_scores.set_defaults(run=cmd_scores_send)
    scores_.add_parser("views", help="the saved views to create in Langfuse").set_defaults(run=cmd_scores_views)

    pair = commands.add_parser("pair", help="a code and a QR code to pair a phone, a tablet or a computer")
    pair.add_argument("--role", choices=["parent", "child"], default="parent")
    pair.add_argument("--label", help="the device's name, e.g. « iPhone de Noël » (default: Parent / Enfant)")
    pair.set_defaults(run=cmd_pair)

    plan_ = commands.add_parser("plan", help="the evening plan, without any model: tasks and skipped tasks")
    plan_.add_argument("--today")
    plan_.add_argument("--student", type=int)
    plan_.set_defaults(run=cmd_plan)

    rules = commands.add_parser("rules", help="the agent's rules").add_subparsers(dest="action", required=True)
    rules.add_parser("list", help="latest version of every rule").set_defaults(run=cmd_rules_list)
    show_rule = rules.add_parser("show", help="a rule in YAML")
    show_rule.add_argument("id")
    show_rule.set_defaults(run=cmd_rules_show)
    add_rule = rules.add_parser("add", help="add or change a rule from a YAML file (a new version)")
    add_rule.add_argument("file")
    add_rule.set_defaults(run=cmd_rules_add)
    for action, status in (("archive", "archived"), ("activate", "active")):
        change = rules.add_parser(action, help=f"set a rule {status} (a new version)")
        change.add_argument("id")
        change.set_defaults(run=cmd_rules_status, status=status)
    set_rule = rules.add_parser("set", help="change fields of a rule, e.g. before_test items=4 (a new version)")
    set_rule.add_argument("id")
    set_rule.add_argument("changes", nargs="+", metavar="champ=valeur")
    set_rule.set_defaults(run=cmd_rules_set)

    instructions = commands.add_parser("instructions", help="instructions in plain French, turned into rules (T21)"
                                       ).add_subparsers(dest="action", required=True)
    add_instruction = instructions.add_parser("add", help="write an instruction here: the agent asks, proposes, you validate")
    add_instruction.add_argument("text", nargs="+")
    add_instruction.add_argument("--today")
    add_instruction.set_defaults(run=cmd_instructions_add)
    instructions.add_parser("list", help="the latest instructions and where they stand").set_defaults(
        run=cmd_instructions_list)
    sync_instructions = instructions.add_parser("sync", help="read the instructions made in the PWA now, and answer them")
    sync_instructions.add_argument("--today")
    sync_instructions.set_defaults(run=cmd_instructions_sync)
    instructions.add_parser("corrections", help="every [Modifier]: the rule understood, the rule saved").set_defaults(
        run=cmd_instructions_corrections)
    eval_instructions = instructions.add_parser("eval", help="the test sentences, read by the model")
    eval_instructions.add_argument("--cases", default="evals/instructions/cases.json")
    eval_instructions.add_argument("--today")
    eval_instructions.set_defaults(run=cmd_instructions_eval)

    ask = commands.add_parser("ask", help="ask for a sheet: a revision on a precise point, or an extra exercise")
    ask.add_argument("subject", help="maths, francais, histoire, anglais, svt, physique… or the École Directe code")
    ask.add_argument("focus", nargs="*", help="what to work on, in your words (optional)")
    ask.add_argument("--revision", action="store_true", help="a revision sheet (default: an extra exercise)")
    ask.add_argument("--items", type=int, help="number of questions")
    ask.add_argument("--now", action="store_true", help="make it now (about 10 minutes), then publish it")
    ask.add_argument("--no-publish", action="store_true", help="with --now: keep it on this laptop")
    ask.set_defaults(run=cmd_ask)
    commands.add_parser("requests", help="the sheets asked for, and what became of them").set_defaults(run=cmd_requests)

    material = commands.add_parser("material", help="course documents (drop folder)").add_subparsers(dest="action", required=True)
    index_ = material.add_parser("index", help="index the drop folder now")
    index_.add_argument("--root", default="data/manual")
    index_.set_defaults(run=cmd_material_index)
    material.add_parser("list", help="indexed documents").set_defaults(run=cmd_material_list)
    search_ = material.add_parser("search", help="pages closest to a lesson")
    which = search_.add_mutually_exclusive_group(required=True)
    which.add_argument("--lesson", help="lesson id (school.lessons.source_id)")
    which.add_argument("--text")
    search_.add_argument("--limit", type=int, default=5)
    search_.set_defaults(run=cmd_material_search)
    eval_ = material.add_parser("eval", help="recall@3 on the retrieval cases")
    eval_.add_argument("--cases", default="evals/retrieval/cases.json")
    eval_.set_defaults(run=cmd_material_eval)

    args = parser.parse_args()
    sys.exit(args.run(args))


if __name__ == "__main__":
    main()

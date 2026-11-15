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
        collector=None if args.no_collect else _collector(args),
        api=_api_sessions(),
        embedder=_embedder(),
        material=ManualSource(),
        progress=lambda message: print(f"{datetime.now():%H:%M}  {message}", flush=True),
    )
    what = f" (séances choisies : {', '.join(args.lesson)}, format {args.format})" if args.lesson else ""
    print(f"Soirée du {today.isoformat()}{what}" + ("" if publish else ", sans publication")
          + ". Compter 7 à 10 minutes par fiche.", flush=True)
    try:
        evening = graph.run(today, student, services, db.database_url(), publish=publish,
                            lesson_ids=args.lesson, forced_format=args.format, restart=args.restart)
    except Exception as error:
        _send_failure(today, f"traitement interrompu ({type(error).__name__} : {str(error)[:150]})", publish)
        raise
    print()
    _print_evening(evening)
    if not args.no_collect:
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
    data.setdefault("origin", Origin(author="parent", on=date.today()).model_dump(mode="json"))
    try:
        rule = Rule.model_validate(data)
    except ValidationError as error:
        sys.exit(f"Règle invalide :\n{error}")
    with _connect() as conn:
        saved = store.save(conn, rule)
    print(f"Règle {saved.id} enregistrée, version {saved.version}, statut {saved.status}.")
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

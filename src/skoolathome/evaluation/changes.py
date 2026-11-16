"""The improvement loop (ARCHITECTURE.md §14.6, T32): every change of the agent comes from a
problem seen, is measured before (make eval), watched after (two weeks), written in a journal, and
undone in one command.

    agent.changes      what changed, why, the make eval behind it, the versions before and after,
                       the command that undoes it
    prompt promote     a prompt version to production, refused without a passing make eval
    prompt rollback    production back on the previous version
    record_config      a model, an engine or a quantization changed in models.toml: written down at
                       the next evening; going back is a `git revert` of models.toml
    alerts             a wrong answer reported on a version promoted less than two weeks ago: the
                       parent is notified at once, with the rollback command (the rollback stays manual)
    compare            the real feedback before and after a change, for the weekly review
    export / restore   the reference set, every prompt version and the journal, in data/evals/
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from skoolathome.agent import config, formats, prompts, tracing

WATCH_DAYS = 14
EXPORTS = Path("data/evals")  # child data: never committed, part of the usual backup
CONFIG_UNDO = "git revert du commit qui a changé src/skoolathome/agent/models.toml"


def record(conn: psycopg.Connection, kind: str, name: str, before: str, after: str, why: str = "",
           evidence: dict | None = None, command: str = "") -> int:
    return conn.execute(
        "INSERT INTO agent.changes (kind, name, before, after, why, eval, command) VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id", (kind, name, before, after, why[:600], Jsonb(evidence or {}), command),
    ).fetchone()[0]


def journal(conn: psycopg.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT id, at, kind, name, before, after, why, eval, command FROM agent.changes "
                        "ORDER BY at DESC, id DESC LIMIT %s", (limit,)).fetchall()
    keys = ("id", "at", "kind", "name", "before", "after", "why", "eval", "command")
    return [dict(zip(keys, row, strict=True)) for row in rows]


# ------------------------------------------------------------------------------ prompts


def promoted(conn: psycopg.Connection, name: str, before: int | None, after: int, why: str, evidence: dict) -> int:
    """The label moved in Langfuse, then the journal: a failed move writes nothing."""
    prompts.move_label(name, after, "production")
    return record(conn, "prompt", name, f"v{before}" if before else "", f"v{after}", why, evidence,
                  f"skoolathome prompt rollback {name}")


def promote(conn: psycopg.Connection, name: str, version: int, why: str = "", *, dry_run: bool = False) -> str:
    """`prompt promote <name> <version>`: "" when done, else why it is refused."""
    from skoolathome.evaluation import runner

    production = prompts.label_version(name, "production")
    if production == version:
        return f"la version {version} est déjà en production"
    problem, runs = runner.check_version(conn, name, version)
    if problem:
        return problem
    if not dry_run:
        promoted(conn, name, production, version, why, runner.evidence(runs))
    return ""


def previous(conn: psycopg.Connection, name: str, current: int | None) -> int | None:
    """The version production had before the current one, from the journal."""
    # The promotion that brought the current version: rolling back twice goes back twice.
    row = conn.execute("SELECT before FROM agent.changes WHERE kind = 'prompt' AND name = %s "
                       "AND after = %s AND before <> '' ORDER BY at DESC, id DESC LIMIT 1",
                       (name, f"v{current}")).fetchone()
    return int(row[0].lstrip("v")) if row else None


def rollback(conn: psycopg.Connection, name: str, why: str = "", to: int | None = None) -> tuple[str, int | None]:
    """`prompt rollback <name>`: production back on the previous version, in one command. No make
    eval: it goes back to a version that held production before. ("" or why not, the version)."""
    current = prompts.label_version(name, "production")
    target = to or previous(conn, name, current)
    if target is None:
        return ("aucune promotion de cette consigne dans le journal : donne la version avec --to", None)
    if target == current:
        return (f"la version {target} est déjà en production", None)
    prompts.move_label(name, target, "production")
    record(conn, "rollback", name, f"v{current}" if current else "", f"v{target}", why,
           command=f"skoolathome prompt promote {name} {current}")
    return "", target


# ------------------------------------------------------------------------ configuration


def config_parts(conf: config.Config | None = None) -> dict[str, str]:
    """What models.toml decides: the engine and each model with its quantization."""
    conf = conf or config.load()
    parts = {"engine": f"{conf.engine.name} {conf.engine.base_url}"}
    for role, model in conf.models.items():
        parts[role] = f"{model.model} {model.quantization}" + (f" @{model.url(conf.engine)}" if model.base_url else "")
    return parts


def record_config(conn: psycopg.Connection, conf: config.Config | None = None) -> list[str]:
    """Each part of the configuration that differs from the journal's last word on it, written
    down with the latest make eval made with it (T30: an engine change is a change too)."""
    written = []
    for part, value in config_parts(conf).items():
        row = conn.execute("SELECT after FROM agent.changes WHERE kind = 'config' AND name = %s "
                           "ORDER BY at DESC, id DESC LIMIT 1", (part,)).fetchone()
        if row is not None and row[0] == value:
            continue
        runs = conn.execute(
            "SELECT DISTINCT ON (family) id, family, passed, finished_at FROM agent.eval_runs "
            "WHERE finished_at IS NOT NULL AND (config->>%s = %s OR %s = 'engine') "
            "ORDER BY family, finished_at DESC", (part, value.split(" @")[0], part),
        ).fetchall()
        record(conn, "config", part, row[0] if row else "", value,
               "" if row else "état de départ du journal",
               {"runs": [{"id": r[0], "family": r[1], "passed": r[2], "finished_at": r[3].isoformat()} for r in runs]},
               CONFIG_UNDO)
        written.append(part)
    return written


# ----------------------------------------------------------------------- watching after


def _prompt_of(kind: str) -> str:
    shape = formats.FORMATS.get(kind)
    return shape.prompt if shape else ""


def alerts(conn: psycopg.Connection, days: int = WATCH_DAYS) -> list[dict]:
    """Wrong answers reported on sheets made by a version promoted less than two weeks ago (or after
    a change of model), not notified yet."""
    rows = conn.execute(
        "SELECT r.id, r.sheet_id, coalesce(r.item_id, ''), s.kind, s.prompt_version, s.subject_code, s.created_at, "
        "c.id, c.kind, c.name, c.before, c.after, c.at FROM feedback.reports r "
        "JOIN agent.sheets s ON s.id = r.sheet_id "
        "JOIN agent.changes c ON c.at >= now() - %s AND c.kind IN ('prompt', 'config') AND c.before <> '' "
        "AND s.created_at >= c.at "
        "WHERE r.reason = 'wrong_answer' AND NOT EXISTS (SELECT 1 FROM agent.change_alerts a WHERE a.report_id = r.id) "
        "ORDER BY r.id, c.at DESC", (timedelta(days=days),),
    ).fetchall()
    found: dict[int, dict] = {}
    for report, sheet, item, kind, version, code, _, change, change_kind, name, before, after, at in rows:
        concerned = (change_kind == "config" or name == formats.JUDGE_PROMPT
                     or (name == _prompt_of(kind) and version == after))
        if not concerned or report in found:
            continue
        undo = f"skoolathome prompt rollback {name}" if change_kind == "prompt" else CONFIG_UNDO
        found[report] = {"report": report, "change": change, "sheet": sheet, "item": item, "subject": code,
                         "title": "Révisions : réponse fausse sur une version récente",
                         "body": (f"{name} {before} → {after} (le {at:%d/%m}) : réponse signalée fausse, fiche {sheet}"
                                  f"{' ' + item if item else ''}. Pour revenir en arrière : {undo}")[:400]}
    return list(found.values())


def notified(conn: psycopg.Connection, sent: list[dict]) -> None:
    for alert in sent:
        conn.execute("INSERT INTO agent.change_alerts (report_id, change_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                     (alert["report"], alert["change"]))


def _side(conn: psycopg.Connection, where: str, params: tuple) -> dict:
    """Real feedback on a set of sheets: made, abstained, success alone, reports, wrong answers."""
    sheets = conn.execute(f"SELECT id, status FROM agent.sheets s WHERE {where}", params).fetchall()
    ids = [s[0] for s in sheets]
    solo = [r[0] for r in conn.execute("SELECT correct FROM feedback.attempts WHERE mode = 'solo' AND sheet_id = ANY(%s)",
                                       (ids,)).fetchall()]
    reasons = [r[0] for r in conn.execute("SELECT reason FROM feedback.reports WHERE sheet_id = ANY(%s)", (ids,)).fetchall()]
    made = len(sheets)
    return {"sheets": made, "abstained": sum(1 for s in sheets if s[1] == "abstained"),
            "answers": len(solo), "success": round(sum(solo) / len(solo), 3) if solo else None,
            "reports": len(reasons), "wrong_answers": reasons.count("wrong_answer"),
            "reports_per_sheet": round(len(reasons) / made, 3) if made else None}


def compare(conn: psycopg.Connection, change: dict, days: int = WATCH_DAYS) -> dict:
    """Before and after a change: the sheets of the previous version against those of the new one
    (a prompt of a format), or the two weeks before against the days since (a model, the judge)."""
    at = change["at"]
    prompt = change["kind"] in ("prompt", "rollback") and change["name"] != formats.JUDGE_PROMPT
    kinds = [kind for kind, shape in formats.FORMATS.items() if shape.prompt == change["name"]]
    if prompt and kinds:
        before = _side(conn, "kind = ANY(%s) AND prompt_version = %s AND created_at < %s", (kinds, change["before"], at))
        after = _side(conn, "kind = ANY(%s) AND prompt_version = %s AND created_at >= %s", (kinds, change["after"], at))
    else:
        before = _side(conn, "created_at BETWEEN %s AND %s", (at - timedelta(days=days), at))
        after = _side(conn, "created_at >= %s", (at,))
    return {"before": before, "after": after}


def recent(conn: psycopg.Connection, day: date, days: int = WATCH_DAYS) -> list[dict]:
    """The changes still watched on that day (less than two weeks old), with their before and after."""
    rows = [c for c in journal(conn, 100) if c["before"] and c["at"].date() > day - timedelta(days=days)]
    return [{**c, "compared": compare(conn, c)} for c in rows]


# ------------------------------------------------------------------------------ export


def export(conn: psycopg.Connection, root: Path = EXPORTS, day: date | None = None) -> Path:
    """The reference set, every version of every prompt (with its labels) and the journal, in
    data/evals/<date>/: enough to rebuild the datasets and the prompts in an empty Langfuse."""
    folder = root / (day or date.today()).isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    rows = conn.execute("SELECT id, family, origin, input, expected, metadata FROM agent.eval_cases ORDER BY id").fetchall()
    _write(folder / "cases.json", [dict(zip(("id", "family", "origin", "input", "expected", "metadata"), r, strict=True))
                                   for r in rows])
    _write(folder / "prompts.json", prompt_versions())
    _write(folder / "changes.json", journal(conn, 10_000))
    runs = conn.execute("SELECT id, family, label, config, finished_at, cases, metrics, passed FROM agent.eval_runs "
                        "WHERE finished_at IS NOT NULL ORDER BY id").fetchall()
    _write(folder / "runs.json", [dict(zip(("id", "family", "label", "config", "finished_at", "cases", "metrics",
                                            "passed"), r, strict=True)) for r in runs])
    return folder


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str))


def prompt_versions() -> dict[str, list[dict]]:
    """Every version of every prompt in Langfuse, oldest first; without Langfuse, the local copies."""
    lf = tracing.client()
    found: dict[str, list[dict]] = {}
    for name in prompts.names():
        versions: list[dict] = []
        if lf is not None:
            try:
                listed = lf.api.prompts.list(name=name).data
                numbers = sorted({v for meta in listed if meta.name == name for v in meta.versions})
                for number in numbers:
                    got = lf.api.prompts.get(name, version=number)
                    versions.append({"version": number, "labels": sorted(set(got.labels) - {"latest"}),
                                     "messages": [{"role": m.role, "content": m.content} for m in got.prompt],
                                     "commit_message": got.commit_message or ""})
            except Exception:  # noqa: BLE001  (Langfuse stopped: the local copy, at least)
                versions = []
        found[name] = versions or [{"version": None, "labels": ["local"], "messages": prompts.local_messages(name),
                                    "commit_message": "copie locale"}]
    return found


def restore(conn: psycopg.Connection, folder: Path) -> dict:
    """An export back: the cases into the local set (then the Langfuse datasets), and the prompt
    versions into an empty Langfuse, in their order, with their labels."""
    from skoolathome.evaluation import cases

    data = json.loads((folder / "cases.json").read_text())
    for row in data:
        cases.save(conn, cases.Case(row["id"], row["family"], row["origin"], row["input"], row["expected"],
                                    row.get("metadata") or {}))
    conn.execute("UPDATE agent.eval_cases SET synced_at = NULL")
    sent = cases.sync(conn)
    lf = tracing.client()
    created = 0
    if lf is not None:
        for name, versions in json.loads((folder / "prompts.json").read_text()).items():
            for version in versions:
                if version["version"] is None:
                    continue
                lf.create_prompt(name=name, prompt=version["messages"], type="chat", labels=version["labels"],
                                 commit_message=version.get("commit_message") or f"restauré de {folder.name}")
                created += 1
        lf.flush()
    return {"cases": len(data), "synced": sent, "prompt_versions": created}


def exported_this_week(root: Path = EXPORTS, day: date | None = None) -> bool:
    day = day or date.today()
    monday = day - timedelta(days=day.weekday())
    if not root.exists():
        return False
    days = [_is_day(p.name) for p in root.iterdir()]
    return any(d is not None and monday <= d <= day for d in days)


def _is_day(name: str) -> date | None:
    try:
        return datetime.strptime(name, "%Y-%m-%d").date()
    except ValueError:
        return None

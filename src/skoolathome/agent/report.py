"""The end-of-evening report (ARCHITECTURE.md §15): « OK : 3 fiches, contrôle jeudi » or
« KO : motif », with the RAM and swap observed. Sent by Web Push to the parent devices (T17).

What goes online: counts, subjects, dates, abstention reasons. Never a lesson's text.
"""

from __future__ import annotations

from datetime import date

import psycopg
from psycopg.types.json import Jsonb

from skoolathome import sysinfo
from skoolathome.subjects import weekday


def _gb(value: float) -> str:
    return f"{value / 2**30:.1f}".replace(".", ",")


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'s' if count > 1 else ''}"


def memory_sample(memory: sysinfo.Memory | None = None) -> dict:
    memory = memory or sysinfo.memory()
    return {"used": memory.used, "total": memory.total, "swap": memory.swap_used}


def _memory_line(samples: list[dict]) -> tuple[str, dict]:
    samples = samples or [memory_sample()]
    peak = max(s["used"] for s in samples)
    swap = max(s["swap"] for s in samples)
    total = samples[-1]["total"]
    return f"RAM max {_gb(peak)}/{_gb(total)} Go, swap {_gb(swap)} Go", {"used_max": peak, "total": total, "swap_max": swap}


def build(run_date: date, state: dict) -> dict:
    collect = state.get("collect") or {}
    outcomes = list((state.get("outcomes") or {}).values())
    published = state.get("published") or {}
    feedback = state.get("feedback") or {}
    documents = state.get("documents") or {}
    made = [o for o in outcomes if o["status"] == "ready"]
    abstained = [o for o in outcomes if o["status"] == "abstained"]

    problems, failed = [], False
    collected = collect.get("status")
    if collected == "two_factor_required":
        failed = True
        problems.append("École Directe demande la double authentification : lance skoolathome-ed-probe --interactive")
    elif collected == "failed":
        failed = True
        problems.append("collecte École Directe en échec")
    elif collected == "partial":
        problems.append("collecte partielle")
    if published.get("error"):
        failed = True
        problems.append(f"publication en échec : {published['error'][:120]}")
    if feedback.get("error"):
        problems.append("retours de la tablette non récupérés")
    if documents.get("failed"):
        problems.append(f"{_plural(len(documents['failed']), 'document')} non indexé(s)")

    count = _plural(len(made), "fiche")
    requested = sum(1 for o in made if o["task"].get("request_id"))
    if requested:
        count += f" dont {requested} demandée{'s' if requested > 1 else ''}"
    if made and published.get("skipped"):
        count += " (non publiées)"
    parts = [count]
    if abstained:
        parts.append(_plural(len(abstained), "abstention"))
    tests = state.get("tests") or []
    if tests:
        parts.append(("contrôle " if len(tests) == 1 else "contrôles ")
                     + ", ".join(f"{weekday(date.fromisoformat(t['day']))} ({t['subject']})" for t in tests[:3]))
    waiting = (state.get("instructions") or {}).get("waiting", 0)
    if waiting:
        parts.append(f"{_plural(waiting, 'consigne')} à valider")
    memory, memory_details = _memory_line(state.get("memory") or [])
    status = "ko" if failed else "ok"
    head = f"KO : {problems[0]}. " if failed else "OK : "
    others = problems[1:] if failed else problems
    notes = " ; ".join(others)
    body = head + ", ".join(parts) + "." + (f" {notes[:1].upper()}{notes[1:]}." if notes else "") + f" {memory}."
    return {
        "run_date": run_date.isoformat(),
        "status": status,
        "title": f"Révisions : {'fiches demandées' if state.get('only_requests') else 'soirée'} {status.upper()}",
        "body": body[:600],
        "details": {
            "sheets": [{"id": o["task"]["id"], "subject": o["task"]["subject"], "type": o["task"]["kind"],
                        "visible_from": o["task"]["visible_from"]} for o in made],
            "abstentions": [{"id": o["task"]["id"], "subject": o["task"]["subject"], "reason": o.get("reason", "")[:300]}
                            for o in abstained],
            "skipped": (state.get("plan") or {}).get("skipped", [])[:30],
            "problems": problems,
            "collect": collected or "skipped",
            "memory": memory_details,
        },
    }


def failure(run_date: date, reason: str) -> dict:
    """The evening could not run at all (Ollama stopped, the graph crashed)."""
    memory, details = _memory_line([])
    return {"run_date": run_date.isoformat(), "status": "ko", "title": "Révisions : soirée KO",
            "body": f"KO : {reason[:300]}. Pas de nouvelles fiches ce soir. {memory}.",
            "details": {"problems": [reason[:300]], "memory": details}}


def save(conn: psycopg.Connection, report: dict) -> None:
    conn.execute(
        "INSERT INTO agent.runs (run_date, status, title, body, details) VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (run_date) DO UPDATE SET status = EXCLUDED.status, title = EXCLUDED.title, "
        "body = EXCLUDED.body, details = EXCLUDED.details, finished_at = now()",
        (report["run_date"], report["status"], report["title"], report["body"], Jsonb(report["details"])),
    )

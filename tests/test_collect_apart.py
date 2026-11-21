"""The École Directe collection and the model, apart: a stuck Ollama never holds the collection
back, and an École Directe failure never stops the sheets nor reads as a model problem."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date

import psycopg
import pytest

from skoolathome import cli, locks
from skoolathome.collect import RunResult
from skoolathome.store import db

TODAY = date(2026, 11, 17)


@pytest.fixture
def laptop(conn, monkeypatch, tmp_path):
    """The CLI on the test database, with every call to the outside recorded."""
    url = db.database_url(conn.info.dbname)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_connect", lambda: psycopg.connect(url, autocommit=True))
    seen = {"order": [], "notices": [], "homes": []}

    def preflight():
        seen["order"].append("ollama")
        return None

    class Client:
        def notify(self, title, body, url="/", tag="agent"):
            seen["notices"].append((title, body))

    @contextmanager
    def sessions():
        yield Client()

    monkeypatch.setattr(cli, "_preflight", preflight)
    monkeypatch.setattr(cli, "_api_sessions", lambda: sessions)
    monkeypatch.setattr(cli, "_notify", lambda title, body: None)
    monkeypatch.setattr(cli, "_send_home_now", lambda student, today: seen["homes"].append(student))
    monkeypatch.setattr(cli, "cmd_summary", lambda args: 0)
    return seen


def collecting(monkeypatch, seen, status: str, errors: list | None = None) -> None:
    from skoolathome import collect as collect_module
    from skoolathome.collectors.ecoledirecte import source

    class Replay:
        def __init__(self, directory):
            pass

        def close(self):
            pass

    def collect(conn, client, **options):
        seen["order"].append("école directe")
        return RunResult(run_id=1, status=status, student_id="4242", counts={}, errors=errors or [])

    monkeypatch.setattr(source, "ReplayClient", Replay)
    monkeypatch.setattr(collect_module, "collect", collect)


def collect_args(**values) -> argparse.Namespace:
    return argparse.Namespace(**{"student": None, "today": TODAY.isoformat(), "replay": "replay", "no_download": False,
                                 "publish": True, **values})


def test_the_collect_timer_never_asks_ollama_and_puts_the_home_page_online(laptop, monkeypatch):
    collecting(monkeypatch, laptop, "ok")
    assert cli.cmd_collect(collect_args()) == 0
    assert laptop["order"] == ["école directe"] and laptop["homes"] == ["4242"] and laptop["notices"] == []


def test_a_failed_collection_is_told_as_an_ecole_directe_problem(laptop, monkeypatch):
    collecting(monkeypatch, laptop, "two_factor_required")
    assert cli.cmd_collect(collect_args()) == 1
    [(title, body)] = laptop["notices"]
    assert title == "École Directe : collecte en échec" and "double authentification" in body
    assert "fiches du soir se font quand même" in body and laptop["homes"] == []
    laptop["notices"].clear()
    collecting(monkeypatch, laptop, "failed", [{"kind": "login", "ref": "", "error": "identifiants refusés"}])
    cli.cmd_collect(collect_args())
    assert "identifiants refusés" in laptop["notices"][0][1]


def evening_args(**values) -> argparse.Namespace:
    return argparse.Namespace(**{"student": None, "today": TODAY.isoformat(), "no_publish": True, "lesson": None,
                                 "format": None, "only_requests": False, "restart": False, "collect": False, **values})


def running_evening(monkeypatch, laptop) -> list:
    from skoolathome.agent import graph, llm

    services = []

    def run(run_date, student, given, database_url, **options):
        laptop["order"].append("modèle")
        services.append(given)
        return graph.Evening(results=[], report={"status": "ok", "title": "t", "body": "b"}, plan={}, resumed=False)

    monkeypatch.setattr(graph, "run", run)
    monkeypatch.setattr(llm, "Models", lambda: None)
    monkeypatch.setattr(cli, "_embedder", lambda: None)
    return services


def test_the_evening_works_on_the_data_collected_apart(laptop, monkeypatch):
    services = running_evening(monkeypatch, laptop)
    collecting(monkeypatch, laptop, "ok")
    assert cli.cmd_evening(evening_args()) == 0
    assert laptop["order"] == ["ollama", "modèle"] and services[0].collector is None  # no login


def test_a_collection_by_hand_comes_before_ollama_is_even_asked(laptop, monkeypatch):
    running_evening(monkeypatch, laptop)
    collecting(monkeypatch, laptop, "failed")
    from skoolathome import secrets
    from skoolathome.collectors.ecoledirecte import client as ed_client

    monkeypatch.setattr(secrets, "read", lambda name: {"username": "u", "password": "p"})

    class Client:
        def __init__(self, *args):
            pass

        def close(self):
            pass

    monkeypatch.setattr(ed_client, "EcoleDirecteClient", Client)
    cli.cmd_evening(evening_args(collect=True))
    # École Directe failed: the sheets are made all the same, on what is known.
    assert laptop["order"] == ["école directe", "ollama", "modèle"]


def test_the_evening_waits_for_a_collection_in_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    with locks.collection(wait=False) as held:
        assert held
        with locks.collection(wait=False) as second:
            assert not second  # the evening would wait here
        with locks.model(wait=False) as model:
            assert model       # and the model is not held by the collection


def test_the_report_says_old_data_without_failing_the_evening():
    from datetime import datetime, timedelta

    from skoolathome.agent import report

    old = (datetime.now().astimezone() - timedelta(days=3)).isoformat()
    built = report.build(TODAY, {"collect": {"status": "skipped", "last_at": old}})
    assert built["status"] == "ok" and "Données École Directe du" in built["body"]  # a note, not a KO
    fresh = report.build(TODAY, {"collect": {"status": "skipped", "last_at": datetime.now().astimezone().isoformat()}})
    assert "École Directe" not in fresh["body"]
    assert "la collecte École Directe, à part, n'est pas concernée" in report.failure(TODAY, "Ollama ne répond pas")["body"]

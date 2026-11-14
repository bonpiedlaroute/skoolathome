"""Shared fixtures: anonymized École Directe responses, and a throw-away test database."""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
import pytest

from skoolathome.store import db
from skoolathome.store.migrate import migrate

FIXTURES = Path(__file__).parent / "fixtures" / "ecoledirecte"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture(autouse=True)
def no_langfuse(monkeypatch):
    """Tests never send traces, even on a laptop where Langfuse is configured."""
    try:
        from skoolathome.agent import tracing
    except ImportError:  # the agent group is not installed
        return
    monkeypatch.setattr(tracing, "client", lambda: None)


@pytest.fixture
def conn():
    """A fresh migrated database, dropped at the end. Skipped when Postgres is not running."""
    name = f"skoolathome_test_{os.getpid()}"
    try:
        admin = psycopg.connect(db.database_url("postgres"), autocommit=True, connect_timeout=3)
    except (psycopg.OperationalError, FileNotFoundError) as error:
        pytest.skip(f"local Postgres unavailable: {error}")
    admin.execute(f"DROP DATABASE IF EXISTS {name}")
    admin.execute(f"CREATE DATABASE {name}")
    try:
        with psycopg.connect(db.database_url(name), autocommit=True) as connection:
            migrate(connection)
            yield connection
    finally:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()

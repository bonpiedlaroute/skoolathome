"""Connection to the local application database."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

import psycopg

from skoolathome.secrets import DEFAULT_DIR

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5433
DEFAULT_NAME = "skoolathome"
DEFAULT_USER = "skoolathome"


def _password() -> str:
    directory = Path(os.environ.get("SKOOLATHOME_SECRETS", DEFAULT_DIR))
    path = directory / "app-db-password"
    if not path.exists():
        raise FileNotFoundError(f"{path} manque : voir tmp/T03-postgres-local.md")
    if path.stat().st_mode & 0o077:
        raise PermissionError(f"{path} est lisible par d'autres : chmod 600 {path}")
    return path.read_text().strip()


def database_url(name: str = DEFAULT_NAME) -> str:
    """SKOOLATHOME_DATABASE_URL if set, otherwise the local container of deploy/compose.yaml."""
    url = os.environ.get("SKOOLATHOME_DATABASE_URL")
    if url:
        return url
    return f"postgresql://{DEFAULT_USER}:{quote(_password())}@{DEFAULT_HOST}:{DEFAULT_PORT}/{name}"


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or database_url())

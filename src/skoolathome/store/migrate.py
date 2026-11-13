"""Tiny migration tool: numbered SQL files, each applied once, in its own transaction."""

from __future__ import annotations

from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def available(directory: Path = MIGRATIONS_DIR) -> list[tuple[str, Path]]:
    """(version, file) pairs in order. The version is the file name before the first underscore."""
    return [(path.name.split("_", 1)[0], path) for path in sorted(directory.glob("[0-9]*.sql"))]


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply the migrations not applied yet. Returns the versions applied by this call."""
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version text PRIMARY KEY, name text NOT NULL, "
            "applied_at timestamptz NOT NULL DEFAULT now())"
        )
    done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for version, path in available(directory):
        if version in done:
            continue
        # One transaction per migration: it is applied entirely, or not at all.
        with conn.transaction():
            conn.execute(path.read_text())
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                (version, path.name),
            )
        applied.append(version)
    return applied

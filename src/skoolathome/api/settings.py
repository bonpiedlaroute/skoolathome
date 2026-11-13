"""Configuration from environment variables (set in Vercel, or locally for tests)."""

from __future__ import annotations

import os


def database_url() -> str:
    """Neon gives DATABASE_URL; SKOOLATHOME_API_DATABASE_URL overrides it for local runs."""
    url = os.environ.get("SKOOLATHOME_API_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def bootstrap_code() -> str:
    """One-time code that pairs the first parent device. Empty: bootstrap disabled."""
    return os.environ.get("SKOOLATHOME_BOOTSTRAP_CODE", "")


def cookie_secure() -> bool:
    """Always on in production. Turn off only for plain-http local tests."""
    return os.environ.get("SKOOLATHOME_COOKIE_SECURE", "1") != "0"

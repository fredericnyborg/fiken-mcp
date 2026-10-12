"""Konfigurasjon fra miljøvariabler. Hemmeligheter settes som nysky-secrets, aldri i git."""

from __future__ import annotations

import os


def public_url() -> str:
    """Offentlig base-URL for serveren (issuer, redirect-URI-er)."""
    return os.getenv("PUBLIC_URL", "https://fiken-mcp.autoajour.no").rstrip("/")


def fiken_redirect_uri() -> str:
    return f"{public_url()}/fiken/callback"


def fiken_client_id() -> str:
    return _require("FIKEN_CLIENT_ID")


def fiken_client_secret() -> str:
    return _require("FIKEN_CLIENT_SECRET")


def database_url() -> str:
    return _require("DATABASE_URL")


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} er ikke satt (server-secret).")
    return value

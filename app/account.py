"""Kontosiden /konto: brukeren logger inn med Fiken og ser/trekker tilbake tilkoblinger.

Ingen egen brukerkonto: Fiken-innloggingen (e-post fra GET /user) er identiteten,
som for MCP-tilkoblingene. Innlogging til siden går gjennom samme /fiken/callback,
med `state` = "web_<nonce>.<hmac>" og en nettleserbundet cookie (ingen DB-rad).
Sesjonen er en HMAC-signert cookie (user_id + utløp), gyldig i 1 time.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any

from app import config, db, fiken_oauth, vault

WEB_PREFIX = "web_"
SESSION_COOKIE = "fiken_mcp_session"
SESSION_TTL_SECONDS = 60 * 60


def _key(purpose: str) -> bytes:
    return hmac.new(config.fiken_client_secret().encode(), purpose.encode(), hashlib.sha256).digest()


def _sign(purpose: str, value: str) -> str:
    return hmac.new(_key(purpose), value.encode(), hashlib.sha256).hexdigest()


# -- innlogging -----------------------------------------------------------------


def start_login(prefix: str = WEB_PREFIX) -> tuple[str, str, str]:
    """Returnerer (Fiken-URL, login-cookie-verdi, nonce) for innlogging til en nettside.

    `prefix` skiller kontosiden (web_) fra adminsiden (adm_) i callbacken.
    """
    nonce = prefix + secrets.token_urlsafe(24)
    state = fiken_oauth.make_state(nonce, config.fiken_client_secret())
    url = fiken_oauth.build_authorize_url(state, config.fiken_client_id(), config.fiken_redirect_uri())
    return url, login_binding(nonce), nonce


def login_binding(nonce: str) -> str:
    return _sign("web-login", nonce)


def is_web_state(request_id: str) -> bool:
    return request_id.startswith(WEB_PREFIX)


# -- sesjon ---------------------------------------------------------------------


def make_session(user_id: str, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + SESSION_TTL_SECONDS)
    payload = f"{user_id}.{exp}"
    return f"{payload}.{_sign('session', payload)}"


def read_session(cookie: str | None, now: float | None = None) -> str | None:
    """user_id hvis sesjonscookien er gyldig og ikke utløpt, ellers None."""
    if not cookie:
        return None
    parts = cookie.split(".")
    if len(parts) != 3:
        return None
    user_id, exp, sig = parts
    if not hmac.compare_digest(sig, _sign("session", f"{user_id}.{exp}")):
        return None
    try:
        if int(exp) < (now if now is not None else time.time()):
            return None
    except ValueError:
        return None
    return user_id


def csrf_token(session_cookie: str) -> str:
    return _sign("account-csrf", session_cookie)


# -- data -----------------------------------------------------------------------


async def user_id_for_login(tokens: dict, fiken_user: dict) -> str | None:
    """Finn brukeren for en Fiken-innlogging. Oppdaterer tokens hvis brukeren finnes.

    Brukere som aldri har koblet til en MCP-klient opprettes ikke (vi lagrer ikke
    Fiken-tilgang uten at den brukes).
    """
    email = (fiken_user.get("email") or "").strip().lower()
    if not email:
        return None
    iv, ct = vault.encrypt(fiken_oauth.tokens_to_blob(tokens))
    pool = await db.pool()
    user_id = await pool.fetchval(
        "UPDATE fiken_users SET token_iv = $2, token_ct = $3, updated_at = now() "
        "WHERE email = $1 RETURNING id",
        email, iv, ct,
    )
    return str(user_id) if user_id else None


async def overview(user_id: str) -> dict[str, Any]:
    pool = await db.pool()
    user = await pool.fetchrow(
        "SELECT email, name, token_ct IS NOT NULL AS connected FROM fiken_users WHERE id = $1",
        user_id,
    )
    grants = await pool.fetch(
        "SELECT g.id, g.created_at, g.last_used_at, c.info->>'client_name' AS client_name, "
        "c.info->'redirect_uris'->>0 AS redirect_uri "
        "FROM mcp_grants g LEFT JOIN mcp_clients c ON c.client_id = g.client_id "
        "WHERE g.user_id = $1 AND g.revoked_at IS NULL ORDER BY g.created_at DESC",
        user_id,
    )
    return {"user": dict(user) if user else None, "grants": [dict(g) for g in grants]}


async def revoke_grant(user_id: str, grant_id: str) -> bool:
    pool = await db.pool()
    async with pool.acquire() as conn, conn.transaction():
        ok = await conn.fetchval(
            "UPDATE mcp_grants SET revoked_at = now() "
            "WHERE id = $1::uuid AND user_id = $2 AND revoked_at IS NULL RETURNING true",
            grant_id, user_id,
        )
        await conn.execute("DELETE FROM mcp_tokens WHERE grant_id = $1::uuid", grant_id)
    return bool(ok)


async def disconnect_all(user_id: str) -> None:
    """Trekk alle tilkoblinger og slett lagrede Fiken-tokens."""
    pool = await db.pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "DELETE FROM mcp_tokens WHERE grant_id IN (SELECT id FROM mcp_grants WHERE user_id = $1)",
            user_id,
        )
        await conn.execute(
            "UPDATE mcp_grants SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL",
            user_id,
        )
        await conn.execute(
            "UPDATE fiken_users SET token_iv = NULL, token_ct = NULL, updated_at = now() WHERE id = $1",
            user_id,
        )


async def fiken_login(
    code: str | None, request_id: str, error: str | None, login_cookie: str | None
) -> tuple[dict, dict]:
    """Felles for nettside-innlogging: sjekk nettleserbinding, veksle kode, hent Fiken-bruker."""
    from app.mcp_auth import LoginError, fetch_fiken_user

    if not login_cookie or not hmac.compare_digest(login_cookie, login_binding(request_id)):
        raise LoginError("Innloggingen ble ikke startet i denne nettleseren. Prøv igjen.")
    if error or not code:
        raise LoginError("Fiken-innloggingen ble avbrutt.")
    try:
        tokens = await fiken_oauth.exchange_code(code, fiken_oauth.make_state(request_id, config.fiken_client_secret()))
    except fiken_oauth.FikenOAuthError as exc:
        raise LoginError("Fiken avviste innloggingskoden. Prøv igjen.") from exc
    return tokens, await fetch_fiken_user(tokens["access_token"])


async def complete_web_login(
    code: str | None, request_id: str, error: str | None, login_cookie: str | None
) -> tuple[str | None, str]:
    """Fullfør innlogging til kontosiden. Returnerer (user_id eller None, e-post)."""
    tokens, fiken_user = await fiken_login(code, request_id, error, login_cookie)
    email = (fiken_user.get("email") or "").strip().lower()
    return await user_id_for_login(tokens, fiken_user), email

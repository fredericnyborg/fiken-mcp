"""OAuth 2.1-autorisasjonsserver for MCP-klienter (Claude m.fl.).

Fiken-innloggingen ER identiteten, så det finnes ingen egen konto:

  1. Klienten registrerer seg (DCR, /register) og sender brukeren til /authorize
     (PKCE). `authorize()` lagrer forespørselen og sender nettleseren til vår
     samtykkeside /consent/<id>, som viser hvilken klient som ber om tilgang.
  2. Ved «Godkjenn» (`approve_consent()`) settes en nettleser-bundet cookie og
     brukeren sendes til Fiken med en signert `state` som peker på forespørselen.
     Samtykkesiden er påkrevd fordi alle MCP-klienter deler én Fiken-app: uten
     den kunne en angriper registrere en klient, sende offeret en lenke og – hvis
     Fiken husker samtykket – få offerets tilgang (confused deputy, jf. MCP-spec).
  3. Fiken sender brukeren tilbake til /fiken/callback. `complete_fiken_login()`
     krever godkjent forespørsel + cookien fra samme nettleser, og
     veksler koden mot Fiken-tokens, henter GET /user (e-post = bruker-ID),
     lagrer tokens kryptert, oppretter en grant + MCP-engangskode og sender
     brukeren tilbake til klientens redirect_uri.
  4. Klienten veksler koden (PKCE) mot access/refresh-token på /token.
  5. /mcp slår opp access-tokenet; grant og Fiken-tilkobling må være aktive.

MCP-tokens og -koder lagres kun som sha256-hash.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import asyncpg
import httpx
from fastmcp.server.auth.auth import (
    AccessToken,
    ClientRegistrationOptions,
    OAuthProvider,
    RevocationOptions,
)
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app import config, db, fiken_oauth, vault
from app.fiken_client import API_BASE

SCOPE = "fiken"

REQUEST_TTL_SECONDS = 15 * 60
CODE_TTL_SECONDS = 5 * 60
ACCESS_TTL_SECONDS = 60 * 60
REFRESH_TTL_SECONDS = 90 * 24 * 60 * 60


LOGIN_COOKIE = "fiken_mcp_login"
CSRF_COOKIE = "fiken_mcp_csrf"


class LoginError(Exception):
    """Feil i Fiken-innloggingen som vises til brukeren i nettleseren."""


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def browser_binding(request_id: str) -> str:
    """Cookie-verdi som binder Fiken-runden til nettleseren som godkjente."""
    key = config.fiken_client_secret().encode()
    return hmac.new(key, f"login:{request_id}".encode(), hashlib.sha256).hexdigest()


def _ts(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, UTC)


def _epoch(dt: datetime | None) -> int | None:
    return int(dt.timestamp()) if dt is not None else None


class FikenMCPOAuthProvider(OAuthProvider):
    def __init__(self) -> None:
        super().__init__(
            base_url=config.public_url(),
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=[SCOPE],
        )

    # -- klienter --------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        pool = await db.pool()
        info = await pool.fetchval("SELECT info FROM mcp_clients WHERE client_id = $1", client_id)
        return OAuthClientInformationFull.model_validate_json(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        pool = await db.pool()
        await pool.execute(
            "INSERT INTO mcp_clients (client_id, info) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (client_id) DO UPDATE SET info = EXCLUDED.info",
            client_info.client_id,
            client_info.model_dump_json(),
        )

    # -- autorisasjon ----------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if client.client_id is None:
            raise AuthorizeError(error="invalid_request", error_description="client_id mangler")
        request_id = secrets.token_urlsafe(24)
        pool = await db.pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM mcp_auth_requests WHERE expires_at < now()")
            await conn.execute("DELETE FROM mcp_codes WHERE expires_at < now()")
            await conn.execute("DELETE FROM mcp_tokens WHERE expires_at < now()")
            await conn.execute(
                "INSERT INTO mcp_auth_requests (id, client_id, params, expires_at) "
                "VALUES ($1, $2, $3::jsonb, now() + make_interval(secs => $4))",
                request_id,
                client.client_id,
                params.model_dump_json(),
                REQUEST_TTL_SECONDS,
            )
        return f"{config.public_url()}/consent/{request_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        pool = await db.pool()
        data = await pool.fetchval(
            "SELECT data FROM mcp_codes WHERE code_hash = $1 AND client_id = $2 AND expires_at > now()",
            hash_secret(authorization_code),
            client.client_id,
        )
        if data is None:
            return None
        return AuthorizationCode.model_validate_json(data).model_copy(
            update={"code": authorization_code}
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        pool = await db.pool()
        async with pool.acquire() as conn, conn.transaction():
            grant_id = await conn.fetchval(
                "DELETE FROM mcp_codes WHERE code_hash = $1 AND client_id = $2 RETURNING grant_id",
                hash_secret(authorization_code.code),
                client.client_id,
            )
            if grant_id is None:
                raise TokenError("invalid_grant", "Koden er ukjent eller allerede brukt.")
            return await _issue(
                conn, grant_id, client.client_id or "", authorization_code.scopes,
                authorization_code.resource,
            )

    # -- refresh ---------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        pool = await db.pool()
        row = await pool.fetchrow(
            "SELECT t.scopes, t.expires_at FROM mcp_tokens t "
            "JOIN mcp_grants g ON g.id = t.grant_id AND g.revoked_at IS NULL "
            "WHERE t.token_hash = $1 AND t.kind = 'refresh' AND t.client_id = $2 "
            "AND t.expires_at > now()",
            hash_secret(refresh_token),
            client.client_id,
        )
        if row is None:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=client.client_id or "",
            scopes=list(row["scopes"]),
            expires_at=_epoch(row["expires_at"]),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if not set(scopes) <= set(refresh_token.scopes):
            raise TokenError("invalid_scope", "Forespurte scopes overstiger opprinnelig tilgang.")
        pool = await db.pool()
        async with pool.acquire() as conn, conn.transaction():
            # Rotasjon: et refresh-token kan bare brukes én gang.
            row = await conn.fetchrow(
                "DELETE FROM mcp_tokens WHERE token_hash = $1 AND kind = 'refresh' "
                "RETURNING grant_id, resource",
                hash_secret(refresh_token.token),
            )
            if row is None:
                raise TokenError("invalid_grant", "Refresh-token er ukjent eller brukt.")
            await conn.execute(
                "DELETE FROM mcp_tokens WHERE grant_id = $1 AND kind = 'access'", row["grant_id"]
            )
            return await _issue(
                conn, row["grant_id"], client.client_id or "",
                scopes or refresh_token.scopes, row["resource"],
            )

    # -- verifisering ----------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        pool = await db.pool()
        row = await pool.fetchrow(
            "SELECT t.client_id, t.scopes, t.resource, t.expires_at, g.id AS grant_id, "
            "u.id AS user_id, u.email "
            "FROM mcp_tokens t "
            "JOIN mcp_grants g ON g.id = t.grant_id AND g.revoked_at IS NULL "
            "JOIN fiken_users u ON u.id = g.user_id AND u.token_ct IS NOT NULL "
            "WHERE t.token_hash = $1 AND t.kind = 'access' AND t.expires_at > now()",
            hash_secret(token),
        )
        if row is None:
            return None
        await pool.execute(
            "UPDATE mcp_grants SET last_used_at = now() WHERE id = $1 "
            "AND (last_used_at IS NULL OR last_used_at < now() - interval '5 minutes')",
            row["grant_id"],
        )
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=list(row["scopes"]),
            expires_at=_epoch(row["expires_at"]),
            resource=row["resource"],
            subject=str(row["user_id"]),
            claims={"user_id": str(row["user_id"]), "email": row["email"]},
        )

    async def verify_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        return await self.load_access_token(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        pool = await db.pool()
        await pool.execute("DELETE FROM mcp_tokens WHERE token_hash = $1", hash_secret(token.token))


async def _issue(
    conn: asyncpg.Connection,
    grant_id: Any,
    client_id: str,
    scopes: list[str],
    resource: str | None,
) -> OAuthToken:
    access = secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(32)
    now = time.time()
    await conn.executemany(
        "INSERT INTO mcp_tokens (token_hash, kind, grant_id, client_id, scopes, resource, expires_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7)",
        [
            (hash_secret(access), "access", grant_id, client_id, scopes, resource,
             _ts(now + ACCESS_TTL_SECONDS)),
            (hash_secret(refresh), "refresh", grant_id, client_id, scopes, resource,
             _ts(now + REFRESH_TTL_SECONDS)),
        ],
    )
    return OAuthToken(
        access_token=access,
        token_type="Bearer",
        expires_in=ACCESS_TTL_SECONDS,
        refresh_token=refresh,
        scope=" ".join(scopes),
    )


# -- Fiken-callback ----------------------------------------------------------


async def fetch_fiken_user(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.get(
            f"{API_BASE}/user",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
    if resp.status_code != 200:
        raise LoginError(f"Kunne ikke hente Fiken-bruker ({resp.status_code}).")
    return resp.json()


async def get_consent(request_id: str) -> dict[str, str]:
    """Info til samtykkesiden. LoginError hvis forespørselen er ukjent/utløpt/brukt."""
    pool = await db.pool()
    row = await pool.fetchrow(
        "SELECT r.params, c.info FROM mcp_auth_requests r "
        "JOIN mcp_clients c ON c.client_id = r.client_id "
        "WHERE r.id = $1 AND r.expires_at > now() AND NOT r.approved",
        request_id,
    )
    if row is None:
        raise LoginError("Forespørselen er utløpt eller allerede brukt. Start tilkoblingen på nytt.")
    params = AuthorizationParams.model_validate_json(row["params"])
    client = OAuthClientInformationFull.model_validate_json(row["info"])
    return {
        "client_name": client.client_name or "Ukjent klient",
        "redirect_host": urlparse(str(params.redirect_uri)).netloc,
    }


async def approve_consent(request_id: str) -> str:
    """Marker forespørselen som godkjent. Returnerer Fiken-authorize-URL-en."""
    pool = await db.pool()
    ok = await pool.fetchval(
        "UPDATE mcp_auth_requests SET approved = true "
        "WHERE id = $1 AND expires_at > now() AND NOT approved RETURNING true",
        request_id,
    )
    if not ok:
        raise LoginError("Forespørselen er utløpt eller allerede brukt. Start tilkoblingen på nytt.")
    state = fiken_oauth.make_state(request_id, config.fiken_client_secret())
    return fiken_oauth.build_authorize_url(state, config.fiken_client_id(), config.fiken_redirect_uri())


async def deny_consent(request_id: str) -> str:
    """Avvis: slett forespørselen og send brukeren tilbake til klienten med feil."""
    pool = await db.pool()
    row = await pool.fetchrow(
        "DELETE FROM mcp_auth_requests WHERE id = $1 RETURNING params", request_id
    )
    if row is None:
        raise LoginError("Forespørselen er utløpt eller allerede brukt.")
    params = AuthorizationParams.model_validate_json(row["params"])
    return construct_redirect_uri(
        str(params.redirect_uri), error="access_denied",
        error_description="Brukeren avviste tilgang.", state=params.state,
    )


async def complete_fiken_login(
    code: str | None, state: str | None, error: str | None, login_cookie: str | None
) -> str:
    """Fullfør Fiken-runden. Returnerer URL-en nettleseren skal sendes til."""
    request_id = fiken_oauth.verify_state(state or "", config.fiken_client_secret())
    if request_id is None:
        raise LoginError("Ugyldig eller manipulert state. Start tilkoblingen på nytt.")
    if not login_cookie or not hmac.compare_digest(login_cookie, browser_binding(request_id)):
        raise LoginError(
            "Innloggingen ble ikke startet i denne nettleseren. Start tilkoblingen på nytt "
            "fra MCP-klienten din."
        )

    pool = await db.pool()
    # Engangs: forespørselen slettes uansett utfall.
    row = await pool.fetchrow(
        "DELETE FROM mcp_auth_requests WHERE id = $1 AND expires_at > now() AND approved "
        "RETURNING client_id, params",
        request_id,
    )
    if row is None:
        raise LoginError("Innloggingen er utløpt eller allerede brukt. Start tilkoblingen på nytt.")
    params = AuthorizationParams.model_validate_json(row["params"])
    redirect_uri = str(params.redirect_uri)

    if error or not code:
        return construct_redirect_uri(
            redirect_uri, error="access_denied",
            error_description="Fiken-tilgang ble ikke gitt.", state=params.state,
        )

    try:
        tokens = await fiken_oauth.exchange_code(code, state or "")
    except fiken_oauth.FikenOAuthError as exc:
        raise LoginError("Fiken avviste innloggingskoden. Prøv igjen.") from exc
    fiken_user = await fetch_fiken_user(tokens["access_token"])
    email = (fiken_user.get("email") or "").strip().lower()
    if not email:
        raise LoginError("Fiken returnerte ingen e-post for brukeren.")

    iv, ct = vault.encrypt(fiken_oauth.tokens_to_blob(tokens))
    mcp_code = secrets.token_urlsafe(32)
    auth_code = AuthorizationCode(
        code=mcp_code,
        scopes=params.scopes or [SCOPE],
        expires_at=time.time() + CODE_TTL_SECONDS,
        client_id=row["client_id"],
        code_challenge=params.code_challenge,
        redirect_uri=params.redirect_uri,
        redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
        resource=params.resource,
    )
    async with pool.acquire() as conn, conn.transaction():
        user_id = await conn.fetchval(
            "INSERT INTO fiken_users (email, name, token_iv, token_ct) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name, token_iv = EXCLUDED.token_iv, "
            "token_ct = EXCLUDED.token_ct, updated_at = now() RETURNING id",
            email, fiken_user.get("name"), iv, ct,
        )
        grant_id = await conn.fetchval(
            "INSERT INTO mcp_grants (user_id, client_id) VALUES ($1, $2) RETURNING id",
            user_id, row["client_id"],
        )
        await conn.execute(
            "INSERT INTO mcp_codes (code_hash, client_id, grant_id, data, expires_at) "
            "VALUES ($1, $2, $3, $4::jsonb, $5)",
            hash_secret(mcp_code), row["client_id"], grant_id,
            auth_code.model_dump_json(), _ts(auth_code.expires_at),
        )
    return construct_redirect_uri(redirect_uri, code=mcp_code, state=params.state)

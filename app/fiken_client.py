"""Tynn async-klient mot Fiken API v2 for én innlogget bruker.

- Henter brukerens Fiken-token (kryptert i DB) og fornyer det ved behov, under
  radlås så to samtidige kall ikke bruker samme refresh-token.
- Fiken tåler ett samtidig kall per bruker: kallene serialiseres per bruker og
  spres med minst MIN_GAP_SECONDS. 429 (og 503 på GET) prøves på nytt med backoff.
- Svar returneres rått. Lister får paginering fra Fiken-Api-*-headerne.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from typing import Any
from urllib.parse import quote

import httpx

from app import db, fiken_oauth, vault

API_BASE = "https://api.fiken.no/api/v2"

MIN_GAP_SECONDS = 0.25
MAX_RETRIES = 3
TIMEOUT_SECONDS = 30
MAX_ERROR_BODY = 2000
COMPANY_CACHE_SECONDS = 600

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_\-:.]+$")


class FikenError(Exception):
    """Feil fra Fiken eller tilkoblingen, med tekst som kan vises til brukeren."""


class FikenAPIError(FikenError):
    def __init__(self, status: int, body: str) -> None:
        if len(body) > MAX_ERROR_BODY:
            body = body[:MAX_ERROR_BODY] + " ...[forkortet]"
        super().__init__(f"Fiken {status}: {body}")
        self.status = status
        self.body = body


class ReconnectRequired(FikenError):
    pass


_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_last_start: dict[str, float] = defaultdict(float)
_company_cache: dict[str, tuple[float, list[str]]] = {}


def segment(value: Any) -> str:
    """Valider og URL-kod ett sti-segment (hindrer ../ og skråstreker)."""
    text = str(value)
    if not text or text in (".", "..") or not _SAFE_SEGMENT.match(text):
        raise FikenError(f"Ugyldig verdi i sti: {text!r}")
    return quote(text, safe=":")


def check_raw_path(path: str) -> str:
    """Valider en rå sti for fiken_get: må starte med / og bestå av trygge segmenter."""
    if not path.startswith("/"):
        raise FikenError("Stien må starte med '/', f.eks. /companies/<slug>/accounts")
    path = path.split("?", 1)[0].rstrip("/")
    for seg in path.split("/")[1:]:
        segment(seg)
    return path


def pagination(headers: httpx.Headers) -> dict[str, int] | None:
    out: dict[str, int] = {}
    for key, header in (
        ("page", "Fiken-Api-Page"),
        ("pageSize", "Fiken-Api-Page-Size"),
        ("pageCount", "Fiken-Api-Page-Count"),
        ("resultCount", "Fiken-Api-Result-Count"),
    ):
        try:
            out[key] = int(headers[header])
        except (KeyError, ValueError):
            pass
    return out or None


def _clean_query(query: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (query or {}).items():
        if value is None:
            continue
        out[key] = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return out


async def _access_token(user_id: str) -> str:
    """Gyldig Fiken access-token for brukeren; fornyer under radlås ved behov."""
    pool = await db.pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT token_iv, token_ct FROM fiken_users WHERE id = $1 FOR UPDATE", user_id
            )
            if row is None or not row["token_ct"]:
                raise ReconnectRequired(
                    "Fiken er ikke koblet til. Koble fra og til igjen i MCP-klienten."
                )
            blob = vault.decrypt(row["token_iv"], row["token_ct"])
            if not fiken_oauth.needs_refresh(blob):
                return blob["access_token"]
            try:
                fresh = await fiken_oauth.refresh(blob["refresh_token"])
            except fiken_oauth.FikenOAuthError as exc:
                if not exc.is_invalid_grant:
                    raise FikenError(str(exc)) from exc
                # Fiken har trukket tilgangen: fjern tokens og alle grants (committes
                # før feilen kastes), så klienten må gjennom innloggingen på nytt.
                await conn.execute(
                    "UPDATE fiken_users SET token_iv = NULL, token_ct = NULL, updated_at = now() "
                    "WHERE id = $1",
                    user_id,
                )
                await conn.execute(
                    "UPDATE mcp_grants SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL",
                    user_id,
                )
                revoked = True
            else:
                revoked = False
                blob = fiken_oauth.tokens_to_blob(fresh, prev=blob)
                iv, ct = vault.encrypt(blob)
                await conn.execute(
                    "UPDATE fiken_users SET token_iv = $2, token_ct = $3, updated_at = now() "
                    "WHERE id = $1",
                    user_id,
                    iv,
                    ct,
                )
    if revoked:
        raise ReconnectRequired("Fiken-tilgangen er utløpt eller trukket tilbake. Koble til på nytt.")
    return blob["access_token"]


async def send(
    access_token: str,
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    json: Any = None,
    files: dict[str, Any] | None = None,
    data: dict[str, str] | None = None,
    content: bytes | None = None,
    content_type: str | None = None,
    lock_key: str | None = None,
) -> httpx.Response:
    """Ett kall mot Fiken (serialisert per bruker, med retry på 429/503)."""
    url = f"{API_BASE}{path}"
    key = lock_key or access_token
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    async with _locks[key]:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
            for attempt in range(MAX_RETRIES + 1):
                wait = MIN_GAP_SECONDS - (time.monotonic() - _last_start[key])
                if wait > 0:
                    await asyncio.sleep(wait)
                _last_start[key] = time.monotonic()
                resp = await http.request(
                    method,
                    url,
                    params=_clean_query(query),
                    json=json,
                    files=files,
                    data=data,
                    content=content,
                    headers=headers,
                )
                retryable = resp.status_code == 429 or (resp.status_code == 503 and method == "GET")
                if not retryable or attempt == MAX_RETRIES:
                    return resp
                try:
                    delay = float(resp.headers.get("Retry-After", ""))
                except ValueError:
                    delay = 0.5 * 2**attempt
                await asyncio.sleep(min(max(delay, 0), 30))
    raise AssertionError("unreachable")


def parse(resp: httpx.Response, *, paged: bool = False) -> Any:
    """Gjør et Fiken-svar om til et JSON-vennlig resultat."""
    if resp.status_code >= 400:
        raise FikenAPIError(resp.status_code, resp.text)
    if resp.status_code == 201:
        return {"created": True, "location": resp.headers.get("Location")}
    if resp.status_code == 204 or not resp.content:
        return {"success": True}
    try:
        payload = resp.json()
    except ValueError:
        return {"success": True, "text": resp.text[:MAX_ERROR_BODY]}
    if paged:
        page = pagination(resp.headers)
        if page is not None:
            return {"items": payload, "pagination": page}
    return payload


class FikenSession:
    """Fiken-tilgang for én innlogget MCP-bruker."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    async def request(self, method: str, path: str, *, paged: bool = False, **kwargs: Any) -> Any:
        token = await _access_token(self.user_id)
        resp = await send(token, method, path, lock_key=self.user_id, **kwargs)
        return parse(resp, paged=paged)

    async def company_slug(self, slug: str | None) -> str:
        """Bruk oppgitt slug, eller brukerens eneste selskap hvis den mangler."""
        if slug:
            return segment(slug)
        cached = _company_cache.get(self.user_id)
        if cached and time.monotonic() - cached[0] < COMPANY_CACHE_SECONDS:
            slugs = cached[1]
        else:
            companies = await self.request("GET", "/companies", query={"pageSize": 100})
            slugs = [c.get("slug") for c in companies if isinstance(c, dict) and c.get("slug")]
            _company_cache[self.user_id] = (time.monotonic(), slugs)
        if len(slugs) == 1:
            return slugs[0]
        if not slugs:
            raise FikenError("Brukeren har ingen Fiken-selskaper.")
        raise FikenError(
            "Brukeren har flere selskaper. Oppgi companySlug, en av: " + ", ".join(slugs)
        )

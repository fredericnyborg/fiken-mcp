"""Fiken OAuth2 (authorization code, konfidensiell klient).

  - Authorize: https://fiken.no/oauth/authorize (response_type, client_id, redirect_uri, state)
  - Token:     https://fiken.no/oauth/token (HTTP Basic client-auth;
               grant_type=authorization_code | refresh_token; `state` påkrevd ved code-veksling)
  - Ingen scope-parameter. Ett token gir tilgang til alle brukerens selskaper.

`state` binder Fiken-runden til en ventende MCP-autorisasjon: "<request_id>.<hmac>".
Forespørselen er engangs (slettes ved callback) og har kort levetid i DB.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import urlencode

import httpx

from app import config

AUTHORIZE_URL = "https://fiken.no/oauth/authorize"
TOKEN_URL = "https://fiken.no/oauth/token"

REFRESH_BUFFER_SECONDS = 120


class FikenOAuthError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Fiken token-feil ({status}): {body[:300]}")
        self.status = status
        self.body = body

    @property
    def is_invalid_grant(self) -> bool:
        return self.status in (400, 401) and "invalid_grant" in self.body


# -- state ------------------------------------------------------------------


def _sign(request_id: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"fiken-state:{request_id}".encode(), hashlib.sha256).hexdigest()


def make_state(request_id: str, secret: str) -> str:
    return f"{request_id}.{_sign(request_id, secret)}"


def verify_state(state: str, secret: str) -> str | None:
    """Returner request_id hvis staten er gyldig signert, ellers None."""
    request_id, sep, sig = state.rpartition(".")
    if not sep or not request_id or not sig:
        return None
    if hmac.compare_digest(sig, _sign(request_id, secret)):
        return request_id
    return None


def build_authorize_url(state: str, client_id: str, redirect_uri: str) -> str:
    query = urlencode(
        {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "state": state}
    )
    return f"{AUTHORIZE_URL}?{query}"


# -- token-endepunktet --------------------------------------------------------


def basic_header(client_id: str, secret: str) -> str:
    return "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()


async def _token_request(form: dict[str, str]) -> dict:
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.post(
            TOKEN_URL,
            headers={
                "Authorization": basic_header(config.fiken_client_id(), config.fiken_client_secret()),
                "Accept": "application/json",
            },
            data=form,
        )
    if resp.status_code != 200:
        raise FikenOAuthError(resp.status_code, resp.text)
    return resp.json()


async def exchange_code(code: str, state: str) -> dict:
    return await _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.fiken_redirect_uri(),
            "state": state,
        }
    )


async def refresh(refresh_token: str) -> dict:
    return await _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})


# -- token-blob (krypteres med vault) ----------------------------------------


def tokens_to_blob(token_resp: dict, prev: dict | None = None, now: float | None = None) -> dict:
    """Bygg/oppdater token-blobben. Beholder gammelt refresh-token hvis Fiken ikke sender nytt."""
    blob = dict(prev or {})
    blob["access_token"] = token_resp["access_token"]
    if token_resp.get("refresh_token"):
        blob["refresh_token"] = token_resp["refresh_token"]
    expires_in = int(token_resp.get("expires_in") or 3600)
    blob["expires_at"] = int((now if now is not None else time.time()) + expires_in)
    return blob


def needs_refresh(blob: dict, now: float | None = None) -> bool:
    if not blob.get("refresh_token"):
        return False
    try:
        expires_at = int(blob.get("expires_at", 0))
    except (TypeError, ValueError):
        return True
    return (now if now is not None else time.time()) > expires_at - REFRESH_BUFFER_SECONDS

"""fiken-mcp: rå MCP-server for Fiken API v2.

Ruter: /mcp (MCP, streamable HTTP), OAuth (/register, /authorize, /token, /revoke,
/.well-known/...), /consent/<id> (samtykkeside), /fiken/callback (Fiken OAuth-retur)
og /health.
"""

from __future__ import annotations

import hmac
import html
import logging
import secrets
from urllib.parse import urlparse

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app import config, tools
from app.mcp_auth import (
    CSRF_COOKIE,
    LOGIN_COOKIE,
    REQUEST_TTL_SECONDS,
    FikenMCPOAuthProvider,
    LoginError,
    approve_consent,
    browser_binding,
    complete_fiken_login,
    deny_consent,
    get_consent,
)

log = logging.getLogger("fiken-mcp")

mcp = FastMCP(
    name="Fiken",
    instructions=(
        "Rå tilgang til Fiken API v2 for brukerens egne Fiken-selskaper. Start med "
        "list_companies for å finne companySlug. Alle beløp er heltall i øre (100 = 1 kr). "
        "Verktøyene speiler Fikens endepunkter; body sendes i Fikens eget JSON-format. "
        "Skriveverktøy endrer regnskapet direkte – bekreft med brukeren før bokføring."
    ),
    auth=FikenMCPOAuthProvider(),
)
tools.register(mcp)


SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _page(title: str, message: str, status: int, extra_html: str = "") -> HTMLResponse:
    body = (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        f"<title>{html.escape(title)}</title>"
        "<body style='font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem;line-height:1.5'>"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>{extra_html}</body>"
    )
    return HTMLResponse(body, status_code=status, headers=SECURITY_HEADERS)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None or origin == "null":
        return request.headers.get("sec-fetch-site") in (None, "same-origin")
    want = urlparse(config.public_url())
    got = urlparse(origin)
    return (got.scheme, got.netloc) == (want.scheme, want.netloc)


@mcp.custom_route("/consent/{request_id}", methods=["GET"])
async def consent_page(request: Request) -> Response:
    request_id = request.path_params["request_id"]
    try:
        info = await get_consent(request_id)
    except LoginError as exc:
        return _page("Tilkobling feilet", str(exc), 400)
    csrf = secrets.token_urlsafe(24)
    action = html.escape(f"/consent/{request_id}")
    form = (
        f"<p><b>{html.escape(info['client_name'])}</b> ber om full lese- og skrivetilgang til "
        "Fiken-regnskapet ditt (alle selskaper du har tilgang til). Etter godkjenning sendes du "
        f"tilbake til <b>{html.escape(info['redirect_host'])}</b>.</p>"
        "<p>Godkjenn bare hvis du selv startet denne tilkoblingen.</p>"
        f"<form method=post action='{action}'>"
        f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
        "<button name=action value=approve style='padding:.6rem 1.2rem;margin-right:.5rem'>Godkjenn og logg inn med Fiken</button>"
        "<button name=action value=deny style='padding:.6rem 1.2rem'>Avbryt</button></form>"
    )
    resp = _page("Koble til Fiken", "", 200, form)
    resp.set_cookie(
        CSRF_COOKIE, csrf, max_age=REQUEST_TTL_SECONDS, path="/consent",
        secure=True, httponly=True, samesite="strict",
    )
    return resp


@mcp.custom_route("/consent/{request_id}", methods=["POST"])
async def consent_submit(request: Request) -> Response:
    request_id = request.path_params["request_id"]
    form = await request.form()
    cookie = request.cookies.get(CSRF_COOKIE) or ""
    if not _same_origin(request) or not cookie or not hmac.compare_digest(
        str(form.get("csrf") or ""), cookie
    ):
        return _page("Tilkobling feilet", "Ugyldig skjema. Last siden på nytt.", 403)
    try:
        if form.get("action") != "approve":
            return RedirectResponse(await deny_consent(request_id), status_code=303)
        target = await approve_consent(request_id)
    except LoginError as exc:
        return _page("Tilkobling feilet", str(exc), 400)
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(
        LOGIN_COOKIE, browser_binding(request_id), max_age=REQUEST_TTL_SECONDS,
        path="/fiken/callback", secure=True, httponly=True, samesite="lax",
    )
    resp.delete_cookie(CSRF_COOKIE, path="/consent")
    return resp


@mcp.custom_route("/fiken/callback", methods=["GET"])
async def fiken_callback(request: Request) -> Response:
    q = request.query_params
    try:
        target = await complete_fiken_login(
            q.get("code"), q.get("state"), q.get("error"), request.cookies.get(LOGIN_COOKIE)
        )
    except LoginError as exc:
        return _page("Tilkobling feilet", str(exc), 400)
    except Exception:
        log.exception("Fiken-callback feilet")
        return _page("Tilkobling feilet", "Uventet feil. Prøv igjen senere.", 500)
    resp = RedirectResponse(target, status_code=302)
    resp.delete_cookie(LOGIN_COOKIE, path="/fiken/callback")
    return resp


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> Response:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/", methods=["GET"])
async def index(_: Request) -> Response:
    return _page(
        "Fiken MCP",
        "MCP-server for Fiken. Legg til https://fiken-mcp.autoajour.no/mcp som connector i "
        "Claude (eller en annen MCP-klient) og logg inn med Fiken.",
        200,
    )


app = mcp.http_app(path="/mcp", stateless_http=True)

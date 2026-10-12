"""fiken-mcp: rå MCP-server for Fiken API v2.

Ruter: /mcp (MCP, streamable HTTP), OAuth (/register, /authorize, /token, /revoke,
/.well-known/...), /consent/<id> (samtykkeside), /fiken/callback (Fiken OAuth-retur),
/konto (se og trekk tilbake tilkoblinger) og /health.
"""

from __future__ import annotations

import hmac
import html
import logging
import secrets
import uuid
from urllib.parse import urlparse

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app import account, config, fiken_oauth, tools
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


WEB_LOGIN_COOKIE = "fiken_mcp_web_login"


@mcp.custom_route("/fiken/callback", methods=["GET"])
async def fiken_callback(request: Request) -> Response:
    q = request.query_params
    request_id = fiken_oauth.verify_state(q.get("state") or "", config.fiken_client_secret())
    if request_id is not None and account.is_web_state(request_id):
        return await _web_login_callback(request, request_id)
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


async def _web_login_callback(request: Request, request_id: str) -> Response:
    q = request.query_params
    try:
        user_id, email = await account.complete_web_login(
            q.get("code"), request_id, q.get("error"), request.cookies.get(WEB_LOGIN_COOKIE)
        )
    except LoginError as exc:
        return _page("Innlogging feilet", str(exc), 400)
    except Exception:
        log.exception("Innlogging til kontosiden feilet")
        return _page("Innlogging feilet", "Uventet feil. Prøv igjen senere.", 500)
    if user_id is None:
        resp = _page(
            "Ingen tilkoblinger",
            f"Fiken-brukeren {email} har ingen tilkoblinger til Fiken MCP, og vi lagrer ingen data om den.",
            200,
        )
    else:
        resp = RedirectResponse("/konto", status_code=303)
        resp.set_cookie(
            account.SESSION_COOKIE, account.make_session(user_id),
            max_age=account.SESSION_TTL_SECONDS, path="/konto",
            secure=True, httponly=True, samesite="lax",
        )
    resp.delete_cookie(WEB_LOGIN_COOKIE, path="/fiken/callback")
    return resp


# -- kontoside ------------------------------------------------------------------


BUTTON = "padding:.5rem 1rem;cursor:pointer"


def _fmt(dt) -> str:
    return dt.astimezone().strftime("%d.%m.%Y %H:%M") if dt else "aldri"


def _session(request: Request) -> tuple[str | None, str]:
    cookie = request.cookies.get(account.SESSION_COOKIE) or ""
    return account.read_session(cookie), cookie


async def _check_post(request: Request) -> tuple[str | None, Response | None]:
    """Felles sjekk for POST på /konto: gyldig sesjon, same-origin og CSRF."""
    user_id, cookie = _session(request)
    if user_id is None:
        return None, RedirectResponse("/konto", status_code=303)
    form = await request.form()
    if not _same_origin(request) or not hmac.compare_digest(
        str(form.get("csrf") or ""), account.csrf_token(cookie)
    ):
        return None, _page("Ugyldig forespørsel", "Last siden på nytt og prøv igjen.", 403)
    return user_id, None


@mcp.custom_route("/konto", methods=["GET"])
async def account_page(request: Request) -> Response:
    user_id, cookie = _session(request)
    if user_id is None:
        return _page(
            "Mine Fiken-tilkoblinger",
            "Logg inn med Fiken for å se hvilke AI-klienter som har tilgang til regnskapet ditt, "
            "og trekke tilgangen tilbake.",
            200,
            f"<p><a href='/konto/login' style='{BUTTON};display:inline-block;border:1px solid;"
            "text-decoration:none'>Logg inn med Fiken</a></p>",
        )
    data = await account.overview(user_id)
    if data["user"] is None:
        resp = RedirectResponse("/konto", status_code=303)
        resp.delete_cookie(account.SESSION_COOKIE, path="/konto")
        return resp
    csrf = html.escape(account.csrf_token(cookie))
    hidden = f"<input type=hidden name=csrf value='{csrf}'>"
    rows = "".join(
        "<li style='margin:.75rem 0'>"
        f"<b>{html.escape(g['client_name'] or 'Ukjent klient')}</b>"
        f" <span style='color:#666'>({html.escape(urlparse(g['redirect_uri'] or '').netloc)})</span><br>"
        f"Koblet til {_fmt(g['created_at'])} · sist brukt {_fmt(g['last_used_at'])}"
        f"<form method=post action='/konto/revoke/{html.escape(str(g['id']))}' style='margin-top:.25rem'>"
        f"{hidden}<button style='{BUTTON}'>Trekk tilbake</button></form></li>"
        for g in data["grants"]
    ) or "<li>Ingen aktive tilkoblinger.</li>"
    user = data["user"]
    extra = (
        f"<ul style='padding-left:1.2rem'>{rows}</ul>"
        "<h2>Koble fra alt</h2>"
        "<p>Trekker alle tilkoblinger og sletter Fiken-tilgangen vi har lagret. Du kan også fjerne "
        "appen i Fiken under Brukerinnstillinger → API.</p>"
        f"<form method=post action='/konto/disconnect'>{hidden}"
        f"<button style='{BUTTON}'>Koble fra alt og slett data</button></form>"
        f"<form method=post action='/konto/logout' style='margin-top:2rem'>{hidden}"
        f"<button style='{BUTTON}'>Logg ut</button></form>"
    )
    who = f"Innlogget som {user['name'] or ''} ({user['email']})."
    if not user["connected"]:
        who += " Ingen lagret Fiken-tilgang."
    return _page("Mine Fiken-tilkoblinger", who, 200, extra)


@mcp.custom_route("/konto/login", methods=["GET"])
async def account_login(_: Request) -> Response:
    url, binding, _nonce = account.start_login()
    resp = RedirectResponse(url, status_code=303)
    resp.set_cookie(
        WEB_LOGIN_COOKIE, binding, max_age=REQUEST_TTL_SECONDS, path="/fiken/callback",
        secure=True, httponly=True, samesite="lax",
    )
    return resp


@mcp.custom_route("/konto/revoke/{grant_id}", methods=["POST"])
async def account_revoke(request: Request) -> Response:
    user_id, error = await _check_post(request)
    if error is not None:
        return error
    try:
        grant_id = str(uuid.UUID(request.path_params["grant_id"]))
    except ValueError:
        return RedirectResponse("/konto", status_code=303)
    await account.revoke_grant(user_id, grant_id)
    return RedirectResponse("/konto", status_code=303)


@mcp.custom_route("/konto/disconnect", methods=["POST"])
async def account_disconnect(request: Request) -> Response:
    user_id, error = await _check_post(request)
    if error is not None:
        return error
    await account.disconnect_all(user_id)
    return RedirectResponse("/konto", status_code=303)


@mcp.custom_route("/konto/logout", methods=["POST"])
async def account_logout(request: Request) -> Response:
    _user_id, error = await _check_post(request)
    resp = error or RedirectResponse("/konto", status_code=303)
    resp.delete_cookie(account.SESSION_COOKIE, path="/konto")
    return resp


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> Response:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/", methods=["GET"])
async def index(_: Request) -> Response:
    return _page(
        "Fiken MCP",
        "MCP-server for Fiken. Legg til https://fiken-mcp.autoajour.no/mcp som connector i "
        "Claude (eller en annen MCP-klient) og logg inn med Fiken. Ingen egen konto trengs.",
        200,
        "<p><a href='/konto'>Se og trekk tilbake tilkoblingene dine</a></p>",
    )


app = mcp.http_app(path="/mcp", stateless_http=True)

"""fiken-mcp: rå MCP-server for Fiken API v2.

Ruter: /mcp (MCP, streamable HTTP), OAuth (/register, /authorize, /token, /revoke,
/.well-known/...), /consent/<id> (samtykkeside), /fiken/callback (Fiken OAuth-retur),
/konto (se og trekk tilbake tilkoblinger) og /health.
"""

from __future__ import annotations

import hmac
import html
import json
import logging
import secrets
import uuid
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastmcp import FastMCP
from starlette.requests import Request
from mcp.types import Icon
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from app import account, admin, config, fiken_oauth, pages, tools
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
    # Ikonet Claude o.l. viser for connectoren.
    icons=[Icon(src=f"{config.public_url()}/icon.png", mimeType="image/png", sizes=["512x512"])],
)
tools.register(mcp)
mcp.add_middleware(admin.UsageMiddleware())


SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _page(title: str, message: str, status: int, extra_html: str = "") -> HTMLResponse:
    body = (f"<p class='muted center'>{html.escape(message)}</p>" if message else "") + extra_html
    return HTMLResponse(pages.render(title, body), status_code=status, headers=SECURITY_HEADERS)


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
    client = html.escape(info["client_name"])
    form = (
        f"<p class='muted center'><b>{client}</b> ber om tilgang til Fiken-regnskapet ditt.</p>"
        "<ul>"
        "<li>Lese regnskapsdata: kontoer, bilag, fakturaer, kontakter m.m.</li>"
        "<li>Føre og endre i regnskapet på dine vegne, f.eks. bilag og fakturaer</li>"
        "<li>Gjelder alle Fiken-selskaper du har tilgang til</li>"
        "</ul>"
        "<div class=info>"
        f"<div>Neste steg: <b>logg inn hos Fiken</b></div>"
        f"<div>Sendes deretter tilbake til: <b>{html.escape(info['redirect_host'])}</b></div>"
        "<div style='margin-top:.4rem'>Godkjenn bare hvis du selv startet denne tilkoblingen. "
        "Du kan trekke tilgangen tilbake når som helst under Mine tilkoblinger.</div>"
        "</div>"
        f"<form method=post action='{action}'>"
        f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
        "<div class=row>"
        "<button class='btn btn-outline' name=action value=deny>Avbryt</button>"
        "<button class='btn btn-primary' name=action value=approve>Logg inn med Fiken</button>"
        "</div></form>"
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


OSLO = ZoneInfo("Europe/Oslo")


def _fmt(dt) -> str:
    return dt.astimezone(OSLO).strftime("%d.%m.%Y %H:%M") if dt else "aldri"


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
            "<div class=row><a class='btn btn-primary' href='/konto/login'>Logg inn med Fiken</a></div>",
        )
    data = await account.overview(user_id)
    if data["user"] is None:
        resp = RedirectResponse("/konto", status_code=303)
        resp.delete_cookie(account.SESSION_COOKIE, path="/konto")
        return resp
    csrf = html.escape(account.csrf_token(cookie))
    hidden = f"<input type=hidden name=csrf value='{csrf}'>"
    rows = "".join(
        "<div class=grant><div>"
        f"<div><b>{html.escape(g['client_name'] or 'Ukjent klient')}</b></div>"
        f"<div class=muted>{html.escape(urlparse(g['redirect_uri'] or '').netloc)}</div>"
        f"<div class=muted>Koblet til {_fmt(g['created_at'])} · sist brukt {_fmt(g['last_used_at'])}</div>"
        f"</div><form method=post action='/konto/revoke/{html.escape(str(g['id']))}'>"
        f"{hidden}<button class='btn btn-outline btn-sm'>Trekk tilbake</button></form></div>"
        for g in data["grants"]
    ) or "<p class='muted center'>Ingen aktive tilkoblinger.</p>"
    user = data["user"]
    extra = (
        f"{rows}"
        "<h2>Koble fra alt</h2>"
        "<p class=muted>Trekker alle tilkoblinger og sletter Fiken-tilgangen vi har lagret. Du kan også "
        "fjerne appen i Fiken under Brukerinnstillinger → API.</p>"
        f"<form method=post action='/konto/disconnect'>{hidden}<div class=row>"
        "<button class='btn btn-danger'>Koble fra alt og slett data</button></div></form>"
        f"<form method=post action='/konto/logout'>{hidden}<div class=row>"
        "<button class='btn btn-outline'>Logg ut</button></div></form>"
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


# -- statistikk for Autoajour-admin ---------------------------------------------


@mcp.custom_route("/admin/api/stats", methods=["GET"])
async def admin_stats(request: Request) -> Response:
    if not admin.stats_token_ok(request.headers.get("authorization")):
        return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"Cache-Control": "no-store"})
    return JSONResponse(await admin.stats(), headers={"Cache-Control": "no-store"})


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> Response:
    return JSONResponse({"status": "ok"})


STATIC_DIR = Path(__file__).parent / "app" / "static"


@mcp.custom_route("/favicon.ico", methods=["GET"])
async def favicon(_: Request) -> Response:
    return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")


@mcp.custom_route("/icon.png", methods=["GET"])
async def icon(_: Request) -> Response:
    return FileResponse(STATIC_DIR / "icon.png", media_type="image/png")


INDEX_DESCRIPTION = (
    "Gratis MCP-server for Fiken. Koble Claude eller en annen AI-assistent til Fiken med full "
    "lese- og skrivetilgang: fakturaer, kjøp, salg, kontakter, bilag og mer. Logg inn med Fiken."
)


def _index_html() -> str:
    url = html.escape(config.public_url())
    return f"""
<p class=center>Gratis MCP-server som gir Claude og andre AI-assistenter <b>full lese- og
skrivetilgang</b> til regnskapet ditt i Fiken.</p>

<h2>Kom i gang</h2>
<ol>
<li>Åpne Claude → <b>Innstillinger → Connectors → Legg til egendefinert</b>.</li>
<li>Lim inn <code>{url}/mcp</code></li>
<li>Logg inn med Fiken og godkjenn. Ferdig – ingen egen konto trengs.</li>
</ol>

<h2>Hva kan AI-assistenten gjøre?</h2>
<ul>
<li>Lage, sende og kreditere fakturaer, tilbud og ordrebekreftelser</li>
<li>Registrere kjøp, salg, betalinger og bilag – også med vedlegg</li>
<li>Slå opp og oppdatere kontakter, produkter og prosjekter</li>
<li>Hente kontoplan, saldoer, bilagsjournal og banktransaksjoner</li>
<li>Føre timer og lage fakturautkast fra timeføringer</li>
</ul>
<p class=muted>Alle 150+ endepunkter i Fiken API v2 er tilgjengelige som verktøy.</p>

<h2>Sikkerhet</h2>
<ul>
<li>Du logger inn direkte hos Fiken – vi ser aldri passordet ditt.</li>
<li>Fiken-nøklene lagres kryptert (AES-256-GCM) på norsk infrastruktur.</li>
<li>Du ser og trekker tilbake tilganger under <a href='/konto'>Mine tilkoblinger</a>.</li>
</ul>

<div class=row><a class='btn btn-primary' href='/konto'>Mine tilkoblinger</a>
<a class='btn btn-outline' href='https://autoajour.no'>Om Autoajour</a></div>
"""


def _index_jsonld() -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "Fiken MCP",
        "applicationCategory": "BusinessApplication",
        "operatingSystem": "Web",
        "url": config.public_url() + "/",
        "description": INDEX_DESCRIPTION,
        "inLanguage": "nb",
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "NOK"},
        "publisher": {"@type": "Organization", "name": "Autoajour", "url": "https://autoajour.no"},
    }
    # `</` escapes så JSON-en ikke kan lukke script-taggen.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f"<script type=application/ld+json>{payload}</script>"


@mcp.custom_route("/", methods=["GET"])
async def index(_: Request) -> Response:
    base = config.public_url()
    title = "Fiken MCP – koble Claude til Fiken"
    og = (
        f"<meta property=og:title content=\"{html.escape(title)}\">"
        f"<meta property=og:description content=\"{html.escape(INDEX_DESCRIPTION)}\">"
        f"<meta property=og:url content=\"{html.escape(base)}/\">"
        "<meta property=og:type content=website><meta property=og:locale content=nb_NO>"
        f"<meta property=og:image content=\"{html.escape(base)}/icon.png\">"
    )
    page = pages.render(
        "Fiken MCP",
        _index_html(),
        description=INDEX_DESCRIPTION,
        indexable=True,
        head_title=f"{title} (gratis) – Autoajour",
        canonical=base + "/",
        extra_head=og + _index_jsonld(),
        wide=True,
    )
    # Forsiden er offentlig og lik for alle: tillat caching (de andre sidene er no-store).
    headers = {**SECURITY_HEADERS, "Cache-Control": "public, max-age=3600"}
    return HTMLResponse(page, headers=headers)


@mcp.custom_route("/robots.txt", methods=["GET"])
async def robots(_: Request) -> Response:
    body = (
        "User-agent: *\n"
        "Allow: /$\n"
        "Disallow: /konto\n"
        "Disallow: /consent/\n"
        "Disallow: /fiken/\n"
        "Disallow: /admin/\n"
        "Disallow: /mcp\n"
        f"\nSitemap: {config.public_url()}/sitemap.xml\n"
    )
    return PlainTextResponse(body)


@mcp.custom_route("/sitemap.xml", methods=["GET"])
async def sitemap(_: Request) -> Response:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{html.escape(config.public_url())}/</loc></url></urlset>"
    )
    return Response(body, media_type="application/xml")


app = mcp.http_app(path="/mcp", stateless_http=True)

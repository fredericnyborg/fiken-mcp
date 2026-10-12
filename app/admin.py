"""Adminside /admin: bruksstatistikk for eieren av tjenesten.

Innlogging med Fiken (samme flyt som /konto, state-prefiks «adm_»). Bare e-poster i
ADMIN_EMAILS (kommaseparert nysky-secret) slipper inn. Sesjonen er en HMAC-signert
cookie med e-post og utløp. Statistikken er aggregert; verktøykall telles per dag og
verktøy uten bruker-ID (UsageMiddleware).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from app import config, db

ADMIN_PREFIX = "adm_"
SESSION_COOKIE = "fiken_mcp_admin"
SESSION_TTL_SECONDS = 60 * 60

log = logging.getLogger("fiken-mcp")


def admin_emails() -> set[str]:
    raw = os.getenv("ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_admin(email: str) -> bool:
    return email.strip().lower() in admin_emails()


def is_admin_state(request_id: str) -> bool:
    return request_id.startswith(ADMIN_PREFIX)


# -- sesjon ---------------------------------------------------------------------


def _sign(value: str) -> str:
    key = hmac.new(config.fiken_client_secret().encode(), b"admin-session", hashlib.sha256).digest()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def make_session(email: str, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + SESSION_TTL_SECONDS)
    payload = f"{email}|{exp}"
    return f"{payload}|{_sign(payload)}"


def read_session(cookie: str | None, now: float | None = None) -> str | None:
    """E-post hvis sesjonen er gyldig, ikke utløpt og e-posten fortsatt er admin."""
    if not cookie:
        return None
    parts = cookie.rsplit("|", 2)
    if len(parts) != 3:
        return None
    email, exp, sig = parts
    if not hmac.compare_digest(sig, _sign(f"{email}|{exp}")):
        return None
    try:
        if int(exp) < (now if now is not None else time.time()):
            return None
    except ValueError:
        return None
    return email if is_admin(email) else None


# -- telling av verktøykall --------------------------------------------------------


async def record_call(tool: str, failed: bool) -> None:
    try:
        pool = await db.pool()
        await pool.execute(
            "INSERT INTO usage_daily (day, tool, calls, errors) VALUES ($1, $2, 1, $3) "
            "ON CONFLICT (day, tool) DO UPDATE SET calls = usage_daily.calls + 1, "
            "errors = usage_daily.errors + EXCLUDED.errors",
            datetime.now(UTC).date(), tool, 1 if failed else 0,
        )
    except Exception:  # statistikk skal aldri ødelegge et verktøykall
        log.exception("Kunne ikke registrere bruksstatistikk")


class UsageMiddleware(Middleware):
    """Teller hvert verktøykall (og om det feilet) per dag og verktøy."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool = context.message.name
        try:
            result = await call_next(context)
        except Exception:
            await record_call(tool, failed=True)
            raise
        await record_call(tool, failed=bool(getattr(result, "is_error", False)))
        return result


# -- statistikk ----------------------------------------------------------------------


async def stats() -> dict[str, Any]:
    pool = await db.pool()
    async with pool.acquire() as conn:
        totals = await conn.fetchrow(
            "SELECT "
            "(SELECT count(*) FROM fiken_users) AS users, "
            "(SELECT count(*) FROM fiken_users WHERE token_ct IS NOT NULL) AS connected_users, "
            "(SELECT count(*) FROM mcp_grants WHERE revoked_at IS NULL) AS active_grants, "
            "(SELECT count(DISTINCT user_id) FROM mcp_grants "
            " WHERE revoked_at IS NULL AND last_used_at > now() - interval '7 days') AS active_7d, "
            "(SELECT count(DISTINCT user_id) FROM mcp_grants "
            " WHERE revoked_at IS NULL AND last_used_at > now() - interval '30 days') AS active_30d, "
            "(SELECT coalesce(sum(calls), 0) FROM usage_daily WHERE day > current_date - 30) AS calls_30d, "
            "(SELECT coalesce(sum(errors), 0) FROM usage_daily WHERE day > current_date - 30) AS errors_30d"
        )
        new_users = await conn.fetch(
            "SELECT date_trunc('week', created_at)::date AS week, count(*) AS n FROM fiken_users "
            "WHERE created_at > now() - interval '12 weeks' GROUP BY 1 ORDER BY 1 DESC"
        )
        daily = await conn.fetch(
            "SELECT day, sum(calls) AS calls, sum(errors) AS errors FROM usage_daily "
            "WHERE day > current_date - 14 GROUP BY day ORDER BY day DESC"
        )
        top_tools = await conn.fetch(
            "SELECT tool, sum(calls) AS calls, sum(errors) AS errors FROM usage_daily "
            "WHERE day > current_date - 30 GROUP BY tool ORDER BY 2 DESC LIMIT 15"
        )
        clients = await conn.fetch(
            "SELECT coalesce(c.info->>'client_name', 'Ukjent') AS client, count(*) AS n "
            "FROM mcp_grants g LEFT JOIN mcp_clients c ON c.client_id = g.client_id "
            "WHERE g.revoked_at IS NULL GROUP BY 1 ORDER BY 2 DESC"
        )
    return {
        "totals": dict(totals),
        "new_users": [dict(r) for r in new_users],
        "daily": [dict(r) for r in daily],
        "top_tools": [dict(r) for r in top_tools],
        "clients": [dict(r) for r in clients],
    }

"""Bruksstatistikk for Autoajours admin-konsoll.

GET /admin/api/stats returnerer aggregerte tall som JSON. Endepunktet krever
`Authorization: Bearer <FIKEN_MCP_STATS_TOKEN>` (nysky-secret, samme verdi i
Autoajour-backenden). Tilgangen til selve visningen styres i Autoajour
(plattform-admin). Uten satt token er endepunktet avslått.

Verktøykall telles per dag og verktøy uten bruker-ID (UsageMiddleware).
"""

from __future__ import annotations

import hmac
import logging
import os
from datetime import UTC, datetime
from typing import Any

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from app import db

log = logging.getLogger("fiken-mcp")


def stats_token_ok(authorization: str | None) -> bool:
    """Sjekk Bearer-token mot FIKEN_MCP_STATS_TOKEN (konstant tid). Usatt token = avslått."""
    expected = os.getenv("FIKEN_MCP_STATS_TOKEN", "")
    if len(expected) < 32 or not authorization or not authorization.startswith("Bearer "):
        return False
    return hmac.compare_digest(authorization[len("Bearer "):].strip(), expected)


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
        "new_users": [{"week": r["week"].isoformat(), "n": r["n"]} for r in new_users],
        "daily": [
            {"day": r["day"].isoformat(), "calls": r["calls"], "errors": r["errors"]} for r in daily
        ],
        "top_tools": [dict(r) for r in top_tools],
        "clients": [dict(r) for r in clients],
        "generated_at": datetime.now(UTC).isoformat(),
    }

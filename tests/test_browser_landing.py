"""/mcp viser en forklarende side for nettlesere, men MCP-trafikk går uendret videre."""

import httpx

import main

BROWSER = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


async def _get(path, accept):
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="https://fiken-mcp.example.no") as c:
        return await c.get(path, headers={"Accept": accept})


async def test_browser_gets_landing_page():
    for path in ("/mcp", "/mcp/"):
        r = await _get(path, BROWSER)
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Innstillinger → Connectors" in r.text
        assert "/mcp</b>" in r.text
        assert r.headers["x-frame-options"] == "DENY"


async def test_mcp_traffic_is_untouched():
    # Stateless-modus: GET fra MCP-klienter avvises som før (405), POST krever innlogging.
    for accept in ("application/json, text/event-stream", "text/event-stream"):
        assert (await _get("/mcp", accept)).status_code == 405
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="https://fiken-mcp.example.no") as c:
        r = await c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert r.status_code == 401

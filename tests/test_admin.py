import pytest

from app import admin

TOKEN = "x" * 40


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("FIKEN_MCP_STATS_TOKEN", TOKEN)


def test_stats_token():
    assert admin.stats_token_ok(f"Bearer {TOKEN}")
    for bad in [None, "", TOKEN, f"Bearer {TOKEN}x", "Bearer ", f"Basic {TOKEN}"]:
        assert not admin.stats_token_ok(bad)


@pytest.mark.parametrize("value", ["", "short-token"])
def test_disabled_without_strong_token(monkeypatch, value):
    monkeypatch.setenv("FIKEN_MCP_STATS_TOKEN", value)
    assert not admin.stats_token_ok(f"Bearer {value}")


async def test_usage_middleware_counts_success_and_failure(monkeypatch):
    calls = []

    async def fake_record(tool, failed):
        calls.append((tool, failed))

    monkeypatch.setattr(admin, "record_call", fake_record)

    class Ctx:
        class message:
            name = "list_accounts"

    class Result:
        is_error = False

    mw = admin.UsageMiddleware()

    async def ok(_):
        return Result()

    async def boom(_):
        raise RuntimeError("x")

    await mw.on_call_tool(Ctx, ok)
    with pytest.raises(RuntimeError):
        await mw.on_call_tool(Ctx, boom)
    assert calls == [("list_accounts", False), ("list_accounts", True)]

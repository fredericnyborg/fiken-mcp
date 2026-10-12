import pytest

from app import admin


@pytest.fixture(autouse=True)
def admins(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "Boss@Example.no, other@x.no")


def test_admin_emails_normalised():
    assert admin.is_admin("boss@example.no") and admin.is_admin(" BOSS@example.no ")
    assert not admin.is_admin("nobody@x.no")


def test_no_admins_when_unset(monkeypatch):
    monkeypatch.delenv("ADMIN_EMAILS")
    assert not admin.is_admin("boss@example.no")


def test_session_roundtrip_expiry_and_tamper():
    cookie = admin.make_session("boss@example.no", now=1000)
    assert admin.read_session(cookie, now=1000) == "boss@example.no"
    assert admin.read_session(cookie, now=1000 + admin.SESSION_TTL_SECONDS + 1) is None
    email, exp, sig = cookie.rsplit("|", 2)
    assert admin.read_session(f"other@x.no|{exp}|{sig}", now=1000) is None
    for bad in [None, "", "x", "a|b", "a|b|c|d"]:
        assert admin.read_session(bad, now=0) is None


def test_session_revoked_when_removed_from_admins(monkeypatch):
    cookie = admin.make_session("boss@example.no", now=1000)
    monkeypatch.setenv("ADMIN_EMAILS", "other@x.no")
    assert admin.read_session(cookie, now=1000) is None


def test_admin_state_prefix():
    url, binding, nonce = __import__("app.account", fromlist=["x"]).start_login(admin.ADMIN_PREFIX)
    assert admin.is_admin_state(nonce) and not nonce.startswith("web_")


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

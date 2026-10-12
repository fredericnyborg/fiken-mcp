import pytest

from app import account, config, fiken_oauth


def test_session_roundtrip_and_expiry():
    cookie = account.make_session("u-1", now=1000)
    assert account.read_session(cookie, now=1000) == "u-1"
    assert account.read_session(cookie, now=1000 + account.SESSION_TTL_SECONDS + 1) is None


@pytest.mark.parametrize("cookie", [None, "", "a.b", "u-1.9999999999.bad", "u-2.9999999999.x.y"])
def test_session_rejects_garbage(cookie):
    assert account.read_session(cookie, now=0) is None


def test_session_rejects_tampered_user():
    user, exp, sig = account.make_session("u-1", now=0).split(".")
    assert account.read_session(f"u-2.{exp}.{sig}", now=0) is None


def test_csrf_bound_to_session():
    a, b = account.make_session("u-1", now=0), account.make_session("u-2", now=0)
    assert account.csrf_token(a) != account.csrf_token(b)


def test_start_login_uses_web_state_and_binding():
    url, binding, nonce = account.start_login()
    assert account.is_web_state(nonce)
    assert binding == account.login_binding(nonce)
    state = url.split("state=")[1]
    assert fiken_oauth.verify_state(state, config.fiken_client_secret()) == nonce


async def test_web_login_requires_same_browser():
    from app.mcp_auth import LoginError

    with pytest.raises(LoginError, match="nettleseren"):
        await account.complete_web_login("code", "web_x", None, "wrong")
    with pytest.raises(LoginError, match="avbrutt"):
        await account.complete_web_login(None, "web_x", "access_denied", account.login_binding("web_x"))

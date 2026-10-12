import base64
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from app import fiken_oauth


def test_state_roundtrip():
    state = fiken_oauth.make_state("req123", "secret")
    assert fiken_oauth.verify_state(state, "secret") == "req123"


@pytest.mark.parametrize(
    "state",
    ["", "req123", "req123.", ".abc", "req124." + "0" * 64],
)
def test_state_rejects_garbage(state):
    assert fiken_oauth.verify_state(state, "secret") is None


def test_state_rejects_tampering_and_wrong_secret():
    state = fiken_oauth.make_state("req123", "secret")
    request_id, _, sig = state.rpartition(".")
    assert fiken_oauth.verify_state(f"other.{sig}", "secret") is None
    assert fiken_oauth.verify_state(state, "another-secret") is None


def test_authorize_url_has_required_params_and_no_scope():
    url = fiken_oauth.build_authorize_url("st.ate", "cid", "https://x.no/fiken/callback")
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == fiken_oauth.AUTHORIZE_URL
    q = parse_qs(parsed.query)
    assert q == {
        "response_type": ["code"],
        "client_id": ["cid"],
        "redirect_uri": ["https://x.no/fiken/callback"],
        "state": ["st.ate"],
    }


@respx.mock
async def test_exchange_code_uses_basic_auth_and_sends_state():
    route = respx.post(fiken_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "expires_in": 3600})
    )
    out = await fiken_oauth.exchange_code("the-code", "the-state")
    assert out["access_token"] == "a"
    req = route.calls.last.request
    expected = base64.b64encode(b"test-client:test-secret").decode()
    assert req.headers["Authorization"] == f"Basic {expected}"
    form = parse_qs(req.content.decode())
    assert form == {
        "grant_type": ["authorization_code"],
        "code": ["the-code"],
        "redirect_uri": ["https://fiken-mcp.example.no/fiken/callback"],
        "state": ["the-state"],
    }


@respx.mock
async def test_refresh_sends_refresh_grant():
    route = respx.post(fiken_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "a2", "expires_in": 60})
    )
    await fiken_oauth.refresh("r1")
    form = parse_qs(route.calls.last.request.content.decode())
    assert form == {"grant_type": ["refresh_token"], "refresh_token": ["r1"]}


@respx.mock
async def test_token_error_flags_invalid_grant():
    respx.post(fiken_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    with pytest.raises(fiken_oauth.FikenOAuthError) as info:
        await fiken_oauth.refresh("dead")
    assert info.value.is_invalid_grant


@respx.mock
async def test_token_server_error_is_not_invalid_grant():
    respx.post(fiken_oauth.TOKEN_URL).mock(return_value=httpx.Response(500, text="oops"))
    with pytest.raises(fiken_oauth.FikenOAuthError) as info:
        await fiken_oauth.refresh("r")
    assert not info.value.is_invalid_grant


def test_tokens_to_blob_keeps_old_refresh_token():
    blob = fiken_oauth.tokens_to_blob(
        {"access_token": "a1", "refresh_token": "r1", "expires_in": 100}, now=1000
    )
    assert blob == {"access_token": "a1", "refresh_token": "r1", "expires_at": 1100}
    blob2 = fiken_oauth.tokens_to_blob({"access_token": "a2", "expires_in": 100}, prev=blob, now=2000)
    assert blob2 == {"access_token": "a2", "refresh_token": "r1", "expires_at": 2100}


def test_needs_refresh():
    blob = {"access_token": "a", "refresh_token": "r", "expires_at": 1000}
    assert not fiken_oauth.needs_refresh(blob, now=1000 - fiken_oauth.REFRESH_BUFFER_SECONDS - 1)
    assert fiken_oauth.needs_refresh(blob, now=1000 - fiken_oauth.REFRESH_BUFFER_SECONDS + 1)
    assert not fiken_oauth.needs_refresh({"access_token": "a", "expires_at": 0}, now=10**10)
    assert fiken_oauth.needs_refresh({"refresh_token": "r", "expires_at": "garbage"}, now=0)

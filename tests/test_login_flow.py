"""Fiken-callback-flyten (mcp_auth.complete_fiken_login) med stubbet DB og HTTP."""

import json
import uuid
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from mcp.server.auth.provider import AuthorizationParams

from app import config, db, fiken_oauth, mcp_auth, vault
from app.fiken_client import API_BASE


class FakeConn:
    def __init__(self, store):
        self.store = store

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield

        return tx()

    async def fetchrow(self, sql, *args):
        if sql.startswith("DELETE FROM mcp_auth_requests"):
            assert "approved" in sql  # kun godkjente forespørsler kan fullføres
            return self.store["requests"].pop(args[0], None)
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        if "INSERT INTO fiken_users" in sql:
            self.store["user"] = args
            return uuid.uuid4()
        if "INSERT INTO mcp_grants" in sql:
            return uuid.uuid4()
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        assert "INSERT INTO mcp_codes" in sql
        self.store["code"] = args


class FakePool(FakeConn):
    @asynccontextmanager
    async def _acquire(self):
        yield self

    def acquire(self):
        return self._acquire()


@pytest.fixture
def store(monkeypatch):
    s = {"requests": {}}
    pool = FakePool(s)

    async def get_pool():
        return pool

    monkeypatch.setattr(db, "pool", get_pool)
    return s


def pending(store, request_id="req1"):
    params = AuthorizationParams(
        state="client-state", scopes=["fiken"], code_challenge="c" * 43,
        redirect_uri="https://claude.ai/api/mcp/auth_callback", redirect_uri_provided_explicitly=True,
    )
    store["requests"][request_id] = {"client_id": "mcp-client", "params": params.model_dump_json()}
    return fiken_oauth.make_state(request_id, config.fiken_client_secret())


COOKIE = mcp_auth.browser_binding("req1")


async def test_invalid_state_is_rejected_before_db(store):
    with pytest.raises(mcp_auth.LoginError):
        await mcp_auth.complete_fiken_login("code", "req1.badsig", None, COOKIE)


@pytest.mark.parametrize("cookie", [None, "", mcp_auth.browser_binding("other-request")])
async def test_callback_requires_same_browser(store, cookie):
    state = pending(store)
    with pytest.raises(mcp_auth.LoginError, match="nettleseren"):
        await mcp_auth.complete_fiken_login("code", state, None, cookie)
    assert "req1" in store["requests"]  # ikke forbrukt


async def test_request_is_single_use(store):
    state = pending(store)
    store["requests"].clear()
    with pytest.raises(mcp_auth.LoginError, match="utløpt"):
        await mcp_auth.complete_fiken_login("code", state, None, COOKIE)


async def test_denied_redirects_with_error(store):
    state = pending(store)
    url = await mcp_auth.complete_fiken_login(None, state, "access_denied", COOKIE)
    q = parse_qs(urlparse(url).query)
    assert q["error"] == ["access_denied"] and q["state"] == ["client-state"]


@respx.mock
async def test_happy_path_stores_encrypted_tokens_and_returns_code(store):
    state = pending(store)
    token_route = respx.post(fiken_oauth.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "fa", "refresh_token": "fr", "expires_in": 3600})
    )
    respx.get(f"{API_BASE}/user").mock(
        return_value=httpx.Response(200, json={"name": "Ola", "email": "Ola@Example.no"})
    )
    url = await mcp_auth.complete_fiken_login("fiken-code", state, None, COOKIE)

    parsed = urlparse(url)
    assert parsed.netloc == "claude.ai"
    q = parse_qs(parsed.query)
    assert q["state"] == ["client-state"]
    code = q["code"][0]

    assert parse_qs(token_route.calls.last.request.content.decode())["state"] == [state]

    email, name, iv, ct = store["user"]
    assert email == "ola@example.no" and name == "Ola"
    assert "fa" not in ct and vault.decrypt(iv, ct)["refresh_token"] == "fr"

    code_hash, client_id, _grant, data, _exp = store["code"]
    assert code_hash == mcp_auth.hash_secret(code) and code != code_hash
    assert client_id == "mcp-client"
    assert json.loads(data)["code_challenge"] == "c" * 43


@respx.mock
async def test_rejected_fiken_code(store):
    state = pending(store)
    respx.post(fiken_oauth.TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(mcp_auth.LoginError):
        await mcp_auth.complete_fiken_login("bad", state, None, COOKIE)

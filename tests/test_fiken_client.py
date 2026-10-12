import httpx
import pytest
import respx

from app import fiken_client
from app.fiken_client import FikenAPIError, FikenError, FikenSession


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    monkeypatch.setattr(fiken_client, "MIN_GAP_SECONDS", 0)
    fiken_client._company_cache.clear()


@pytest.mark.parametrize("value", [123, "1920", "1500:10001", "my-company", "a1b2-c3d4"])
def test_segment_accepts(value):
    assert fiken_client.segment(value) == str(value)


@pytest.mark.parametrize("value", ["", ".", "..", "a/b", "a?b", "a b", "%2e%2e", "x#y"])
def test_segment_rejects(value):
    with pytest.raises(FikenError):
        fiken_client.segment(value)


def test_check_raw_path():
    assert fiken_client.check_raw_path("/companies/abc/accounts") == "/companies/abc/accounts"
    for bad in ["companies/abc", "/companies/../user", "/companies/a b"]:
        with pytest.raises(FikenError):
            fiken_client.check_raw_path(bad)


def test_parse_variants():
    assert fiken_client.parse(httpx.Response(201, headers={"Location": "https://x/1"})) == {
        "created": True, "location": "https://x/1",
    }
    assert fiken_client.parse(httpx.Response(204)) == {"success": True}
    paged = httpx.Response(
        200, json=[{"id": 1}],
        headers={"Fiken-Api-Page": "0", "Fiken-Api-Page-Size": "25", "Fiken-Api-Page-Count": "3",
                 "Fiken-Api-Result-Count": "61"},
    )
    assert fiken_client.parse(paged, paged=True) == {
        "items": [{"id": 1}],
        "pagination": {"page": 0, "pageSize": 25, "pageCount": 3, "resultCount": 61},
    }
    assert fiken_client.parse(httpx.Response(200, json=[1]), paged=True) == [1]
    with pytest.raises(FikenAPIError) as info:
        fiken_client.parse(httpx.Response(400, text="x" * 5000))
    assert info.value.status == 400 and len(info.value.body) < 2100


@respx.mock
async def test_send_retries_429_then_succeeds(monkeypatch):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(fiken_client.asyncio, "sleep", fake_sleep)
    route = respx.get(f"{fiken_client.API_BASE}/user").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json={"ok": 1})]
    )
    resp = await fiken_client.send("tok", "GET", "/user", query={"flag": True, "none": None})
    assert resp.status_code == 200 and route.call_count == 2
    assert 2.0 in sleeps
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.url.params["flag"] == "true" and "none" not in req.url.params


@respx.mock
async def test_send_does_not_retry_post_503(monkeypatch):
    route = respx.post(f"{fiken_client.API_BASE}/x").mock(return_value=httpx.Response(503))
    resp = await fiken_client.send("tok", "POST", "/x", json={})
    assert resp.status_code == 503 and route.call_count == 1


@respx.mock
async def test_company_slug_resolution(monkeypatch):
    async def token(_):
        return "tok"

    monkeypatch.setattr(fiken_client, "_access_token", token)
    route = respx.get(f"{fiken_client.API_BASE}/companies").mock(
        return_value=httpx.Response(200, json=[{"slug": "only-one"}])
    )
    session = FikenSession("u1")
    assert await session.company_slug(None) == "only-one"
    assert await session.company_slug(None) == "only-one"
    assert route.call_count == 1  # cachet
    assert await session.company_slug("explicit") == "explicit"

    route.mock(return_value=httpx.Response(200, json=[{"slug": "a"}, {"slug": "b"}]))
    with pytest.raises(FikenError, match="a, b"):
        await FikenSession("u2").company_slug(None)

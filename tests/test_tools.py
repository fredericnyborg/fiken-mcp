import pytest
from fastmcp.exceptions import ToolError

from app import tools
from app.endpoints import ENDPOINTS
from app.tools import endpoint_tool, input_schema, journal_balance_errors

BY_NAME = {e.name: e for e in ENDPOINTS}


def test_registry_is_sane():
    names = [e.name for e in ENDPOINTS]
    assert len(names) == len(set(names))
    assert {"list_companies", "create_journal_entry", "list_accounts", "create_invoice"} <= set(names)
    for e in ENDPOINTS:
        assert e.method in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        assert e.path == "" or e.path.startswith("/")
        if e.kind == "read":
            assert e.method == "GET" and not e.body


def test_schema_for_paged_and_path_params():
    s = input_schema(BY_NAME["list_invoices"])
    assert {"companySlug", "page", "pageSize", "query"} <= set(s["properties"])
    s = input_schema(BY_NAME["get_account"])
    assert s["properties"]["accountCode"] == {"type": "string"} and s["required"] == ["accountCode"]
    s = input_schema(BY_NAME["delete_sale"])
    assert s["required"] == ["saleId", "description"]
    assert "companySlug" not in input_schema(BY_NAME["list_companies"])["properties"]


def ok_entry(lines):
    return {"journalEntries": [{"description": "x", "date": "2026-01-01", "lines": lines}]}


def test_balance_ok_one_sided():
    body = ok_entry([{"amount": 10000, "debitAccount": "6300"}, {"amount": 10000, "creditAccount": "1920:10001"}])
    assert journal_balance_errors(body) == []


def test_balance_ok_two_sided_line():
    assert journal_balance_errors(ok_entry([{"amount": 500, "debitAccount": "6300", "creditAccount": "1920"}])) == []


def test_balance_detects_imbalance():
    errs = journal_balance_errors(ok_entry([{"amount": 10000, "debitAccount": "6300"}, {"amount": 9000, "creditAccount": "1920"}]))
    assert errs and "1000 øre" in errs[0]


def test_balance_skips_vat_and_account_lines():
    assert journal_balance_errors(ok_entry([{"amount": 800, "debitAccount": "6300", "debitVatCode": 1}, {"amount": 1000, "creditAccount": "1920"}])) == []
    assert journal_balance_errors(ok_entry([{"amount": 800, "account": "6300"}])) == []


@pytest.mark.parametrize(
    "body",
    [{}, {"journalEntries": []}, ok_entry([]), ok_entry([{"amount": 1.5, "debitAccount": "1"}]), ok_entry([{"amount": 1}])],
)
def test_balance_rejects_malformed(body):
    assert journal_balance_errors(body)


class FakeSession:
    def __init__(self):
        self.calls = []

    async def company_slug(self, slug):
        return slug or "default-co"

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return {"ok": True}


@pytest.fixture
def session(monkeypatch):
    fake = FakeSession()
    monkeypatch.setattr(tools, "current_session", lambda: fake)
    return fake


async def test_endpoint_tool_builds_path_and_query(session):
    tool = endpoint_tool(BY_NAME["list_invoices"])
    await tool.run({"page": 2, "query": {"issueDateGe": "2026-01-01"}})
    method, path, kwargs = session.calls[0]
    assert (method, path) == ("GET", "/companies/default-co/invoices")
    assert kwargs["query"] == {"issueDateGe": "2026-01-01", "page": 2}
    assert kwargs["paged"] is True and kwargs["json"] is None


async def test_endpoint_tool_required_query_and_slug(session):
    await endpoint_tool(BY_NAME["delete_sale"]).run({"companySlug": "co", "saleId": 7, "description": "feil"})
    method, path, kwargs = session.calls[0]
    assert (method, path) == ("PATCH", "/companies/co/sales/7/delete")
    assert kwargs["query"] == {"description": "feil"}


async def test_endpoint_tool_rejects_bad_path_param(session):
    with pytest.raises(ToolError):
        await endpoint_tool(BY_NAME["get_account"]).run({"accountCode": "../user"})
    assert session.calls == []


async def test_journal_entry_not_sent_when_unbalanced(session):
    body = ok_entry([{"amount": 100, "debitAccount": "6300"}])
    with pytest.raises(ToolError, match="Ikke sendt"):
        await endpoint_tool(BY_NAME["create_journal_entry"]).run({"body": body})
    assert session.calls == []


async def test_journal_entry_sent_when_balanced(session):
    body = ok_entry([{"amount": 100, "debitAccount": "6300"}, {"amount": 100, "creditAccount": "1920"}])
    await endpoint_tool(BY_NAME["create_journal_entry"]).run({"companySlug": "co", "body": body})
    method, path, kwargs = session.calls[0]
    assert (method, path) == ("POST", "/companies/co/generalJournalEntries")
    assert kwargs["json"] == body


async def test_company_root_and_user_paths(session):
    await endpoint_tool(BY_NAME["get_company"]).run({"companySlug": "co"})
    await endpoint_tool(BY_NAME["get_user"]).run({})
    assert [c[1] for c in session.calls] == ["/companies/co", "/user"]


def test_decode_upload_validation():
    import base64

    assert tools.decode_upload("a.pdf", base64.b64encode(b"%PDF").decode()) == b"%PDF"
    with pytest.raises(ToolError):
        tools.decode_upload("a.exe", "AAAA")
    with pytest.raises(ToolError):
        tools.decode_upload("a.pdf", "not base64!")


async def test_add_attachment_multipart(monkeypatch):
    import base64

    import httpx
    import respx
    from fastmcp import FastMCP

    from app import fiken_client

    async def token(_):
        return "tok"

    monkeypatch.setattr(fiken_client, "_access_token", token)
    monkeypatch.setattr(fiken_client, "MIN_GAP_SECONDS", 0)
    monkeypatch.setattr(tools, "current_session", lambda: fiken_client.FikenSession("u1"))
    mcp = FastMCP("t")
    tools.register(mcp)
    add = await mcp.get_tool("add_attachment")
    url = f"{fiken_client.API_BASE}/companies/co/sales/5/attachments"
    with respx.mock:
        route = respx.post(url).mock(return_value=httpx.Response(201, headers={"Location": "x"}))
        await add.run({"resource": "sale", "id": 5, "companySlug": "co", "filename": "k.pdf",
                       "fileBase64": base64.b64encode(b"%PDF-1").decode(), "attachToSale": True})
        req = route.calls.last.request
        assert req.headers["Content-Type"].startswith("multipart/form-data")
        assert b'name="file"; filename="k.pdf"' in req.content and b"%PDF-1" in req.content
        assert req.url.params["attachToSale"] == "true"

        await add.run({"resource": "sale", "id": 5, "companySlug": "co", "inboxDocumentId": 9})
        req = route.calls.last.request
        assert req.headers["Content-Type"] == "multipart/form-data; boundary=fikenmcp"
        assert req.url.params["inboxDocumentId"] == "9"

    with pytest.raises(ToolError, match="nøyaktig én"):
        await add.run({"resource": "sale", "id": 5, "companySlug": "co"})

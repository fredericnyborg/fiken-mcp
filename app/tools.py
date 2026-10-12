"""MCP-verktøyene: ett per Fiken-endepunkt (fra app/endpoints.py) + noen få spesialverktøy.

Verktøyene er tynne. Argumentene speiler Fikens API (sti-parametre, query, rå body),
og svaret er Fikens JSON uendret. Beløp er i øre.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.tools import Tool, ToolResult
from mcp.types import TextContent, ToolAnnotations

from app.endpoints import ENDPOINTS, STRING_PATH_PARAMS, Endpoint
from app.fiken_client import FikenError, FikenSession, check_raw_path, segment

ANNOTATIONS = {
    "read": ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    "write": ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
    "destructive": ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False),
}

ALLOWED_UPLOAD_EXTENSIONS = (".png", ".jpeg", ".jpg", ".gif", ".pdf")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

COMPANY_SLUG_SCHEMA = {
    "type": "string",
    "description": "Company slug from list_companies. May be omitted if the user has only one company.",
}


# -- felles ---------------------------------------------------------------------


def current_session() -> FikenSession:
    token = get_access_token()
    user_id = (token.claims or {}).get("user_id") if token else None
    if not user_id:
        raise ToolError("Ikke autentisert.")
    return FikenSession(user_id)


def result(data: Any) -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))])


def journal_balance_errors(body: Any) -> list[str]:
    """Sjekk at hvert bilag balanserer før det sendes.

    Bilag med mva-koder (debet netto, kredit brutto) eller «account»-linjer hoppes over;
    der er det Fiken som validerer.
    """
    errors: list[str] = []
    entries = body.get("journalEntries") if isinstance(body, dict) else None
    if not isinstance(entries, list) or not entries:
        return ["body.journalEntries må være en ikke-tom liste."]
    for i, entry in enumerate(entries):
        lines = entry.get("lines") if isinstance(entry, dict) else None
        if not isinstance(lines, list) or not lines:
            errors.append(f"journalEntries[{i}].lines må være en ikke-tom liste.")
            continue
        if any(
            isinstance(line, dict) and any(line.get(k) not in (None, "") for k in ("vatCode", "debitVatCode", "creditVatCode"))
            for line in lines
        ):
            continue
        if any(isinstance(line, dict) and line.get("account") for line in lines):
            continue  # «account»-linjer har Fikens egen fortegnslogikk; Fiken validerer
        total = 0
        for j, line in enumerate(lines):
            amount = line.get("amount") if isinstance(line, dict) else None
            if not isinstance(amount, int) or isinstance(amount, bool):
                errors.append(f"journalEntries[{i}].lines[{j}].amount må være heltall i øre.")
                continue
            debit, credit = line.get("debitAccount"), line.get("creditAccount")
            if debit and credit:
                continue
            if debit:
                total += amount
            elif credit:
                total -= amount
            else:
                errors.append(f"journalEntries[{i}].lines[{j}] mangler konto.")
        if total != 0:
            errors.append(
                f"journalEntries[{i}] balanserer ikke: debet - kredit = {total} øre ({total / 100:.2f} kr)."
            )
    return errors


VALIDATORS = {"journal_balance": journal_balance_errors}


# -- endepunkt-verktøy ------------------------------------------------------------


def input_schema(ep: Endpoint) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    if ep.company:
        props["companySlug"] = COMPANY_SLUG_SCHEMA
    for name in ep.path_params:
        props[name] = {"type": "string"} if name in STRING_PATH_PARAMS else {"type": "integer"}
        required.append(name)
    if ep.paged:
        props["page"] = {"type": "integer", "minimum": 0, "description": "0-based page (default 0)"}
        props["pageSize"] = {"type": "integer", "minimum": 1, "maximum": 100, "description": "Default 25, max 100"}
    for name in ep.required_query:
        props[name] = {"type": "string"}
        required.append(name)
    for name in ep.optional_query:
        props[name] = {"type": "string"}
    if ep.method == "GET" and (ep.paged or ep.filters):
        hint = f" Known filters: {', '.join(ep.filters)}." if ep.filters else ""
        props["query"] = {
            "type": "object",
            "additionalProperties": True,
            "description": "Extra query parameters passed straight to Fiken. Dates are YYYY-MM-DD." + hint,
        }
    if ep.body:
        props["body"] = {
            "type": "object",
            "additionalProperties": True,
            "description": "Request body in Fiken's own JSON format. Amounts are integers in øre.",
        }
        required.append("body")
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def describe(ep: Endpoint) -> str:
    text = f"{ep.description}. Fiken: {ep.method} {'/companies/{companySlug}' if ep.company else ''}{ep.path}"
    if ep.paged:
        text += ". Paginated: result is {items, pagination}; fetch page+1 while page+1 < pageCount"
    return text


class FikenEndpointTool(Tool):
    endpoint: Endpoint

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        ep = self.endpoint
        session = current_session()
        try:
            if ep.validate and ep.body:
                errors = VALIDATORS[ep.validate](arguments.get("body"))
                if errors:
                    raise ToolError("Ikke sendt til Fiken: " + " ".join(errors))
            path = ep.path
            for name in ep.path_params:
                if arguments.get(name) is None:
                    raise ToolError(f"Mangler {name}.")
                path = path.replace("{" + name + "}", segment(arguments[name]))
            if ep.company:
                slug = await session.company_slug(arguments.get("companySlug"))
                path = f"/companies/{slug}{path}"
            query: dict[str, Any] = dict(arguments.get("query") or {})
            for name in ("page", "pageSize", *ep.required_query, *ep.optional_query):
                if arguments.get(name) is not None:
                    query[name] = arguments[name]
            data = await session.request(
                ep.method, path, paged=ep.paged, query=query,
                json=arguments.get("body") if ep.body else None,
            )
        except FikenError as exc:
            raise ToolError(str(exc)) from exc
        return result(data)


def endpoint_tool(ep: Endpoint) -> FikenEndpointTool:
    return FikenEndpointTool(
        name=ep.name,
        description=describe(ep),
        parameters=input_schema(ep),
        annotations=ANNOTATIONS[ep.kind],
        endpoint=ep,
    )


# -- spesialverktøy ---------------------------------------------------------------

AttachmentResource = Literal[
    "contact", "invoice", "invoice_draft", "credit_note_draft", "journal_entry", "offer_draft",
    "order_confirmation_draft", "purchase", "purchase_draft", "sale", "sale_draft",
]

ATTACHMENT_BASE: dict[str, str] = {
    "contact": "/contacts",
    "invoice": "/invoices",
    "invoice_draft": "/invoices/drafts",
    "credit_note_draft": "/creditNotes/drafts",
    "journal_entry": "/journalEntries",
    "offer_draft": "/offers/drafts",
    "order_confirmation_draft": "/orderConfirmations/drafts",
    "purchase": "/purchases",
    "purchase_draft": "/purchases/drafts",
    "sale": "/sales",
    "sale_draft": "/sales/drafts",
}


def decode_upload(filename: str, file_base64: str) -> bytes:
    if not filename.lower().endswith(ALLOWED_UPLOAD_EXTENSIONS):
        raise ToolError(f"Filnavnet må slutte på {', '.join(ALLOWED_UPLOAD_EXTENSIONS)}.")
    try:
        raw = base64.b64decode(file_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError("fileBase64 er ikke gyldig base64.") from exc
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ToolError("Filen er for stor (maks 25 MB).")
    return raw


EMPTY_MULTIPART = (b"--fikenmcp--\r\n", "multipart/form-data; boundary=fikenmcp")


def register(mcp: FastMCP) -> None:
    for ep in ENDPOINTS:
        mcp.add_tool(endpoint_tool(ep))

    @mcp.tool(annotations=ANNOTATIONS["read"])
    async def fiken_get(path: str, query: dict[str, Any] | None = None) -> ToolResult:
        """Raw GET against any Fiken API v2 path (escape hatch for endpoints without a tool),
        e.g. /companies/{slug}/accounts. Path is relative to https://api.fiken.no/api/v2."""
        session = current_session()
        try:
            return result(await session.request("GET", check_raw_path(path), paged=True, query=query))
        except FikenError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=ANNOTATIONS["read"])
    async def list_attachments(
        resource: AttachmentResource, id: int, companySlug: str | None = None
    ) -> ToolResult:
        """Returns all attachments on a contact, invoice, sale, purchase, journal entry or draft."""
        session = current_session()
        try:
            slug = await session.company_slug(companySlug)
            path = f"/companies/{slug}{ATTACHMENT_BASE[resource]}/{segment(id)}/attachments"
            return result(await session.request("GET", path))
        except FikenError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=ANNOTATIONS["write"])
    async def add_attachment(
        resource: AttachmentResource,
        id: int,
        filename: str | None = None,
        fileBase64: str | None = None,
        comment: str | None = None,
        attachToPayment: bool | None = None,
        attachToSale: bool | None = None,
        ehfDocumentId: int | None = None,
        inboxDocumentId: int | None = None,
        companySlug: str | None = None,
    ) -> ToolResult:
        """Uploads an attachment (.png/.jpeg/.jpg/.gif/.pdf, base64) to a resource, or attaches an
        existing EHF/inbox document by ID (sale, purchase, journal_entry and their drafts).
        comment: contacts only. attachToPayment/attachToSale: sales and purchases."""
        session = current_session()
        sources = [fileBase64 is not None, ehfDocumentId is not None, inboxDocumentId is not None]
        if sum(sources) != 1:
            raise ToolError("Oppgi nøyaktig én av fileBase64, ehfDocumentId eller inboxDocumentId.")
        query = {
            "attachToPayment": attachToPayment, "attachToSale": attachToSale,
            "ehfDocumentId": ehfDocumentId, "inboxDocumentId": inboxDocumentId,
        }
        kwargs: dict[str, Any] = {"query": query}
        if fileBase64 is not None:
            if not filename:
                raise ToolError("filename er påkrevd sammen med fileBase64.")
            raw = decode_upload(filename, fileBase64)
            files: dict[str, Any] = {"filename": (None, filename), "file": (filename, raw)}
            if comment:
                files["comment"] = (None, comment)
            kwargs["files"] = files
        else:
            kwargs["content"], kwargs["content_type"] = EMPTY_MULTIPART
        try:
            slug = await session.company_slug(companySlug)
            path = f"/companies/{slug}{ATTACHMENT_BASE[resource]}/{segment(id)}/attachments"
            return result(await session.request("POST", path, **kwargs))
        except FikenError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=ANNOTATIONS["destructive"])
    async def delete_attachment(
        resource: AttachmentResource, id: int, attachmentUuid: str, companySlug: str | None = None
    ) -> ToolResult:
        """Deletes an attachment (uuid from list_attachments) from a resource."""
        session = current_session()
        try:
            slug = await session.company_slug(companySlug)
            path = (
                f"/companies/{slug}{ATTACHMENT_BASE[resource]}/{segment(id)}"
                f"/attachments/{segment(attachmentUuid)}"
            )
            return result(await session.request("DELETE", path))
        except FikenError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=ANNOTATIONS["write"])
    async def create_inbox_document(
        filename: str,
        fileBase64: str,
        name: str | None = None,
        description: str | None = None,
        companySlug: str | None = None,
    ) -> ToolResult:
        """Uploads a document (.png/.jpeg/.jpg/.gif/.pdf, base64) to the company's Fiken inbox."""
        session = current_session()
        raw = decode_upload(filename, fileBase64)
        files: dict[str, Any] = {
            "filename": (None, filename),
            "name": (None, name or filename),
            "file": (filename, raw),
        }
        if description:
            files["description"] = (None, description)
        try:
            slug = await session.company_slug(companySlug)
            return result(await session.request("POST", f"/companies/{slug}/inbox", files=files))
        except FikenError as exc:
            raise ToolError(str(exc)) from exc

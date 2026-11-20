"""Sammenlign app/endpoints.py med Fikens OpenAPI-spesifikasjon.

    curl -so /tmp/fiken.yaml https://api.fiken.no/api/v2/docs/swagger.yaml
    uv run python scripts/compare_spec.py /tmp/fiken.yaml

Viser endepunkter som mangler (vedlegg og innboks dekkes av spesialverktøy i app/tools.py),
verktøy som ikke lenger finnes i spesifikasjonen, utgåtte endepunkter og avvik i
query-parametere, body og paginering.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.endpoints import ENDPOINTS  # noqa: E402

SPECIAL = re.compile(r"/attachments(/\{\})?$|^/companies/\{\}/inbox$")  # spesialverktøy


def norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path) or "/"


def main(spec_path: str) -> int:
    spec = yaml.safe_load(Path(spec_path).read_text())
    comps = spec.get("components", {}).get("parameters", {})

    def res(p: dict) -> dict:
        return comps[p["$ref"].split("/")[-1]] if "$ref" in p else p

    theirs: dict[tuple[str, str], dict] = {}
    for path, ops in spec["paths"].items():
        common = [res(p) for p in ops.get("parameters", [])]
        for method, op in ops.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            params = common + [res(p) for p in op.get("parameters", [])]
            theirs[(method.upper(), norm(path))] = {
                "id": op.get("operationId"),
                "query": [p for p in params if p.get("in") == "query"],
                "body": "requestBody" in op,
                "deprecated": op.get("deprecated", False),
            }

    ours = {(e.method, norm(("/companies/{companySlug}" if e.company else "") + e.path)): e for e in ENDPOINTS}
    problems = 0
    print(f"Fiken API {spec.get('info', {}).get('version')}: {len(theirs)} operasjoner, {len(ours)} endepunkt-verktøy")

    for key in sorted(set(theirs) - set(ours), key=lambda k: k[1]):
        if not SPECIAL.search(key[1]):
            problems += 1
            print("MANGLER   ", *key, theirs[key]["id"])
    for key in sorted(set(ours) - set(theirs), key=lambda k: k[1]):
        problems += 1
        print("UTGÅTT    ", *key, ours[key].name)
    for key, e in sorted(ours.items(), key=lambda kv: kv[1].name):
        op = theirs.get(key)
        if op is None:
            continue
        req = {p["name"] for p in op["query"] if p.get("required")}
        opt = {p["name"] for p in op["query"] if not p.get("required")} - {"page", "pageSize"}
        mine = set(e.optional_query) | set(e.filters)
        notes = []
        if op["deprecated"]:
            notes.append("deprecated")
        if req != set(e.required_query):
            notes.append(f"påkrevd query: spec={sorted(req)} vår={sorted(e.required_query)}")
        if opt - mine - req:
            notes.append(f"mangler valgfri query: {sorted(opt - mine - req)}")
        if mine - opt - req:
            notes.append(f"ukjent query: {sorted(mine - opt - req)}")
        if op["body"] != e.body:
            notes.append(f"body: spec={op['body']} vår={e.body}")
        if any(p["name"] == "page" for p in op["query"]) != e.paged:
            notes.append("paginering avviker")
        if notes:
            problems += 1
            print("AVVIK     ", e.name, "|", "; ".join(notes))
    print("OK" if not problems else f"{problems} avvik")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

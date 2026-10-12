# CLAUDE.md — fiken-mcp

Rå MCP-server for Fiken API v2. Eget prosjekt, atskilt fra Autoajour. Se README.md.

## Ship
nysky speiler GitHub `fredericnyborg/fiken-mcp` (`main`) og deployer automatisk. Merge til
`main` = live. Claude kan ikke kjøre `nysky.mjs`; manuelle nysky-kommandoer kjøres av brukeren.

## Regler
- Secrets ALDRI i git (`FIKEN_CLIENT_ID`, `FIKEN_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY` er nysky-secrets).
- Nytt skjema → `migrations.sql`, idempotent (`IF NOT EXISTS`). Ingen pgcrypto (PG16, `gen_random_uuid` er innebygd).
- `nysky.toml`: `[[routes]]` MÅ stå etter alle topp-nivå-felter.
- Entrypoint kjører `/app/.venv/bin/uvicorn` direkte (ikke `uv run` – låst egress).
- `pyproject.toml` trenger `default-groups = ["base"]`, ellers ingen uvicorn i containeren.
- Verktøy er tynne og 1:1 med Fiken-endepunkter: legg nye inn i `app/endpoints.py`.
- Beløp er heltall i øre.

## Test
`uv sync --group dev && uv run pytest` (HTTP stubbet med respx, ingen DB nødvendig).

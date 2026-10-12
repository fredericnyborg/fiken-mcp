# fiken-mcp

Rå MCP-server for [Fiken API v2](https://api.fiken.no/api/v2/docs/). Hver bruker kobler
sitt eget Fiken via OAuth, og får alle Fiken-endepunktene som MCP-verktøy.

- **URL:** `https://fiken-mcp.autoajour.no/mcp` (streamable HTTP)
- **Stack:** Python 3.11, FastMCP (offisiell `mcp`-SDK under), asyncpg, Postgres (nysky-datastore)
- **Verktøy:** 157 stk – 152 genereres 1:1 fra endepunkter i `app/endpoints.py`, pluss
  `fiken_get` (rå GET), `list_attachments` / `add_attachment` / `delete_attachment` og
  `create_inbox_document`. Svarene er Fikens JSON uendret. Beløp i øre.

## Koble til (sluttbruker)

Claude → Innstillinger → Connectors → *Legg til egendefinert* → `https://fiken-mcp.autoajour.no/mcp`.
Du sendes til Fiken for å logge inn og godkjenne. Ferdig.

## Auth-flyt

```
Claude ──DCR /register──▶ fiken-mcp
Claude ──/authorize (PKCE)──▶ fiken-mcp ──302──▶ /consent/<id> (samtykkeside: klientnavn + retur-domene)
Bruker «Godkjenn» (CSRF + same-origin) ──303──▶ fiken.no/oauth/authorize (state = request_id.HMAC)
                                         + cookie som binder runden til denne nettleseren
Fiken ──/fiken/callback?code&state──▶ fiken-mcp
        ├─ POST fiken.no/oauth/token (Basic-auth, state)  → Fiken-tokens
        ├─ GET /user                                      → e-post = bruker-ID
        ├─ lagre Fiken-tokens AES-256-GCM-kryptert (fiken_users)
        └─ 302 → Claude redirect_uri med MCP-engangskode
Claude ──/token (PKCE)──▶ access (1 t) + refresh (90 d, roteres)
Claude ──/mcp Bearer──▶ verktøy → Fiken API med brukerens token (fornyes automatisk)
```

- **Samtykkesiden er et sikkerhetskrav:** alle MCP-klienter deler én Fiken-app. Uten den kunne
  en angriper registrere en klient og sende offeret en innloggingslenke; husker Fiken samtykket,
  ville angriperen fått offerets tilgang. Callback krever i tillegg cookien fra nettleseren som
  godkjente (MCP-spesifikasjonens krav til proxy-servere).
- MCP-tokens/-koder lagres kun som sha256-hash.
- Fiken-`state` er engangs (forespørselen slettes ved callback) og utløper etter 15 min.
- Trekker brukeren tilgangen i Fiken (refresh gir `invalid_grant`), slettes Fiken-tokenene og
  alle MCP-grants for brukeren, så klienten må logge inn på nytt.
- Fiken tåler ett samtidig kall per bruker: kall serialiseres per bruker (≥ 250 ms mellom),
  429/503 prøves på nytt med `Retry-After`.
- `create_journal_entry` sjekker at hvert bilag balanserer før det sendes (hoppes over når
  linjene har mva-koder eller `account`-felt – da validerer Fiken).

## Filer

| Fil | Innhold |
|---|---|
| `main.py` | FastMCP-app, samtykkeside, `/fiken/callback`, `/health` |
| `app/mcp_auth.py` | OAuth-server for MCP-klienter + Fiken-innlogging |
| `app/fiken_oauth.py` | Fiken OAuth: state, authorize-URL, token/refresh, token-blob |
| `app/fiken_client.py` | Fiken API-klient: token-fornyelse, serialisering, retry, paginering |
| `app/endpoints.py` | Register over Fiken-endepunkter (→ verktøy) |
| `app/tools.py` | Verktøygenerering + spesialverktøy + bilagsbalanse |
| `app/vault.py` | AES-256-GCM |
| `migrations.sql` | Idempotent skjema (kjøres av `release_command`) |

## Utvikling

```bash
uv sync --group dev
uv run pytest
```

## Drift (nysky)

Første gang (kjøres av deg – Claude kan ikke kjøre `nysky.mjs`):

1. **Fiken partner-app** på fiken.no/api/partner. Redirect-URI:
   `https://fiken-mcp.autoajour.no/fiken/callback`.
2. **DNS hos Domeneshop:** `CNAME fiken-mcp → inn.nysky.no`.
3. **Opprett appen + secrets** (fra en klon av dette repoet):
   ```bash
   node nysky.mjs secret put FIKEN_CLIENT_ID
   node nysky.mjs secret put FIKEN_CLIENT_SECRET
   python3 -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())" | node nysky.mjs secret put TOKEN_ENCRYPTION_KEY
   ```
4. **Koble nysky til GitHub-repoet** `fredericnyborg/fiken-mcp` (push-to-deploy, som Autoajour).
   `[container].git` i `nysky.toml` peker på `nyborgfrederic/fiken-mcp`; juster om speilet heter noe annet.

Deretter: merge til `main` → nysky deployer automatisk.

`TOKEN_ENCRYPTION_KEY` må aldri byttes uten migrering – da kan ikke lagrede Fiken-tokens
dekrypteres, og alle brukere må koble til på nytt.

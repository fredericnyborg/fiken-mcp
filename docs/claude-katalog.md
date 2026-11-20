# Innsending til Claudes Connectors Directory

> **Status:** klar til innsending – sist sjekket 2026-11-20 mot
> https://claude.com/docs/connectors/building/submission og Anthropics Software Directory Policy.

Fiken har godkjent appen for produksjon og bruk av navnet «Fiken».

## Slik sender du inn
1. https://claude.ai/directory/manage → **Submit new** → **MCP connector** (krever betalt Claude-plan).
2. Fyll ut stegene med tekstene under. Verktøyene synkes automatisk fra serveren.
3. Spørsmål: mcp-review@anthropic.com.

## Krav → hvor de er oppfylt
| Krav | Oppfylt |
|---|---|
| HTTPS + Streamable HTTP | `https://fiken-mcp.autoajour.no/mcp` |
| OAuth 2.x, PKCE S256, DCR, 401 + `WWW-Authenticate` | FastMCP `OAuthProvider` (`app/mcp_auth.py`) |
| Callback `https://claude.ai/api/mcp/auth_callback` + loopback (Claude Code) | DCR: klienten registrerer egne redirect-URI-er |
| `title` + `readOnlyHint`/`destructiveHint` på alle verktøy | `app/tools.py` (`annotations()`), testet i `test_every_tool_meets_claude_directory_rules` |
| Lese og skrive i separate verktøy | Ett verktøy per endepunkt; `fiken_get` er kun GET og nevner Fiken API |
| Ingen pengeoverføring | Ingen verktøy flytter penger; betalingsverktøyene sier «bookkeeping only» |
| Sparsomme svar | Kompakt JSON, paginering (`pageSize` maks 100) |
| Personvernerklæring | https://fiken-mcp.autoajour.no/personvern (`app/legal.py`) |
| Dokumentasjon + feilsøking + support | https://fiken-mcp.autoajour.no/hjelp, support@autoajour.no |
| Sletting av brukerdata | `/konto` → «Koble fra alt og slett data» sletter brukerraden |
| Ikon | `/icon.png` (512×512) |

## Listing-tekster
- **Navn:** Fiken
- **One-liner (≤ 200):** Read and manage your Fiken accounting – invoices, sales, purchases, contacts, journal entries and balances – straight from Claude.
- **Beskrivelse (≤ 2000):**
  Fiken is a Norwegian accounting system used by small businesses. This connector gives Claude access to your own Fiken companies through the official Fiken API v2, signed in with your Fiken account.
  Claude can look up invoices, sales, purchases, contacts, products, projects, the chart of accounts, account balances and bank balances; create and send invoices, offers and credit notes; register purchases, sales and payments; post journal entries (checked for balance before sending); upload receipts to the Fiken inbox; and log time.
  All amounts are integers in øre (1/100 NOK). Tools that change, delete or send documents are annotated so Claude asks before running them. No tool transfers money – payments are only recorded in the books. Accounting data is not stored by the connector; only encrypted Fiken tokens and connection metadata are kept, in Norway. Users can revoke or delete everything at /konto.
- **Kategorier:** Finance, Productivity (Business)
- **Dokumentasjon:** https://fiken-mcp.autoajour.no/hjelp
- **Personvern:** https://fiken-mcp.autoajour.no/personvern
- **Support:** support@autoajour.no
- **Utgiver:** Autoajour AS

## Bruksområder / eksempelprompter (minst 3)
1. «Which customer invoices in Fiken are overdue and unpaid? Sum them per customer.»
2. «Create an invoice draft to Ola AS for 3 hours of consulting at NOK 1,200 excl. VAT.»
3. «What was the balance on account 1920 at 31 October, and list October's transactions.»
4. «Upload this receipt to the Fiken inbox and register it as a purchase from Elkjøp on account 6540.»

## Autentisering (portal-steg 6)
`oauth_dcr` – Dynamic Client Registration. Ingen klient-ID/hemmelighet å legge inn.

## Testkonto (portal-steg 8) – må lages av deg
- Opprett et demoselskap i Fiken med eksempeldata (kunder, fakturaer, kjøp, bilag).
- Lag en egen Fiken-bruker for Anthropic med tilgang kun til demoselskapet. Del innloggingen
  i portalen, ikke på e-post.
- Kjør alle verktøyene én gang (MCP Inspector eller custom connector i Claude) før innsending.

## Personvern/compliance (portal-steg 7 og 9)
- Behandlingsansvarlig: Autoajour AS. Databehandler: nysky (Norge).
- Lagret: e-post/navn, krypterte Fiken-tokens, tilkoblinger, hash av MCP-tokens, anonym bruksstatistikk.
- Ikke lagret: regnskapsdata, samtaler.
- «Financial transactions»: ingen pengeoverføring; skriveverktøy krever bekreftelse (destructiveHint).

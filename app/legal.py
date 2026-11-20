"""Offentlige sider som Claude-katalogen krever: personvernerklæring (/personvern, /privacy)
og dokumentasjon/support (/hjelp, /help). Innholdet beskriver det koden faktisk gjør –
endres lagring eller logging, må teksten her oppdateres i samme PR.
"""

from __future__ import annotations

import html

from app import config

UPDATED = "2026-11-20"


def _mail() -> str:
    m = html.escape(config.SUPPORT_EMAIL)
    return f"<a href='mailto:{m}'>{m}</a>"


def privacy_html() -> str:
    pub, mail = html.escape(config.PUBLISHER), _mail()
    return f"""
<p class='muted center'>Sist oppdatert {UPDATED} · <a href='#en'>English below</a></p>

<h2>Hvem er ansvarlig</h2>
<p>{pub} er behandlingsansvarlig for personopplysningene som Fiken MCP lagrer.
Kontakt: {mail}.</p>

<h2>Hva vi lagrer</h2>
<ul>
<li><b>Fiken-brukeren din:</b> e-post og navn (fra Fiken), brukt som identitet.</li>
<li><b>Fiken-tilgang:</b> tilgangs- og fornyelsesnøkkel fra Fiken, kryptert med AES-256-GCM.</li>
<li><b>Tilkoblinger:</b> hvilken AI-klient du har godkjent (navn og retur-adresse), når den ble
koblet til og sist brukt. Våre egne tilgangsnøkler lagres bare som sha256-hash.</li>
<li><b>Bruksstatistikk:</b> antall verktøykall og feil per verktøy per dag – uten bruker-ID og
uten regnskapsdata.</li>
<li><b>Tekniske logger</b> hos driftsleverandøren (bl.a. IP-adresse og forespurt adresse), brukt
til drift og feilsøking.</li>
</ul>

<h2>Hva vi ikke lagrer</h2>
<p>Regnskapsdata (fakturaer, bilag, kontakter, saldoer, vedlegg o.l.) sendes rett mellom
AI-klienten og Fiken og lagres ikke hos oss. Vi ser ikke Fiken-passordet ditt, og vi leser ikke
samtalene dine med AI-assistenten.</p>

<h2>Formål og grunnlag</h2>
<p>Opplysningene brukes bare til å koble AI-klienten din til Fiken etter ditt eget ønske
(avtale, GDPR art. 6 nr. 1 b) og til drift og sikkerhet (berettiget interesse, art. 6 nr. 1 f).
Vi selger ikke data og bruker dem ikke til markedsføring eller AI-trening.</p>

<h2>Lagring og sletting</h2>
<ul>
<li>Data lagres i Norge hos nysky, som er databehandler for oss.</li>
<li>Tilgangsnøkler varer 1 time, fornyelsesnøkler 90 dager (roteres ved bruk).</li>
<li>Under <a href='/konto'>Mine tilkoblinger</a> kan du trekke tilbake én klient, eller velge
«Koble fra alt», som sletter alt vi har lagret om deg umiddelbart.</li>
<li>Trekker du tilgangen i Fiken, sletter vi Fiken-nøklene og tilkoblingene ved neste kall.</li>
</ul>

<h2>Dine rettigheter</h2>
<p>Du kan be om innsyn, retting, sletting, begrensning og dataportabilitet, og protestere på
behandlingen. Skriv til {mail}. Du kan klage til
<a href='https://www.datatilsynet.no'>Datatilsynet</a>.</p>

<h2>Tredjeparter</h2>
<p>Fiken AS behandler regnskapet ditt etter egne vilkår. AI-klienten du kobler til (f.eks.
Claude fra Anthropic) behandler det du deler i samtalen etter sine vilkår.</p>

<h2 id=en>English summary</h2>
<p>{pub} ({mail}) is the controller. We store your Fiken e-mail and name, your Fiken OAuth tokens
(AES-256-GCM encrypted), which AI clients you approved and when they were last used, hashed
MCP tokens, daily per-tool call counts without user IDs, and hosting logs (IP, path). Accounting
data passes through to Fiken and is <b>not stored</b>. Data is hosted in Norway (nysky). We do not
sell data or use it for advertising or AI training. Access tokens last 1 hour, refresh tokens
90 days. «Koble fra alt» at <a href='/konto'>/konto</a> deletes everything about you
immediately. For access, correction or deletion requests, e-mail {mail}.</p>
"""


def help_html() -> str:
    url = html.escape(config.public_url() + "/mcp")
    mail = _mail()
    return f"""
<p class='muted center'><a href='#en'>English below</a></p>

<h2>Koble til</h2>
<ol>
<li>Claude → <b>Innstillinger → Connectors</b>: velg Fiken, eller legg til egendefinert med
<code>{url}</code>.</li>
<li>Godkjenn klienten på samtykkesiden, logg inn med Fiken og godkjenn der.</li>
<li>Har du flere selskaper i Fiken, si hvilket (verktøyene tar <code>companySlug</code>).</li>
</ol>

<h2>Eksempler</h2>
<ul>
<li>«Hvilke kundefakturaer i Fiken er forfalt og ikke betalt?»</li>
<li>«Lag et fakturautkast til Ola AS på 3 timer rådgivning à 1 200 kr eks. mva.»</li>
<li>«Hva er saldoen på konto 1920 per 31.10, og hvilke transaksjoner har vi hatt i oktober?»</li>
<li>«Last opp denne kvitteringen til innboksen i Fiken.»</li>
</ul>

<h2>Verktøy</h2>
<p>Alle endepunktene i <a href='https://api.fiken.no/api/v2/docs/'>Fiken API v2</a> finnes som
egne verktøy (lese, opprette, endre, slette, sende), pluss en rå GET for endepunkter uten eget
verktøy. Beløp er heltall i øre (100 = 1 kr). Verktøyene som endrer eller sletter, og de som
sender dokumenter til kunder, er merket slik at Claude ber deg bekrefte først. Ingen verktøy
overfører penger: betalinger registreres bare i regnskapet.</p>

<h2>Feilsøking</h2>
<ul>
<li><b>«Ikke autentisert» / må logge inn på nytt:</b> tilgangen er trukket i Fiken eller utløpt.
Koble til på nytt i Claude.</li>
<li><b>Fiken avviser en postering:</b> feilmeldingen fra Fiken vises uendret – typisk feil
konto/mva-kombinasjon eller et bilag som ikke balanserer.</li>
<li><b>Treg respons / 429:</b> Fiken tillater ett kall om gangen per bruker; vi venter og prøver
på nytt automatisk.</li>
<li><b>Feil selskap:</b> be assistenten kjøre <code>list_companies</code> og bruke riktig
<code>companySlug</code>.</li>
</ul>

<h2>Support</h2>
<p>{mail} – vi svarer normalt innen én virkedag. Se også <a href='/personvern'>personvern</a> og
<a href='/konto'>Mine tilkoblinger</a>.</p>

<h2 id=en>English</h2>
<p>Fiken MCP connects Claude to the Norwegian accounting system
<a href='https://www.fiken.no'>Fiken</a> through the official Fiken API v2. Add
<code>{url}</code> as a connector, approve the client, and sign in with Fiken. Every Fiken API v2
endpoint is a separate tool; amounts are integers in øre (1/100 NOK). Write, delete and send tools
are annotated so Claude asks before running them. No tool transfers money – payments are only
recorded in the books. Errors from Fiken are returned verbatim. Support: {mail}.</p>
"""

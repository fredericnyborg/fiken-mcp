-- Idempotent skjema for fiken-mcp. Kjøres av release_command ved hver deploy.

-- Én rad per Fiken-bruker (identitet = e-post fra GET /user).
-- Fiken-tokens ligger kun kryptert (AES-256-GCM, app/vault.py).
CREATE TABLE IF NOT EXISTS fiken_users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text NOT NULL UNIQUE,
    name          text,
    token_iv      text,
    token_ct      text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- MCP-klienter registrert via Dynamic Client Registration.
CREATE TABLE IF NOT EXISTS mcp_clients (
    client_id   text PRIMARY KEY,
    info        jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Ventende /authorize-forespørsler (samtykkeside → Fiken → callback).
CREATE TABLE IF NOT EXISTS mcp_auth_requests (
    id          text PRIMARY KEY,
    client_id   text NOT NULL,
    params      jsonb NOT NULL,
    approved    boolean NOT NULL DEFAULT false,
    expires_at  timestamptz NOT NULL
);

-- En grant = én godkjent kobling (MCP-klient ↔ Fiken-bruker).
CREATE TABLE IF NOT EXISTS mcp_grants (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES fiken_users(id) ON DELETE CASCADE,
    client_id     text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz,
    revoked_at    timestamptz
);

-- Engangskoder (sha256-hash). Byttes mot tokens på /token.
CREATE TABLE IF NOT EXISTS mcp_codes (
    code_hash   text PRIMARY KEY,
    client_id   text NOT NULL,
    grant_id    uuid NOT NULL REFERENCES mcp_grants(id) ON DELETE CASCADE,
    data        jsonb NOT NULL,
    expires_at  timestamptz NOT NULL
);

-- Access/refresh-tokens (kun sha256-hash).
CREATE TABLE IF NOT EXISTS mcp_tokens (
    token_hash  text PRIMARY KEY,
    kind        text NOT NULL CHECK (kind IN ('access', 'refresh')),
    grant_id    uuid NOT NULL REFERENCES mcp_grants(id) ON DELETE CASCADE,
    client_id   text NOT NULL,
    scopes      text[] NOT NULL,
    resource    text,
    expires_at  timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS mcp_tokens_grant_idx ON mcp_tokens (grant_id);
CREATE INDEX IF NOT EXISTS mcp_grants_user_idx ON mcp_grants (user_id);

-- Bruksstatistikk for adminsiden: kun aggregerte tall per dag og verktøy.
-- Ingen bruker-ID og ingen regnskapsdata (jf. Fiken-avtalen om sammenstilling).
CREATE TABLE IF NOT EXISTS usage_daily (
    day     date NOT NULL,
    tool    text NOT NULL,
    calls   integer NOT NULL DEFAULT 0,
    errors  integer NOT NULL DEFAULT 0,
    PRIMARY KEY (day, tool)
);

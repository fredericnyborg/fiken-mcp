"""AES-256-GCM-kryptering av Fiken-tokens at rest.

Nøkkelen er TOKEN_ENCRYPTION_KEY (32 byte, base64). Hver kryptering bruker en
tilfeldig 12-byte nonce; nonce og chiffertekst (inkl. GCM-tag) lagres separat.
Generer nøkkel: python3 -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
"""

from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _key() -> bytes:
    raw = os.environ.get("TOKEN_ENCRYPTION_KEY")
    if not raw:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY er ikke satt (server-secret).")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY er ikke gyldig base64.") from exc
    if len(key) != 32:
        raise RuntimeError(f"TOKEN_ENCRYPTION_KEY må være 32 byte, er {len(key)}.")
    return key


def encrypt(data: dict) -> tuple[str, str]:
    """Krypter en dict som JSON. Returnerer (iv_b64, ct_b64)."""
    iv = os.urandom(12)
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    ct = AESGCM(_key()).encrypt(iv, payload, None)
    return base64.b64encode(iv).decode(), base64.b64encode(ct).decode()


def decrypt(iv_b64: str, ct_b64: str) -> dict:
    """Dekrypter. Kaster cryptography.exceptions.InvalidTag ved feil nøkkel/tukling."""
    raw = AESGCM(_key()).decrypt(base64.b64decode(iv_b64), base64.b64decode(ct_b64), None)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Dekryptert blob er ikke et JSON-objekt.")
    return data

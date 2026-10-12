import base64

import pytest
from cryptography.exceptions import InvalidTag

from app import vault


def test_roundtrip_and_random_nonce():
    data = {"access_token": "a", "refresh_token": "r", "expires_at": 1}
    iv1, ct1 = vault.encrypt(data)
    iv2, ct2 = vault.encrypt(data)
    assert (iv1, ct1) != (iv2, ct2)
    assert len(base64.b64decode(iv1)) == 12
    assert "access_token" not in ct1
    assert vault.decrypt(iv1, ct1) == data


def test_wrong_key_fails(monkeypatch):
    iv, ct = vault.encrypt({"a": "b"})
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", base64.b64encode(b"x" * 32).decode())
    with pytest.raises(InvalidTag):
        vault.decrypt(iv, ct)


def test_tampered_ciphertext_fails():
    iv, ct = vault.encrypt({"a": "b"})
    raw = bytearray(base64.b64decode(ct))
    raw[0] ^= 1
    with pytest.raises(InvalidTag):
        vault.decrypt(iv, base64.b64encode(bytes(raw)).decode())


@pytest.mark.parametrize("key", [None, "not base64!", base64.b64encode(b"short").decode()])
def test_bad_key(monkeypatch, key):
    if key is None:
        monkeypatch.delenv("TOKEN_ENCRYPTION_KEY")
    else:
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    with pytest.raises(RuntimeError):
        vault.encrypt({"a": "b"})

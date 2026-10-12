import base64
import os

import pytest

os.environ.setdefault("FIKEN_CLIENT_ID", "test-client")
os.environ.setdefault("FIKEN_CLIENT_SECRET", "test-secret")
os.environ.setdefault("PUBLIC_URL", "https://fiken-mcp.example.no")


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", base64.b64encode(b"k" * 32).decode())

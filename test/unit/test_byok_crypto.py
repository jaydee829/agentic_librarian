"""Unit tests for core/byok.py's KMS crypto seam (monetization arc 3/3). The fake-KMS-
client pattern here mirrors enrichment/tasks.py's `_client()` lazy-import seam:
`set_kms_client` injects a fake client so no real `google-cloud-kms` network call ever
happens in these tests."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from agentic_librarian.core import byok

KEY_NAME = "projects/p/locations/us-central1/keyRings/librarian/cryptoKeys/byok-credentials"


class _FakeKmsClient:
    """Records encrypt/decrypt request dicts and returns SimpleNamespace responses
    shaped like the real KMS EncryptResponse/DecryptResponse (.ciphertext/.plaintext)."""

    def __init__(self, decrypt_error: Exception | None = None):
        self.encrypt_calls: list[dict] = []
        self.decrypt_calls: list[dict] = []
        self._decrypt_error = decrypt_error

    def encrypt(self, request):
        self.encrypt_calls.append(request)
        return SimpleNamespace(ciphertext=request["plaintext"][::-1])  # trivial reversible stand-in

    def decrypt(self, request):
        self.decrypt_calls.append(request)
        if self._decrypt_error is not None:
            raise self._decrypt_error
        return SimpleNamespace(plaintext=request["ciphertext"][::-1])


class _FakeCredentialSession:
    """Stands in for a SQLAlchemy Session: session.get(Model, pk) returns whatever row
    was configured, and records the (model, pk) it was called with."""

    def __init__(self, row=None):
        self._row = row
        self.get_calls: list[tuple] = []

    def get(self, model, pk):
        self.get_calls.append((model, pk))
        return self._row


class _FakeCredentialRow:
    def __init__(self, encrypted_key: bytes):
        self.encrypted_key = encrypted_key


@pytest.fixture(autouse=True)
def _reset_kms_client():
    """set_kms_client caches a module global — reset after every test so a fake client
    never leaks into a later, unrelated test."""
    yield
    byok.set_kms_client(None)


def test_encrypt_then_decrypt_round_trips(monkeypatch):
    monkeypatch.setenv("KMS_KEY_NAME", KEY_NAME)
    fake = _FakeKmsClient()
    byok.set_kms_client(fake)

    ciphertext = byok.encrypt_key("sk-real-secret")
    assert ciphertext != b"sk-real-secret"  # never store the plaintext bytes verbatim
    plaintext = byok.decrypt_key(ciphertext)

    assert plaintext == "sk-real-secret"
    assert fake.encrypt_calls[0]["name"] == KEY_NAME
    assert fake.encrypt_calls[0]["plaintext"] == b"sk-real-secret"
    assert fake.decrypt_calls[0]["name"] == KEY_NAME
    assert fake.decrypt_calls[0]["ciphertext"] == ciphertext


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: byok.encrypt_key("secret"), id="encrypt"),
        pytest.param(lambda: byok.decrypt_key(b"some-ciphertext"), id="decrypt"),
    ],
)
def test_kms_key_name_unset_raises_not_configured(monkeypatch, call):
    monkeypatch.delenv("KMS_KEY_NAME", raising=False)
    byok.set_kms_client(_FakeKmsClient())
    with pytest.raises(byok.ByokNotConfigured):
        call()


def test_decrypt_kms_exception_raises_byok_key_error(monkeypatch):
    monkeypatch.setenv("KMS_KEY_NAME", KEY_NAME)
    byok.set_kms_client(_FakeKmsClient(decrypt_error=RuntimeError("kms unavailable")))
    with pytest.raises(byok.ByokKeyError):
        byok.decrypt_key(b"some-ciphertext")


def test_resolve_gemini_key_returns_none_when_no_row(monkeypatch):
    monkeypatch.setenv("KMS_KEY_NAME", KEY_NAME)
    byok.set_kms_client(_FakeKmsClient())
    user_id = uuid4()
    session = _FakeCredentialSession(row=None)

    assert byok.resolve_gemini_key(session, user_id) is None
    assert session.get_calls[0][1] == (user_id, "gemini")


def test_resolve_gemini_key_decrypts_existing_row(monkeypatch):
    monkeypatch.setenv("KMS_KEY_NAME", KEY_NAME)
    byok.set_kms_client(_FakeKmsClient())
    ciphertext = byok.encrypt_key("sk-real-secret")
    session = _FakeCredentialSession(row=_FakeCredentialRow(encrypted_key=ciphertext))

    assert byok.resolve_gemini_key(session, uuid4()) == "sk-real-secret"


def test_resolve_gemini_key_propagates_decrypt_failure(monkeypatch):
    monkeypatch.setenv("KMS_KEY_NAME", KEY_NAME)
    byok.set_kms_client(_FakeKmsClient(decrypt_error=RuntimeError("kms unavailable")))
    session = _FakeCredentialSession(row=_FakeCredentialRow(encrypted_key=b"ciphertext"))

    with pytest.raises(byok.ByokKeyError):
        byok.resolve_gemini_key(session, uuid4())

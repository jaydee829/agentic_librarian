"""Unit tests for api/credentials.py's DB-free guards (monetization arc 3/3, BYOK):
shape validation, live-validation-seam failure mapping, and the byok_unavailable 503
when KMS_KEY_NAME is unset. Mirrors test_kofi_webhook.py's tiny-app-with-just-the-router
pattern, plus test_api_history.py's get_current_user dependency-override seam."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_librarian.api import credentials
from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.api.credentials import router
from agentic_librarian.core import byok

USER = AuthenticatedUser(id=uuid4(), email="reader@example.com")
KEY_NAME = "projects/p/locations/us-central1/keyRings/librarian/cryptoKeys/byok-credentials"


class _FakeKmsClient:
    def __init__(self):
        self.encrypt_calls: list[dict] = []

    def encrypt(self, request):
        self.encrypt_calls.append(request)
        return SimpleNamespace(ciphertext=b"ciphertext-bytes")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_seams():
    """db_manager and the KMS client are module/global caches mutated by tests below —
    reset both after every test so nothing leaks (kofi test's _reset_db_manager pattern,
    extended for byok's client seam)."""
    original_db = credentials.db_manager
    yield
    credentials.set_db_manager(original_db)
    byok.set_kms_client(None)


def _mock_db(get_return=None) -> MagicMock:
    mock_db = MagicMock()
    mock_session = MagicMock()
    mock_db.get_session.return_value.__enter__.return_value = mock_session
    mock_session.get.return_value = get_return
    return mock_db


@pytest.mark.parametrize(
    "api_key",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace-only"),
        pytest.param("x" * 201, id="oversize"),
    ],
)
def test_put_credentials_shape_guard_is_422(api_key):
    resp = _client().put("/me/credentials", json={"api_key": api_key})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_api_key"


def test_put_credentials_failed_live_validation_is_422(monkeypatch):
    monkeypatch.setattr(credentials, "_validate_key", lambda k: False)
    resp = _client().put("/me/credentials", json={"api_key": "AIzaLooksLikeAKeyButIsNot"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_api_key"


def test_put_credentials_kms_unset_is_503(monkeypatch):
    monkeypatch.setattr(credentials, "_validate_key", lambda k: True)
    monkeypatch.delenv("KMS_KEY_NAME", raising=False)
    resp = _client().put("/me/credentials", json={"api_key": "AIzaLooksLikeARealKey"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "byok_unavailable"


def test_put_credentials_success_stores_ciphertext_never_plaintext(monkeypatch):
    monkeypatch.setenv("KMS_KEY_NAME", KEY_NAME)
    monkeypatch.setattr(credentials, "_validate_key", lambda k: True)
    fake_kms = _FakeKmsClient()
    byok.set_kms_client(fake_kms)
    mock_db = _mock_db(get_return=None)  # no existing row -> insert path
    credentials.set_db_manager(mock_db)

    plaintext = "AIzaSomeRealSecretLookingKey"
    resp = _client().put("/me/credentials", json={"api_key": plaintext})

    assert resp.status_code == 200
    assert resp.json() == {"configured": True}
    added_row = mock_db.get_session.return_value.__enter__.return_value.add.call_args[0][0]
    assert added_row.vendor == "gemini"
    assert added_row.encrypted_key == b"ciphertext-bytes"
    assert added_row.kms_key_name == KEY_NAME
    # The plaintext key must never end up inside anything handed to session.add().
    assert plaintext.encode() not in added_row.encrypted_key


def test_get_credentials_unconfigured_is_false():
    credentials.set_db_manager(_mock_db(get_return=None))
    resp = _client().get("/me/credentials")
    assert resp.status_code == 200
    assert resp.json() == {"configured": False, "updated_at": None}


def test_delete_credentials_is_idempotent_when_no_row_exists():
    mock_db = _mock_db(get_return=None)
    credentials.set_db_manager(mock_db)
    resp = _client().delete("/me/credentials")
    assert resp.status_code == 200
    assert resp.json() == {"configured": False}
    mock_db.get_session.return_value.__enter__.return_value.delete.assert_not_called()

"""Unit tests for api/credentials.py's DB-free guards (monetization arc 3/3, BYOK):
shape validation, live-validation-seam failure mapping, the byok_unavailable 503 when
KMS_KEY_NAME is unset or KMS itself fails, the upsert's update branch, and the
concurrent-first-PUT insert race (IntegrityError -> re-query -> update, per
get_or_create.py's SAVEPOINT pattern). Mirrors test_kofi_webhook.py's
tiny-app-with-just-the-router pattern, plus test_api_history.py's get_current_user
dependency-override seam."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

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


class _RaisingKmsClient:
    """Simulates a KMS-side outage (not a config problem) at encrypt time."""

    def encrypt(self, request):
        raise RuntimeError("kms unavailable")


class _RacingSession:
    """First flush() (inside the nested first-insert attempt) raises IntegrityError,
    simulating a concurrent PUT that won the (user_id, vendor) primary-key race.
    Every later flush() succeeds. get() returns None until the race has "happened"
    (mirroring the real DB: nothing to find before the concurrent writer commits),
    then returns the row the concurrent writer inserted."""

    def __init__(self, existing_row):
        self._existing_row = existing_row
        self.flush_calls = 0
        self.get_calls: list[tuple] = []
        self.add_calls: list[object] = []

    def get(self, model, pk):
        self.get_calls.append((model, pk))
        return None if self.flush_calls == 0 else self._existing_row

    def add(self, obj):
        self.add_calls.append(obj)

    def begin_nested(self):
        return contextlib.nullcontext()

    def flush(self):
        self.flush_calls += 1
        if self.flush_calls == 1:
            raise IntegrityError("insert", {}, Exception("duplicate key"))


class _FakeDbManager:
    def __init__(self, session):
        self._session = session

    @contextlib.contextmanager
    def get_session(self):
        yield self._session


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


def test_put_credentials_kms_encrypt_failure_is_503(monkeypatch):
    """A KMS outage at encrypt time (not a config problem — KMS_KEY_NAME IS set) must
    map to the same byok_unavailable 503 shape, not a raw 500."""
    monkeypatch.setenv("KMS_KEY_NAME", KEY_NAME)
    monkeypatch.setattr(credentials, "_validate_key", lambda k: True)
    byok.set_kms_client(_RaisingKmsClient())

    resp = _client().put("/me/credentials", json={"api_key": "AIzaSomeRealLookingKey"})

    assert resp.status_code == 503
    assert resp.json()["detail"] == {
        "code": "byok_unavailable",
        "message": "Key storage is temporarily unavailable — try again.",
    }


def test_put_credentials_update_path_mutates_existing_row_not_add(monkeypatch):
    """A re-PUT (row already exists) must mutate the existing row in place, never call
    session.add() a second time."""
    monkeypatch.setenv("KMS_KEY_NAME", KEY_NAME)
    monkeypatch.setattr(credentials, "_validate_key", lambda k: True)
    byok.set_kms_client(_FakeKmsClient())

    existing_row = MagicMock()
    mock_db = _mock_db(get_return=existing_row)
    credentials.set_db_manager(mock_db)

    resp = _client().put("/me/credentials", json={"api_key": "AIzaUpdatedReplacementKey"})

    assert resp.status_code == 200
    assert resp.json() == {"configured": True}
    session = mock_db.get_session.return_value.__enter__.return_value
    session.add.assert_not_called()
    assert existing_row.encrypted_key == b"ciphertext-bytes"
    assert existing_row.kms_key_name == KEY_NAME


def test_put_credentials_concurrent_insert_race_falls_through_to_update(monkeypatch):
    """Two concurrent first-time PUTs: this request's insert loses the (user_id, vendor)
    primary-key race (IntegrityError on flush) -> re-query finds the winner's row ->
    fall through to the update path with THIS request's (newer) ciphertext, and still
    respond 200 rather than a spurious 500."""
    monkeypatch.setenv("KMS_KEY_NAME", KEY_NAME)
    monkeypatch.setattr(credentials, "_validate_key", lambda k: True)
    byok.set_kms_client(_FakeKmsClient())

    existing_row = MagicMock()
    fake_session = _RacingSession(existing_row)
    credentials.set_db_manager(_FakeDbManager(fake_session))

    resp = _client().put("/me/credentials", json={"api_key": "AIzaConcurrentWinnerKey"})

    assert resp.status_code == 200
    assert resp.json() == {"configured": True}
    assert existing_row.encrypted_key == b"ciphertext-bytes"
    assert existing_row.kms_key_name == KEY_NAME
    assert fake_session.flush_calls == 2  # the losing insert attempt, then the recovery update


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

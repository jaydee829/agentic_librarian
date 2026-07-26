"""BYOK credentials endpoint against a real Postgres (db_integration — executed locally,
Postgres up; CI-only otherwise, see test/conftest.py). Follows test_account_api.py's
client-fixture pattern; additionally patches api/credentials.py's live-validation seam
and core/byok.py's KMS client seam (monetization arc 3/3) so no real Gemini/KMS call
happens. Asserts the row is ciphertext-only (the plaintext key appears nowhere in the
stored bytes) and that tier flips free->byok->free across PUT/DELETE via /api/account —
tiers.effective_tier is the single source of truth this exercises end to end."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import auth
from agentic_librarian.api import credentials as credentials_mod
from agentic_librarian.api import main as api_main
from agentic_librarian.core import byok
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.models import UserCredential
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration

KEY_NAME = "projects/p/locations/us-central1/keyRings/librarian/cryptoKeys/byok-credentials"
PLAINTEXT_KEY = "AIzaSuperSecretGeminiKeyThatMustNeverBeStored"


class _FakeKmsClient:
    """Reversible stand-in ciphertext (never the real algorithm) — enough to prove the
    endpoint stores/round-trips whatever encrypt_key hands back, and never the
    plaintext, without a real KMS call."""

    def encrypt(self, request):
        return SimpleNamespace(ciphertext=request["plaintext"][::-1])

    def decrypt(self, request):
        return SimpleNamespace(plaintext=request["ciphertext"][::-1])


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    monkeypatch.setattr(credentials_mod, "db_manager", manager)
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        auth.get_current_user,
        lambda: auth.AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL),
    )
    monkeypatch.setattr(credentials_mod, "_validate_key", lambda k: True)
    monkeypatch.setenv("KMS_KEY_NAME", KEY_NAME)
    byok.set_kms_client(_FakeKmsClient())
    yield TestClient(api_main.app)
    byok.set_kms_client(None)


def test_get_credentials_unconfigured_before_any_put(client):
    resp = client.get("/api/me/credentials")
    assert resp.status_code == 200
    assert resp.json() == {"configured": False, "updated_at": None}


def test_put_then_get_flips_configured_true(client):
    put_resp = client.put("/api/me/credentials", json={"api_key": PLAINTEXT_KEY})
    assert put_resp.status_code == 200
    assert put_resp.json() == {"configured": True}

    get_resp = client.get("/api/me/credentials")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["configured"] is True
    assert body["updated_at"] is not None


def test_put_stores_ciphertext_only_never_plaintext(client, db_url):
    resp = client.put("/api/me/credentials", json={"api_key": PLAINTEXT_KEY})
    assert resp.status_code == 200

    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        row = session.get(UserCredential, (DEFAULT_USER_ID, "gemini"))
        assert row is not None
        assert row.encrypted_key == PLAINTEXT_KEY.encode("utf-8")[::-1]  # the fake KMS "ciphertext"
        assert row.encrypted_key != PLAINTEXT_KEY.encode("utf-8")
        assert PLAINTEXT_KEY.encode("utf-8") not in row.encrypted_key
        assert row.kms_key_name == KEY_NAME


def test_delete_removes_the_row(client, db_url):
    client.put("/api/me/credentials", json={"api_key": PLAINTEXT_KEY})

    del_resp = client.delete("/api/me/credentials")
    assert del_resp.status_code == 200
    assert del_resp.json() == {"configured": False}

    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        assert session.get(UserCredential, (DEFAULT_USER_ID, "gemini")) is None

    get_resp = client.get("/api/me/credentials")
    assert get_resp.json() == {"configured": False, "updated_at": None}


def test_delete_is_idempotent_when_no_row_exists(client):
    resp = client.delete("/api/me/credentials")
    assert resp.status_code == 200
    assert resp.json() == {"configured": False}


def test_tier_flips_free_byok_free_across_put_and_delete(client):
    assert client.get("/api/account").json()["tier"] == "free"

    put_resp = client.put("/api/me/credentials", json={"api_key": PLAINTEXT_KEY})
    assert put_resp.status_code == 200
    assert client.get("/api/account").json()["tier"] == "byok"

    del_resp = client.delete("/api/me/credentials")
    assert del_resp.status_code == 200
    assert client.get("/api/account").json()["tier"] == "free"

"""Unit tests for the Ko-fi webhook's DB-free guards (monetization arc 2/3): malformed
payload, verification-token fail-closed posture, and the 500-on-DB-error path Ko-fi's
retry logic relies on for idempotent recovery. Mirrors test_firebase_auth_proxy.py's
tiny-app-with-just-the-router pattern — no real DB, no real app lifespan."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_librarian.api import kofi
from agentic_librarian.api.kofi import router


@pytest.fixture(autouse=True)
def _reset_db_manager():
    """Reset the module-global db_manager after each test so a stub never leaks."""
    original = kofi.db_manager
    yield
    kofi.set_db_manager(original)


class _RaisingSessionManager:
    """Stands in for DatabaseManager: get_session() raises before yielding, simulating
    a DB outage — the webhook must surface it as 500 so Ko-fi retries."""

    def get_session(self):
        raise RuntimeError("db unavailable")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


VALID_TOKEN = "the-shared-secret"


@pytest.mark.parametrize(
    "form,expected_status",
    [
        pytest.param({}, 422, id="no-data-field-is-422-fastapi-validation"),
        pytest.param({"data": "not json"}, 400, id="data-not-json-is-400"),
        pytest.param({"data": "[1, 2, 3]"}, 400, id="data-not-an-object-is-400"),
    ],
)
def test_malformed_payload_guards(monkeypatch, form, expected_status):
    monkeypatch.setenv("KOFI_VERIFICATION_TOKEN", VALID_TOKEN)
    resp = _client().post("/webhooks/kofi", data=form)
    assert resp.status_code == expected_status


def test_verification_token_unset_fails_closed_even_with_token_in_payload(monkeypatch):
    monkeypatch.delenv("KOFI_VERIFICATION_TOKEN", raising=False)
    payload = '{"verification_token": "anything", "kofi_transaction_id": "t1"}'
    resp = _client().post("/webhooks/kofi", data={"data": payload})
    assert resp.status_code == 403


def test_verification_token_mismatch_is_403(monkeypatch):
    monkeypatch.setenv("KOFI_VERIFICATION_TOKEN", VALID_TOKEN)
    payload = '{"verification_token": "wrong-token", "kofi_transaction_id": "t1"}'
    resp = _client().post("/webhooks/kofi", data={"data": payload})
    assert resp.status_code == 403


def test_db_layer_error_after_valid_token_is_500(monkeypatch):
    """Token OK but the DB layer raises -> 500, not a swallowed/misreported error. Ko-fi
    retries non-2xx and kofi_transaction_id idempotency makes the retry safe."""
    monkeypatch.setenv("KOFI_VERIFICATION_TOKEN", VALID_TOKEN)
    kofi.set_db_manager(_RaisingSessionManager())
    payload = f'{{"verification_token": "{VALID_TOKEN}", "kofi_transaction_id": "t1"}}'
    resp = _client().post("/webhooks/kofi", data={"data": payload})
    assert resp.status_code == 500

"""Unit tests for the Ko-fi webhook's DB-free guards (monetization arc 2/3): malformed
payload, verification-token fail-closed posture, and the 500-on-DB-error path Ko-fi's
retry logic relies on for idempotent recovery. Mirrors test_firebase_auth_proxy.py's
tiny-app-with-just-the-router pattern — no real DB, no real app lifespan."""

from __future__ import annotations

import json
from contextlib import contextmanager

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


class _FakeQuery:
    """Minimal chainable stand-in for a SQLAlchemy Query: every filter() no-ops, first()
    always reports 'nothing found' (no duplicate row, no matched user)."""

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return None


class _FakeSession:
    def query(self, *args, **kwargs):
        return _FakeQuery()

    def add(self, obj):
        pass

    def flush(self):
        pass


class _WorkingSessionManager:
    """Stands in for DatabaseManager: get_session() yields a working fake session (the
    _RaisingSessionManager's pattern inverted) so a handler run can reach a 2xx without a
    real DB — used for the non-finite-amount guard, which must survive all the way to the
    DB-write branch rather than crash in entitlements.classify() first."""

    @contextmanager
    def get_session(self):
        yield _FakeSession()


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


@pytest.mark.parametrize(
    "amount_str",
    [
        pytest.param("nan", id="amount-nan"),
        pytest.param("-nan", id="amount-negative-nan"),
        pytest.param("Infinity", id="amount-infinity"),
    ],
)
def test_non_finite_amount_does_not_crash_classify(monkeypatch, amount_str):
    """Decimal("nan")/Decimal("-nan")/Decimal("Infinity") all parse WITHOUT raising
    InvalidOperation, so the handler's own try/except around Decimal(...) never catches
    them. Without an explicit is_finite() guard, entitlements.classify()'s
    `amount >= threshold` ordering comparison raises decimal.InvalidOperation on a
    non-finite operand -> an uncaught 500 BEFORE the event is durably stored, and since
    Ko-fi retries non-2xx responses with the SAME payload, that crash would repeat
    forever. Uses a working fake session (not the 500 test's raising one) so the request
    reaches the DB-write branch and actually returns 2xx."""
    monkeypatch.setenv("KOFI_VERIFICATION_TOKEN", VALID_TOKEN)
    kofi.set_db_manager(_WorkingSessionManager())
    payload = json.dumps(
        {
            "verification_token": VALID_TOKEN,
            "kofi_transaction_id": f"txn-{amount_str}",
            "amount": amount_str,
            # No email -> no User lookup needed from the fake session; the payment still
            # goes through the add()/flush() write path and lands on "unmatched".
        }
    )
    resp = _client().post("/webhooks/kofi", data={"data": payload})
    assert resp.status_code == 200
    # Non-finite amount is clamped to 0 -> classifies as a tip (amount 0 < the annual
    # threshold, no tier_name, not a subscription payment).
    assert resp.json() == {"status": "unmatched", "kind": "tip"}


@pytest.mark.parametrize(
    "raw_tier,expected_kind",
    [
        pytest.param(123, "tip", id="tier-name-int"),
        pytest.param(["annual"], "tip", id="tier-name-list"),
        pytest.param("Annual", "annual", id="tier-name-string-still-classifies"),
    ],
)
def test_non_string_tier_name_does_not_crash_classify(monkeypatch, raw_tier, expected_kind):
    """Same failure class as the non-finite amount: an attacker-shaped non-string
    tier_name would crash classify()'s .strip().casefold() before the event is
    persisted -> deterministic 500 retried forever. The handler str-coerces it like
    the sibling fields (a coerced "123" simply matches no annual tier name)."""
    monkeypatch.setenv("KOFI_VERIFICATION_TOKEN", VALID_TOKEN)
    kofi.set_db_manager(_WorkingSessionManager())
    payload = json.dumps(
        {
            "verification_token": VALID_TOKEN,
            "kofi_transaction_id": f"txn-tier-{expected_kind}-{type(raw_tier).__name__}",
            "amount": "5.00",
            "tier_name": raw_tier,
        }
    )
    resp = _client().post("/webhooks/kofi", data={"data": payload})
    assert resp.status_code == 200
    assert resp.json() == {"status": "unmatched", "kind": expected_kind}

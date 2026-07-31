"""Unit tests for the BMC (Buy Me a Coffee) webhook (monetization arc 2/3, BMC
revector): HMAC-SHA256 signature verification over the RAW body, malformed-payload
guards, and the lifecycle matrix (grant/cap/tip/ignore) driven through a fake
DB session so no real Postgres is needed. Mirrors test_firebase_auth_proxy.py's
tiny-app-with-just-the-router pattern. db_integration coverage of the real
never-shrink/never-extend math against Postgres lives in
test/integration/test_bmc_webhook_db.py."""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_librarian.api import bmc
from agentic_librarian.api.bmc import router
from agentic_librarian.db.models import User

SECRET = "the-shared-secret"


def _signed(body: bytes, secret: str) -> dict[str, str]:
    return {"x-signature-sha256": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


@pytest.fixture(autouse=True)
def _reset_db_manager():
    """Reset the module-global db_manager after each test so a stub never leaks."""
    original = bmc.db_manager
    yield
    bmc.set_db_manager(original)


@dataclass
class _FakeUser:
    """Stands in for a matched agentic_librarian.db.models.User row: a real, mutable
    object so apply_grant()/apply_cap() writes are directly observable after the
    handler call, without a real session/commit."""

    id: object
    email: str
    subscriber_until: datetime | None = None


class _FakeQuery:
    """Minimal chainable stand-in for a SQLAlchemy Query: filter() no-ops, first()
    returns a preset result."""

    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _FakeSession:
    """Minimal stand-in for a SQLAlchemy Session driving the handler's two lookups
    (Payment duplicate-by-event-id, User-by-email) plus add()/flush(). query()
    dispatches on the `entity` argument: `User` -> the preloaded fake user (or None);
    anything else (Payment.id in the real handler) -> the duplicate-flag result."""

    def __init__(self, *, user: _FakeUser | None = None, duplicate: bool = False):
        self.user = user
        self.duplicate = duplicate
        self.added: list = []

    def query(self, entity, *args):
        if entity is User:
            return _FakeQuery(self.user)
        return _FakeQuery(object() if self.duplicate else None)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


class _WorkingSessionManager:
    def __init__(self, session: _FakeSession):
        self._session = session

    @contextmanager
    def get_session(self):
        yield self._session


class _RaisingSessionManager:
    """Stands in for DatabaseManager: get_session() raises before yielding, simulating
    a DB outage — the webhook must surface it as 500 so BMC retries."""

    def get_session(self):
        raise RuntimeError("db unavailable")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _post(body: dict, *, secret: str = SECRET) -> object:
    raw = json.dumps(body).encode()
    return _client().post("/webhooks/bmc", content=raw, headers=_signed(raw, secret))


GOOD_BODY = json.dumps({"event_id": "evt-1", "type": "donation.created", "live_mode": True, "data": {}}).encode()
OTHER_BODY = json.dumps({"event_id": "evt-2", "type": "donation.created", "live_mode": True, "data": {}}).encode()


# ---------------------------------------------------------------------------
# Signature verification (fail-closed) — never reaches the DB.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_secret,headers",
    [
        pytest.param(None, _signed(GOOD_BODY, SECRET), id="env-unset-even-with-valid-looking-signature"),
        pytest.param(SECRET, {}, id="missing-signature-header"),
        pytest.param(SECRET, {"x-signature-sha256": "0" * 64}, id="wrong-signature"),
        pytest.param(SECRET, _signed(OTHER_BODY, SECRET), id="signature-of-different-body-is-tamper"),
    ],
)
def test_signature_guard_rejects_with_403(monkeypatch, env_secret, headers):
    if env_secret is None:
        monkeypatch.delenv("BMC_WEBHOOK_SECRET", raising=False)
    else:
        monkeypatch.setenv("BMC_WEBHOOK_SECRET", env_secret)
    resp = _client().post("/webhooks/bmc", content=GOOD_BODY, headers=headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Malformed payload guards (valid signature, bad body) — never reaches the DB.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"not json", id="non-json-body"),
        pytest.param(b"[1, 2, 3]", id="json-non-object"),
        pytest.param(json.dumps({"type": "donation.created"}).encode(), id="missing-event-id"),
        pytest.param(json.dumps({"event_id": "evt-1"}).encode(), id="missing-type"),
    ],
)
def test_malformed_payload_guards_are_400(monkeypatch, body):
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    resp = _client().post("/webhooks/bmc", content=body, headers=_signed(body, SECRET))
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Lifecycle matrix — DB-backed via the fake session.
# ---------------------------------------------------------------------------


def test_membership_started_month_matched_email_is_applied_monthly(monkeypatch):
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    user = _FakeUser(id="u-monthly", email="subscriber@example.com", subscriber_until=None)
    bmc.set_db_manager(_WorkingSessionManager(_FakeSession(user=user)))
    resp = _post(
        {
            "event_id": "evt-monthly",
            "type": "membership.started",
            "live_mode": True,
            "data": {
                "supporter_email": "Subscriber@Example.com",
                "amount": "3.00",
                "currency": "USD",
                "duration_type": "month",
                "current_period_end": int((datetime.now(UTC) + timedelta(days=20)).timestamp()),
                # No "status" key: missing status must count as active.
            },
        }
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "applied", "kind": "monthly"}


def test_membership_started_year_matched_email_is_applied_annual(monkeypatch):
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    user = _FakeUser(id="u-annual", email="annual@example.com", subscriber_until=None)
    bmc.set_db_manager(_WorkingSessionManager(_FakeSession(user=user)))
    resp = _post(
        {
            "event_id": "evt-annual",
            "type": "membership.started",
            "live_mode": True,
            "data": {
                "supporter_email": "annual@example.com",
                "duration_type": "year",
                "current_period_end": int((datetime.now(UTC) + timedelta(days=370)).timestamp()),
                "status": "active",
            },
        }
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "applied", "kind": "annual"}


def test_same_event_id_replayed_is_duplicate(monkeypatch):
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    bmc.set_db_manager(_WorkingSessionManager(_FakeSession(duplicate=True)))
    resp = _post(
        {
            "event_id": "evt-replay",
            "type": "donation.created",
            "live_mode": True,
            "data": {"supporter_email": "someone@example.com"},
        }
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "duplicate"}


def test_membership_updated_lower_period_end_never_shrinks_standing(monkeypatch):
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    far_future = datetime.now(UTC) + timedelta(days=400)
    user = _FakeUser(id="u-stacked", email="stacked@example.com", subscriber_until=far_future)
    bmc.set_db_manager(_WorkingSessionManager(_FakeSession(user=user)))
    resp = _post(
        {
            "event_id": "evt-update-lower",
            "type": "membership.updated",
            "live_mode": True,
            "data": {
                "supporter_email": "stacked@example.com",
                "duration_type": "month",
                "current_period_end": int((datetime.now(UTC) + timedelta(days=5)).timestamp()),
                "status": "active",
            },
        }
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "applied", "kind": "monthly"}
    assert user.subscriber_until == far_future  # never-shrink


def test_membership_started_canceled_status_is_recorded_not_granted(monkeypatch):
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    user = _FakeUser(id="u-canceled-status", email="canceled-status@example.com", subscriber_until=None)
    bmc.set_db_manager(_WorkingSessionManager(_FakeSession(user=user)))
    resp = _post(
        {
            "event_id": "evt-canceled-status",
            "type": "membership.started",
            "live_mode": True,
            "data": {
                "supporter_email": "canceled-status@example.com",
                "duration_type": "month",
                "current_period_end": int((datetime.now(UTC) + timedelta(days=20)).timestamp()),
                "status": "canceled",
            },
        }
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "recorded", "kind": "monthly"}
    assert user.subscriber_until is None


def test_membership_cancelled_cancel_at_period_end_true_is_capped(monkeypatch):
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    user = _FakeUser(
        id="u-cancel", email="cancel@example.com", subscriber_until=datetime.now(UTC) + timedelta(days=100)
    )
    bmc.set_db_manager(_WorkingSessionManager(_FakeSession(user=user)))
    resp = _post(
        {
            "event_id": "evt-cancel",
            "type": "membership.cancelled",
            "live_mode": True,
            "data": {
                "supporter_email": "cancel@example.com",
                "cancel_at_period_end": "true",
                "current_period_end": int((datetime.now(UTC) + timedelta(days=10)).timestamp()),
            },
        }
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "capped", "kind": "ignore"}


def test_membership_paused_is_capped(monkeypatch):
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    user = _FakeUser(
        id="u-paused", email="paused@example.com", subscriber_until=datetime.now(UTC) + timedelta(days=100)
    )
    bmc.set_db_manager(_WorkingSessionManager(_FakeSession(user=user)))
    resp = _post(
        {
            "event_id": "evt-paused",
            "type": "membership.paused",
            "live_mode": True,
            "data": {
                "supporter_email": "paused@example.com",
                "paused_at": int(datetime.now(UTC).timestamp()),
            },
        }
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "capped", "kind": "ignore"}


def test_donation_created_matched_email_is_recorded(monkeypatch):
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    user = _FakeUser(id="u-tipper", email="tipper@example.com", subscriber_until=None)
    bmc.set_db_manager(_WorkingSessionManager(_FakeSession(user=user)))
    resp = _post(
        {
            "event_id": "evt-tip",
            "type": "donation.created",
            "live_mode": True,
            "data": {"supporter_email": "tipper@example.com", "amount": "5.00"},
        }
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "recorded", "kind": "tip"}


def test_donation_created_unknown_email_is_unmatched(monkeypatch):
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    bmc.set_db_manager(_WorkingSessionManager(_FakeSession(user=None)))
    resp = _post(
        {
            "event_id": "evt-tip-unknown",
            "type": "donation.created",
            "live_mode": True,
            "data": {"supporter_email": "nobody@example.com", "amount": "5.00"},
        }
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "unmatched", "kind": "tip"}


def test_donation_refunded_matched_email_is_recorded(monkeypatch):
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    user = _FakeUser(id="u-refund", email="refund@example.com", subscriber_until=None)
    bmc.set_db_manager(_WorkingSessionManager(_FakeSession(user=user)))
    resp = _post(
        {
            "event_id": "evt-refund",
            "type": "donation.refunded",
            "live_mode": True,
            "data": {"supporter_email": "refund@example.com"},
        }
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "recorded", "kind": "ignore"}


def test_shop_order_created_no_email_is_ignored(monkeypatch):
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    bmc.set_db_manager(_WorkingSessionManager(_FakeSession(user=None)))
    resp = _post(
        {
            "event_id": "evt-shop",
            "type": "shop_order.created",
            "live_mode": True,
            "data": {},
        }
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored", "kind": "ignore"}


@pytest.mark.parametrize(
    "event_id,amount_field",
    [
        pytest.param("evt-amount-nan", {"amount": "nan"}, id="amount-nan"),
        pytest.param("evt-amount-missing", {}, id="amount-missing"),
    ],
)
def test_non_finite_or_missing_amount_processes_as_zero_no_500(monkeypatch, event_id, amount_field):
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    session = _FakeSession(user=None)
    bmc.set_db_manager(_WorkingSessionManager(session))
    resp = _post(
        {
            "event_id": event_id,
            "type": "donation.created",
            "live_mode": True,
            "data": {**amount_field},
        }
    )
    assert resp.status_code == 200
    assert len(session.added) == 1
    assert session.added[0].amount == Decimal("0")


def test_live_mode_false_is_still_processed_status_unchanged(monkeypatch):
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    bmc.set_db_manager(_WorkingSessionManager(_FakeSession(user=None)))
    resp = _post(
        {
            "event_id": "evt-test-mode",
            "type": "shop_order.created",
            "live_mode": False,
            "data": {},
        }
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored", "kind": "ignore"}


def test_db_layer_error_after_valid_signature_is_500(monkeypatch):
    """Signature OK but the DB layer raises -> 500, not a swallowed/misreported error.
    BMC retries non-2xx and provider_event_id idempotency makes the retry safe."""
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    bmc.set_db_manager(_RaisingSessionManager())
    resp = _post(
        {
            "event_id": "evt-db-down",
            "type": "donation.created",
            "live_mode": True,
            "data": {"supporter_email": "someone@example.com"},
        }
    )
    assert resp.status_code == 500

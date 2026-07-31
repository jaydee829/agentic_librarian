"""End-to-end BMC (Buy Me a Coffee) webhook matrix against a real Postgres
(db_integration — CI-only, see test/conftest.py; run locally with
POSTGRES_HOST=localhost and the compose db up). Follows
test_internal_complete_edition_api.py's pattern: full FastAPI app via TestClient (no
lifespan, since it's used without a `with` block), module's db_manager monkeypatched
to the isolated test DB.

Exact subscriber_until timestamps aren't assertable through HTTP (no way to freeze the
webhook's internal `now`), so the matrix asserts a slack range around each grant instead
(never-shrink/never-extend cases assert exact equality against the seeded value, since
those must not move at all). Rows are seeded with EXPLICIT, distinct created_at values
(the #147 same-timestamp flake lesson)."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import bmc as bmc_mod
from agentic_librarian.api import main as api_main
from agentic_librarian.cli import main as cli_main
from agentic_librarian.db.models import Payment, User
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration

SECRET = "test-bmc-webhook-secret"
SLACK = timedelta(minutes=5)


@pytest.fixture
def client(db_url, monkeypatch):
    monkeypatch.setattr(bmc_mod, "db_manager", DatabaseManager(db_url))
    monkeypatch.setenv("BMC_WEBHOOK_SECRET", SECRET)
    yield TestClient(api_main.app)


def _seed_user(db, email: str, subscriber_until: datetime | None, created_at: datetime):
    """Returns the new user's id only — never a live ORM object across a closed
    session boundary (the #11 CLAUDE.md pitfall: expire-on-commit + session.close()
    turns any later attribute access into DetachedInstanceError, CI-only since local
    runs never reach a real Postgres to exercise this path)."""
    with db.get_session() as session:
        user = User(email=email, subscriber_until=subscriber_until, created_at=created_at)
        session.add(user)
        session.flush()
        return user.id


def _post(client, **overrides):
    data_overrides = overrides.pop("data", {})
    envelope = {
        "event_id": "evt-1",
        "type": "membership.started",
        "live_mode": True,
        "created": int(datetime.now(UTC).timestamp()),
        "attempt": 1,
        "data": {
            "supporter_email": "Subscriber@Example.com",
            "amount": "3.00",
            "currency": "USD",
            "duration_type": "month",
            "id": "sub-1",
            **data_overrides,
        },
        **overrides,
    }
    body = json.dumps(envelope).encode()
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhooks/bmc", content=body, headers={"x-signature-sha256": sig})


def test_membership_started_applies_period_end_plus_grace(client, db_url):
    db = DatabaseManager(db_url)
    now = datetime.now(UTC)
    user_id = _seed_user(db, "subscriber@example.com", None, now - timedelta(days=1))
    period_end = now + timedelta(days=20)

    resp = _post(
        client,
        event_id="started-txn",
        data={
            "supporter_email": "Subscriber@Example.com",
            "current_period_end": int(period_end.timestamp()),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "applied", "kind": "monthly"}

    with db.get_session() as session:
        refreshed = session.get(User, user_id)
        payment = session.query(Payment).filter(Payment.provider_event_id == "started-txn").first()
        expected = period_end + timedelta(days=5)
        assert expected - SLACK <= refreshed.subscriber_until <= expected + SLACK
        assert payment is not None
        assert payment.provider == "bmc"
        assert payment.email == "subscriber@example.com"  # lowercased at ingest
        assert payment.matched_user_id == user_id
        assert expected - SLACK <= payment.granted_until <= expected + SLACK


def test_membership_updated_with_later_period_end_advances(client, db_url):
    db = DatabaseManager(db_url)
    now = datetime.now(UTC)
    prior_until = now + timedelta(days=10)
    user_id = _seed_user(db, "advance@example.com", prior_until, now - timedelta(days=1))
    new_period_end = now + timedelta(days=60)

    resp = _post(
        client,
        event_id="advance-txn",
        type="membership.updated",
        data={
            "supporter_email": "advance@example.com",
            "current_period_end": int(new_period_end.timestamp()),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "applied", "kind": "monthly"}

    with db.get_session() as session:
        refreshed = session.get(User, user_id)
        payment = session.query(Payment).filter(Payment.provider_event_id == "advance-txn").first()
        expected = new_period_end + timedelta(days=5)
        assert expected - SLACK <= refreshed.subscriber_until <= expected + SLACK
        assert payment is not None
        assert expected - SLACK <= payment.granted_until <= expected + SLACK


def test_membership_updated_with_earlier_period_end_unchanged_never_shrinks(client, db_url):
    db = DatabaseManager(db_url)
    now = datetime.now(UTC)
    prior_until = now + timedelta(days=400)
    user_id = _seed_user(db, "noshrink@example.com", prior_until, now - timedelta(days=1))
    earlier_period_end = now + timedelta(days=10)

    resp = _post(
        client,
        event_id="noshrink-txn",
        type="membership.updated",
        data={
            "supporter_email": "noshrink@example.com",
            "current_period_end": int(earlier_period_end.timestamp()),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "applied", "kind": "monthly"}

    with db.get_session() as session:
        refreshed = session.get(User, user_id)
        assert refreshed.subscriber_until == prior_until


def test_membership_cancelled_cancel_at_period_end_is_capped(client, db_url):
    db = DatabaseManager(db_url)
    now = datetime.now(UTC)
    prior_until = now + timedelta(days=400)
    user_id = _seed_user(db, "cancel-at-end@example.com", prior_until, now - timedelta(days=1))
    period_end = now + timedelta(days=15)

    resp = _post(
        client,
        event_id="cancel-at-end-txn",
        type="membership.cancelled",
        data={
            "supporter_email": "cancel-at-end@example.com",
            "cancel_at_period_end": "true",
            "current_period_end": int(period_end.timestamp()),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "capped", "kind": "ignore"}

    with db.get_session() as session:
        refreshed = session.get(User, user_id)
        payment = session.query(Payment).filter(Payment.provider_event_id == "cancel-at-end-txn").first()
        expected = period_end + timedelta(days=5)
        assert expected - SLACK <= refreshed.subscriber_until <= expected + SLACK
        assert payment is not None
        assert expected - SLACK <= payment.granted_until <= expected + SLACK


def test_membership_cancelled_immediate_via_canceled_at_is_capped_there(client, db_url):
    db = DatabaseManager(db_url)
    now = datetime.now(UTC)
    prior_until = now + timedelta(days=400)
    user_id = _seed_user(db, "cancel-now@example.com", prior_until, now - timedelta(days=1))
    canceled_at = now

    resp = _post(
        client,
        event_id="cancel-now-txn",
        type="membership.cancelled",
        data={
            "supporter_email": "cancel-now@example.com",
            "canceled_at": int(canceled_at.timestamp()),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "capped", "kind": "ignore"}

    with db.get_session() as session:
        refreshed = session.get(User, user_id)
        payment = session.query(Payment).filter(Payment.provider_event_id == "cancel-now-txn").first()
        expected = canceled_at + timedelta(days=5)
        assert expected - SLACK <= refreshed.subscriber_until <= expected + SLACK
        assert payment is not None
        assert expected - SLACK <= payment.granted_until <= expected + SLACK


def test_duplicate_event_id_is_noop(client, db_url):
    db = DatabaseManager(db_url)
    now = datetime.now(UTC)
    user_id = _seed_user(db, "replay@example.com", None, now - timedelta(days=1))

    first = _post(client, event_id="replay-txn", data={"supporter_email": "replay@example.com"})
    assert first.status_code == 200
    assert first.json()["status"] == "applied"

    with db.get_session() as session:
        after_first = session.get(User, user_id).subscriber_until

    second = _post(client, event_id="replay-txn", data={"supporter_email": "replay@example.com"})
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}

    with db.get_session() as session:
        after_second = session.get(User, user_id).subscriber_until
        rows = session.query(Payment).filter(Payment.provider_event_id == "replay-txn").all()

    assert after_second == after_first
    assert len(rows) == 1


def test_unmatched_then_payments_match_applies_payload_horizon(client, db_url, monkeypatch, capsys):
    """The webhook stores an unmatched payment (no user exists yet for the payer
    email); the operator later invites the user and runs `payments match`, which
    recomputes classify()/horizon()/apply_grant() from the STORED row's payload —
    the exact CLI path in cli.py's _run_payments_match, matching test_cli_invite.py's
    idioms (monkeypatch _invite_db_manager to the isolated test DB)."""
    db = DatabaseManager(db_url)
    monkeypatch.setattr("agentic_librarian.cli._invite_db_manager", lambda: db)
    now = datetime.now(UTC)
    period_end = now + timedelta(days=45)

    resp = _post(
        client,
        event_id="unmatched-txn",
        data={
            "supporter_email": "latecomer@example.com",
            "current_period_end": int(period_end.timestamp()),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "unmatched", "kind": "monthly"}

    with db.get_session() as session:
        payment = session.query(Payment).filter(Payment.provider_event_id == "unmatched-txn").first()
        assert payment is not None
        assert payment.matched_user_id is None
        assert payment.granted_until is None

    user_id = _seed_user(db, "latecomer@example.com", None, now - timedelta(days=1))
    assert cli_main(["payments", "match", "unmatched-txn", "latecomer@example.com"]) == 0

    with db.get_session() as session:
        refreshed = session.get(User, user_id)
        payment = session.query(Payment).filter(Payment.provider_event_id == "unmatched-txn").first()
        expected = period_end + timedelta(days=5)
        assert expected - SLACK <= refreshed.subscriber_until <= expected + SLACK
        assert payment.matched_user_id == user_id
        assert expected - SLACK <= payment.granted_until <= expected + SLACK


def test_donation_created_matched_is_recorded_granted_until_null(client, db_url):
    db = DatabaseManager(db_url)
    now = datetime.now(UTC)
    user_id = _seed_user(db, "tipper@example.com", None, now - timedelta(days=1))

    resp = _post(
        client,
        event_id="tip-txn",
        type="donation.created",
        data={"supporter_email": "tipper@example.com", "amount": "5.00"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "recorded", "kind": "tip"}

    with db.get_session() as session:
        refreshed = session.get(User, user_id)
        payment = session.query(Payment).filter(Payment.provider_event_id == "tip-txn").first()
        assert refreshed.subscriber_until is None
        assert payment.granted_until is None
        assert payment.amount == Decimal("5.00")
        assert payment.matched_user_id == user_id


def test_donation_refunded_matched_is_recorded(client, db_url):
    db = DatabaseManager(db_url)
    now = datetime.now(UTC)
    user_id = _seed_user(db, "refund@example.com", None, now - timedelta(days=1))

    resp = _post(
        client,
        event_id="refund-txn",
        type="donation.refunded",
        data={"supporter_email": "refund@example.com", "amount": "5.00"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "recorded", "kind": "ignore"}

    with db.get_session() as session:
        refreshed = session.get(User, user_id)
        payment = session.query(Payment).filter(Payment.provider_event_id == "refund-txn").first()
        assert refreshed.subscriber_until is None
        assert payment.granted_until is None
        assert payment.matched_user_id == user_id

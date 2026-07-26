"""End-to-end Ko-fi webhook matrix against a real Postgres (db_integration — CI-only,
see test/conftest.py). Follows test_internal_complete_edition_api.py's pattern: full
FastAPI app via TestClient (no lifespan, since it's used without a `with` block),
module's db_manager monkeypatched to the isolated test DB.

Exact subscriber_until timestamps aren't assertable through HTTP (no way to freeze the
webhook's internal `now`), so the matrix asserts a slack range around each grant instead.
Rows are seeded with EXPLICIT, distinct created_at values (the #147 same-timestamp
flake lesson)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import kofi as kofi_mod
from agentic_librarian.api import main as api_main
from agentic_librarian.db.models import Payment, User
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration

VALID_TOKEN = "test-kofi-verification-token"
SLACK = timedelta(minutes=5)


@pytest.fixture
def client(db_url, monkeypatch):
    monkeypatch.setattr(kofi_mod, "db_manager", DatabaseManager(db_url))
    monkeypatch.setenv("KOFI_VERIFICATION_TOKEN", VALID_TOKEN)
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


def _post(client, **event_overrides):
    event = {
        "verification_token": VALID_TOKEN,
        "kofi_transaction_id": "txn-1",
        "email": "Subscriber@Example.com",
        "amount": "5.00",
        "currency": "USD",
        "type": "Subscription",
        "is_subscription_payment": True,
        "tier_name": None,
        **event_overrides,
    }
    return client.post("/webhooks/kofi", data={"data": json.dumps(event)})


@pytest.mark.parametrize(
    "prior_offset_days,expected_days",
    [
        pytest.param(-10, 33, id="lapsed-subscriber-restarts-from-now"),
        pytest.param(20, 33, id="active-subscriber-stacks-from-existing-expiry"),
    ],
)
def test_membership_payment_matched_email_extends_subscriber_until(client, db_url, prior_offset_days, expected_days):
    db = DatabaseManager(db_url)
    now = datetime.now(UTC)
    prior = now + timedelta(days=prior_offset_days)
    user_id = _seed_user(db, "subscriber@example.com", prior, now - timedelta(days=1))

    before_call = datetime.now(UTC)
    resp = _post(client, kofi_transaction_id="membership-txn", email="Subscriber@Example.com")
    assert resp.status_code == 200
    assert resp.json() == {"status": "applied", "kind": "monthly"}

    with db.get_session() as session:
        refreshed = session.get(User, user_id)
        payment = session.query(Payment).filter(Payment.kofi_transaction_id == "membership-txn").first()
        base = prior if prior > before_call else before_call
        expected_min = base + timedelta(days=expected_days) - SLACK
        expected_max = base + timedelta(days=expected_days) + SLACK
        assert expected_min <= refreshed.subscriber_until <= expected_max
        assert payment is not None
        assert payment.email == "subscriber@example.com"  # lowercased at ingest
        assert payment.matched_user_id == user_id
        assert payment.entitlement_days == 33
        assert payment.is_subscription_payment is True


def test_annual_tier_name_grants_370_days(client, db_url):
    db = DatabaseManager(db_url)
    now = datetime.now(UTC)
    user_id = _seed_user(db, "annual@example.com", None, now - timedelta(days=1))

    before_call = datetime.now(UTC)
    resp = _post(
        client,
        kofi_transaction_id="annual-txn",
        email="annual@example.com",
        tier_name="Annual",
        is_subscription_payment=True,
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "applied", "kind": "annual"}

    with db.get_session() as session:
        refreshed = session.get(User, user_id)
        payment = session.query(Payment).filter(Payment.kofi_transaction_id == "annual-txn").first()
        expected_min = before_call + timedelta(days=370) - SLACK
        expected_max = before_call + timedelta(days=370) + SLACK
        assert expected_min <= refreshed.subscriber_until <= expected_max
        assert payment.entitlement_days == 370


def test_tip_below_threshold_is_recorded_without_entitlement(client, db_url):
    db = DatabaseManager(db_url)
    now = datetime.now(UTC)
    user_id = _seed_user(db, "tipper@example.com", None, now - timedelta(days=1))

    resp = _post(
        client,
        kofi_transaction_id="tip-txn",
        email="tipper@example.com",
        amount="3.00",
        type="Donation",
        is_subscription_payment=False,
        tier_name=None,
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "recorded", "kind": "tip"}

    with db.get_session() as session:
        refreshed = session.get(User, user_id)
        payment = session.query(Payment).filter(Payment.kofi_transaction_id == "tip-txn").first()
        assert refreshed.subscriber_until is None
        assert payment.entitlement_days == 0
        assert payment.amount == Decimal("3.00")


def test_unknown_email_is_unmatched(client):
    resp = _post(
        client,
        kofi_transaction_id="unmatched-txn",
        email="nobody-here@example.com",
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "unmatched", "kind": "monthly"}


def test_same_txn_replay_is_duplicate_no_second_row_no_double_extend(client, db_url):
    db = DatabaseManager(db_url)
    now = datetime.now(UTC)
    user_id = _seed_user(db, "replay@example.com", None, now - timedelta(days=1))

    first = _post(client, kofi_transaction_id="replay-txn", email="replay@example.com")
    assert first.status_code == 200
    assert first.json()["status"] == "applied"

    with db.get_session() as session:
        after_first = session.get(User, user_id).subscriber_until

    second = _post(client, kofi_transaction_id="replay-txn", email="replay@example.com")
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}

    with db.get_session() as session:
        after_second = session.get(User, user_id).subscriber_until
        rows = session.query(Payment).filter(Payment.kofi_transaction_id == "replay-txn").all()

    assert after_second == after_first
    assert len(rows) == 1

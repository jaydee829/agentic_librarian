"""Operator account tooling (Lift 1, ADR-048; monetization arc 2/3, BMC revector task 2):
adding a friend, comping/correcting a supporter entitlement, and reconciling a BMC
payment that couldn't be auto-matched to a user are all commands, not psql."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from agentic_librarian.cli import main
from agentic_librarian.db.models import Payment, User
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration

SLACK = timedelta(minutes=5)


@pytest.fixture(autouse=True)
def _cli_db(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr("agentic_librarian.cli._invite_db_manager", lambda: manager)
    yield manager


def _seed_user(db, email, subscriber_until=None, created_at=None):
    """Returns the new user's id only — never a live ORM object across a closed session
    boundary (expire-on-commit + session close = DetachedInstanceError on later attribute
    access, CI-only since local runs never hit a real Postgres to exercise this path).
    Flushes immediately so later FK-dependent inserts (Payment.matched_user_id) in the
    SAME test see the row (PR #155 lesson)."""
    with db.get_session() as session:
        user = User(
            email=email,
            subscriber_until=subscriber_until,
            created_at=created_at or (datetime.now(UTC) - timedelta(days=1)),
        )
        session.add(user)
        session.flush()
        return user.id


def _seed_payment(db, **overrides):
    fields = {
        "provider": "bmc",
        "provider_event_id": "txn-1",
        "event_type": "membership.started",
        "email": "payer@example.com",
        "amount": Decimal("5.00"),
        "currency": "USD",
        "level_name": None,
        "duration_type": None,
        "subscription_id": None,
        "payload": {},
        "matched_user_id": None,
        "granted_until": None,
        "created_at": datetime.now(UTC) - timedelta(days=1),
        **overrides,
    }
    with db.get_session() as session:
        payment = Payment(**fields)
        session.add(payment)
        session.flush()
        return payment.id


def test_invite_creates_lowercased_row(_cli_db, capsys):
    assert main(["user", "invite", "Friend@Example.COM", "--name", "Pat"]) == 0
    with _cli_db.get_session() as session:
        row = session.query(User).filter(User.email == "friend@example.com").one()
        assert row.firebase_uid is None
        assert row.display_name == "Pat"
    assert "Invited friend@example.com" in capsys.readouterr().out


def test_invite_existing_email_is_idempotent(_cli_db, capsys):
    assert main(["user", "invite", "friend@example.com"]) == 0
    assert main(["user", "invite", "friend@example.com"]) == 0
    out = capsys.readouterr().out
    assert "already exists" in out
    with _cli_db.get_session() as session:
        assert session.query(User).filter(User.email == "friend@example.com").count() == 1


def test_invite_rejects_non_email(capsys):
    assert main(["user", "invite", "not-an-email"]) == 2


@pytest.mark.parametrize(
    "extra_args,expected_days",
    [
        pytest.param([], 33, id="default-is-one-month-33-days"),
        pytest.param(["--months", "3"], 99, id="months-3-times-33"),
        pytest.param(["--days", "45"], 45, id="days-exact"),
    ],
)
def test_subscribe_grants_expected_days(_cli_db, capsys, extra_args, expected_days):
    email = "comp@example.com"
    user_id = _seed_user(_cli_db, email)
    before = datetime.now(UTC)
    assert main(["user", "subscribe", email, *extra_args]) == 0
    with _cli_db.get_session() as session:
        refreshed = session.get(User, user_id)
        expected_min = before + timedelta(days=expected_days) - SLACK
        expected_max = before + timedelta(days=expected_days) + SLACK
        assert expected_min <= refreshed.subscriber_until <= expected_max
    assert email in capsys.readouterr().out


def test_subscribe_until_sets_absolute_not_extend(_cli_db, capsys):
    """--until SETS subscriber_until to the given date at UTC midnight, even when an
    existing (still-active) horizon is further out than that date — proving it's an
    absolute set, not entitlements.extend()'s stack-if-active behavior."""
    email = "comp-until@example.com"
    user_id = _seed_user(_cli_db, email, subscriber_until=datetime.now(UTC) + timedelta(days=500))
    assert main(["user", "subscribe", email, "--until", "2027-01-01"]) == 0
    with _cli_db.get_session() as session:
        refreshed = session.get(User, user_id)
        assert refreshed.subscriber_until == datetime(2027, 1, 1, tzinfo=UTC)
    assert "2027-01-01" in capsys.readouterr().out


def test_subscribe_unknown_email_exits_2(_cli_db, capsys):
    assert main(["user", "subscribe", "nobody@example.com"]) == 2
    assert "error" in capsys.readouterr().err


def test_subscribe_rejects_non_email(_cli_db, capsys):
    assert main(["user", "subscribe", "not-an-email"]) == 2
    assert "error" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra_args",
    [["--days", "0"], ["--days", "-30"], ["--months", "0"], ["--months", "-5"]],
    ids=["days-zero", "days-negative", "months-zero", "months-negative"],
)
def test_subscribe_rejects_non_positive_grants(_cli_db, capsys, extra_args):
    """Fat-finger guardrail: a negative/zero grant must not silently shrink paid time —
    reducing/revoking is deliberate-only via the absolute --until."""
    email = "guard@example.com"
    horizon = datetime.now(UTC) + timedelta(days=200)
    user_id = _seed_user(_cli_db, email, subscriber_until=horizon)
    assert main(["user", "subscribe", email, *extra_args]) == 2
    assert "--until" in capsys.readouterr().err
    with _cli_db.get_session() as session:
        assert session.get(User, user_id).subscriber_until == horizon


def test_payments_list_unmatched_filters_out_matched_rows(_cli_db, capsys):
    user_id = _seed_user(_cli_db, "matched@example.com")
    _seed_payment(
        _cli_db,
        provider_event_id="txn-matched",
        email="matched@example.com",
        matched_user_id=user_id,
        granted_until=datetime.now(UTC) + timedelta(days=33),
    )
    _seed_payment(_cli_db, provider_event_id="txn-unmatched", email="nobody@example.com")
    assert main(["payments", "list", "--unmatched"]) == 0
    out = capsys.readouterr().out
    assert "txn-unmatched" in out
    assert "txn-matched" not in out


def test_payments_list_without_filter_shows_matched_and_unmatched(_cli_db, capsys):
    user_id = _seed_user(_cli_db, "matched2@example.com")
    _seed_payment(
        _cli_db,
        provider_event_id="txn-m2",
        email="matched2@example.com",
        matched_user_id=user_id,
        granted_until=datetime.now(UTC) + timedelta(days=33),
    )
    _seed_payment(_cli_db, provider_event_id="txn-u2", email="nobody2@example.com")
    assert main(["payments", "list"]) == 0
    out = capsys.readouterr().out
    assert "txn-m2" in out
    assert "txn-u2" in out
    assert "nobody2@example.com" in out


@pytest.mark.parametrize(
    "event_type,duration_type,expected_kind,expected_days",
    [
        pytest.param("membership.started", "month", "monthly", 33, id="monthly"),
        pytest.param("membership.started", "year", "annual", 370, id="annual"),
        pytest.param("donation.created", None, "tip", 0, id="tip-no-entitlement"),
    ],
)
def test_payments_match_recomputes_entitlement_from_stored_row(
    _cli_db, capsys, event_type, duration_type, expected_kind, expected_days
):
    """match must recompute classify()/horizon()/apply_grant() from the STORED payment
    fields (event_type, duration_type, payload) — not anything on the command line — so
    CLI and webhook grant identically. The seeded payload has no current_period_end, so
    the grant falls back to entitlements.extend() at grant_days(kind) — same 33/370/0
    day figures as the flat Ko-fi grants this replaces."""
    email = f"match-{expected_kind}@example.com"
    event_id = f"txn-{expected_kind}"
    user_id = _seed_user(_cli_db, email)
    _seed_payment(
        _cli_db,
        provider_event_id=event_id,
        event_type=event_type,
        email=email,
        amount=Decimal("5.00"),
        duration_type=duration_type,
    )
    before = datetime.now(UTC)
    assert main(["payments", "match", event_id, email]) == 0
    with _cli_db.get_session() as session:
        payment = session.query(Payment).filter(Payment.provider_event_id == event_id).one()
        refreshed = session.get(User, user_id)
        assert payment.matched_user_id == user_id
        if expected_days > 0:
            expected_min = before + timedelta(days=expected_days) - SLACK
            expected_max = before + timedelta(days=expected_days) + SLACK
            assert expected_min <= refreshed.subscriber_until <= expected_max
            assert expected_min <= payment.granted_until <= expected_max
        else:
            assert refreshed.subscriber_until is None
            assert payment.granted_until is None


def test_payments_match_refuses_unknown_transaction(_cli_db, capsys):
    _seed_user(_cli_db, "someone@example.com")
    assert main(["payments", "match", "nope-txn", "someone@example.com"]) == 2
    assert "error" in capsys.readouterr().err


def test_payments_match_refuses_unknown_email(_cli_db, capsys):
    _seed_payment(_cli_db, provider_event_id="txn-orphan", email="payer@example.com")
    assert main(["payments", "match", "txn-orphan", "nobody@example.com"]) == 2
    assert "error" in capsys.readouterr().err


def test_payments_match_refuses_already_matched(_cli_db, capsys):
    user_id = _seed_user(_cli_db, "already@example.com")
    _seed_payment(
        _cli_db,
        provider_event_id="txn-already",
        email="already@example.com",
        matched_user_id=user_id,
        granted_until=datetime.now(UTC) + timedelta(days=33),
    )
    assert main(["payments", "match", "txn-already", "already@example.com"]) == 2
    assert "already matched" in capsys.readouterr().err

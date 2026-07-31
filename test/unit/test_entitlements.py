"""Monetization arc 2/3 (BMC revector): entitlement classification and grant/cap math
are pure and DB-free."""

from datetime import UTC, datetime, timedelta

import pytest

from agentic_librarian.core import entitlements

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "event_type,duration_type,expected",
    [
        pytest.param("membership.started", "month", "monthly", id="membership-started-month"),
        pytest.param("membership.started", "year", "annual", id="membership-started-year"),
        pytest.param("membership.updated", "year", "annual", id="membership-updated-year"),
        pytest.param("recurring_donation.started", "month", "monthly", id="recurring-donation-started-month"),
        pytest.param("membership.started", None, "monthly", id="membership-started-none-defaults-monthly"),
        pytest.param("donation.created", None, "tip", id="donation-created-is-tip"),
        pytest.param("donation.refunded", None, "ignore", id="donation-refunded-is-ignore"),
        pytest.param("membership.cancelled", "month", "ignore", id="membership-cancelled-routed-elsewhere"),
        pytest.param("shop_order.created", None, "ignore", id="shop-order-is-ignore"),
        pytest.param("", None, "ignore", id="empty-event-type-is-ignore"),
    ],
)
def test_classify(event_type, duration_type, expected):
    assert entitlements.classify(event_type, duration_type) == expected


@pytest.mark.parametrize(
    "kind,days",
    [
        pytest.param("monthly", 33, id="monthly-33"),
        pytest.param("annual", 370, id="annual-370"),
        pytest.param("tip", 0, id="tip-0"),
        pytest.param("ignore", 0, id="ignore-0"),
    ],
)
def test_grant_days(kind, days):
    assert entitlements.grant_days(kind) == days


@pytest.mark.parametrize(
    "env_value,expected",
    [
        pytest.param(None, 5, id="unset-defaults-5"),
        pytest.param("10", 10, id="explicit-10"),
        pytest.param("0", 5, id="zero-falls-back-to-default"),
        pytest.param("-3", 5, id="negative-falls-back-to-default"),
        pytest.param("abc", 5, id="non-numeric-falls-back-to-default"),
    ],
)
def test_grace_days(monkeypatch, env_value, expected):
    if env_value is None:
        monkeypatch.delenv("BMC_GRACE_DAYS", raising=False)
    else:
        monkeypatch.setenv("BMC_GRACE_DAYS", env_value)
    assert entitlements.grace_days() == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        pytest.param(1719825600, datetime(2024, 7, 1, 9, 20, tzinfo=UTC), id="int-unix-ts"),
        pytest.param("1719825600", datetime(2024, 7, 1, 9, 20, tzinfo=UTC), id="string-digits-unix-ts"),
        pytest.param(None, None, id="none-is-none"),
        pytest.param(0, None, id="zero-is-none"),
        pytest.param(-5, None, id="negative-is-none"),
        pytest.param("nope", None, id="non-numeric-string-is-none"),
        pytest.param(40000000000, None, id="past-year-3000-guard-is-none"),
    ],
)
def test_ts_to_dt(value, expected):
    assert entitlements.ts_to_dt(value) == expected


@pytest.mark.parametrize(
    "period_end,env_grace,expected",
    [
        pytest.param(NOW, None, NOW + timedelta(days=5), id="default-grace-5"),
        pytest.param(None, None, None, id="none-period-end-is-none"),
        pytest.param(NOW, "10", NOW + timedelta(days=10), id="env-grace-10-respected"),
    ],
)
def test_horizon(monkeypatch, period_end, env_grace, expected):
    if env_grace is None:
        monkeypatch.delenv("BMC_GRACE_DAYS", raising=False)
    else:
        monkeypatch.setenv("BMC_GRACE_DAYS", env_grace)
    assert entitlements.horizon(period_end) == expected


@pytest.mark.parametrize(
    "current,new_horizon,expected",
    [
        pytest.param(None, NOW, NOW, id="none-current-takes-horizon"),
        pytest.param(NOW - timedelta(days=1), NOW, NOW, id="earlier-current-takes-horizon"),
        pytest.param(NOW + timedelta(days=1), NOW, NOW + timedelta(days=1), id="later-current-never-shrinks"),
    ],
)
def test_apply_grant(current, new_horizon, expected):
    assert entitlements.apply_grant(current, new_horizon) == expected


@pytest.mark.parametrize(
    "current,cap,expected",
    [
        pytest.param(None, NOW, None, id="none-current-stays-none"),
        pytest.param(NOW + timedelta(days=1), NOW, NOW, id="later-current-capped"),
        pytest.param(NOW - timedelta(days=1), NOW, NOW - timedelta(days=1), id="earlier-current-never-extends"),
        pytest.param(None, None, None, id="none-current-none-cap-stays-none"),
        pytest.param(
            NOW + timedelta(days=1),
            None,
            NOW + timedelta(days=1),
            id="none-cap-never-guess-shrinks-standing",
        ),
    ],
)
def test_apply_cap(current, cap, expected):
    assert entitlements.apply_cap(current, cap) == expected


@pytest.mark.parametrize(
    "current,days,expected",
    [
        pytest.param(None, 33, NOW + timedelta(days=33), id="first-sub"),
        pytest.param(NOW - timedelta(days=10), 33, NOW + timedelta(days=33), id="lapsed-restarts-from-now"),
        pytest.param(NOW + timedelta(days=5), 33, NOW + timedelta(days=38), id="active-stacks"),
        pytest.param(NOW + timedelta(days=100), 370, NOW + timedelta(days=470), id="annual-stacks-too"),
    ],
)
def test_extend(current, days, expected):
    assert entitlements.extend(current, days, now=NOW) == expected

"""Monetization arc 2/3: entitlement classification is pure and DB-free."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from agentic_librarian.core import entitlements

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "kofi_type,is_sub,tier_name,amount,expected",
    [
        ("Subscription", True, "Supporter", Decimal("3.00"), "monthly"),
        ("Subscription", True, None, Decimal("3.00"), "monthly"),
        ("Shop Order", False, "Annual", Decimal("25.00"), "annual"),
        ("Shop Order", False, "ANNUAL", Decimal("25.00"), "annual"),  # case-insensitive
        # "Annual Supporter" != "annual" under exact case-folded match, so tier_name does
        # NOT win here; falls through to is_sub=True -> monthly. See NOTE in task-1-brief.md.
        ("Subscription", True, "Annual Supporter", Decimal("25.00"), "monthly"),
        ("Donation", False, None, Decimal("25.00"), "annual"),  # one-off >= threshold
        ("Donation", False, None, Decimal("30.00"), "annual"),
        ("Donation", False, None, Decimal("24.99"), "tip"),
        ("Donation", False, None, Decimal("3.00"), "tip"),
        ("Commission", False, None, Decimal("10.00"), "tip"),
    ],
)
def test_classify_defaults(monkeypatch, kofi_type, is_sub, tier_name, amount, expected):
    monkeypatch.delenv("KOFI_ANNUAL_TIER_NAMES", raising=False)
    monkeypatch.delenv("KOFI_ANNUAL_MIN_AMOUNT", raising=False)
    assert entitlements.classify(kofi_type, is_sub, tier_name, amount) == expected


def test_classify_env_tier_names(monkeypatch):
    monkeypatch.setenv("KOFI_ANNUAL_TIER_NAMES", "yearly, gold ")
    assert entitlements.classify("Shop Order", False, "Gold", Decimal("25.00")) == "annual"
    assert entitlements.classify("Shop Order", False, "Annual", Decimal("10.00")) == "tip"


@pytest.mark.parametrize("kind,days", [("monthly", 33), ("annual", 370), ("tip", 0)])
def test_grant_days(kind, days):
    assert entitlements.grant_days(kind) == days


@pytest.mark.parametrize(
    "current,days,expected",
    [
        (None, 33, NOW + timedelta(days=33)),  # first sub
        (NOW - timedelta(days=10), 33, NOW + timedelta(days=33)),  # lapsed: restart from now
        (NOW + timedelta(days=5), 33, NOW + timedelta(days=38)),  # active: stack
        (NOW + timedelta(days=100), 370, NOW + timedelta(days=470)),  # annual stacks too
    ],
)
def test_extend(current, days, expected):
    assert entitlements.extend(current, days, now=NOW) == expected

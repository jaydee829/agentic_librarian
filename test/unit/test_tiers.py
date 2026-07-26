"""#100: tier decision + env-tunable budget knobs are DB-free and unit-testable
(the un-gate-DB-free-paths lesson)."""

from datetime import UTC, datetime, timedelta

import pytest

from agentic_librarian.core import budgets, tiers

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "subscriber_until,has_cred,expected",
    [
        (None, False, "free"),
        (NOW - timedelta(days=1), False, "free"),  # lapsed
        (NOW + timedelta(days=1), False, "supporter"),
        (None, True, "byok"),
        (NOW + timedelta(days=1), True, "byok"),  # byok wins over supporter
    ],
)
def test_tier_for(subscriber_until, has_cred, expected):
    assert tiers.tier_for(subscriber_until, has_cred, now=NOW) == expected


@pytest.mark.parametrize(
    "fn,tier,expected",
    [
        (tiers.chat_turns_per_day, "free", 20),
        (tiers.chat_turns_per_day, "supporter", 200),
        (tiers.chat_turns_per_day, "byok", 2000),
        (tiers.import_max_rows, "free", 300),
        (tiers.import_max_rows, "supporter", 2000),
        (tiers.grounded_calls_per_day, "free", 300),
        (tiers.grounded_calls_per_day, "supporter", 1500),
        (tiers.grounded_calls_per_day, "byok", 15000),
    ],
)
def test_default_budgets(monkeypatch, fn, tier, expected):
    for var in tiers._DEFAULTS:
        monkeypatch.delenv(var, raising=False)
    assert fn(tier) == expected


def test_global_governor_default(monkeypatch):
    monkeypatch.delenv("GROUNDED_CALLS_PER_DAY_GLOBAL", raising=False)
    assert tiers.grounded_calls_per_day_global() == 1400


@pytest.mark.parametrize("raw,expected", [("50", 50), ("abc", 20), ("", 20), ("-5", 20), ("0", 20)])
def test_env_override_and_fallback(monkeypatch, raw, expected):
    monkeypatch.setenv("CHAT_TURNS_PER_DAY_FREE", raw)
    assert tiers.chat_turns_per_day("free") == expected


def test_chat_message_max_chars_default(monkeypatch):
    monkeypatch.delenv("CHAT_MESSAGE_MAX_CHARS", raising=False)
    assert tiers.chat_message_max_chars() == 4000


def test_grounding_model_name_default(monkeypatch):
    monkeypatch.delenv("GROUNDING_MODEL", raising=False)
    monkeypatch.delenv("EXPLORER_MODEL", raising=False)
    assert tiers.grounding_model_name() == "gemini-2.5-flash"


def test_next_utc_day_start_is_next_midnight_plus_jitter():
    now = datetime(2026, 7, 25, 23, 50, tzinfo=UTC)
    when = budgets.next_utc_day_start_with_jitter(now=now, jitter_seconds=600)
    assert when == datetime(2026, 7, 26, 0, 10, tzinfo=UTC)


def test_next_utc_day_start_jitter_bounds():
    now = datetime(2026, 7, 25, 3, 0, tzinfo=UTC)
    when = budgets.next_utc_day_start_with_jitter(now=now)
    midnight = datetime(2026, 7, 26, tzinfo=UTC)
    assert midnight <= when <= midnight + timedelta(seconds=1800)

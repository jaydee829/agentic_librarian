"""#113: the seeded-history window is env-tunable (CHAT_HISTORY_SEED_LIMIT, default 30).
DB-free — the parsing helper must be testable without Postgres (db_integration lesson:
un-gate DB-free paths so they run locally)."""

import pytest

from agentic_librarian.chat.transcript import _seed_limit


def test_default_is_30(monkeypatch):
    monkeypatch.delenv("CHAT_HISTORY_SEED_LIMIT", raising=False)
    assert _seed_limit() == 30


@pytest.mark.parametrize("raw,expected", [("5", 5), ("30", 30), ("100", 100)])
def test_valid_override(monkeypatch, raw, expected):
    monkeypatch.setenv("CHAT_HISTORY_SEED_LIMIT", raw)
    assert _seed_limit() == expected


@pytest.mark.parametrize("raw", ["abc", "", "-5", "0", "3.5"])
def test_invalid_or_nonpositive_falls_back_to_default(monkeypatch, raw):
    monkeypatch.setenv("CHAT_HISTORY_SEED_LIMIT", raw)
    assert _seed_limit() == 30

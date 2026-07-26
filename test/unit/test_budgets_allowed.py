"""DB-free unit tests for the two fail-open budget wrappers in core/budgets.py —
chat_turn_allowed and enrichment_allowed (#100 Task 1 review carry-forward). No Postgres:
the limit-reached/under-limit branches use a db_manager stub whose get_session() yields a
sentinel (never queried directly — the counting/tier functions that would touch it are
monkeypatched), and the fail-open branch points db_manager at an unreachable host
(mirrors test/unit/test_usage_recorder.py)."""

import logging
from contextlib import contextmanager
from uuid import uuid4

import pytest

from agentic_librarian.core import budgets, tiers
from agentic_librarian.db.session import DatabaseManager


class _StubManager:
    """A db_manager stand-in that never touches a real database — get_session() yields a
    sentinel object. Only safe when every function that would query the session is
    monkeypatched out by the test."""

    @contextmanager
    def get_session(self):
        yield object()


@pytest.fixture
def stub_db_manager():
    """Point budgets.db_manager at the DB-free stub, restoring the real one after."""
    original = budgets.db_manager
    budgets.set_db_manager(_StubManager())
    yield
    budgets.set_db_manager(original)


@pytest.fixture
def unreachable_db_manager():
    """Point budgets.db_manager at a host that can never resolve, restoring after."""
    original = budgets.db_manager
    budgets.set_db_manager(DatabaseManager("postgresql://x:x@nohost-never-resolves:1/x"))
    yield
    budgets.set_db_manager(original)


@pytest.mark.parametrize(
    "used,limit,expect_allowed",
    [
        pytest.param(0, 2, True, id="under_limit"),
        pytest.param(1, 2, True, id="just_under_limit"),
        pytest.param(2, 2, False, id="at_limit"),
        pytest.param(5, 2, False, id="over_limit"),
    ],
)
def test_chat_turn_allowed_limit_branches(monkeypatch, stub_db_manager, used, limit, expect_allowed):
    monkeypatch.setattr(tiers, "effective_tier", lambda session, user_id: "free")
    monkeypatch.setattr(tiers, "chat_turns_per_day", lambda tier: limit)
    monkeypatch.setattr(budgets, "chat_turns_today", lambda session, user_id: used)

    allowed, message = budgets.chat_turn_allowed(uuid4())

    assert allowed is expect_allowed
    if expect_allowed:
        assert message == ""
    else:
        assert str(limit) in message


def test_chat_turn_allowed_fails_open_on_db_error(caplog, unreachable_db_manager):
    with caplog.at_level(logging.WARNING):
        allowed, message = budgets.chat_turn_allowed(uuid4())
    assert allowed is True
    assert message == ""
    assert "chat budget check failed open" in caplog.text


@pytest.mark.parametrize(
    "global_used,global_limit,user_used,user_limit,expect_allowed,message_contains",
    [
        pytest.param(0, 100, 0, 5, True, None, id="under_both_budgets"),
        pytest.param(0, 100, 4, 5, True, None, id="under_per_user_budget"),
        pytest.param(100, 100, 0, 5, False, "governor", id="global_governor_reached"),
        pytest.param(0, 100, 5, 5, False, "tier", id="per_user_budget_reached"),
    ],
)
def test_enrichment_allowed_limit_branches(
    monkeypatch,
    stub_db_manager,
    global_used,
    global_limit,
    user_used,
    user_limit,
    expect_allowed,
    message_contains,
):
    monkeypatch.setattr(tiers, "grounded_calls_per_day_global", lambda: global_limit)
    monkeypatch.setattr(tiers, "grounded_calls_per_day", lambda tier: user_limit)
    monkeypatch.setattr(tiers, "effective_tier", lambda session, user_id: "free")

    def _fake_grounded_calls_today(session, user_id):
        return global_used if user_id is None else user_used

    monkeypatch.setattr(budgets, "grounded_calls_today", _fake_grounded_calls_today)

    allowed, message = budgets.enrichment_allowed(uuid4())

    assert allowed is expect_allowed
    if expect_allowed:
        assert message == ""
    else:
        assert message_contains in message


def test_enrichment_allowed_under_limit_with_no_user_checks_only_governor(monkeypatch, stub_db_manager):
    """user_id=None (pre-attribution tasks) must check only the global governor — the
    per-user branch is never reached (tier lookup would need a real user row)."""
    monkeypatch.setattr(tiers, "grounded_calls_per_day_global", lambda: 100)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("per-user tier lookup must not run when user_id is None")

    monkeypatch.setattr(tiers, "effective_tier", _fail_if_called)
    monkeypatch.setattr(budgets, "grounded_calls_today", lambda session, user_id: 0)

    allowed, message = budgets.enrichment_allowed(None)

    assert allowed is True
    assert message == ""


def test_enrichment_allowed_fails_open_on_db_error(caplog, unreachable_db_manager):
    with caplog.at_level(logging.WARNING):
        allowed, message = budgets.enrichment_allowed(uuid4())
    assert allowed is True
    assert message == ""
    assert "enrichment budget check failed open" in caplog.text

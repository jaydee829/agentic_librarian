"""#100: budget counting queries + the effective_tier DB wrapper, executed against a
real Postgres (db_integration — CI-only, see test/conftest.py). Rows are seeded with
EXPLICIT, distinct created_at values (the #147 same-timestamp flake lesson)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from agentic_librarian.core import budgets, tiers
from agentic_librarian.core.user_context import DEFAULT_USER_ID
from agentic_librarian.db.models import Conversation, Message, Usage, User, UserCredential
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration

OTHER_USER = UUID("00000000-0000-4000-8000-0000000000ff")

# Fixed at noon so it's always >= today's midnight regardless of the real wall-clock
# time the suite happens to run at; _utc_day_start() has no upper bound, so a "today"
# row need not be <= "now".
_NOW = datetime.now(UTC)
TODAY = _NOW.replace(hour=12, minute=0, second=0, microsecond=0)
YESTERDAY = TODAY - timedelta(days=1)

GROUNDING_MODEL = tiers.grounding_model_name()


@pytest.fixture
def budgets_db(db_url):
    """Point the budgets module at the isolated test DB (test_usage_db.py pattern)."""
    manager = DatabaseManager(db_url)
    original = budgets.db_manager
    budgets.set_db_manager(manager)
    yield manager
    budgets.set_db_manager(original)


@pytest.mark.parametrize(
    "role,owner,when,expected_count",
    [
        ("user", "self", "today", 1),
        ("assistant", "self", "today", 0),
        ("user", "self", "yesterday", 0),
        ("user", "other", "today", 0),
    ],
    ids=[
        "todays-user-message-counts",
        "assistant-role-excluded",
        "yesterdays-message-excluded",
        "other-users-conversation-excluded",
    ],
)
def test_chat_turns_today(budgets_db, role, owner, when, expected_count):
    user_id = OTHER_USER if owner == "other" else DEFAULT_USER_ID
    created_at = TODAY if when == "today" else YESTERDAY
    with budgets_db.get_session() as session:
        if owner == "other":
            session.merge(User(id=OTHER_USER, email="other@example.com"))
        convo = Conversation(user_id=user_id)
        session.add(convo)
        session.flush()
        session.add(Message(conversation_id=convo.id, role=role, content="x", created_at=created_at))

    with budgets_db.get_session() as session:
        count = budgets.chat_turns_today(session, DEFAULT_USER_ID)
    assert count == expected_count


@pytest.mark.parametrize(
    "model,key_source,when,expected_count",
    [
        (GROUNDING_MODEL, "app", "today", 1),
        ("some-other-model", "app", "today", 0),
        (GROUNDING_MODEL, "byok", "today", 0),
        (GROUNDING_MODEL, "app", "yesterday", 0),
    ],
    ids=["matching-row-counts", "wrong-model-excluded", "byok-key-source-excluded", "yesterdays-row-excluded"],
)
def test_grounded_calls_today_filters(budgets_db, model, key_source, when, expected_count):
    created_at = TODAY if when == "today" else YESTERDAY
    with budgets_db.get_session() as session:
        session.add(
            Usage(
                user_id=DEFAULT_USER_ID,
                key_source=key_source,
                vendor="gemini",
                model=model,
                input_tokens=1,
                output_tokens=1,
                created_at=created_at,
            )
        )

    with budgets_db.get_session() as session:
        count = budgets.grounded_calls_today(session, DEFAULT_USER_ID)
    assert count == expected_count


def test_grounded_calls_today_per_user_vs_global(budgets_db):
    """user_id=None is the global governor — it must see BOTH users' rows; a specific
    user_id must see only that user's."""
    with budgets_db.get_session() as session:
        session.merge(User(id=OTHER_USER, email="other@example.com"))
        session.add_all(
            [
                Usage(
                    user_id=DEFAULT_USER_ID,
                    key_source="app",
                    vendor="gemini",
                    model=GROUNDING_MODEL,
                    input_tokens=1,
                    output_tokens=1,
                    created_at=TODAY,
                ),
                Usage(
                    user_id=OTHER_USER,
                    key_source="app",
                    vendor="gemini",
                    model=GROUNDING_MODEL,
                    input_tokens=1,
                    output_tokens=1,
                    created_at=TODAY + timedelta(minutes=1),
                ),
            ]
        )

    with budgets_db.get_session() as session:
        mine = budgets.grounded_calls_today(session, DEFAULT_USER_ID)
        theirs = budgets.grounded_calls_today(session, OTHER_USER)
        everyone = budgets.grounded_calls_today(session, None)
    assert mine == 1
    assert theirs == 1
    assert everyone == 2


@pytest.mark.parametrize(
    "days_from_now,has_cred,expected",
    [
        (None, False, "free"),
        (30, False, "supporter"),
        (None, True, "byok"),
    ],
    ids=["plain-user-is-free", "future-subscriber-until-is-supporter", "gemini-credential-is-byok"],
)
def test_effective_tier_db_wrapper(budgets_db, days_from_now, has_cred, expected):
    subscriber_until = TODAY + timedelta(days=days_from_now) if days_from_now is not None else None
    with budgets_db.get_session() as session:
        user = session.get(User, DEFAULT_USER_ID)
        user.subscriber_until = subscriber_until
        if has_cred:
            session.add(
                UserCredential(
                    user_id=DEFAULT_USER_ID,
                    vendor="gemini",
                    encrypted_key=b"ciphertext",
                    kms_key_name="projects/test/keyRings/test/cryptoKeys/test",
                )
            )

    with budgets_db.get_session() as session:
        tier = tiers.effective_tier(session, DEFAULT_USER_ID)
    assert tier == expected

"""#100 monetization arc 2/3: GET /api/account, executed against a real Postgres
(db_integration — CI-only, see test/conftest.py). Tier comes straight from
tiers.effective_tier — this test asserts the endpoint's shape, not a second tier
computation. Rows are seeded with EXPLICIT, distinct created_at values (#147 lesson)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import auth
from agentic_librarian.api import main as api_main
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.models import User
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        auth.get_current_user,
        lambda: auth.AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL),
    )
    yield TestClient(api_main.app)


def test_account_free_user_has_no_subscriber_until(client):
    body = client.get("/api/account").json()
    assert body == {
        "email": DEFAULT_USER_EMAIL,
        "display_name": "Justin",
        "tier": "free",
        "subscriber_until": None,
    }


def test_account_future_subscriber_until_is_supporter(client, db_url):
    manager = DatabaseManager(db_url)
    until = (datetime.now(UTC) + timedelta(days=30)).replace(microsecond=0)
    with manager.get_session() as session:
        row = session.get(User, DEFAULT_USER_ID)
        row.subscriber_until = until

    body = client.get("/api/account").json()
    assert body["tier"] == "supporter"
    assert body["subscriber_until"] == until.isoformat()
    assert body["email"] == DEFAULT_USER_EMAIL

"""/api/recommendations payload carries read_status/last_read/rating (A3)."""

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import main as api_main
from agentic_librarian.api import recommendations as recs_module
from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.core.user_context import DEFAULT_USER_ID
from agentic_librarian.db.models import Author as AuthorModel
from agentic_librarian.db.models import Edition, ReadingHistory, Suggestions, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


@pytest.fixture()
def recs_client(db_url):
    manager = DatabaseManager(db_url)
    original = recs_module.db_manager
    recs_module.set_db_manager(manager)
    with manager.get_session() as session:
        author = AuthorModel(name="Rec Author")
        read_work = Work(title="Read Work", contributors=[WorkContributor(author=author, role="Author")])
        unread_work = Work(title="Unread Work", contributors=[WorkContributor(author=author, role="Author")])
        read_ed = Edition(work=read_work, format="ebook")
        session.add_all([author, read_work, unread_work, read_ed])
        session.flush()
        session.add(
            ReadingHistory(
                edition_id=read_ed.id,
                user_id=DEFAULT_USER_ID,
                date_completed=date(2019, 5, 1),
                user_rating=4,
            )
        )
        session.add(
            Suggestions(
                work_id=read_work.id,
                user_id=DEFAULT_USER_ID,
                context="recommendation",
                justification="j1",
                status="Suggested",
                suggested_at=datetime(2026, 6, 2, tzinfo=UTC),
            )
        )
        session.add(
            Suggestions(
                work_id=unread_work.id,
                user_id=DEFAULT_USER_ID,
                context="recommendation",
                justification="j2",
                status="Suggested",
                suggested_at=datetime(2026, 6, 1, tzinfo=UTC),
            )
        )
        session.flush()
    api_main.app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=DEFAULT_USER_ID, email="jaydee829@gmail.com"
    )
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.pop(get_current_user, None)
    recs_module.set_db_manager(original)


def test_recommendations_payload_carries_read_status(recs_client):
    resp = recs_client.get("/api/recommendations")
    assert resp.status_code == 200
    by_title = {r["title"]: r for r in resp.json()}

    read = by_title["Read Work"]
    assert read["read_status"] == "reread"
    assert read["last_read"] == "2019-05-01"
    assert read["rating"] == 4

    fresh = by_title["Unread Work"]
    assert fresh["read_status"] == "new"
    assert fresh["last_read"] is None
    assert fresh["rating"] is None

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import auth
from agentic_librarian.api import main as api_main
from agentic_librarian.api import recommendations as rec_mod
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.models import Author, Suggestions, User, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    monkeypatch.setattr(rec_mod, "db_manager", manager)
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        auth.get_current_user,
        lambda: auth.AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL),
    )
    yield TestClient(api_main.app)


def _seed_suggestion(manager, *, user_id, title, author, status="Suggested", justification="because", genres=None):
    with manager.get_session() as s:
        work = Work(title=title, genres=genres)
        s.add(work)
        s.flush()
        a = Author(name=author)
        s.add(a)
        s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        sug = Suggestions(work_id=work.id, user_id=user_id, status=status, justification=justification)
        s.add(sug)
        s.flush()
        return sug.id, work.id


def test_lists_only_active_suggestions_for_the_user(client, db_url):
    manager = DatabaseManager(db_url)
    sid, wid = _seed_suggestion(
        manager, user_id=DEFAULT_USER_ID, title="Dune", author="Herbert", genres=["science-fiction"]
    )
    _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Old Pick", author="X", status="Dismissed")

    body = client.get("/api/recommendations").json()

    assert [r["title"] for r in body] == ["Dune"]  # Dismissed one excluded
    row = body[0]
    assert row["id"] == str(sid)
    assert row["work_id"] == str(wid)
    assert row["authors"] == ["Herbert"]
    assert row["justification"] == "because"
    assert row["status"] == "Suggested"
    assert row["genres"] == ["science-fiction"]


def test_does_not_leak_another_users_suggestions(client, db_url):
    manager = DatabaseManager(db_url)
    other_id = uuid4()
    with manager.get_session() as s:
        s.add(User(id=other_id, email="other@example.com"))
        s.flush()
    _seed_suggestion(manager, user_id=other_id, title="Secret", author="Nobody")

    body = client.get("/api/recommendations").json()

    assert body == []  # the default user sees none of the other user's suggestions


def test_dismiss_marks_status_and_removes_from_list(client, db_url):
    manager = DatabaseManager(db_url)
    sid, _ = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Meh", author="Z")

    resp = client.post(f"/api/recommendations/{sid}/status", json={"status": "Dismissed"})
    assert resp.status_code == 200
    assert resp.json() == {"id": str(sid), "status": "Dismissed"}

    assert client.get("/api/recommendations").json() == []  # no longer active


def test_rejects_unknown_status(client, db_url):
    manager = DatabaseManager(db_url)
    sid, _ = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Meh", author="Z")

    resp = client.post(f"/api/recommendations/{sid}/status", json={"status": "Banished"})
    assert resp.status_code == 422


def test_cannot_dismiss_another_users_suggestion(client, db_url):
    manager = DatabaseManager(db_url)
    other_id = uuid4()
    with manager.get_session() as s:
        s.add(User(id=other_id, email="other2@example.com"))
        s.flush()
    sid, _ = _seed_suggestion(manager, user_id=other_id, title="Secret", author="Nobody")

    resp = client.post(f"/api/recommendations/{sid}/status", json={"status": "Dismissed"})
    assert resp.status_code == 404  # scoping: not the caller's suggestion


def test_mark_read_removes_from_active_list(client, db_url):
    manager = DatabaseManager(db_url)
    sid, _ = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Read It", author="Q")

    resp = client.post(f"/api/recommendations/{sid}/status", json={"status": "Read"})
    assert resp.status_code == 200
    assert resp.json() == {"id": str(sid), "status": "Read"}
    assert client.get("/api/recommendations").json() == []  # no longer "Suggested"


def test_genres_defaults_to_empty_list_when_not_set(client, db_url):
    """Work with no genres returns genres=[] (the `or []` fallback in the handler)."""
    manager = DatabaseManager(db_url)
    _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Genreless", author="Anon")

    body = client.get("/api/recommendations").json()

    assert len(body) == 1
    assert body[0]["genres"] == []


@pytest.mark.parametrize("status", ["Dismissed", "Read", "Removed"])
def test_status_endpoint_accepts_each_allowed_value(client, db_url, status):
    manager = DatabaseManager(db_url)
    sug_id, _work_id = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title=f"Allowed {status}", author="A. Uthor")

    resp = client.post(f"/api/recommendations/{sug_id}/status", json={"status": status})

    assert resp.status_code == 200
    assert resp.json() == {"id": str(sug_id), "status": status}
    with manager.get_session() as s:
        assert s.get(Suggestions, sug_id).status == status


@pytest.mark.parametrize("bad", ["removed", "Skipped", "", "Suggested"])
def test_status_endpoint_rejects_bad_vocab(client, db_url, bad):
    # Exact-match vocabulary: lowercase 'removed' is rejected (only the MCP tool
    # normalizes case), and re-activating to 'Suggested' is not a client verb.
    manager = DatabaseManager(db_url)
    sug_id, _work_id = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title=f"Bad {bad!r}", author="A. Uthor")

    resp = client.post(f"/api/recommendations/{sug_id}/status", json={"status": bad})

    assert resp.status_code == 422
    with manager.get_session() as s:
        assert s.get(Suggestions, sug_id).status == "Suggested"  # untouched on rejection


def test_remove_marks_status_and_removes_from_list(client, db_url):
    # The 'Not right now' flow end-to-end: Removed resolves the pick out of the
    # active list while keeping the row (neutral, auditable — GH #130).
    manager = DatabaseManager(db_url)
    sug_id, _work_id = _seed_suggestion(manager, user_id=DEFAULT_USER_ID, title="Neutral Exit", author="A. Uthor")

    resp = client.post(f"/api/recommendations/{sug_id}/status", json={"status": "Removed"})
    assert resp.status_code == 200

    listed = client.get("/api/recommendations").json()
    assert all(item["id"] != str(sug_id) for item in listed)
    with manager.get_session() as s:
        assert s.get(Suggestions, sug_id).status == "Removed"  # row kept, not deleted

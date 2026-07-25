from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import auth
from agentic_librarian.api import availability as availability_mod
from agentic_librarian.api import main as api_main
from agentic_librarian.availability import service
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.models import Author, UserLibrary, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    monkeypatch.setattr(availability_mod, "db_manager", manager)
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        auth.get_current_user,
        lambda: auth.AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL),
    )
    yield TestClient(api_main.app)


def _seed_work(manager, *, title, author_name):
    with manager.get_session() as s:
        work = Work(title=title)
        s.add(work)
        s.flush()
        a = Author(name=author_name)
        s.add(a)
        s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        s.flush()
        return work.id


def _seed_library(manager, *, user_id, slug, name, sort_order=0):
    with manager.get_session() as s:
        s.add(
            UserLibrary(
                user_id=user_id,
                provider="libby",
                library_slug=slug,
                display_name=name,
                sort_order=sort_order,
            )
        )
        s.flush()


def test_availability_returns_links_and_empty_libby_when_service_returns_empty(client, db_url, monkeypatch):
    """POST /availability for a seeded work returns 200 with links (including amazon)
    and libby==[] when service.batch_availability is patched to return an all-empty dict."""
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager, title="Dune", author_name="Frank Herbert")
    _seed_library(manager, user_id=DEFAULT_USER_ID, slug="seattle", name="Seattle Public Library")

    # Patch the service so no Thunder call goes out; returns empty (no match) for every pair
    monkeypatch.setattr(
        service,
        "batch_availability",
        lambda db_manager, libs, items: {(lib["slug"], title, author): [] for lib in libs for title, author in items},
    )

    resp = client.post("/api/availability", json={"work_ids": [str(work_id)]})
    assert resp.status_code == 200

    body = resp.json()
    assert str(work_id) in body

    entry = body[str(work_id)]
    assert "links" in entry
    link_kinds = [lnk["kind"] for lnk in entry["links"]]
    assert "amazon" in link_kinds

    assert entry["libby"] == []


def test_availability_returns_200_when_service_returns_none(client, db_url, monkeypatch):
    """POST /availability is always 200 even when service.batch_availability returns None
    for a pair (Thunder down). libby badge is simply absent from results."""
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager, title="Foundation", author_name="Isaac Asimov")
    _seed_library(manager, user_id=DEFAULT_USER_ID, slug="nypl", name="NYPL")

    # None = Thunder failure; endpoint must not raise
    monkeypatch.setattr(
        service,
        "batch_availability",
        lambda db_manager, libs, items: {(lib["slug"], title, author): None for lib in libs for title, author in items},
    )

    resp = client.post("/api/availability", json={"work_ids": [str(work_id)]})
    assert resp.status_code == 200

    body = resp.json()
    assert str(work_id) in body
    entry = body[str(work_id)]
    assert "links" in entry
    # None return → service returned falsy → no badge appended
    assert entry["libby"] == []


def test_availability_empty_work_ids_returns_empty_dict(client, db_url):
    """Empty work_ids list returns an empty dict (fast path)."""
    resp = client.post("/api/availability", json={"work_ids": []})
    assert resp.status_code == 200
    assert resp.json() == {}


def test_availability_skips_unknown_work_ids(client, db_url, monkeypatch):
    """work_ids that don't exist in the DB are silently omitted from the result."""
    monkeypatch.setattr(
        service,
        "batch_availability",
        lambda db_manager, libs, items: {(lib["slug"], title, author): [] for lib in libs for title, author in items},
    )
    fake_id = str(uuid4())
    resp = client.post("/api/availability", json={"work_ids": [fake_id]})
    assert resp.status_code == 200
    assert resp.json() == {}

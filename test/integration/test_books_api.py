from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import auth
from agentic_librarian.api import books as books_mod
from agentic_librarian.api import main as api_main
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.models import Author, Edition, ReadingHistory, Suggestions, User, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.enrichment import two_phase

pytestmark = pytest.mark.db_integration


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        auth.get_current_user,
        lambda: auth.AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL),
    )
    yield TestClient(api_main.app)


class _FakeManager:
    def __init__(self, result):
        self._result = result

    def enrich(self, title, author, format="Paperback", **kwargs):
        return self._result


def _stub_fast(monkeypatch, result):
    monkeypatch.setattr(two_phase, "create_fast_scout_manager", lambda: _FakeManager(result))


def test_add_book_persists_logs_and_enqueues(client, monkeypatch):
    enqueued = []
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: enqueued.append(wid) or True)
    _stub_fast(
        monkeypatch,
        {
            "title": "Project Hail Mary",
            "contributors": [{"name": "Andy Weir", "role": "Author"}],
            "genres": [],
            "moods": [],
        },
    )

    resp = client.post(
        "/api/books", json={"title": "Project Hail Mary", "author": "Andy Weir", "format": "ebook", "rating": 5}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Project Hail Mary"
    assert body["read_number"] == 1
    assert body["already_logged"] is False
    assert body["enrichment_enqueued"] is True
    assert enqueued == [body["work_id"]]  # newly created → deep pass enqueued


def test_add_book_not_found_returns_404(client, monkeypatch):
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    _stub_fast(monkeypatch, {})  # scouts find nothing

    resp = client.post("/api/books", json={"title": "Nope", "author": "Nobody"})
    assert resp.status_code == 404


def test_add_book_rereads_do_not_reenqueue(client, db_url, monkeypatch):
    calls = []
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: calls.append(wid) or True)
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        work = Work(title="Dune")
        s.add(work)
        s.flush()
        a = Author(name="Frank Herbert")
        s.add(a)
        s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        s.add(Edition(work_id=work.id, format="ebook"))
        s.flush()
    _stub_fast(monkeypatch, {"title": "Dune", "contributors": [{"name": "Frank Herbert", "role": "Author"}]})

    resp = client.post(
        "/api/books",
        json={"title": "Dune", "author": "Frank Herbert", "format": "ebook", "date_completed": "2020-01-01"},
    )
    assert resp.status_code == 200
    assert resp.json()["enrichment_enqueued"] is False  # de-dup hit → no re-enqueue
    assert calls == []


def test_add_book_rejects_future_date(client, monkeypatch):
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    _stub_fast(monkeypatch, {"title": "X", "contributors": [{"name": "Y", "role": "Author"}]})
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    resp = client.post("/api/books", json={"title": "X", "author": "Y", "date_completed": tomorrow})
    assert resp.status_code == 422


def test_add_book_rejects_blank_title(client):
    resp = client.post("/api/books", json={"title": "   ", "author": "Y"})
    assert resp.status_code == 422


def test_add_book_is_user_scoped(client, db_url, monkeypatch):
    # The read-event lands on the authenticated user, not another.
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    other = uuid4()
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        s.add(User(id=other, email="other@example.com"))
        s.flush()
    _stub_fast(monkeypatch, {"title": "Hyperion", "contributors": [{"name": "Dan Simmons", "role": "Author"}]})

    client.post("/api/books", json={"title": "Hyperion", "author": "Dan Simmons"})
    with manager.get_session() as s:
        rows = s.query(ReadingHistory).filter(ReadingHistory.user_id == other).all()
        assert rows == []  # nothing logged to the other user
        mine = s.query(ReadingHistory).filter(ReadingHistory.user_id == DEFAULT_USER_ID).all()
        assert len(mine) == 1


def test_add_book_rejects_boolean_rating(client, monkeypatch):
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    _stub_fast(monkeypatch, {"title": "X", "contributors": [{"name": "Y", "role": "Author"}]})
    resp = client.post("/api/books", json={"title": "X", "author": "Y", "rating": True})
    assert resp.status_code == 422


def test_add_book_same_date_reports_already_logged(client, monkeypatch):
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    _stub_fast(
        monkeypatch,
        {"title": "Solaris", "contributors": [{"name": "Stanislaw Lem", "role": "Author"}], "genres": [], "moods": []},
    )
    payload = {"title": "Solaris", "author": "Stanislaw Lem", "format": "ebook", "date_completed": "2021-05-01"}

    first = client.post("/api/books", json=payload).json()
    second = client.post("/api/books", json=payload).json()

    assert first["already_logged"] is False and first["read_number"] == 1
    assert second["already_logged"] is True  # same work + same date = no new read-event
    assert second["enrichment_enqueued"] is False  # de-dup hit on the 2nd → no re-enqueue


def test_add_book_survives_enqueue_failure(client, monkeypatch):
    def _boom(wid):
        raise RuntimeError("cloud tasks down")

    monkeypatch.setattr(books_mod, "enqueue_enrichment", _boom)
    _stub_fast(
        monkeypatch,
        {"title": "Blindsight", "contributors": [{"name": "Peter Watts", "role": "Author"}], "genres": [], "moods": []},
    )

    resp = client.post("/api/books", json={"title": "Blindsight", "author": "Peter Watts"})
    assert resp.status_code == 200  # the book is saved even though enqueue raised
    assert resp.json()["enrichment_enqueued"] is False


def _seed_picked_work(db_url, *, title, author, status="Suggested", user_id=DEFAULT_USER_ID):
    """A catalog work with an Author link and one suggestion row for user_id."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        work = Work(title=title)
        s.add(work)
        s.flush()
        a = Author(name=author)
        s.add(a)
        s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        sug = Suggestions(work_id=work.id, user_id=user_id, status=status, justification="pitched")
        s.add(sug)
        s.flush()
        return work.id, sug.id


def test_add_book_resolves_active_pick(client, db_url, monkeypatch):
    # GH #130 invariant: a book in your history is never simultaneously an active pick.
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    work_id, sug_id = _seed_picked_work(db_url, title="The Fifth Season", author="N. K. Jemisin")
    _stub_fast(
        monkeypatch, {"title": "The Fifth Season", "contributors": [{"name": "N. K. Jemisin", "role": "Author"}]}
    )

    resp = client.post("/api/books", json={"title": "The Fifth Season", "author": "N. K. Jemisin", "format": "ebook"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["pick_resolved"] is True
    assert body["work_id"] == str(work_id)  # fast pass dedup'd onto the seeded work
    with DatabaseManager(db_url).get_session() as s:
        assert s.get(Suggestions, sug_id).status == "Read"  # pick resolved, not deleted
        mine = s.query(ReadingHistory).filter(ReadingHistory.user_id == DEFAULT_USER_ID).all()
        assert len(mine) == 1  # the read event was still written (assertion completeness)


def test_add_book_without_pick_reports_not_resolved(client, monkeypatch):
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    _stub_fast(monkeypatch, {"title": "Piranesi", "contributors": [{"name": "Susanna Clarke", "role": "Author"}]})

    resp = client.post("/api/books", json={"title": "Piranesi", "author": "Susanna Clarke"})
    assert resp.status_code == 200
    assert resp.json()["pick_resolved"] is False


def test_add_book_duplicate_still_resolves_pick(client, db_url, monkeypatch):
    # The already_logged early-return branch must ALSO resolve (re-adding a book
    # already in history still clears its stale pick).
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    work_id, sug_id = _seed_picked_work(db_url, title="Annihilation", author="Jeff VanderMeer")
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        edition = Edition(work_id=work_id, format="ebook")
        s.add(edition)
        s.flush()
        s.add(ReadingHistory(edition_id=edition.id, user_id=DEFAULT_USER_ID, date_completed=date(2021, 5, 1)))
        s.flush()
    _stub_fast(monkeypatch, {"title": "Annihilation", "contributors": [{"name": "Jeff VanderMeer", "role": "Author"}]})

    resp = client.post(
        "/api/books",
        json={"title": "Annihilation", "author": "Jeff VanderMeer", "format": "ebook", "date_completed": "2021-05-01"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["already_logged"] is True
    assert body["pick_resolved"] is True
    with manager.get_session() as s:
        assert s.get(Suggestions, sug_id).status == "Read"


def test_add_book_leaves_dismissed_pick_untouched(client, db_url, monkeypatch):
    # Resolution only touches 'Suggested' rows — it never rewrites resolved statuses.
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    _work_id, sug_id = _seed_picked_work(db_url, title="Uprooted", author="Naomi Novik", status="Dismissed")
    _stub_fast(monkeypatch, {"title": "Uprooted", "contributors": [{"name": "Naomi Novik", "role": "Author"}]})

    resp = client.post("/api/books", json={"title": "Uprooted", "author": "Naomi Novik"})

    assert resp.status_code == 200
    assert resp.json()["pick_resolved"] is False
    with DatabaseManager(db_url).get_session() as s:
        assert s.get(Suggestions, sug_id).status == "Dismissed"


def test_add_book_leaves_other_users_pick_untouched(client, db_url, monkeypatch):
    monkeypatch.setattr(books_mod, "enqueue_enrichment", lambda wid: True)
    other = uuid4()
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        s.add(User(id=other, email="other-pick@example.com"))
        s.flush()
    _work_id, sug_id = _seed_picked_work(db_url, title="Circe", author="Madeline Miller", user_id=other)
    _stub_fast(monkeypatch, {"title": "Circe", "contributors": [{"name": "Madeline Miller", "role": "Author"}]})

    resp = client.post("/api/books", json={"title": "Circe", "author": "Madeline Miller"})

    assert resp.status_code == 200
    assert resp.json()["pick_resolved"] is False
    with manager.get_session() as s:
        assert s.get(Suggestions, sug_id).status == "Suggested"  # the other user's pick survives

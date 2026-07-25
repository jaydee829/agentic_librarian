"""/api/history returns ONLY the authenticated user's read events (Lift 1, ADR-048)."""

from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import main as api_main
from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.core.user_context import DEFAULT_USER_ID
from agentic_librarian.db.models import Author as AuthorModel
from agentic_librarian.db.models import Edition, ReadingHistory, User, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration

FRIEND_ID = UUID("00000000-0000-4000-8000-000000000002")


@pytest.fixture()
def two_user_client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    with manager.get_session() as session:
        session.add(User(id=FRIEND_ID, email="friend@example.com"))
        author = AuthorModel(name="A. Uthor")
        work = Work(title="Shared Book", contributors=[WorkContributor(author=author, role="Author")])
        edition = Edition(work=work, format="ebook")
        session.add_all([author, work, edition])
        session.flush()
        session.add(ReadingHistory(edition_id=edition.id, user_id=DEFAULT_USER_ID, date_completed=date(2021, 1, 1)))
        session.add(ReadingHistory(edition_id=edition.id, user_id=FRIEND_ID, date_completed=date(2022, 2, 2)))
        session.flush()

    def _as(user_id, email):
        api_main.app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=user_id, email=email)
        return TestClient(api_main.app)

    yield _as
    api_main.app.dependency_overrides.pop(get_current_user, None)


def test_history_is_scoped_to_the_caller(two_user_client):
    mine = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com").get("/api/history").json()
    assert [h["date_completed"] for h in mine] == ["2021-01-01"]
    theirs = two_user_client(FRIEND_ID, "friend@example.com").get("/api/history").json()
    assert [h["date_completed"] for h in theirs] == ["2022-02-02"]


def test_history_pagination_correct_with_multi_contributor_work(two_user_client, db_url):
    # A work with 2 author-contributors multiplies the join rows; LIMIT must still count
    # ReadingHistory rows, not multiplied rows (the joinedload-vs-selectinload question).
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        a1 = AuthorModel(name="First Author")
        a2 = AuthorModel(name="Second Author")
        work = Work(
            title="Co-Authored Book",
            contributors=[WorkContributor(author=a1, role="Author"), WorkContributor(author=a2, role="Author")],
        )
        edition = Edition(work=work, format="ebook")
        session.add_all([a1, a2, work, edition])
        session.flush()
        for d in (date(2023, 3, 3), date(2024, 4, 4), date(2025, 5, 5)):
            session.add(ReadingHistory(edition_id=edition.id, user_id=DEFAULT_USER_ID, date_completed=d))
        session.flush()

    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    page = client.get("/api/history?limit=3&offset=0").json()
    # 3 newest reads (all on the co-authored work) must come back as 3 DISTINCT history rows,
    # each listing both authors — not collapsed by row multiplication.
    assert [h["date_completed"] for h in page] == ["2025-05-05", "2024-04-04", "2023-03-03"]
    assert all(set(h["authors"]) == {"First Author", "Second Author"} for h in page)
    # The boundary Gemini's review worried about: limit < read-count must return exactly `limit`
    # distinct ReadingHistory rows (not fewer because the join multiplied by 2 contributors).
    page2 = client.get("/api/history?limit=2&offset=0").json()
    assert [h["date_completed"] for h in page2] == ["2025-05-05", "2024-04-04"]


def test_history_paginates_newest_first(two_user_client, db_url):
    # Add three more reads for DEFAULT_USER on the shared edition, distinct dates.
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        edition_id = session.query(Edition).first().id
        for d in (date(2023, 3, 3), date(2024, 4, 4), date(2025, 5, 5)):
            session.add(ReadingHistory(edition_id=edition_id, user_id=DEFAULT_USER_ID, date_completed=d))
        session.flush()

    c = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    page1 = c.get("/api/history?limit=2&offset=0").json()
    page2 = c.get("/api/history?limit=2&offset=2").json()
    assert [h["date_completed"] for h in page1] == ["2025-05-05", "2024-04-04"]  # newest first
    assert [h["date_completed"] for h in page2] == ["2023-03-03", "2021-01-01"]  # next page, no overlap


def test_history_includes_genre_and_top_three_tropes(two_user_client, db_url):
    from agentic_librarian.db.models import Trope, WorkTrope

    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        author = AuthorModel(name="Trope Author")
        work = Work(
            title="Tropey Book",
            genres=["Fantasy", "Adventure"],
            contributors=[WorkContributor(author=author, role="Author")],
        )
        edition = Edition(work=work, format="ebook")
        session.add_all([author, work, edition])
        session.flush()
        for name, score in [("Heist", 0.90), ("Found Family", 0.95), ("Antihero", 0.99), ("Low Score", 0.10)]:
            trope = Trope(name=name)
            session.add(trope)
            session.flush()
            session.add(WorkTrope(work_id=work.id, trope_id=trope.id, relevance_score=score))
        session.add(ReadingHistory(edition_id=edition.id, user_id=DEFAULT_USER_ID, date_completed=date(2026, 1, 2)))
        session.flush()

    rows = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com").get("/api/history").json()
    row = next(r for r in rows if r["title"] == "Tropey Book")
    assert row["genre"] == "Fantasy"
    assert row["tropes"] == ["Antihero", "Found Family", "Heist"]  # top 3, score desc; "Low Score" dropped


def test_history_top_tropes_prefer_justified_over_slug_fallbacks(two_user_client, db_url):
    """#70 display fix: slug fallbacks carry default relevance 1.0 with NULL justification;
    real scout tropes (justified, lower relevance) must still win the top-3."""
    from agentic_librarian.db.models import Trope, WorkTrope

    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        author = AuthorModel(name="Slugged Author")
        work = Work(
            title="Slugged Book",
            genres=["Fantasy"],
            moods=["Dark", "Sad"],
            contributors=[WorkContributor(author=author, role="Author")],
        )
        edition = Edition(work=work, format="ebook")
        session.add_all([author, work, edition])
        session.flush()
        links = [
            ("Dark", 1.0, None),  # slug fallbacks: NULL justification, default relevance
            ("Sad", 1.0, None),
            ("Fantasy", 1.0, None),
            ("Heist Gone Wrong", 0.70, "the vault job unravels"),  # real scout tropes
            ("Reluctant Mentor", 0.90, "trains the thief against his will"),
        ]
        for name, score, just in links:
            trope = Trope(name=name)
            session.add(trope)
            session.flush()
            session.add(WorkTrope(work_id=work.id, trope_id=trope.id, relevance_score=score, justification=just))
        session.add(ReadingHistory(edition_id=edition.id, user_id=DEFAULT_USER_ID, date_completed=date(2026, 1, 3)))
        session.flush()

    rows = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com").get("/api/history").json()
    row = next(r for r in rows if r["title"] == "Slugged Book")
    # Both justified tropes lead (relevance desc) despite lower scores; ONE slug fills slot 3.
    assert row["tropes"] == ["Reluctant Mentor", "Heist Gone Wrong", "Dark"]


def test_history_no_tropes_returns_empty_list(two_user_client):
    rows = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com").get("/api/history").json()
    shared = next(r for r in rows if r["title"] == "Shared Book")
    assert shared["tropes"] == []
    assert shared["genre"] is None


def test_delete_history_removes_only_callers_row(two_user_client):
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry_id = client.get("/api/history").json()[0]["id"]
    assert client.delete(f"/api/history/{entry_id}").status_code == 200
    assert entry_id not in [h["id"] for h in client.get("/api/history").json()]


def test_delete_history_other_users_row_is_404(two_user_client, db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        friend_id = str(session.query(ReadingHistory).filter(ReadingHistory.user_id == FRIEND_ID).first().id)
    assert (
        two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com").delete(f"/api/history/{friend_id}").status_code == 404
    )
    with manager.get_session() as session:
        assert session.get(ReadingHistory, UUID(friend_id)) is not None


def test_patch_history_updates_rating_date_notes(two_user_client):
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry_id = client.get("/api/history").json()[0]["id"]
    resp = client.patch(
        f"/api/history/{entry_id}", json={"rating": 5, "date_completed": "2020-12-31", "notes": "loved it"}
    )
    assert resp.status_code == 200
    assert resp.json()["rating"] == 5 and resp.json()["date_completed"] == "2020-12-31"
    row = next(h for h in client.get("/api/history").json() if h["id"] == entry_id)
    assert row["rating"] == 5 and row["date_completed"] == "2020-12-31" and row["notes"] == "loved it"


def test_patch_history_rejects_bad_input_and_other_users(two_user_client, db_url):
    from datetime import timedelta

    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry_id = client.get("/api/history").json()[0]["id"]
    assert client.patch(f"/api/history/{entry_id}", json={"rating": True}).status_code == 422
    assert client.patch(f"/api/history/{entry_id}", json={"rating": 9}).status_code == 422
    future = (date.today() + timedelta(days=3)).isoformat()
    assert client.patch(f"/api/history/{entry_id}", json={"date_completed": future}).status_code == 422
    assert client.patch(f"/api/history/{entry_id}", json={"date_completed": None}).status_code == 422
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        friend_id = str(session.query(ReadingHistory).filter(ReadingHistory.user_id == FRIEND_ID).first().id)
    assert client.patch(f"/api/history/{friend_id}", json={"rating": 3}).status_code == 404


def _db(db_url):
    return DatabaseManager(db_url)


def _my_entry(client, fmt="ebook"):
    """The fixture's seeded read, selected by title AND format: several tests seed a second
    'Shared Book' read at the SAME date_completed, and /api/history tie-breaks equal dates by
    ReadingHistory.id — a random UUID — so title alone is a coin flip (the CI-only flake:
    the collision test grabbed the audiobook row and its PATCH became a same-format 200)."""
    return next(h for h in client.get("/api/history").json() if h["title"] == "Shared Book" and h["format"] == fmt)


def test_patch_format_creates_sibling_edition_and_repoints(two_user_client, db_url):
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    # Seed rating + notes first so the repoint has non-null scalars to preserve (#96 lens).
    assert client.patch(f"/api/history/{entry['id']}", json={"rating": 4, "notes": "keep me"}).status_code == 200
    resp = client.patch(f"/api/history/{entry['id']}", json={"format": "audiobook"})
    assert resp.status_code == 200
    assert resp.json()["format"] == "audiobook"
    assert resp.json()["enrichment_enqueued"] is False  # no Cloud Tasks in tests
    with _db(db_url).get_session() as s:
        row = s.get(ReadingHistory, UUID(entry["id"]))
        work_id = row.edition.work_id
        formats = {e.format for e in s.query(Edition).filter(Edition.work_id == work_id)}
        assert row.edition.format == "audiobook"
        assert formats == {"ebook", "audiobook"}  # old edition intact (shared catalog object)
        # Assertion completeness (#96 lesson): untouched fields survive the repoint; the
        # friend's row on the ORIGINAL edition is untouched.
        assert row.date_completed == date(2021, 1, 1)
        assert row.user_rating == 4
        assert row.user_notes == "keep me"
        friend = s.query(ReadingHistory).filter(ReadingHistory.user_id == FRIEND_ID).one()
        assert friend.edition.format == "ebook"


def test_patch_combined_date_and_format_valid_at_final_state_succeeds(two_user_client, db_url):
    """C1 sibling: a combined date+format edit valid at the FINAL (audiobook, 2020-06-06)
    but colliding at the INTERMEDIATE (ebook, 2020-06-06) must 200, not 500. Pre-Fix-1 the
    autoflush of row.date_completed during the dup pre-check tripped the unique index outside
    the flush try/except."""
    with _db(db_url).get_session() as s:
        edition_id = s.query(Edition).filter(Edition.format == "ebook").first().id
        # Occupy the intermediate (ebook, 2020-06-06) pair (mirrors the date-collision setup).
        s.add(ReadingHistory(edition_id=edition_id, user_id=DEFAULT_USER_ID, date_completed=date(2020, 6, 6)))
        s.flush()
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)  # the 2021-01-01 read
    resp = client.patch(f"/api/history/{entry['id']}", json={"date_completed": "2020-06-06", "format": "audiobook"})
    assert resp.status_code == 200
    assert resp.json()["format"] == "audiobook"
    assert resp.json()["date_completed"] == "2020-06-06"


def test_patch_format_reuses_existing_sibling_edition(two_user_client, db_url):
    with _db(db_url).get_session() as s:
        work_id = s.query(Edition).filter(Edition.format == "ebook").first().work_id
        s.add(Edition(work_id=work_id, format="audiobook", isbn_13="9781111111111"))
        s.flush()
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    assert client.patch(f"/api/history/{entry['id']}", json={"format": "audiobook"}).status_code == 200
    with _db(db_url).get_session() as s:
        assert s.query(Edition).filter(Edition.work_id == work_id).count() == 2  # reused, not duplicated
        row = s.get(ReadingHistory, UUID(entry["id"]))
        assert row.edition.isbn_13 == "9781111111111"


def test_patch_same_format_is_a_noop(two_user_client, db_url):
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    assert client.patch(f"/api/history/{entry['id']}", json={"format": "ebook"}).status_code == 200
    with _db(db_url).get_session() as s:
        work_id = s.get(ReadingHistory, UUID(entry["id"])).edition.work_id
        assert s.query(Edition).filter(Edition.work_id == work_id).count() == 1  # nothing minted


def test_patch_format_collision_is_409_and_rolls_back(two_user_client, db_url):
    with _db(db_url).get_session() as s:
        ebook = s.query(Edition).filter(Edition.format == "ebook").first()
        audio = Edition(work_id=ebook.work_id, format="audiobook")
        s.add(audio)
        s.flush()
        # Same user, same work, SAME date — but as an audiobook (an import-duplicate shape).
        s.add(ReadingHistory(edition_id=audio.id, user_id=DEFAULT_USER_ID, date_completed=date(2021, 1, 1)))
        s.flush()
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    resp = client.patch(f"/api/history/{entry['id']}", json={"format": "audiobook", "notes": "should not stick"})
    assert resp.status_code == 409
    assert "already logged" in resp.json()["detail"]
    with _db(db_url).get_session() as s:
        row = s.get(ReadingHistory, UUID(entry["id"]))
        assert row.edition.format == "ebook"  # repoint rolled back
        assert row.user_notes != "should not stick"  # the whole PATCH rolled back, not just format


def test_patch_date_collision_is_409_not_500(two_user_client, db_url):
    """Pre-existing hole the format work closes: date-only edits could always trip
    uq_reading_history_user_edition_date; they must now 409 cleanly."""
    with _db(db_url).get_session() as s:
        edition_id = s.query(Edition).filter(Edition.format == "ebook").first().id
        s.add(ReadingHistory(edition_id=edition_id, user_id=DEFAULT_USER_ID, date_completed=date(2020, 6, 6)))
        s.flush()
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)  # the 2021-01-01 read
    assert client.patch(f"/api/history/{entry['id']}", json={"date_completed": "2020-06-06"}).status_code == 409


def test_patch_format_enqueues_completion_for_new_audiobook(two_user_client, monkeypatch):
    calls = []
    monkeypatch.setattr(api_main, "enqueue_edition_completion", lambda wid, fmt: calls.append((wid, fmt)) or True)
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    resp = client.patch(f"/api/history/{entry['id']}", json={"format": "audiobook"})
    assert resp.status_code == 200
    assert resp.json()["enrichment_enqueued"] is True
    assert len(calls) == 1 and calls[0][1] == "audiobook"


def test_patch_format_enqueue_failure_never_fails_the_edit(two_user_client, monkeypatch):
    def _boom(wid, fmt):
        raise RuntimeError("tasks down")

    monkeypatch.setattr(api_main, "enqueue_edition_completion", _boom)
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    resp = client.patch(f"/api/history/{entry['id']}", json={"format": "audiobook"})
    assert resp.status_code == 200
    assert resp.json()["format"] == "audiobook"  # the edit stuck
    assert resp.json()["enrichment_enqueued"] is False


def test_patch_format_skips_enqueue_when_edition_complete(two_user_client, db_url, monkeypatch):
    from agentic_librarian.db.models import Narrator

    with _db(db_url).get_session() as s:
        work_id = s.query(Edition).filter(Edition.format == "ebook").first().work_id
        done = Edition(work_id=work_id, format="audiobook", isbn_13="9782222222222")
        done.narrators = [Narrator(name="Ray Porter")]
        s.add(done)
        s.flush()
    calls = []
    monkeypatch.setattr(api_main, "enqueue_edition_completion", lambda wid, fmt: calls.append(1) or True)
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    resp = client.patch(f"/api/history/{entry['id']}", json={"format": "audiobook"})
    assert resp.status_code == 200
    assert resp.json()["enrichment_enqueued"] is False
    assert calls == []  # already complete — no paid pass


def test_patch_notes_only_never_enqueues(two_user_client, monkeypatch):
    calls = []
    monkeypatch.setattr(api_main, "enqueue_edition_completion", lambda wid, fmt: calls.append(1) or True)
    client = two_user_client(DEFAULT_USER_ID, "jaydee829@gmail.com")
    entry = _my_entry(client)
    resp = client.patch(f"/api/history/{entry['id']}", json={"notes": "just notes"})
    assert resp.status_code == 200
    assert resp.json()["enrichment_enqueued"] is False
    assert calls == []

from datetime import date, timedelta

import pytest

from agentic_librarian.core.user_context import DEFAULT_USER_ID, as_user
from agentic_librarian.db.models import Author, Edition, ReadingHistory, Work, WorkContributor, WorkTrope
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


class _FakeManager:
    def __init__(self, result):
        self._result = result

    def enrich(self, title, author, format="Paperback", **kwargs):
        return self._result


def _seed_work(manager, *, title, author, fmt="ebook"):
    from agentic_librarian.db.models import Author

    with manager.get_session() as s:
        work = Work(title=title)
        s.add(work)
        s.flush()
        a = Author(name=author)
        s.add(a)
        s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        s.add(Edition(work_id=work.id, format=fmt))
        s.flush()
        return work.id


def test_enrich_deep_updates_same_work_idempotently(db_url, monkeypatch):
    from agentic_librarian.enrichment import two_phase
    from agentic_librarian.scouts import style_manager, trope_manager

    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    monkeypatch.setattr(trope_manager, "get_cached_embedding", lambda *a, **k: [0.1] * 1536)
    monkeypatch.setattr(style_manager, "get_cached_embedding", lambda *a, **k: [0.1] * 1536)

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    deep = {
        "enriched_tropes": [{"trope_name": "Found Family", "relevance_score": 0.9}],
        "narrator_names": [],
        "contributors": [
            {"name": "Frank Herbert", "role": "Author"},
            {"name": "Kevin J. Anderson", "role": "Author"},  # deep pass discovers a co-author
        ],
    }
    monkeypatch.setattr(two_phase, "create_deep_scout_manager", lambda *a, **k: _FakeManager(deep))

    work_id = _seed_work(manager, title="Dune", author="Frank Herbert")

    assert two_phase.enrich_deep(work_id) == "done"
    assert two_phase.enrich_deep(work_id) == "done"  # retry-safe (Cloud Tasks redelivery)

    with manager.get_session() as s:
        links = s.query(WorkTrope).filter_by(work_id=work_id).all()
        assert len(links) == 1  # single trope link despite two runs
        # GH #97: the write session stamps deep_enriched_at on a successful ("done") persist.
        work = s.query(Work).filter_by(title="Dune").one()
        assert work.deep_enriched_at is not None

        # GH #96: a co-author discovered by the deep pass must be LINKED on the existing work
        # (previously the WorkContributor dangled and SQLAlchemy 2.0 silently dropped it), and
        # no orphan Author rows may accumulate.
        work = s.query(Work).filter_by(title="Dune").one()
        roles = {(c.author.name, c.role) for c in work.contributors}
        assert ("Kevin J. Anderson", "Author") in roles  # the co-author the fake deep scout returns
        orphans = (
            s.query(Author)
            .outerjoin(WorkContributor, WorkContributor.author_id == Author.id)
            .filter(WorkContributor.author_id.is_(None))
            .count()
        )
        assert orphans == 0


def test_enrich_deep_returns_missing_not_done_when_nothing_was_persisted(db_url, monkeypatch):
    """Final-review Minor 7 (honesty): if the scouts return a row but persist_enriched_work
    ends up with NOTHING to attach it to (raw_contributors empties out after the malformed-name
    filter — e.g. the deep scout's only contributor entry has no usable name), nothing is
    persisted and deep_enriched_at is never stamped. enrich_deep must report "missing", not lie
    and say "done" — the caller (api/internal.py) maps "missing" to a non-retryable 404, while
    "done" would tell Cloud Tasks (and the operator) a pass succeeded when it did nothing at
    all."""
    from agentic_librarian.enrichment import two_phase

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    # A deep-scout result with contributors that all fail the malformed-name filter (persist.py's
    # raw_contributors comprehension drops entries with no usable string name) — this is the
    # concrete way persist_enriched_work returns None without touching the DB at all, standing in
    # for "the work's identity vanished mid-pass and nothing could be attached."
    deep = {
        "enriched_tropes": [],
        "narrator_names": [],
        "contributors": [{"name": None, "role": "Author"}],
    }
    monkeypatch.setattr(two_phase, "create_deep_scout_manager", lambda *a, **k: _FakeManager(deep))

    work_id = _seed_work(manager, title="Vanishing Work", author="Some Author")

    assert two_phase.enrich_deep(work_id) == "missing"

    with manager.get_session() as s:
        work = s.get(Work, work_id)
        # nothing was persisted or stamped — the work is exactly as it was seeded
        assert work.deep_enriched_at is None


def _seed_duplicate_identity_pair(manager, *, title, author, fmt="ebook"):
    """Seed TWO Work rows sharing the exact same (title, author) identity — a raw duplicate
    pair that predates any unique constraint on works (only Author.name is unique). This is
    the structural shape that makes persist_enriched_work's exact-match Work lookup
    (`Work.title == ... AND Author.name == ...`) resolve to whichever row it created FIRST
    (the .first() winner) regardless of which row enrich_deep was invoked with — the live
    #141 shape (prod: sweep enqueued 9e9cfc45, the stamp+tropes landed on twin a5e56605).
    Returns (twin_id, invoked_id) — twin seeded first so it wins the lookup."""
    from agentic_librarian.db.models import Author

    with manager.get_session() as s:
        a = Author(name=author)
        s.add(a)
        s.flush()

        twin = Work(title=title)
        s.add(twin)
        s.flush()
        s.add(WorkContributor(work_id=twin.id, author_id=a.id, role="Author"))
        s.add(Edition(work_id=twin.id, format=fmt))

        invoked = Work(title=title)
        s.add(invoked)
        s.flush()
        s.add(WorkContributor(work_id=invoked.id, author_id=a.id, role="Author"))
        s.add(Edition(work_id=invoked.id, format=fmt))

        s.flush()
        return twin.id, invoked.id


def test_enrich_deep_redirect_pins_invoked_work_and_records_detection(db_url, monkeypatch):
    """GH #141: when the write session's persist re-check resolves a DIFFERENT existing
    work (the twin), enrich_deep must NOT undo the twin's write. It must: leave the tropes
    on the twin, stamp the INVOKED row (not the twin), record a detected_duplicates row,
    and return "redirected"."""
    from agentic_librarian.db.models import DetectedDuplicate
    from agentic_librarian.enrichment import two_phase
    from agentic_librarian.scouts import style_manager, trope_manager

    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    monkeypatch.setattr(trope_manager, "get_cached_embedding", lambda *a, **k: [0.1] * 1536)
    monkeypatch.setattr(style_manager, "get_cached_embedding", lambda *a, **k: [0.1] * 1536)

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    deep = {
        "enriched_tropes": [{"trope_name": "Found Family", "relevance_score": 0.9}],
        "narrator_names": [],
        "contributors": [{"name": "Casualfarmer, CasualFarmer", "role": "Author"}],
    }
    monkeypatch.setattr(two_phase, "create_deep_scout_manager", lambda *a, **k: _FakeManager(deep))

    twin_id, invoked_id = _seed_duplicate_identity_pair(
        manager, title="Beware of Chicken", author="Casualfarmer, CasualFarmer"
    )

    assert two_phase.enrich_deep(invoked_id) == "redirected"

    with manager.get_session() as s:
        invoked = s.get(Work, invoked_id)

        # the twin got the tropes (persist legitimately landed there — same book, not undone)
        twin_links = s.query(WorkTrope).filter_by(work_id=twin_id).all()
        assert len(twin_links) == 1

        # the invoked row is stamped and gained NO tropes of its own
        assert invoked.deep_enriched_at is not None
        invoked_links = s.query(WorkTrope).filter_by(work_id=invoked_id).all()
        assert len(invoked_links) == 0

        detections = s.query(DetectedDuplicate).filter_by(work_id_a=invoked_id, work_id_b=twin_id).all()
        assert len(detections) == 1
        assert detections[0].source == "deep_pass_redirect"


class _DeletingFakeManager:
    """Same scout-seam shape as _FakeManager, but its enrich() call also deletes the
    invoked work -- standing in for "the invoked row was deleted by something else while
    the slow scouts ran with no session held" (the reviewer's repro for the mid-pass
    deletion). enrich_deep's read session that captured title/author is already closed by
    the time this runs, matching the real no-session-during-scouts window (#94)."""

    def __init__(self, result, *, manager, invoked_id):
        self._result = result
        self._manager = manager
        self._invoked_id = invoked_id

    def enrich(self, title, author, format="Paperback", **kwargs):
        from agentic_librarian.db.models import Edition as _Edition
        from agentic_librarian.db.models import Work as _Work
        from agentic_librarian.db.models import WorkContributor as _WorkContributor

        with self._manager.get_session() as s:
            work = s.get(_Work, self._invoked_id)
            if work is not None:
                # Hard-delete the invoked row's dependents first (editions.work_id and
                # work_contributors.work_id are NOT NULL with no ORM delete-orphan cascade
                # configured on Work.editions/contributors -- session.delete(work) alone would
                # try to null those FKs instead of removing the rows, which is not what a real
                # mid-pass deletion of the work would leave behind).
                s.query(_Edition).filter_by(work_id=self._invoked_id).delete()
                s.query(_WorkContributor).filter_by(work_id=self._invoked_id).delete()
                s.delete(work)
        return self._result


def test_enrich_deep_redirect_missing_invoked_row_leaves_twin_data_intact_no_detection(db_url, monkeypatch):
    """Reviewer finding (I1): the invoked work is deleted mid-pass (between the read session
    that captured its identity and the write session that would stamp/insert against it).
    The write session's persist re-check still resolves the twin and legitimately writes the
    tropes there. Before the fix, the detected_duplicates INSERT (work_id_a=invoked_id) ran
    BEFORE the existence check and FK-violated for real against Postgres, rolling back the
    WHOLE write-session transaction -- destroying the twin's just-persisted tropes along with
    it. This test pins the real behavior: "missing" is returned, the twin KEEPS its
    legitimately-persisted pass data, and no detected_duplicates row exists (the insert must
    never have been attempted, or it would have aborted the transaction)."""
    from agentic_librarian.db.models import DetectedDuplicate
    from agentic_librarian.enrichment import two_phase
    from agentic_librarian.scouts import style_manager, trope_manager

    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    monkeypatch.setattr(trope_manager, "get_cached_embedding", lambda *a, **k: [0.1] * 1536)
    monkeypatch.setattr(style_manager, "get_cached_embedding", lambda *a, **k: [0.1] * 1536)

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    deep = {
        "enriched_tropes": [{"trope_name": "Found Family", "relevance_score": 0.9}],
        "narrator_names": [],
        "contributors": [{"name": "Casualfarmer, CasualFarmer", "role": "Author"}],
    }

    twin_id, invoked_id = _seed_duplicate_identity_pair(
        manager, title="Beware of Chicken", author="Casualfarmer, CasualFarmer"
    )

    monkeypatch.setattr(
        two_phase,
        "create_deep_scout_manager",
        lambda *a, **k: _DeletingFakeManager(deep, manager=manager, invoked_id=invoked_id),
    )

    assert two_phase.enrich_deep(invoked_id) == "missing"

    with manager.get_session() as s:
        # the invoked row is really gone
        assert s.get(Work, invoked_id) is None

        # the twin's persist was NOT rolled back -- it keeps its legitimately-written tropes
        twin_links = s.query(WorkTrope).filter_by(work_id=twin_id).all()
        assert len(twin_links) == 1

        # no detection row: the insert referencing the vanished invoked id must never have
        # landed (the pre-fix code would have FK-violated attempting exactly this insert)
        detections = s.query(DetectedDuplicate).filter_by(work_id_b=twin_id).all()
        assert len(detections) == 0


def test_enrich_deep_redirect_is_idempotent_on_redelivery(db_url, monkeypatch):
    """Cloud Tasks may redeliver: re-running enrich_deep on the same invoked row again must
    stay "redirected" and must NOT pile up a second detected_duplicates row for the same
    (work_id_a, work_id_b) pair (ON CONFLICT DO NOTHING on the composite PK)."""
    from agentic_librarian.db.models import DetectedDuplicate
    from agentic_librarian.enrichment import two_phase
    from agentic_librarian.scouts import style_manager, trope_manager

    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    monkeypatch.setattr(trope_manager, "get_cached_embedding", lambda *a, **k: [0.1] * 1536)
    monkeypatch.setattr(style_manager, "get_cached_embedding", lambda *a, **k: [0.1] * 1536)

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    deep = {
        "enriched_tropes": [{"trope_name": "Found Family", "relevance_score": 0.9}],
        "narrator_names": [],
        "contributors": [{"name": "Casualfarmer, CasualFarmer", "role": "Author"}],
    }
    monkeypatch.setattr(two_phase, "create_deep_scout_manager", lambda *a, **k: _FakeManager(deep))

    twin_id, invoked_id = _seed_duplicate_identity_pair(
        manager, title="Beware of Chicken", author="Casualfarmer, CasualFarmer"
    )

    assert two_phase.enrich_deep(invoked_id) == "redirected"
    assert two_phase.enrich_deep(invoked_id) == "redirected"  # redelivery-safe

    with manager.get_session() as s:
        detections = s.query(DetectedDuplicate).filter_by(work_id_a=invoked_id, work_id_b=twin_id).all()
        assert len(detections) == 1  # no pile-up despite two redirect passes


def test_enrich_deep_returns_false_for_unknown_work(db_url, monkeypatch):
    from uuid import uuid4

    from agentic_librarian.enrichment import two_phase

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    assert two_phase.enrich_deep(uuid4()) == "missing"


def test_enrich_deep_empty_pass_still_stamps_deep_enriched_at(db_url, monkeypatch):
    """GH #97: scouts-found-nothing is NOT a failure — it's a completed pass. The timestamp
    means "the deep pass completed" (including confirmed-empty), so the requeue sweep doesn't
    treat this work as never-attempted."""
    from agentic_librarian.enrichment import two_phase

    monkeypatch.setattr(two_phase, "create_deep_scout_manager", lambda *a, **k: _FakeManager(None))

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    work_id = _seed_work(manager, title="Obscure Title", author="Obscure Author")

    assert two_phase.enrich_deep(work_id) == "empty"

    with manager.get_session() as s:
        work = s.get(Work, work_id)
        assert work.deep_enriched_at is not None


def test_add_read_event_logs_and_dedups_rereads(db_url, monkeypatch):
    from agentic_librarian.enrichment import two_phase

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    work_id = _seed_work(manager, title="Hyperion", author="Dan Simmons")
    today = date.today()
    earlier = today - timedelta(days=30)

    with as_user(DEFAULT_USER_ID):
        first = two_phase.add_read_event(work_id, completed=today, rating=5, notes=None, fmt="ebook")
        dupe = two_phase.add_read_event(work_id, completed=today, rating=5, notes=None, fmt="ebook")
        reread = two_phase.add_read_event(work_id, completed=earlier, rating=4, notes=None, fmt="ebook")

    assert first == {"read_number": 1, "already_logged": False, "pick_resolved": False}
    assert dupe == {"read_number": 1, "already_logged": True, "pick_resolved": False}
    assert reread == {"read_number": 2, "already_logged": False, "pick_resolved": False}
    with manager.get_session() as s:
        rows = s.query(ReadingHistory).join(Edition).filter(Edition.work_id == work_id).all()
        assert len(rows) == 2

"""Per-row import worker: de-dup, shallow miss, not_found, redelivery, suggestion routing,
re-import idempotency (Spec 2026-06-18)."""

from datetime import date

import pytest

from agentic_librarian.core.user_context import DEFAULT_USER_ID
from agentic_librarian.db.models import (
    Author,
    Edition,
    ImportJob,
    ImportRow,
    ReadingHistory,
    Suggestions,
    Work,
    WorkContributor,
)
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.enrichment import two_phase
from agentic_librarian.imports import worker

pytestmark = pytest.mark.db_integration


@pytest.fixture()
def wired(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(worker, "db_manager", manager)
    two_phase.set_db_manager(manager)
    # Never enqueue real Cloud Tasks from the worker in tests.
    monkeypatch.setattr(worker, "enqueue_enrichment", lambda work_id, user_id=None: True)
    return manager


def _seed_work(manager, title="Dune", author="Frank Herbert"):
    with manager.get_session() as s:
        a = Author(name=author)
        w = Work(title=title, contributors=[WorkContributor(author=a, role="Author")])
        e = Edition(work=w, format="ebook")
        s.add_all([a, w, e])
        s.flush()
        return w.id


def _make_row(manager, **kw):
    with manager.get_session() as s:
        job = ImportJob(user_id=DEFAULT_USER_ID, source="goodreads", total_rows=1)
        s.add(job)
        s.flush()
        defaults = {
            "import_job_id": job.id,
            "user_id": DEFAULT_USER_ID,
            "raw_title": "Dune",
            "raw_author": "Frank Herbert",
            "raw_format": "ebook",
            "raw_date": "2024-01-01",
            "date_completed": date(2024, 1, 1),
            "destination": "history",
            "shelf": "read",
            "status": "pending",
        }
        defaults.update(kw)
        row = ImportRow(**defaults)
        s.add(row)
        s.flush()
        return row.id


def test_dedup_links_existing_catalog_work_without_scouts(wired, monkeypatch):
    _seed_work(wired)
    # If a scout were called, this would blow up — proving de-dup short-circuits.
    monkeypatch.setattr(two_phase, "_run_scouts", lambda *a, **k: pytest.fail("scouts ran on a de-dup hit"))
    row_id = _make_row(wired)

    assert worker.process_import_row(row_id) == "done"
    with wired.get_session() as s:
        row = s.get(ImportRow, row_id)
        assert row.status == "done"
        assert row.outcome == "linked"
        assert s.query(ReadingHistory).filter_by(user_id=DEFAULT_USER_ID).count() == 1


def test_miss_creates_work_and_enqueues_deep(wired, monkeypatch):
    # Fake the fast scouts so a miss resolves to a created Work deterministically.
    monkeypatch.setattr(two_phase, "enrich_fast", lambda t, a, f="ebook": (_seed_work(wired, t, a), True))
    enq = {"n": 0, "user_id": None}

    def _fake_enqueue(work_id, user_id=None):
        enq["n"] += 1
        enq["user_id"] = user_id
        return True

    monkeypatch.setattr(worker, "enqueue_enrichment", _fake_enqueue)
    row_id = _make_row(wired, raw_title="New Title", raw_author="New Author")

    assert worker.process_import_row(row_id) == "done"
    with wired.get_session() as s:
        assert s.get(ImportRow, row_id).outcome == "created"
    assert enq["n"] == 1
    assert enq["user_id"] == str(DEFAULT_USER_ID)  # attributed to the row's user (#100)


def test_not_found_marks_failed_and_does_not_retry(wired, monkeypatch):
    monkeypatch.setattr(two_phase, "enrich_fast", lambda t, a, f="ebook": None)
    row_id = _make_row(wired, raw_title="Ghost", raw_author="Nobody")

    assert worker.process_import_row(row_id) == "not_found"
    with wired.get_session() as s:
        row = s.get(ImportRow, row_id)
        assert row.status == "failed"
        assert row.outcome == "not_found"


def test_redelivery_of_done_row_is_a_noop(wired):
    _seed_work(wired)
    row_id = _make_row(wired)
    worker.process_import_row(row_id)
    # Second delivery must not add a second history row.
    assert worker.process_import_row(row_id) == "done"
    with wired.get_session() as s:
        assert s.query(ReadingHistory).filter_by(user_id=DEFAULT_USER_ID).count() == 1


def test_suggestion_routing_writes_suggestion_with_context(wired):
    work_id = _seed_work(wired, "Wishlist Book", "W Author")  # de-dup hit resolves the work_id
    row_id = _make_row(
        wired,
        raw_title="Wishlist Book",
        raw_author="W Author",
        destination="suggestion",
        shelf="to-read",
        date_completed=None,
        raw_date="",
    )
    assert worker.process_import_row(row_id) == "done"
    with wired.get_session() as s:
        sug = s.query(Suggestions).filter_by(user_id=DEFAULT_USER_ID).one()
        assert sug.work_id == work_id
        assert sug.status == "Suggested"
        assert sug.context == "imported:to-read"


def test_reimport_does_not_duplicate(wired):
    _seed_work(wired)
    r1 = _make_row(wired)
    r2 = _make_row(wired)  # same book + date, a re-import
    worker.process_import_row(r1)
    worker.process_import_row(r2)
    with wired.get_session() as s:
        assert s.query(ReadingHistory).filter_by(user_id=DEFAULT_USER_ID).count() == 1
        assert s.get(ImportRow, r2).outcome == "duplicate"

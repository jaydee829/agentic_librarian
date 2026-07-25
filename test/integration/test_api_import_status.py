"""Derived progress + retry, scoped to the owner (Spec 2026-06-18, ADR-048)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_librarian.api import imports as imports_mod
from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.api.imports import router
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.models import ImportJob, ImportRow
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


@pytest.fixture()
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(imports_mod, "db_manager", manager)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL)
    return TestClient(app), manager


def _seed_job(manager, statuses):
    with manager.get_session() as s:
        job = ImportJob(user_id=DEFAULT_USER_ID, source="goodreads", total_rows=len(statuses))
        s.add(job)
        s.flush()
        for st in statuses:
            s.add(
                ImportRow(
                    import_job_id=job.id,
                    user_id=DEFAULT_USER_ID,
                    destination="history",
                    status=st,
                    outcome=("linked" if st == "done" else None),
                    date_completed=date(2024, 1, 1),
                )
            )
        s.flush()
        return job.id


def _seed_row(manager, status, minutes_old):
    with manager.get_session() as s:
        job = ImportJob(user_id=DEFAULT_USER_ID, source="goodreads", total_rows=1)
        s.add(job)
        s.flush()
        s.add(
            ImportRow(
                import_job_id=job.id,
                user_id=DEFAULT_USER_ID,
                destination="history",
                status=status,
                updated_at=datetime.now(UTC) - timedelta(minutes=minutes_old),
            )
        )
        s.flush()
        return job.id


def test_progress_is_derived_from_rows(client):
    c, manager = client
    job_id = _seed_job(manager, ["done", "done", "failed", "pending"])
    body = c.get(f"/api/import/{job_id}").json()
    assert body["total_rows"] == 4
    assert body["counts"]["done"] == 2
    assert body["counts"]["failed"] == 1
    assert body["counts"]["pending"] == 1
    assert body["complete"] is False  # a pending row remains


def test_retry_re_enqueues_failed_rows(client, monkeypatch):
    c, manager = client
    job_id = _seed_job(manager, ["failed", "done"])
    enq = []
    monkeypatch.setattr(imports_mod, "enqueue_import_row", lambda rid: enq.append(rid) or True)
    r = c.post(f"/api/import/{job_id}/retry")
    assert r.status_code == 200
    assert len(enq) == 1  # only the failed row
    body = c.get(f"/api/import/{job_id}").json()
    assert body["counts"].get("failed", 0) == 0
    assert body["counts"]["pending"] == 1


def test_report_lists_skipped_rows(client):
    c, manager = client
    job_id = _seed_job(manager, ["skipped", "done"])
    body = c.get(f"/api/import/{job_id}").json()
    assert body["counts"]["skipped"] == 1
    assert any(item["status"] == "skipped" for item in body["report"])


def test_stalled_processing_rows_are_counted(client):
    c, manager = client
    with manager.get_session() as s:
        job = ImportJob(user_id=DEFAULT_USER_ID, source="goodreads", total_rows=1)
        s.add(job)
        s.flush()
        old = datetime.now(UTC) - timedelta(minutes=30)
        s.add(
            ImportRow(
                import_job_id=job.id,
                user_id=DEFAULT_USER_ID,
                destination="history",
                status="processing",
                updated_at=old,
            )
        )
        s.flush()
        job_id = job.id
    body = c.get(f"/api/import/{job_id}").json()
    assert body["stalled"] == 1
    assert body["complete"] is False


def test_retry_re_enqueues_stranded_pending_rows(client, monkeypatch):
    """A row whose enqueue RPC failed stays 'pending' with no task behind it (#99)."""
    c, manager = client
    job_id = _seed_row(manager, "pending", minutes_old=30)
    enq = []
    monkeypatch.setattr(imports_mod, "enqueue_import_row", lambda rid: enq.append(rid) or True)
    r = c.post(f"/api/import/{job_id}/retry")
    assert r.status_code == 200
    assert r.json()["retried"] == 1
    assert len(enq) == 1


def test_retry_ignores_fresh_pending_rows(client, monkeypatch):
    """Fresh pending rows are normally in-flight — retry must not double-enqueue them."""
    c, manager = client
    job_id = _seed_row(manager, "pending", minutes_old=0)
    enq = []
    monkeypatch.setattr(imports_mod, "enqueue_import_row", lambda rid: enq.append(rid) or True)
    r = c.post(f"/api/import/{job_id}/retry")
    assert r.json()["retried"] == 0
    assert enq == []


def test_stalled_counts_stale_pending_rows(client):
    """'stalled' = rows a retry will re-drive: stale processing + stale pending (#99)."""
    c, manager = client
    job_id = _seed_row(manager, "pending", minutes_old=30)
    body = c.get(f"/api/import/{job_id}").json()
    assert body["stalled"] == 1
    assert body["complete"] is False

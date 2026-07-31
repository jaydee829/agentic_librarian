"""#100: per-tier import row caps + the one-in-flight-import rule (api/imports.py commit),
executed against a real Postgres (db_integration — CI-only, see test/conftest.py). The fast
wiring-only checks (fake session, no real tier/DB behavior) live in
test/unit/test_api_import_commit.py; this file proves the real tier resolution (including a
future subscriber_until) and the real pending/processing-row join."""

import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_librarian.api import imports as imports_mod
from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.api.imports import router
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.models import ImportJob, ImportRow, User
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration

_MAPPING = {"title": "Title", "author": "Author", "date_completed": "Date Read", "shelf": "Exclusive Shelf"}


@pytest.fixture()
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(imports_mod, "db_manager", manager)
    # Never hit real Cloud Tasks; the enqueue call itself is covered in test_api_import_status.py.
    monkeypatch.setattr(imports_mod, "enqueue_import_row", lambda row_id: True)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL)
    return TestClient(app), manager


def _csv(n_rows: int) -> bytes:
    header = "Title,Author,Date Read,Exclusive Shelf\n"
    body = "".join(f"Book {i},Author {i},2024/01/{(i % 27) + 1:02d},read\n" for i in range(n_rows))
    return (header + body).encode()


def _commit(c, n_rows):
    return c.post(
        "/api/import/commit",
        files={"file": ("export.csv", io.BytesIO(_csv(n_rows)), "text/csv")},
        data={"mapping": json.dumps(_MAPPING), "import_to_read": "false", "import_currently_reading": "false"},
    )


def test_free_tier_over_cap_is_413(client, monkeypatch):
    monkeypatch.setenv("IMPORT_MAX_ROWS_FREE", "5")
    c, _manager = client
    r = _commit(c, 6)
    assert r.status_code == 413
    body = r.json()["detail"]
    assert body["code"] == "import_rows_limit"
    assert "5" in body["message"]


def test_supporter_tier_same_file_passes(client, monkeypatch):
    monkeypatch.setenv("IMPORT_MAX_ROWS_FREE", "5")
    c, manager = client
    with manager.get_session() as s:
        user = s.get(User, DEFAULT_USER_ID)
        user.subscriber_until = datetime.now(UTC) + timedelta(days=30)
    r = _commit(c, 6)
    assert r.status_code == 200
    assert r.json()["total_rows"] == 6


def test_second_commit_while_first_still_pending_is_409(client):
    """A RECENT in-flight job (created just now, by the commit above) blocks — the other
    half of the #100 review fix's window invariant."""
    c, _manager = client
    first = _commit(c, 2)
    assert first.status_code == 200
    second = _commit(c, 2)
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "import_in_flight"


def test_old_wedged_pending_row_does_not_block_a_new_commit(client):
    """#100 review fix: an import-row task has no give-up retry bound and job-recovery UI
    lives only in React state, so a row permanently wedged in 'pending'/'processing' must
    not lock the user out of ever importing again. A job older than IN_FLIGHT_WINDOW no
    longer counts as in-flight. #147 lesson: seed created_at explicitly, don't rely on
    wall-clock proximity to a boundary."""
    c, manager = client
    stale = datetime.now(UTC) - timedelta(hours=25)  # older than the 24h IN_FLIGHT_WINDOW
    with manager.get_session() as s:
        job = ImportJob(user_id=DEFAULT_USER_ID, source="goodreads", total_rows=1, created_at=stale)
        s.add(job)
        s.flush()
        s.add(ImportRow(import_job_id=job.id, user_id=DEFAULT_USER_ID, destination="history", status="pending"))

    r = _commit(c, 2)
    assert r.status_code == 200


def test_commit_allowed_again_once_all_rows_reach_terminal_status(client):
    c, manager = client
    first = _commit(c, 2)
    assert first.status_code == 200
    job_id = first.json()["import_job_id"]
    with manager.get_session() as s:
        rows = s.query(ImportRow).filter(ImportRow.import_job_id == job_id).all()
        for row in rows:
            row.status = "done"

    third = _commit(c, 2)
    assert third.status_code == 200

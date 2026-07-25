import io
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_librarian.api import imports as imports_mod
from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.api.imports import router
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID

app = FastAPI()
app.include_router(router, prefix="/api")
app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL)
client = TestClient(app)

CSV = (
    "Book Id,Title,Author,My Rating,Binding,Date Read,Exclusive Shelf,My Review\n"
    "1,Dune,Frank Herbert,5,Kindle Edition,2024/03/05,read,loved it\n"  # history
    "2,Hyperion,Dan Simmons,0,Audiobook,,to-read,\n"  # suggestion (opt-in)
    "3,Blank,No Date,0,Paperback,,read,\n"  # skip (no date)
)

_GOODREADS_MAP = {
    "title": "Title",
    "author": "Author",
    "format": "Binding",
    "date_completed": "Date Read",
    "rating": "My Rating",
    "notes": "My Review",
    "shelf": "Exclusive Shelf",
}


class _Recorder:
    def __init__(self):
        self.jobs = []
        self.rows = []

    def add(self, obj):
        name = type(obj).__name__
        (self.jobs if name == "ImportJob" else self.rows).append(obj)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def flush(self):
        from uuid import uuid4

        for j in self.jobs:
            if getattr(j, "id", None) is None:
                j.id = uuid4()
        for r in self.rows:
            if getattr(r, "id", None) is None:
                r.id = uuid4()


def _fake_manager(rec):
    class _M:
        def get_session(self):
            return rec

    return _M()


def _commit(monkeypatch, *, to_read=True):
    rec = _Recorder()
    monkeypatch.setattr(imports_mod, "db_manager", _fake_manager(rec))
    enq = []
    monkeypatch.setattr(imports_mod, "enqueue_import_row", lambda row_id: enq.append(row_id) or True)
    r = client.post(
        "/api/import/commit",
        files={"file": ("export.csv", io.BytesIO(CSV.encode()), "text/csv")},
        data={
            "mapping": json.dumps(_GOODREADS_MAP),
            "import_to_read": str(to_read).lower(),
            "import_currently_reading": "true",
            "original_filename": "export.csv",
        },
    )
    return r, rec, enq


def test_commit_writes_rows_and_enqueues_only_non_skip(monkeypatch):
    r, rec, enq = _commit(monkeypatch)
    assert r.status_code == 200
    job = rec.jobs[0]
    assert job.total_rows == 3
    dests = sorted(row.destination for row in rec.rows)
    assert dests == ["history", "skip", "suggestion"]
    assert len(enq) == 2  # exactly the two non-skip rows enqueued
    assert r.json()["import_job_id"] == str(job.id)
    skip_row = next(r for r in rec.rows if r.destination == "skip")
    assert skip_row.status == "skipped"
    assert skip_row.skip_reason  # non-empty reason recorded
    hist_row = next(r for r in rec.rows if r.destination == "history")
    assert hist_row.status == "pending"


def test_commit_422_when_required_mapping_missing(monkeypatch):
    monkeypatch.setattr(imports_mod, "db_manager", _fake_manager(_Recorder()))
    bad = dict(_GOODREADS_MAP, date_completed=None)
    r = client.post(
        "/api/import/commit",
        files={"file": ("export.csv", io.BytesIO(CSV.encode()), "text/csv")},
        data={"mapping": json.dumps(bad), "import_to_read": "true", "import_currently_reading": "true"},
    )
    assert r.status_code == 422

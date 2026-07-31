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


class _NullQuery:
    """Chainable no-op ORM query stub. `.first()` returns whatever `result` was configured
    with — the commit-cap unit tests only need to control that one value; the real
    join/filter correctness is proven against Postgres in test_api_import_caps.py."""

    def __init__(self, result=None):
        self._result = result

    def join(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _Recorder:
    def __init__(self, in_flight=None):
        self.jobs = []
        self.rows = []
        # Configurable first()-result for any session.query(...) call made inside commit
        # (the #100 in-flight check; effective_tier's own UserCredential query is bypassed
        # in tests that need this by monkeypatching tiers.effective_tier directly instead).
        self.in_flight = in_flight

    def add(self, obj):
        name = type(obj).__name__
        (self.jobs if name == "ImportJob" else self.rows).append(obj)

    def get(self, model, obj_id):
        return None  # no User row -> tiers.effective_tier resolves 'free' (subscriber_until None)

    def query(self, *entities):
        return _NullQuery(self.in_flight)

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


def _commit(monkeypatch, *, to_read=True, in_flight=None):
    rec = _Recorder(in_flight=in_flight)
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


def test_commit_413_when_over_tier_row_limit(monkeypatch):
    """#100: wiring check only — the real free/supporter/DB-driven cap resolution is
    covered end-to-end against Postgres in test_api_import_caps.py. Uses the real env
    knob (not a stubbed tiers.import_max_rows) so the message's interpolated numbers stay
    honest: the free limit shown must be the small override, and the supporter upsell
    figure must be the real (untouched) env-tunable ceiling — not hardcoded text (#100
    review fix)."""
    monkeypatch.setenv("IMPORT_MAX_ROWS_FREE", "1")
    r, rec, enq = _commit(monkeypatch)
    assert r.status_code == 413
    body = r.json()["detail"]
    assert body["code"] == "import_rows_limit"
    assert "your current limit is 1" in body["message"]
    assert "support Shelfwright for the full 2,000-row limit" in body["message"]
    assert rec.jobs == []  # rejected before anything was written
    assert enq == []


def test_commit_413_over_supporter_cap_has_no_upsell_pitch(monkeypatch):
    """#100 review fix: a supporter already at their own ceiling shouldn't be told to
    'support Shelfwright' for a limit they already have."""
    monkeypatch.setattr(imports_mod.tiers, "effective_tier", lambda session, user_id: "supporter")
    monkeypatch.setenv("IMPORT_MAX_ROWS_SUPPORTER", "1")
    r, rec, enq = _commit(monkeypatch)
    assert r.status_code == 413
    body = r.json()["detail"]
    assert body["code"] == "import_rows_limit"
    assert "your current limit is 1" in body["message"]
    assert "support Shelfwright" not in body["message"]
    assert rec.jobs == []
    assert enq == []


def test_commit_409_when_import_already_in_flight(monkeypatch):
    """#100: wiring check only — real pending/processing-row detection against Postgres is
    covered in test_api_import_caps.py."""
    monkeypatch.setattr(imports_mod.tiers, "effective_tier", lambda session, user_id: "free")
    r, rec, enq = _commit(monkeypatch, in_flight="some-row-id")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "import_in_flight"
    assert rec.jobs == []
    assert enq == []

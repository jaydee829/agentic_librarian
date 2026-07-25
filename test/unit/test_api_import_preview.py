import io
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.api.imports import MAX_ROWS, router
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID

app = FastAPI()
app.include_router(router, prefix="/api")
app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL)
client = TestClient(app)

GOODREADS_CSV = (
    "Book Id,Title,Author,My Rating,Binding,Date Read,Exclusive Shelf,My Review\n"
    "1,Dune,Frank Herbert,5,Kindle Edition,2024/03/05,read,loved it\n"
    "2,Hyperion,Dan Simmons,0,Audiobook,,to-read,\n"
)


def _upload(csv_text, mapping=None):
    data = {"mapping": json.dumps(mapping)} if mapping else {}
    return client.post(
        "/api/import/preview",
        files={"file": ("export.csv", io.BytesIO(csv_text.encode()), "text/csv")},
        data=data,
    )


def test_preview_detects_goodreads_and_suggests_mapping():
    r = _upload(GOODREADS_CSV)
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "goodreads"
    assert body["suggested_mapping"]["title"] == "Title"
    assert body["counts"]["read_dated"] == 1
    assert body["counts"]["to_read"] == 1
    assert len(body["preview_rows"]) == 2


def test_preview_counts_bad_dates_and_reports_first_example():
    csv_text = (
        "title,author,timestamp,shelf\n"
        'Whirlwind,John Ferling,"October 14, 2017 0:34",\n'  # parseable → read_dated
        "The Forgotten,David Baldacci,garbage-one,\n"  # bad → bad_date (the example)
        "First Family,David Baldacci,garbage-two,\n"  # bad → bad_date
        "Different Seasons,Stephen King,,\n"  # blank → read_undated (not "bad")
        "Hyperion,Dan Simmons,garbage-three,to-read\n"  # shelved → never skips on date
    )
    mapping = {"title": "title", "author": "author", "date_completed": "timestamp", "shelf": "shelf"}
    r = _upload(csv_text, mapping=mapping)
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["read_dated"] == 1
    assert body["counts"]["bad_date"] == 2
    assert body["counts"]["read_undated"] == 1
    assert body["counts"]["to_read"] == 1
    assert body["bad_date_example"] == "garbage-one"


def test_preview_without_bad_dates_has_zero_count_and_no_example():
    r = _upload(GOODREADS_CSV)
    body = r.json()
    assert body["counts"]["bad_date"] == 0
    assert body["bad_date_example"] is None


def test_preview_rejects_empty_file():
    r = _upload("")
    assert r.status_code == 422


def test_preview_rejects_oversize_file():
    header = "Title,Author,Date Read,Exclusive Shelf\n"
    body = "".join(f"Book {i},Author {i},2024/01/01,read\n" for i in range(MAX_ROWS + 1))
    r = _upload(header + body)
    assert r.status_code == 422

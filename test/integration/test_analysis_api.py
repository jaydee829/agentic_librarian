from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func

from agentic_librarian.api import analysis as analysis_mod
from agentic_librarian.api import auth
from agentic_librarian.api import main as api_main
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.models import (
    Author,
    AuthorStyle,
    Edition,
    Narrator,
    ReadingHistory,
    Style,
    Trope,
    User,
    Work,
    WorkContributor,
    WorkStyle,
    WorkTrope,
)
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    monkeypatch.setattr(analysis_mod, "db_manager", manager)
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        auth.get_current_user,
        lambda: auth.AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL),
    )
    yield TestClient(api_main.app)


def _seed_read(
    manager,
    *,
    user_id,
    title,
    author,
    genres,
    moods,
    tropes,
    narrator=None,
    fmt="audiobook",
    rating=4,
    completed=None,
    styles=None,
):
    with manager.get_session() as s:
        work = Work(title=title, genres=genres, moods=moods)
        s.add(work)
        s.flush()
        a = Author(name=author)
        s.add(a)
        s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        for tname in tropes:
            t = Trope(name=f"{tname}-{uuid4().hex[:6]}")  # unique() on tropes.name
            s.add(t)
            s.flush()
            s.add(WorkTrope(work_id=work.id, trope_id=t.id, relevance_score=1.0))
        if styles:
            for attr, (sname, category) in styles.items():
                st = Style(name=sname, category=category)
                s.add(st)
                s.flush()
                if category == "Author":
                    s.add(AuthorStyle(author_id=a.id, style_id=st.id, attribute_type=attr))
                else:  # "Work"
                    s.add(WorkStyle(work_id=work.id, style_id=st.id, attribute_type=attr))
        edition = Edition(work_id=work.id, format=fmt)
        if narrator:
            # uq_narrators_name_lower (GH #95): reuse an existing narrator by
            # case-insensitive name instead of blindly inserting, so callers can pass
            # the same narrator name across multiple _seed_read calls (e.g. to model
            # one narrator across two books) without a UniqueViolation.
            n = s.query(Narrator).filter(func.lower(Narrator.name) == narrator.lower()).one_or_none()
            if n is None:
                n = Narrator(name=narrator)
                s.add(n)
                s.flush()
            edition.narrators.append(n)
        s.add(edition)
        s.flush()
        s.add(
            ReadingHistory(
                edition_id=edition.id,
                user_id=user_id,
                date_completed=completed or date.today(),
                user_rating=rating,
            )
        )
        s.flush()


def test_analysis_aggregates_the_users_reading(client, db_url):
    manager = DatabaseManager(db_url)
    _seed_read(
        manager,
        user_id=DEFAULT_USER_ID,
        title="Dune",
        author="Herbert",
        genres=["Sci-Fi"],
        moods=["epic"],
        tropes=["chosen-one"],
        narrator="Vance",
        rating=5,
    )
    _seed_read(
        manager,
        user_id=DEFAULT_USER_ID,
        title="Hyperion",
        author="Simmons",
        genres=["Sci-Fi"],
        moods=["dark"],
        tropes=["chosen-one"],
        narrator="Vance",
        rating=3,
    )

    body = client.get("/api/analysis").json()

    snap = body["snapshot"]
    assert snap["total_read"] == 2
    assert snap["average_rating"] == 4.0
    assert snap["distinct_authors"] == 2
    assert {f["name"]: f["count"] for f in snap["formats"]} == {"audiobook": 2}
    assert {g["name"]: g["count"] for g in body["genres"]} == {"Sci-Fi": 2}
    assert {m["name"] for m in body["moods"]} == {"epic", "dark"}
    assert len(body["top_tropes"]) == 2
    assert {a["name"]: a["count"] for a in body["authors"]} == {"Herbert": 1, "Simmons": 1}
    assert {n["name"]: n["count"] for n in body["narrators"]} == {"Vance": 2}


def test_analysis_empty_for_user_with_no_reading(client):
    body = client.get("/api/analysis").json()
    assert body["snapshot"] == {
        "total_read": 0,
        "read_this_year": 0,
        "average_rating": None,
        "distinct_authors": 0,
        "formats": [],
    }
    assert body["genres"] == []
    assert body["moods"] == []
    assert body["top_tropes"] == []
    assert body["authors"] == []
    assert body["narrators"] == []


def test_analysis_excludes_other_users(client, db_url):
    manager = DatabaseManager(db_url)
    other_id = uuid4()
    with manager.get_session() as s:
        s.add(User(id=other_id, email="other3@example.com"))
        s.flush()
    _seed_read(
        manager,
        user_id=other_id,
        title="NotMine",
        author="Ghost",
        genres=["Horror"],
        moods=["creepy"],
        tropes=["haunting"],
    )

    body = client.get("/api/analysis").json()
    assert body["snapshot"]["total_read"] == 0  # other user's reading is invisible


def test_analysis_includes_style_radar_and_cloud_keys(client, db_url):
    manager = DatabaseManager(db_url)
    _seed_read(
        manager,
        user_id=DEFAULT_USER_ID,
        title="Dune",
        author="Herbert",
        genres=["Sci-Fi"],
        moods=["epic"],
        tropes=["chosen-one"],
        styles={
            "pacing": ("measured", "Author"),
            "tone": ("atmospheric", "Author"),
            "perspective": ("third person omniscient", "Work"),
        },
    )
    body = client.get("/api/analysis").json()

    assert set(body["style_radar"].keys()) == {
        "pace",
        "density",
        "depth",
        "inner_focus",
        "humor",
        "warmth",
        "lexicon",
        "world_building",
    }
    cloud = {row["name"]: row["count"] for row in body["style_cloud"]}
    assert cloud == {"Atmospheric": 1, "Third Person Omniscient": 1}


def test_analysis_empty_user_has_empty_style_fields(client):
    body = client.get("/api/analysis").json()
    assert body["style_cloud"] == []
    assert all(v is None for v in body["style_radar"].values())

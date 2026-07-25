from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.api.main import app
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID

client = TestClient(app)


@pytest.fixture(autouse=True)
def _authed():
    """Endpoints are auth-gated (Lift 1) — these tests exercise the data layer, so
    inject a verified identity via FastAPI's dependency-override seam."""
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL)
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_get_history_empty():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session

        # Mock empty query result with the actual chain
        mock_query = mock_session.query.return_value
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = []

        response = client.get("/api/history")
        assert response.status_code == 200
        assert response.json() == []


def test_get_history_with_data():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session

        # Setup mock data
        mock_history = MagicMock()
        mock_history.id = "test-id"
        mock_history.date_completed = date(2024, 1, 1)
        mock_history.user_rating = 5
        mock_history.edition.format = "hardcover"
        mock_history.edition.work.title = "Test Book"
        mock_contributor = MagicMock()
        mock_contributor.author.name = "Test Author"
        mock_contributor.role = "Author"
        mock_history.edition.work.contributors = [mock_contributor]

        # Mock the actual chain
        mock_query = mock_session.query.return_value
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = [mock_history]

        response = client.get("/api/history")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test Book"
        assert data[0]["authors"] == ["Test Author"]
        assert data[0]["date_completed"] == "2024-01-01"
        assert data[0]["rating"] == 5
        assert data[0]["format"] == "hardcover"


def test_get_history_no_date():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session

        # Setup mock data with None date
        mock_history = MagicMock()
        mock_history.id = "test-id"
        mock_history.date_completed = None
        mock_history.user_rating = None
        mock_history.edition.format = "eBook"
        mock_history.edition.work.title = "No Date Book"
        mock_contributor = MagicMock()
        mock_contributor.author.name = "Test Author"
        mock_contributor.role = "Author"
        mock_history.edition.work.contributors = [mock_contributor]

        mock_query = mock_session.query.return_value
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.options.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = [mock_history]

        response = client.get("/api/history")
        assert response.status_code == 200
        data = response.json()
        assert data[0]["date_completed"] is None


def _history_chain(mock_session, results):
    """Wire query().join().join().filter().options().order_by().offset().limit().all()."""
    mock_query = mock_session.query.return_value
    mock_query.join.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.options.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.offset.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.all.return_value = results
    return mock_query


def test_get_history_pagination_params_forwarded():
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session
        mock_query = _history_chain(mock_session, [])

        response = client.get("/api/history?limit=10&offset=20")
        assert response.status_code == 200
        mock_query.offset.assert_called_once_with(20)
        mock_query.limit.assert_called_once_with(10)


def test_get_history_limit_cap_enforced():
    assert client.get("/api/history?limit=500").status_code == 422
    assert client.get("/api/history?limit=0").status_code == 422
    assert client.get("/api/history?offset=-1").status_code == 422


def _mock_work_trope(name, *, relevance, justification):
    wt = MagicMock()
    wt.trope.name = name
    wt.relevance_score = relevance
    wt.justification = justification
    return wt


@pytest.mark.parametrize(
    ("tropes", "expected_names"),
    [
        pytest.param(
            [
                _mock_work_trope("Slow Burn", relevance=0.9, justification="found in scout summary"),
                _mock_work_trope("Found Family", relevance=0.7, justification="found in scout summary"),
                _mock_work_trope("Dark", relevance=1.0, justification=None),
                _mock_work_trope("Sad", relevance=1.0, justification=None),
                _mock_work_trope("Fantasy", relevance=1.0, justification=None),
            ],
            ["Slow Burn", "Found Family", "Dark"],
            id="justified_tropes_outrank_slugs_top_3",
        ),
        pytest.param(
            [
                _mock_work_trope("Fantasy", relevance=1.0, justification=None),
                _mock_work_trope("Dark", relevance=1.0, justification=None),
            ],
            ["Dark", "Fantasy"],
            id="slug_only_work_still_shows_slugs",
        ),
    ],
)
def test_get_history_tropes_prefer_real_over_slugs(tropes, expected_names):
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session

        mock_history = MagicMock()
        mock_history.id = "test-id"
        mock_history.date_completed = date(2024, 1, 1)
        mock_history.user_rating = 5
        mock_history.edition.format = "hardcover"
        mock_history.edition.work.title = "Test Book"
        mock_contributor = MagicMock()
        mock_contributor.author.name = "Test Author"
        mock_contributor.role = "Author"
        mock_history.edition.work.contributors = [mock_contributor]
        mock_history.edition.work.tropes = tropes

        _history_chain(mock_session, [mock_history])

        response = client.get("/api/history")
        assert response.status_code == 200
        data = response.json()
        assert data[0]["tropes"] == expected_names


def _patch_with_mock_row(json_body):
    """PATCH against a minimal mocked row (validation-focused unit seam)."""
    with patch("agentic_librarian.api.main.db_manager") as mock_db:
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session
        _history_chain(mock_session, [])
        return client.patch("/api/history/00000000-0000-4000-8000-00000000abcd", json=json_body)


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        pytest.param({"format": "vinyl"}, 422, id="unknown_format_rejected"),
        pytest.param({"format": ""}, 422, id="empty_format_rejected"),
        pytest.param({"format": None}, 422, id="null_format_rejected"),
        pytest.param({"format": 7}, 422, id="non_string_format_rejected"),
    ],
)
def test_patch_history_format_vocab_validation(body, expected_status):
    assert _patch_with_mock_row(body).status_code == expected_status


@pytest.mark.parametrize(
    "raw", [pytest.param("Audiobook", id="capitalized"), pytest.param("  audiobook  ", id="padded")]
)
def test_patch_history_format_is_case_and_space_normalized(raw):
    """Accepted spellings normalize to the canonical lowercase vocab before any DB work —
    prove it via the pydantic model directly (the endpoint seam is mocked in this file)."""
    from agentic_librarian.api.main import HistoryUpdate

    assert HistoryUpdate(format=raw).format == "audiobook"

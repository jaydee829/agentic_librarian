import os

import pytest
import requests

import src.agentic_librarian.scouts.metadata_scout as md_scout


def test_fetch_book_metadata_success(monkeypatch):
    class MockResponse:
        @staticmethod
        def json():
            return {
                "items": [
                    {
                        "id": "test_id",
                        "volumeInfo": {
                            "title": "Test Book",
                            "authors": ["Test Author"],
                            "publishedDate": "2020-01-01",
                            "description": "A test book description.",
                            "pageCount": 123,
                            "categories": ["Fiction"],
                            "averageRating": 4.5,
                            "imageLinks": {"thumbnail": "http://example.com/thumb.jpg"},
                        },
                    }
                ]
            }

        @staticmethod
        def raise_for_status():
            pass

    def mock_get(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr("requests.get", mock_get)

    metadata = md_scout.fetch_google_books_metadata("Test Book", "Test Author")
    assert metadata is not None
    assert metadata["google_id"] == "test_id"
    assert metadata["title"] == "Test Book"
    assert metadata["authors"] == ["Test Author"]
    assert metadata["published_date"] == "2020-01-01"
    assert metadata["description"] == "A test book description."
    assert metadata["page_count"] == 123
    assert metadata["categories"] == ["Fiction"]
    assert metadata["average_rating"] == 4.5
    assert metadata["thumbnail"] == "http://example.com/thumb.jpg"


def test_fetch_book_metadata_no_results(monkeypatch):
    class MockResponse:
        @staticmethod
        def json():
            return {}

        @staticmethod
        def raise_for_status():
            pass

    def mock_get(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr("requests.get", mock_get)

    metadata = md_scout.fetch_google_books_metadata(
        "Nonexistent Book", "Unknown Author"
    )
    assert metadata is None

    def mock_get(*args, **kwargs):
        raise requests.exceptions.RequestException("API failure")

    monkeypatch.setattr("requests.get", mock_get)

    metadata = md_scout.fetch_google_books_metadata("Any Book", "Any Author")
    assert metadata is None


def test_fetch_book_metadata_missing_fields(monkeypatch):
    class MockResponse:
        @staticmethod
        def json():
            return {
                "items": [
                    {
                        "id": "test_id_missing_fields",
                        "volumeInfo": {
                            "title": "Test Book Missing Fields",
                            # 'authors' field is missing
                            "publishedDate": "2021-05-05",
                        },
                    }
                ]
            }

        @staticmethod
        def raise_for_status():
            pass

    def mock_get(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr("requests.get", mock_get)

    metadata = md_scout.fetch_google_books_metadata(
        "Test Book Missing Fields", "Test Author"
    )
    assert metadata is not None
    assert metadata["google_id"] == "test_id_missing_fields"
    assert metadata["title"] == "Test Book Missing Fields"
    assert metadata["authors"] == []  # Default to empty list when missing
    assert metadata["published_date"] == "2021-05-05"


def test_fetch_book_metadata_no_items_key(monkeypatch):
    class MockResponse:
        @staticmethod
        def json():
            return {"kind": "books#volumes"}

        @staticmethod
        def raise_for_status():
            pass

    def mock_get(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr("requests.get", mock_get)

    metadata = md_scout.fetch_google_books_metadata("Some Book", "Some Author")
    assert metadata is None


@pytest.mark.integration
def test_fetch_book_metadata_integration_live():
    # Skip if explicitly disabled to avoid network calls in some CI environments
    if os.environ.get("SKIP_INTEGRATION_TESTS") == "1":
        pytest.skip("Skipping integration tests (SKIP_INTEGRATION_TESTS=1)")

    title = "The Way of Kings"
    author = "Brandon Sanderson"
    api_key = os.environ.get("GOOGLE_BOOKS_API_KEY")

    if api_key:
        metadata = md_scout.fetch_google_books_metadata(title, author, api_key=api_key)
    else:
        metadata = md_scout.fetch_google_books_metadata(title, author)

    assert metadata is not None, "Expected live API to return metadata"
    assert "google_id" in metadata
    assert "title" in metadata
    assert "authors" in metadata

    # Ensure the returned metadata matches the queried book/author (case-insensitive, tolerant)
    title_ok = "way of kings" in metadata["title"].lower()
    authors = metadata.get("authors") or []
    author_ok = any("sanderson" in a.lower() for a in authors)
    assert (
        title_ok or author_ok
    ), f"Returned metadata does not appear to match '{title}' by '{author}'"


@pytest.mark.integration
def test_fetch_audible_metadata_integration_live():
    # Skip if explicitly disabled to avoid network calls in some CI environments
    if os.environ.get("SKIP_INTEGRATION_TESTS") == "1":
        pytest.skip("Skipping integration tests (SKIP_INTEGRATION_TESTS=1)")

    title = "The Way of Kings"
    author = "Brandon Sanderson"

    metadata = md_scout.AudiobookScout().extract_metadata_with_gemini(title)

    assert metadata is not None, "Expected live API to return metadata"
    assert "trackId" in metadata
    assert "trackName" in metadata
    assert "artistName" in metadata

    # Ensure the returned metadata matches the queried book/author (case-insensitive, tolerant)
    title_ok = "hobbit" in metadata["trackName"].lower()
    author_ok = "tolkien" in metadata["artistName"].lower()
    assert (
        title_ok or author_ok
    ), f"Returned metadata does not appear to match '{title}' by '{author}'"


@pytest.mark.integration
def test_fetch_hardcover_metadata_integration_live():
    # Skip if explicitly disabled to avoid network calls in some CI environments
    if os.environ.get("SKIP_INTEGRATION_TESTS") == "1":
        pytest.skip("Skipping integration tests (SKIP_INTEGRATION_TESTS=1)")

    title = "The Way of Kings"
    author = "Brandon Sanderson"
    api_key = os.environ.get("HARDCOVER_API_KEY")

    metadata = md_scout.fetch_hardcover_metadata(title, author, api_key=api_key)

    assert metadata is not None, "Expected live API to return metadata"
    assert "id" in metadata
    assert "title" in metadata
    assert "moods" in metadata

    # Ensure the returned metadata matches the queried book/author (case-insensitive, tolerant)
    title_ok = "way of kings" in metadata["title"].lower()
    authors = metadata.get("author_names") or []
    author_ok = any("sanderson" in a.lower() for a in authors)
    assert (
        title_ok or author_ok
    ), f"Returned metadata does not appear to match '{title}' by '{author}'"

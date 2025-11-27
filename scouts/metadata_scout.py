"""Grab additional metadata to flesh out reading list"""

import requests


def fetch_book_metadata(title: str, author: str, api_key: str = None) -> dict:
    """
    Fetches book metadata from Google Books API using Title and Author.

    Args:
        title (str): The book title.
        author (str): The book author.
        api_key (str): Optional Google API Key (recommended for higher rate limits).

    Returns:
        dict: A dictionary containing clean metadata or None if not found.
    """
    base_url = "https://www.googleapis.com/books/v1/volumes"

    query = f"intitle:{title} inauthor:{author}"

    params = {
        "q": query,
        "maxResults": 1,  # Best match
        "langRestrict": "en",  # Restrict to English
        "printType": "books",
    }

    if api_key:
        params["key"] = api_key

    try:
        response = requests.get(base_url, params=params)
        response.raise_for_status()  # Raise error for 4xx/5xx responses
        data = response.json()

        # Check if items exist
        if "items" not in data:
            print(f"Warning: No results found for '{title}' by {author}")
            return None

        # Extract the first result
        book = data["items"][0]["volumeInfo"]

        return {
            "google_id": data["items"][0]["id"],
            "title": book.get("title"),
            "authors": book.get("authors", []),
            "published_date": book.get("publishedDate"),
            "description": book.get("description", ""),
            "page_count": book.get("pageCount", 0),
            "categories": book.get("categories", []),  # genres
            "average_rating": book.get("averageRating"),
            "thumbnail": book.get("imageLinks", {}).get("thumbnail"),
        }

    except requests.exceptions.RequestException as e:
        print(f"API Request failed: {e}")
        return None

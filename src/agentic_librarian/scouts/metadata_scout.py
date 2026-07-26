"""Grab additional metadata to flesh out reading list"""

import json
import os
import re
from abc import ABC, abstractmethod
from contextlib import suppress

import requests
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from agentic_librarian.scouts.grounded_llm import GroundedLLM, get_grounded_llm

# Load environment variables from .env if present (for local dev)
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


# --- CORE ABSTRACTIONS ---

# Retry transient HTTP failures (429 + 5xx) with bounded exponential backoff. Google Books
# rate-limits the per-discovery enrichment burst with 429s (REC-016/REC-020); without this, a burst
# of discoveries silently loses all REST metadata. Bounded (total=3, ~1+2+4s) and Retry-After is NOT
# honored on purpose: a single rate-limited lookup must not stall an interactive recommendation by
# the server-dictated delay — transient blips recover, sustained limits degrade gracefully (skip).
_API_RETRY = Retry(
    total=3,
    backoff_factor=1.0,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET", "POST"}),
    respect_retry_after_header=False,
)

# Shared session for raw page scrapes (AudiobookScout extends LLMScout, which has no
# APIScout session/timeout machinery — GH #103). Same retry policy as the API scouts;
# timeout in SECONDS (requests convention — the genai HttpOptions timeout is milliseconds).
_PAGE_TIMEOUT = 15
_page_session = requests.Session()
_page_session.mount("https://", HTTPAdapter(max_retries=_API_RETRY))
_page_session.mount("http://", HTTPAdapter(max_retries=_API_RETRY))

_gbooks_nokey_warned = False


class BaseScout(ABC):
    """Abstract base class for all metadata scouts."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key
        self.source_name = self.__class__.__name__

    @abstractmethod
    def search(self, title: str, author: str, **kwargs) -> dict:
        """
        Standardized search interface.
        Returns a dictionary of metadata or an empty dict if not found.
        """
        pass

    def _extract_year(self, date_str: str | None) -> int | None:
        """Helper to extract a 4-digit year from a string robustly."""
        if not date_str:
            return None
        match = re.search(r"\b(\d{4})\b", str(date_str))
        if match:
            with suppress(ValueError):
                return int(match.group(1))
        return None


class APIScout(BaseScout):
    """Abstract base class for scouts interacting with structured REST/GraphQL APIs."""

    def __init__(self, api_key: str = None, timeout: int = 10):
        super().__init__(api_key)
        self.timeout = timeout
        # A Session with a retrying adapter so transient 429/5xx (e.g. the Google Books enrichment
        # burst) back off and retry instead of silently dropping a book's metadata.
        self._session = requests.Session()
        adapter = HTTPAdapter(max_retries=_API_RETRY)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _make_request(self, method: str, url: str, **kwargs) -> dict:
        """Shared request logic with error handling."""
        try:
            response = self._session.request(method, url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Warning: {self.source_name} request failed: {e}")
            return {}


class LLMScout(BaseScout):
    """Abstract base class for scouts using LLMs for unstructured data."""

    def __init__(
        self,
        api_key: str = None,
        model_name: str = None,
        llm: GroundedLLM | None = None,
        key_source: str = "app",
    ):
        # Fallback to GOOGLE_SEARCH_API_KEY if no specific key provided (also used by AudiobookScout's
        # Custom Search and by the Gemini provider/embeddings).
        key = api_key or os.environ.get("GOOGLE_SEARCH_API_KEY")
        if not key:
            raise ValueError(f"{self.__class__.__name__} requires a Google API key.")
        super().__init__(key)
        # Backend-selectable grounding LLM (Gemini default; Claude when AGENT_BACKEND=claude). Injectable
        # for tests. model_name is threaded into the Gemini provider (so it isn't silently ignored).
        self.model_name = (
            model_name or os.environ.get("GROUNDING_MODEL") or os.environ.get("EXPLORER_MODEL") or "gemini-2.5-flash"
        )
        # key_source (arc 3/3 BYOK) threads through to grounded-scout usage metering; 'app'
        # unless the caller passed a byok-resolved user key (orchestration/definitions.py).
        self._llm = llm or get_grounded_llm(self.api_key, model_name=self.model_name, key_source=key_source)

    def _safe_extract_json(self, response_text: str, title: str, author: str, retry_count: int = 0) -> dict | None:
        """Cleans and parses LLM JSON output with descriptive error logging."""
        if not response_text:
            # The model can return no text part (e.g. a blocked response). Surface it as
            # a retry rather than crashing on .strip().
            print(f"Warning: empty LLM response for '{title}' by '{author}'.")
            return None

        text = response_text.strip()

        # Pull the JSON payload out of the response, tolerating code fences and any
        # prose a grounded model may add before or after it.
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        else:
            block = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
            if block:
                text = block.group(1).strip()

        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("LLM returned non-dictionary JSON")
            return data
        except (json.JSONDecodeError, ValueError) as e:
            msg = f"Failed to parse LLM JSON for '{title}' by '{author}'."
            if retry_count == 0:
                print(f"Warning: {msg} Retrying... Error: {e}")
                return None
            raise ValueError(f"{msg} Output did not follow valid JSON after retry: {text[:100]}...") from e


# --- IMPLEMENTATIONS ---


class GoogleBooksScout(APIScout):
    """Scout for Google Books API."""

    def __init__(self, api_key: str = None):
        key = api_key or os.environ.get("GOOGLE_BOOKS_API_KEY")
        super().__init__(key)

    def search(self, title: str, author: str, **kwargs) -> dict:
        global _gbooks_nokey_warned
        if not self.api_key and not _gbooks_nokey_warned:
            print(
                "Warning: GoogleBooksScout has no GOOGLE_BOOKS_API_KEY — using the very low "
                "unauthenticated quota; expect 429s on enrichment bursts. Get a free key: "
                "https://developers.google.com/books/docs/v1/using#APIKey"
            )
            _gbooks_nokey_warned = True
        base_url = "https://www.googleapis.com/books/v1/volumes"
        query = f"intitle:{title} inauthor:{author}"
        params = {
            "q": query,
            "maxResults": 1,
            "langRestrict": "en",
            "printType": "books",
        }
        if self.api_key:
            params["key"] = self.api_key

        data = self._make_request("GET", base_url, params=params)
        if not data or "items" not in data:
            return {}

        book = data["items"][0]["volumeInfo"]
        isbn_13 = ""
        identifiers = book.get("industryIdentifiers", [])
        for id_obj in identifiers:
            if id_obj.get("type") == "ISBN_13":
                isbn_13 = id_obj.get("identifier")
                break

        contributors = [{"name": a, "role": "Author"} for a in book.get("authors", [])]

        return {
            "google_id": data["items"][0]["id"],
            "isbn_13": isbn_13,
            "title": book.get("title"),
            "contributors": contributors,
            "published_date": book.get("publishedDate"),
            "description": book.get("description", ""),
            "page_count": book.get("pageCount"),  # Default None
            "genres": book.get("categories", []),
            "average_rating": book.get("averageRating"),
            "thumbnail": book.get("imageLinks", {}).get("thumbnail"),
        }


class HardcoverScout(APIScout):
    """Scout for Hardcover.app GraphQL API."""

    def __init__(self, api_key: str = None):
        key = api_key or os.environ.get("HARDCOVER_API_KEY")
        super().__init__(key)

    def search(self, title: str, author: str, **kwargs) -> dict:
        format_val = kwargs.get("format", "Paperback")
        if not self.api_key:
            return {}
        book_id = self._find_book_id(title, author)
        if book_id is None:
            return {}
        book = self._fetch_book(book_id)
        if not book:
            return {}
        return self._book_to_metadata(book, format_val)

    def _graphql(self, query: str, variables: dict) -> dict:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        url = "https://api.hardcover.app/v1/graphql"
        data = self._make_request("POST", url, headers=headers, json={"query": query, "variables": variables})
        return data.get("data") or {}

    def _find_book_id(self, title: str, author: str) -> int | None:
        # Search by TITLE only (adding the author can rank companion "workbook" entries first).
        query = 'query($q: String!) { search(query: $q, query_type: "Book", per_page: 5) { results } }'
        data = self._graphql(query, {"q": title})
        hits = (((data.get("search") or {}).get("results") or {}).get("hits")) or []
        return self._select_hit(hits, author)

    @staticmethod
    def _select_hit(hits: list, author: str) -> int | None:
        def norm(s: str) -> str:
            return " ".join((s or "").lower().split())

        companions = ("workbook", "summary", "analysis", "study guide", "conversation starters")
        a = norm(author)
        docs = [h.get("document", {}) for h in hits]

        def is_companion(d: dict) -> bool:
            return any(c in (d.get("title") or "").lower() for c in companions)

        def author_matches(d: dict) -> bool:
            # Bidirectional substring match on purpose: tolerate abbreviated/extended author names
            # between the caller and Hardcover (e.g. "S. King" vs "Stephen King"). Author names are
            # never single chars in practice, so this stays specific enough.
            return any(a and norm(n) and (a in norm(n) or norm(n) in a) for n in (d.get("author_names") or []))

        def reads(d: dict) -> int:
            return d.get("users_read_count") or 0

        candidates = [d for d in docs if not is_companion(d) and author_matches(d)]
        if not candidates:
            candidates = [d for d in docs if not is_companion(d)] or docs
        if not candidates:
            return None
        best = max(candidates, key=reads)
        try:
            return int(best.get("id"))
        except (TypeError, ValueError):
            return None

    def _fetch_book(self, book_id: int) -> dict | None:
        query = """
            query GetBook($id: Int!) {
                books(where: {id: {_eq: $id}}) {
                    title
                    description
                    pages
                    release_date
                    contributions { author { name } contribution }
                    moods: cached_tags(path: "Mood")
                    genres: cached_tags(path: "Genre")
                    editions {
                        isbn_13
                        edition_format
                        pages
                        audio_seconds
                        release_date
                        country { name }
                    }
                }
            }
        """
        books = (self._graphql(query, {"id": book_id}).get("books")) or []
        return books[0] if books else None

    def _book_to_metadata(self, book: dict, format_val: str) -> dict:
        editions = book.get("editions") or []

        def fmt_match(e: dict) -> bool:
            return (e.get("edition_format") or "").lower() == format_val.lower()

        def is_us(e: dict) -> bool:
            return ((e.get("country") or {}).get("name")) == "United States of America"

        selected = (
            next((e for e in editions if fmt_match(e) and is_us(e)), None)
            or next((e for e in editions if fmt_match(e)), None)
            or (editions[0] if editions else {})
        )

        audio_seconds = selected.get("audio_seconds")
        contributors = []
        for c in book.get("contributions") or []:
            name = (c.get("author") or {}).get("name")
            role = c.get("contribution") or "Author"
            if name:
                contributors.append({"name": name, "role": role})

        edition_release = selected.get("release_date")
        return {
            "title": book.get("title"),
            "contributors": contributors,
            "edition_format": selected.get("edition_format"),
            "page_count": selected.get("pages") or book.get("pages"),
            "publication_date": edition_release,
            "original_publication_date": book.get("release_date") or edition_release,
            "isbn_13": selected.get("isbn_13"),
            "moods": {m.get("tagSlug") for m in (book.get("moods") or []) if m.get("tagSlug")},
            "genres": {g.get("tagSlug") for g in (book.get("genres") or []) if g.get("tagSlug")},
            "description": book.get("description", ""),
            "audio_minutes": audio_seconds // 60 if audio_seconds else None,
        }


class AudiobookScout(LLMScout):
    """Scouts audiobook metadata from Audible using LLM extraction."""

    def search(self, title: str, author: str, **kwargs) -> dict:
        return self._extract_metadata_from_page(title, author)

    def search_audible_link(self, title: str) -> str | None:
        search_engine_id = os.environ.get("SEARCH_ENGINE_ID")
        if not search_engine_id:
            raise ValueError("Search Engine ID not set.")

        service = build("customsearch", "v1", developerKey=self.api_key)
        search_results = (
            service.cse().list(q=f"site:audible.com {title} audiobook", cx=search_engine_id, num=1).execute()
        )

        if "items" in search_results and len(search_results["items"]) > 0:
            return search_results["items"][0]["link"]
        return None

    def fetch_page_content(self, title: str) -> str:
        url = self.search_audible_link(title)
        if not url:
            raise ValueError(f"No Audible link found for title: {title}")

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36"}
        response = _page_session.get(url, headers=headers, timeout=_PAGE_TIMEOUT)
        soup = BeautifulSoup(response.content, "html.parser")
        for script in soup(["script", "style"]):
            script.extract()
        return soup.get_text()

    def _extract_metadata_from_page(self, title: str, author: str = "Unknown") -> dict:
        text_content = self.fetch_page_content(title)
        prompt = f"""
        Extract the following fields from the text. Return ONLY a raw JSON object.
        - title (string)
        - narrators (list of strings)
        - length_minutes (int): convert "X hrs Y mins" to total minutes
        Input: {text_content[:30000]}

        CRITICAL: If the information is not present in the text, return an empty object {{}}.
        DO NOT hallucinate or guess.
        """
        text = self._llm.generate(prompt, grounded=False)
        data = self._safe_extract_json(text, title, author)
        if data is None:
            text = self._llm.generate(prompt + "\nJSON ONLY.", grounded=False)
            data = self._safe_extract_json(text, title, author, retry_count=1)

        # Normalize keys
        if data and "narrators" in data:
            data["narrator_names"] = data.pop("narrators")
        if data and "length_minutes" in data:
            data["audio_minutes"] = data.pop("length_minutes")

        return data or {}


class DirectKnowledgeScout(LLMScout):
    """Scouts audiobook metadata using direct LLM knowledge and search grounding."""

    def search(self, title: str, author: str, **kwargs) -> dict:
        return self.scout_audiobook(title, author)

    def scout_audiobook(self, title: str, author: str) -> dict:
        """Uses Gemini with search grounding to find audiobook details."""
        prompt = f"""
        Find the official audiobook duration and narrators for:
        Title: {title}
        Author: {author}

        Return ONLY a raw JSON object with:
        - title (string)
        - narrators (list of strings)
        - audio_minutes (int)

        CRITICAL: Use the provided search results to verify these facts.
        If the information is not definitively found, return an empty object {{}}.
        DO NOT guess or provide info from your internal memory if it is not verified by search.
        """

        text = self._llm.generate(prompt, grounded=True)
        data = self._safe_extract_json(text, title, author)
        if data is None:
            prompt += "\n\nSTRICT: Return valid JSON ONLY."
            text = self._llm.generate(prompt, grounded=True)
            data = self._safe_extract_json(text, title, author, retry_count=1)

        if data and "narrators" in data:
            data["narrator_names"] = data.pop("narrators")

        return data or {}


def _flatten_style_map(data: dict | None) -> dict[str, str]:
    """Coerce a scouted style dict to {attribute: non-empty-string}. The work-style prompt asks the
    model to also list attributes that DIFFER from the author baseline, so a value can come back as a
    nested dict (e.g. {"differences": {"pacing": "..."}}). Hoist one level of nested string values to
    the top level and drop anything that is not a non-empty string (REC-021)."""
    out: dict[str, str] = {}
    if not isinstance(data, dict):
        return out
    for key, val in data.items():
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
        elif isinstance(val, dict):
            for sub_key, sub_val in val.items():
                if isinstance(sub_val, str) and sub_val.strip():
                    out.setdefault(sub_key, sub_val.strip())  # top-level value wins on collision
    return out


class StyleScout(LLMScout):
    """Scouts deep style attributes for authors and narrators using LLM knowledge."""

    def search(self, title: str, author: str, **kwargs) -> dict:
        """
        In the context of a work, we might want to scout the style of its primary author,
        the specific style of the work itself, and its narrators.
        """
        style_data = {"author_style": {}, "narrator_styles": {}, "work_style": {}}
        author_baseline = kwargs.get("author_styles", {})

        # 1. Scout Author Style
        style_data["author_style"] = self.scout_author_style(author)

        # 2. Scout Work Specific Style (Informed Scouting, ADR-023). For a new author the
        # DB baseline (author_styles kwarg) is empty, so fall back to the author style we
        # just scouted above rather than scouting the work with no baseline.
        baseline = author_baseline or style_data["author_style"]
        style_data["work_style"] = self.scout_work_style(title, author, author_baseline=baseline)

        # 3. Scout Narrator Styles (if provided in kwargs)
        narrators = kwargs.get("narrators", [])
        for n in narrators:
            style_data["narrator_styles"][n] = self.scout_narrator_style(n)

        return style_data

    def scout_work_style(self, title: str, author: str, author_baseline: dict = None) -> dict:
        """Extracts style attributes specific to a single work, informed by author's usual style."""
        baseline_str = ", ".join([f"{k}: {v}" for k, v in author_baseline.items()]) if author_baseline else "Unknown"

        prompt = f"""
        Analyze the literary style of the book: '{title}' by {author}

        The author's typical style baseline is: [{baseline_str}]

        Focus on work-specific attributes:
        - perspective: (e.g., 1st person, 3rd person limited, omniscient)
        - interiority: (e.g., deep character thoughts, external/plot-focused)
        - thematic_depth: (e.g., light/entertainment, moderate, heavy/philosophical)

        Also identify any attributes where this specific book DIFFERS from the author's usual baseline:
        - pacing, tone, style, prose_density, humor, etc.

        Return ONLY a raw JSON object with these keys.
        If an attribute is identical to the author's general baseline, omit it from the JSON.
        """
        text = self._llm.generate(prompt, grounded=True)
        return _flatten_style_map(self._safe_extract_json(text, "Work Style", title))

    def scout_author_style(self, name: str) -> dict:
        """Extracts style attributes for an author."""
        prompt = f"""
        Analyze the literary style of the author: {name}
        Focus on:
        - pacing: (e.g., fast-paced, slow-burn, brisk)
        - tone: (e.g., cynical, optimistic, atmospheric, clinical)
        - style: (e.g., lyrical, minimalist, technical, humorous)
        - prose_density: (e.g., minimalist, flowery, dense, accessible)
        - dialogue_style: (e.g., naturalistic, formal, witty, sparse)
        - lexicon: (e.g., archaic, academic, simple, specialized)
        - humor: (e.g., dry, satirical, slapstick, none)
        - world_building: (e.g., immersive, heavy, seamless, minimalist)
        - emotional_distance: (e.g., intimate, clinical, detached, warm)

        Return ONLY a raw JSON object with these nine keys.
        If unknown, return empty strings for those keys.
        """
        text = self._llm.generate(prompt, grounded=True)
        return _flatten_style_map(self._safe_extract_json(text, "Author Style", name))

    def scout_narrator_style(self, name: str) -> dict:
        """Extracts performance attributes for an audiobook narrator."""
        prompt = f"""
        Analyze the performance style of the audiobook narrator: {name}
        Provide values for:
        - pacing: (speed and rhythm)
        - voice_differentiation: (ability to create distinct characters)
        - accent_dialect: (accuracy/consistency of accents)
        - pitch_tone: (musicality/depth of voice)
        - consistency: (maintenance of tone/voices over time)
        - emotional_range: (conveying complex emotions)
        - gender_range: (believability of cross-gender performance)
        - production_quality: (clarity and technical quality)

        Return ONLY a raw JSON object with these keys. Use short descriptive phrases (3-5 words max).
        If unknown, return empty strings for those keys.
        """
        text = self._llm.generate(prompt, grounded=True)
        return _flatten_style_map(self._safe_extract_json(text, "Narrator Style", name))


class LLMTropeScout(LLMScout):
    """Scouts deep literary tropes for a work using LLM knowledge."""

    def search(self, title: str, author: str, **kwargs) -> dict:
        """Finds tropes for a specific work."""
        prompt = f"""
        Identify the top 5-10 literary tropes, narrative patterns, or character archetypes for the book:
        Title: {title}
        Author: {author}

        For each trope, provide:
        - trope_name: (standard name, e.g., 'The Chosen One', 'Found Family')
        - description: (general definition of the trope)
        - relevance_score: (float 0.0-1.0 indicating how central it is to this specific book)
        - justification: (how this trope manifests in this specific book)

        CRITICAL: Focus on narrative devices and archetypes. Avoid broad genres (Fantasy, Sci-Fi) or simple moods.
        If you cannot verify that this book actually exists, return {{"tropes": []}}.
        Return ONLY a raw JSON object with a 'tropes' key containing a list of these trope objects.
        """
        text = self._llm.generate(prompt, grounded=True)
        data = self._safe_extract_json(text, "Tropes", title) or {}
        # GH #98: an empty trope list means the model couldn't verify the book — return
        # falsy so ScoutManager doesn't count this scout as a contributing source.
        return data if data.get("tropes") else {}


# --- THE MANAGER ---


class ScoutManager:
    """Orchestrates multiple scouts and merges their results."""

    def __init__(self):
        self.scouts: list[tuple[BaseScout, int]] = []

    def register_scout(self, scout: BaseScout, priority: int = 1):
        """Registers a scout with a given priority (lower = higher priority)."""
        self.scouts.append((scout, priority))
        self.scouts.sort(key=lambda x: x[1])

    def enrich(self, title: str, author: str, format: str = "Paperback", **kwargs) -> dict:
        """Aggregates metadata from all registered scouts."""
        merged_data = {
            "title": title,
            "contributors": [{"name": author, "role": "Author"}],
            "genres": set(),
            "moods": set(),
            "source_priority": [],
            "author_style": {},
            "narrator_styles": {},
            "work_style": {},
            "narrator_names": [],
            "enriched_tropes": [],
        }

        for scout, _ in self.scouts:
            # Skip audiobook-only scouts if format is not audiobook
            if isinstance(scout, AudiobookScout | DirectKnowledgeScout) and "audiobook" not in format.lower():
                continue

            try:
                res = scout.search(title, author, format=format, narrators=merged_data["narrator_names"], **kwargs)
            except Exception as e:  # noqa: BLE001 - one scout's failure must not discard the others' data
                print(f"Warning: {scout.source_name} scout failed: {e}")
                continue
            if not res:
                continue

            merged_data["source_priority"].append(scout.source_name)

            # Prioritized Merge Logic:
            # Only update fields if they are currently empty (None, "", 0, or default title)
            # Since we iterate in priority order, the first valid value found stays.

            if isbn := res.get("isbn_13"):
                merged_data["isbn_13"] = merged_data.get("isbn_13") or isbn

            if merged_data["title"] == title and (t := res.get("title")):
                merged_data["title"] = t

            # Additive fields (Sets)
            merged_data["genres"].update(res.get("genres", []))
            merged_data["moods"].update(res.get("moods", []))

            # Merge Tropes (LLMTropeScout specific)
            if new_tropes := res.get("tropes"):
                merged_data["enriched_tropes"].extend(new_tropes)

            # Merge Contributors (Maintain uniqueness by name + role)
            existing_contributors = {(c["name"], c["role"]) for c in merged_data["contributors"]}
            for new_c in res.get("contributors", []):
                if (new_c["name"], new_c["role"]) not in existing_contributors:
                    merged_data["contributors"].append(new_c)
                    existing_contributors.add((new_c["name"], new_c["role"]))

            # Merge Styles (StyleScout specific)
            if style_data := res.get("author_style"):
                merged_data["author_style"].update(style_data)
            if w_style := res.get("work_style"):
                merged_data["work_style"].update(w_style)
            if n_styles := res.get("narrator_styles"):
                merged_data["narrator_styles"].update(n_styles)

            # Update Narrators list for StyleScout
            if new_narrators := res.get("narrator_names"):
                for n in new_narrators:
                    if n not in merged_data["narrator_names"]:
                        merged_data["narrator_names"].append(n)

            # Single-value fields loop
            for key in [
                "page_count",
                "description",
                "average_rating",
                "thumbnail",
                "audio_minutes",
                "publication_date",
            ]:
                if val := res.get(key):
                    merged_data[key] = merged_data.get(key) or val

            # Special handling for publication year
            if not merged_data.get("original_publication_year"):
                orig_date = res.get("original_publication_date") or res.get("published_date")
                if year := scout._extract_year(orig_date):
                    merged_data["original_publication_year"] = year

        # Final clean up
        merged_data["genres"] = list(merged_data["genres"])
        merged_data["moods"] = list(merged_data["moods"])

        # GH #98: merged_data is seeded from raw caller input, so it is ALWAYS truthy even
        # when every scout failed or returned nothing. If no scout contributed, return {} so
        # callers' not-found paths (books.py 404, import outcome, _run_scouts -> None) work.
        if not merged_data["source_priority"]:
            return {}

        return merged_data

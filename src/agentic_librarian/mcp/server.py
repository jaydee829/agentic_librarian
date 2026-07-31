from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from agentic_librarian.availability import service as availability_service
from agentic_librarian.availability.links import build_links
from agentic_librarian.core.user_context import get_required_user_id
from agentic_librarian.db.get_or_create import get_or_create
from agentic_librarian.db.models import (
    Author,
    AuthorStyle,
    Edition,
    ReadingHistory,
    Style,
    Suggestions,
    Trope,
    UserLibrary,
    Work,
    WorkContributor,
    WorkStyle,
    WorkTrope,
)
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.enrichment import two_phase
from agentic_librarian.enrichment.tasks import enqueue_enrichment
from agentic_librarian.enrichment.two_phase import _normalize, _normalized_col
from agentic_librarian.scouts.style_manager import StyleManager
from agentic_librarian.scouts.trope_manager import TropeManager
from agentic_librarian.scouts.utils import EMBED_MODEL, get_cached_embedding
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Initialize FastMCP server
mcp = FastMCP("agentic_librarian")

# Initialize DatabaseManager (ADR-006)
db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager):
    """Override the global db_manager (primarily for testing)."""
    global db_manager
    db_manager = new_manager


def _parse_uuid(value) -> UUID | None:
    """Validate an agent-supplied id as a UUID; None on anything else (SEC-002).
    Agents may pass titles or garbage where ids belong (REC-016) — never let that
    reach a psycopg2 UUID cast."""
    if not value:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value).strip())
    except (ValueError, TypeError):
        return None


def _current_user_id_or_none() -> str | None:
    """Best-effort attribution for the deep-enrichment enqueue (#100): chat tools run inside
    the turn's as_user(...) scope (chat/stream.py sets it around the driver; make_async_tool's
    asyncio.to_thread copies contextvars), so the user is normally present here — but the
    enqueue itself is best-effort by design, so a missing ambient user must never crash it."""
    try:
        return str(get_required_user_id())
    except Exception:  # noqa: BLE001 - defensive: no ambient user is a valid state for this call
        return None


def _normalize_status(value, allowed: tuple[str, ...]) -> str | None:
    """Case-insensitively match an agent-supplied status to a canonical member of
    `allowed`; None if it matches nothing (SEC-002: strict enum, no coercion)."""
    if not isinstance(value, str):
        return None
    needle = value.strip().lower()
    for canonical in allowed:
        if canonical.lower() == needle:
            return canonical
    return None


@mcp.tool()
def get_server_status() -> str:
    """Check if the Librarian MCP server is running and connected to DB."""
    try:
        with db_manager.get_session() as session:
            session.execute(select(1))
        return "Librarian MCP Server is online and DB connected."
    except Exception as e:
        return f"Librarian MCP Server error: {str(e)}"


# --- Candidate-pool ranking & exclusion (#125) --------------------------------------
# The pool that reaches the Critic must be relevance-ranked at every stage that limits:
# the old code joined works to the nearest tropes with an unordered `.limit()`, making
# pool membership arbitrary among every work sharing those tropes (how a thriller
# request retrieved We Are Legion). Negative targets make "less fantasy" a structural
# exclusion instead of a polite request. The pgvector statements are pg-only:
# compile-inspected in test_search_ranking.py, executed in test_internal_retrieval.py.

_POOL_NEAREST_TAGS = 20  # nearest tropes/styles feeding the pool (5 was a second collapse point)
_POOL_OVERFETCH = 3  # each arm over-fetches limit*3; exclusion/merge trims back to limit
_EXCLUDE_DEMOTE_DISTANCE = 0.35  # candidates this close to a negative rank below all clean ones
# untuned heuristic knob — real embedding-distance distributions should tune it (follow-up)


def _trope_rank_select(query_vector, trope_ids, pool_limit: int, exclude_work_ids=None):
    """Works ranked by their best-matching trope: cosine distance to the query vector,
    penalized for low link relevance (relevance 1.0 → ×1, relevance 0.0 → ×2).
    embedding IS NOT NULL everywhere a distance is aggregated: an unembedded trope can't
    be semantically near anything, and a NULL distance surviving into min() would put a
    Python None into the score maps (relevance_score itself is NOT NULL by schema)."""
    score = func.min(Trope.embedding.cosine_distance(query_vector) * (2.0 - WorkTrope.relevance_score)).label("score")
    stmt = (
        select(WorkTrope.work_id, score)
        .join(Trope, Trope.id == WorkTrope.trope_id)
        .where(WorkTrope.trope_id.in_(trope_ids), Trope.embedding.isnot(None))
    )
    if exclude_work_ids:
        stmt = stmt.where(WorkTrope.work_id.notin_(exclude_work_ids))
    return stmt.group_by(WorkTrope.work_id).order_by(score).limit(pool_limit)


def _work_style_rank_select(query_vector, style_ids, pool_limit: int, exclude_work_ids=None):
    score = func.min(Style.embedding.cosine_distance(query_vector)).label("score")
    stmt = (
        select(WorkStyle.work_id, score)
        .join(Style, Style.id == WorkStyle.style_id)
        .where(WorkStyle.style_id.in_(style_ids), Style.embedding.isnot(None))
    )
    if exclude_work_ids:
        stmt = stmt.where(WorkStyle.work_id.notin_(exclude_work_ids))
    return stmt.group_by(WorkStyle.work_id).order_by(score).limit(pool_limit)


def _author_style_rank_select(query_vector, style_ids, pool_limit: int, exclude_work_ids=None):
    score = func.min(Style.embedding.cosine_distance(query_vector)).label("score")
    stmt = (
        select(WorkContributor.work_id, score)
        .join(Author, Author.id == WorkContributor.author_id)
        .join(AuthorStyle, AuthorStyle.author_id == Author.id)
        .join(Style, Style.id == AuthorStyle.style_id)
        .where(AuthorStyle.style_id.in_(style_ids), Style.embedding.isnot(None))
    )
    if exclude_work_ids:
        stmt = stmt.where(WorkContributor.work_id.notin_(exclude_work_ids))
    return stmt.group_by(WorkContributor.work_id).order_by(score).limit(pool_limit)


def _neg_trope_distance_select(query_vector, work_ids):
    """Best (min) distance from each candidate's tropes to one negative target. Measured
    for EVERY candidate — no LIMIT; an exclusion check must never truncate."""
    return (
        select(WorkTrope.work_id, func.min(Trope.embedding.cosine_distance(query_vector)))
        .join(Trope, Trope.id == WorkTrope.trope_id)
        .where(WorkTrope.work_id.in_(work_ids), Trope.embedding.isnot(None))
        .group_by(WorkTrope.work_id)
    )


def _neg_style_distance_selects(query_vector, work_ids):
    work_arm = (
        select(WorkStyle.work_id, func.min(Style.embedding.cosine_distance(query_vector)))
        .join(Style, Style.id == WorkStyle.style_id)
        .where(WorkStyle.work_id.in_(work_ids), Style.embedding.isnot(None))
        .group_by(WorkStyle.work_id)
    )
    author_arm = (
        select(WorkContributor.work_id, func.min(Style.embedding.cosine_distance(query_vector)))
        .join(Author, Author.id == WorkContributor.author_id)
        .join(AuthorStyle, AuthorStyle.author_id == Author.id)
        .join(Style, Style.id == AuthorStyle.style_id)
        .where(WorkContributor.work_id.in_(work_ids), Style.embedding.isnot(None))
        .group_by(WorkContributor.work_id)
    )
    return work_arm, author_arm


def _merge_min(*score_maps: dict) -> dict:
    """Union score maps keeping the best (lowest) score per key."""
    merged: dict = {}
    for scores in score_maps:
        for key, value in scores.items():
            if key not in merged or value < merged[key]:
                merged[key] = value
    return merged


def _apply_exclusions(pos_scores: dict, neg_scores: dict) -> list:
    """Order candidates by positive score. DROP any at least as close to a negative
    target as to the positives (ties go to the exclusion — user feedback wins); DEMOTE
    near-misses (within _EXCLUDE_DEMOTE_DISTANCE of a negative) below all clean ones.
    pos_scores carry the relevance penalty while neg distances are raw — deliberate
    asymmetry: weak positive evidence earns less protection from a user's exclusion.
    A candidate with no links in an exclusion's space has no measured distance and is
    kept un-demoted (fail-open by design — no evidence to measure)."""
    kept, demoted = [], []
    for wid in sorted(pos_scores, key=pos_scores.get):
        neg = neg_scores.get(wid)
        if neg is None:
            kept.append(wid)
        elif neg <= pos_scores[wid]:
            continue
        elif neg <= _EXCLUDE_DEMOTE_DISTANCE:
            demoted.append(wid)
        else:
            kept.append(wid)
    return kept + demoted


def _warm_embeddings(texts: list[str]) -> None:
    """GH #123: warm the embedding LRU before the session opens so in-session
    _get_embedding calls are cache hits, not network round-trips held under a pooled
    connection (the pool's 5+2 sizing depends on no embed calls inside sessions)."""
    for text in texts:
        try:
            get_cached_embedding(EMBED_MODEL, text)
        except Exception:  # noqa: BLE001 - warming is best-effort; the in-session call retries
            logger.warning("embed warm failed for %r — retrying in-session", text)


@mcp.tool()
def search_internal_database(
    target_tropes: list[str],
    target_styles: list[str] = None,
    limit: int = 10,
    exclude_tropes: list[str] = None,
    exclude_styles: list[str] = None,
) -> list[dict]:
    """
    pgvector similarity search across tropes and literary styles, relevance-ranked.
    exclude_tropes/exclude_styles are NEGATIVE targets (session constraints like
    "less fantasy", "nothing gory"): candidates closer to a negative than to the
    positive targets are dropped; near-misses rank below all clean candidates.
    Results never include works already pitched and awaiting the user's reaction.
    """
    limit = max(int(limit), 0)
    if limit == 0:
        return []
    _warm_embeddings([*(target_tropes or []), *(target_styles or []), *(exclude_tropes or []), *(exclude_styles or [])])

    with db_manager.get_session() as session:
        tm = TropeManager(session=session)
        sm = StyleManager(session=session)
        pool_limit = max(limit, 1) * _POOL_OVERFETCH
        pos_scores: dict[UUID, float] = {}

        # Works with an active 'Suggested' suggestion for this user are excluded at the
        # ranking source: a deflected title must not be retrievable by Critic-direct search,
        # and it must not consume a result slot ahead of the limit (#125 follow-up).
        suggested = [
            row[0]
            for row in session.query(Suggestions.work_id)
            .filter(Suggestions.status == "Suggested", Suggestions.user_id == get_required_user_id())
            .all()
        ]

        # 1. Trope arm — ranked into the pool.
        if target_tropes:
            embeddings = [tm._get_embedding(t) for t in target_tropes]
            avg_vector = np.mean(embeddings, axis=0).tolist()
            trope_ids = [
                t.id
                for t in session.query(Trope)
                .filter(Trope.embedding.isnot(None))
                .order_by(Trope.embedding.cosine_distance(avg_vector))
                .limit(_POOL_NEAREST_TAGS)
                .all()
            ]
            if trope_ids:
                rows = session.execute(_trope_rank_select(avg_vector, trope_ids, pool_limit, suggested)).all()
                pos_scores = _merge_min(pos_scores, dict(rows))

        # 2. Style arm (work styles + primary-author styles) — ranked into the pool.
        if target_styles:
            s_embeddings = [sm._get_embedding(s) for s in target_styles]
            avg_style_vector = np.mean(s_embeddings, axis=0).tolist()
            style_ids = [
                s.id
                for s in session.query(Style)
                .filter(Style.embedding.isnot(None))
                .order_by(Style.embedding.cosine_distance(avg_style_vector))
                .limit(_POOL_NEAREST_TAGS)
                .all()
            ]
            for stmt in (
                _work_style_rank_select(avg_style_vector, style_ids, pool_limit, suggested),
                _author_style_rank_select(avg_style_vector, style_ids, pool_limit, suggested),
            ):
                if style_ids:
                    pos_scores = _merge_min(pos_scores, dict(session.execute(stmt).all()))

        if not pos_scores:
            return []

        # 3. Negative targets: best distance from each candidate to each exclusion.
        candidate_ids = list(pos_scores)
        neg_scores: dict[UUID, float] = {}
        for text in exclude_tropes or []:
            vec = tm._get_embedding(text)
            neg_scores = _merge_min(
                neg_scores, dict(session.execute(_neg_trope_distance_select(vec, candidate_ids)).all())
            )
        for text in exclude_styles or []:
            vec = sm._get_embedding(text)
            for stmt in _neg_style_distance_selects(vec, candidate_ids):
                neg_scores = _merge_min(neg_scores, dict(session.execute(stmt).all()))

        ordered_ids = _apply_exclusions(pos_scores, neg_scores)[:limit]
        if not ordered_ids:
            return []

        # 4. Final retrieval; restore the ranked order.
        works = (
            session.query(Work)
            .options(joinedload(Work.contributors).joinedload(WorkContributor.author))
            .filter(Work.id.in_(ordered_ids))
            .all()
        )
        works_by_id = {w.id: w for w in works}
        return [
            {
                "id": str(w.id),
                "title": w.title,
                "authors": [c.author.name for c in w.contributors],
                "genres": w.genres,
                "description": w.description,
            }
            for wid in ordered_ids
            if (w := works_by_id.get(wid)) is not None
        ]


@mcp.tool()
def get_unacted_suggestions(target_tropes: list[str], target_styles: list[str] = None, limit: int = 5) -> list[dict]:
    """
    Pulls previous recommendations that were never read or ignored,
    ranked by similarity to current target vibes.
    """
    user_id = get_required_user_id()
    # GH #123: warm before the session opens — a no-op cache-hit in-session either way
    # if there turn out to be no suggestions to rank.
    _warm_embeddings([*(target_tropes or []), *(target_styles or [])])

    with db_manager.get_session() as session:
        # 1. Get all unacted suggestions with Eager Loading (Fixes N+1)
        query = (
            session.query(Suggestions)
            .filter(Suggestions.status == "Suggested", Suggestions.user_id == user_id)
            .options(
                joinedload(Suggestions.work).options(
                    selectinload(Work.tropes).joinedload(WorkTrope.trope),
                    selectinload(Work.styles).joinedload(WorkStyle.style),
                    selectinload(Work.contributors)
                    .joinedload(WorkContributor.author)
                    .selectinload(Author.styles)
                    .joinedload(AuthorStyle.style),
                )
            )
        )
        suggestions = query.all()

        if not suggestions:
            return []

        # 2. Rank them semantically if targets are provided
        if not target_tropes and not target_styles:
            return [
                {
                    "id": str(s.work.id),
                    "title": s.work.title,
                    "justification": s.justification,
                    "suggested_at": s.suggested_at.isoformat() if s.suggested_at else None,
                }
                for s in suggestions[:limit]
            ]

        # Use TropeManager/StyleManager to get embeddings for ranking
        tm = TropeManager(session=session)
        sm = StyleManager(session=session)

        target_trope_vec = None
        if target_tropes:
            t_embeddings = [tm._get_embedding(t) for t in target_tropes]
            target_trope_vec = np.mean(t_embeddings, axis=0)

        target_style_vec = None
        if target_styles:
            s_embeddings = [sm._get_embedding(s) for s in target_styles]
            target_style_vec = np.mean(s_embeddings, axis=0)

        def score_suggestion(s):
            score = 0
            # Score by tropes linked to this suggestion's work
            if target_trope_vec is not None and s.work.tropes:
                work_trope_vecs = [
                    np.array(wt.trope.embedding) for wt in s.work.tropes if wt.trope.embedding is not None
                ]
                if work_trope_vecs:
                    avg_work_trope = np.mean(work_trope_vecs, axis=0)
                    score += np.dot(target_trope_vec, avg_work_trope)  # Cosine similarity assumes normalized

            # Score by styles linked to this suggestion's work or author
            if target_style_vec is not None:
                style_links = list(s.work.styles)
                # Primary author styles
                primary_contributor = next((c for c in s.work.contributors if c.role == "Author"), None)
                if primary_contributor:
                    style_links.extend(primary_contributor.author.styles)

                work_style_vecs = [np.array(sl.style.embedding) for sl in style_links if sl.style.embedding is not None]
                if work_style_vecs:
                    avg_work_style = np.mean(work_style_vecs, axis=0)
                    score += np.dot(target_style_vec, avg_work_style)

            return score

        ranked = sorted(suggestions, key=score_suggestion, reverse=True)

        return [
            {
                "id": str(s.work.id),
                "title": s.work.title,
                "justification": s.justification,
                "suggested_at": s.suggested_at.isoformat() if s.suggested_at else None,
            }
            for s in ranked[:limit]
        ]


def get_active_suggestion_work_ids() -> set[str]:
    """Work ids with an ACTIVE ('Suggested') suggestion for the current user. Fresh
    candidate sets exclude these (#125): a pitched-but-unacted book must not be
    re-offered on the next request; resolving the suggestion (Accepted / Dismissed /
    Already Read) frees the work again. A curation-layer helper (candidates.py), not
    an MCP tool — the LLM never needs it directly."""
    user_id = get_required_user_id()
    with db_manager.get_session() as session:
        rows = (
            session.query(Suggestions.work_id)
            .filter(Suggestions.status == "Suggested", Suggestions.user_id == user_id)
            .all()
        )
        return {str(wid) for (wid,) in rows}


def reread_eligibility(date_completed: date) -> tuple[bool, float]:
    """The re-read rule in ONE place: a finished book becomes re-read-eligible more than
    2.0 years after completion. Returns (is_re_read_candidate, years_since_completion)."""
    years_since = (date.today() - date_completed).days / 365.25
    return years_since > 2.0, years_since


@mcp.tool()
def check_reading_history(title: str, author: str) -> dict:
    """Checks if a book has been read and determines re-read eligibility."""
    user_id = get_required_user_id()
    with db_manager.get_session() as session:
        entry = (
            session.query(ReadingHistory)
            .join(Edition)
            .join(Work)
            .join(WorkContributor)
            .join(Author)
            .filter(ReadingHistory.user_id == user_id)
            .filter(Work.title == title)
            .filter(Author.name == author)
            .order_by(ReadingHistory.date_completed.desc())
            .first()
        )

        if entry:
            is_candidate, years_since = reread_eligibility(entry.date_completed)
            return {
                "status": "Read",
                "date_completed": entry.date_completed.isoformat(),
                "years_since_completion": round(years_since, 2),
                "is_re_read_candidate": is_candidate,
                "rating": entry.user_rating,
            }
        return {"status": "Unread", "is_re_read_candidate": True}


@mcp.tool()
def check_availability(title: str, author: str) -> dict:
    """Check library + retail availability for a book and return where to get it. Use when
    recommending a title or when the user asks where/how to read it. Returns {title, author,
    libraries: [{library, slug, formats:[{format, available, copies_available, copies_owned,
    holds_ratio, wait_days}]}], links:[{kind,label,url}], note}. 'libraries' is the user's
    saved Libby systems with live availability; 'links' (Libby/Hoopla/Bookshop/Amazon) is
    always present. Narrate it naturally; never paste the raw dict."""
    if not _valid_name(title) or not _valid_name(author):
        return {
            "title": title,
            "author": author,
            "libraries": [],
            "links": [],
            "note": "Error: title and author must be non-empty strings.",
        }
    user_id = get_required_user_id()  # before try: unset context must raise, not soft-fail (ADR-048)
    libraries: list[dict] = []
    note = ""
    with db_manager.get_session() as session:
        libs = [
            {"slug": r.library_slug, "name": r.display_name}
            for r in session.query(UserLibrary)
            .filter(UserLibrary.user_id == user_id, UserLibrary.provider == "libby")
            .order_by(UserLibrary.sort_order)
            .all()
        ]
    if not libs:
        note = "No libraries saved — the reader can add theirs in Settings."
    try:
        availability = availability_service.batch_availability(db_manager, libs, [(title, author)])
    except Exception as exc:  # noqa: BLE001 - never throw into the agent loop
        logger.warning("check_availability batch lookup failed for %r by %r: %s", title, author, exc)
        availability = {(lib["slug"], title, author): None for lib in libs}
    for lib in libs:
        formats = availability.get((lib["slug"], title, author))
        if formats is None:
            logger.warning(
                "check_availability lookup failed for %r by %r at %r",
                title,
                author,
                lib.get("name"),
            )
        elif formats:
            libraries.append({"library": lib["name"], "slug": lib["slug"], "formats": formats})
    links = build_links(title, author, libraries=libs)
    if libs and not libraries and not note:
        note = "Couldn't confirm live availability — offer the search links."
    return {"title": title, "author": author, "libraries": libraries, "links": links, "note": note}


@mcp.tool()
def get_read_status(work_ids: list[str]) -> dict:
    """Batch read-status for the current user across many works (one query). For each given
    work id: {"status": "Read"|"Unread", "last_read": ISO|None, "years_since": float|None,
    "is_re_read_candidate": bool, "rating": int|None}. Works with no read row are "Unread".
    Used by the recommendation curation to annotate candidates without N per-title calls."""
    user_id = get_required_user_id()
    by_uuid: dict = {}
    for wid in work_ids:
        u = _parse_uuid(wid)
        if u is not None:
            by_uuid[u] = wid
    result: dict[str, dict] = {
        wid: {
            "status": "Unread",
            "last_read": None,
            "years_since": None,
            "is_re_read_candidate": True,
            "rating": None,
        }
        for wid in work_ids
    }
    if not by_uuid:
        return result
    with db_manager.get_session() as session:
        rows = (
            session.query(ReadingHistory, Edition.work_id)
            .join(Edition)
            .filter(Edition.work_id.in_(list(by_uuid.keys())), ReadingHistory.user_id == user_id)
            .order_by(ReadingHistory.date_completed.desc())
            .all()
        )
        seen: set = set()
        for rh, work_uuid in rows:
            if work_uuid in seen:  # rows are date-desc; first per work is the latest read
                continue
            seen.add(work_uuid)
            is_candidate, years_since = reread_eligibility(rh.date_completed)
            result[by_uuid[work_uuid]] = {
                "status": "Read",
                "last_read": rh.date_completed.isoformat(),
                "years_since": round(years_since, 2),
                "is_re_read_candidate": is_candidate,
                "rating": rh.user_rating,
            }
    return result


@mcp.tool()
def get_recommendation_candidates(
    target_tropes: list[str],
    target_styles: list[str] | None = None,
    limit: int = 10,
    exclude_tropes: list[str] | None = None,
    exclude_styles: list[str] | None = None,
) -> dict:
    """Read-status-aware, novelty-balanced candidates for a recommendation. Returns
    {"candidates":[{id,title,authors,genres,description,read_status,last_read,rating}],
    "has_unread","unread_count","reread_count"}. candidates is unread-first and excludes books
    finished <2y ago, plus books already pitched and awaiting the user's reaction.
    ALWAYS pass the user's session constraints ("less fantasy", "nothing gory") as
    exclude_tropes/exclude_styles — matching candidates are structurally dropped.
    If has_unread is false, delegate to the Explorer for a fresh discovery.
    This is the Critic's primary catalog tool."""
    from agentic_librarian.agents.candidates import curate_candidates

    return curate_candidates(
        target_tropes, target_styles, limit=limit, exclude_tropes=exclude_tropes, exclude_styles=exclude_styles
    )


_READING_STATUSES = ("read",)


def _valid_name(value, max_len: int = 500) -> bool:
    """Non-empty string within length bounds — for agent-supplied titles/authors (SEC-002)."""
    return isinstance(value, str) and bool(value.strip()) and len(value) <= max_len


@mcp.tool()
def update_reading_status(
    title: str,
    author: str,
    status: str,
    notes: str | None = None,
    date_completed: str | None = None,
    year: int | None = None,
) -> str:
    """Updates history based on feedback (e.g. 'I read that years ago'). date_completed
    (ISO YYYY-MM-DD) takes precedence over year (a bare year is written as Jan 1 of that
    year — the documented convention for an unknown month/day, not a claim the user said
    "January 1"); with neither, the completion date is ASSUMED to be today and the reply
    says so — the caller should ask the user roughly when they read it if the exact date
    matters, since an assumed-today date wrongly blocks the 2-year re-read rule for years.
    Two 'read' calls with the same year (no exact date) resolve to the same Jan 1 date and
    dedup to a single row rather than logging a second read. A year-only entry also opens
    the 2-year re-read window up to ~11 months early, since it is dated Jan 1 regardless of
    which month the book was actually finished."""
    if not _valid_name(title):
        return "Error: title must be a non-empty string of at most 500 characters."
    if not _valid_name(author):
        return "Error: author must be a non-empty string of at most 500 characters."
    canonical = _normalize_status(status, _READING_STATUSES)
    if canonical is None:
        # Previously any unknown status returned success while writing NOTHING (silent
        # false-success). Reject honestly instead (SEC-002).
        return f"Error: status must be one of {', '.join(_READING_STATUSES)}; got {status!r}."
    notes = notes[:2000] if isinstance(notes, str) else None

    # date resolution (GH #112): date_completed > year (Jan 1 convention) > today-fallback.
    assumed_today = False
    if date_completed is not None:
        try:
            completed = date.fromisoformat(str(date_completed))
        except ValueError:
            return f"Error: date_completed must be ISO YYYY-MM-DD; got {date_completed!r}."
        if completed > date.today():
            return f"Error: date_completed {completed.isoformat()} is in the future."
    elif year is not None:
        if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= date.today().year:
            return f"Error: year must be between 1900 and {date.today().year}; got {year!r}."
        completed = date(year, 1, 1)  # convention: unknown month/day -> Jan 1 (documented)
    else:
        completed = date.today()
        assumed_today = True

    get_required_user_id()  # before try: unset context must raise, not soft-fail (ADR-048)
    try:
        with db_manager.get_session() as session:
            work = (
                session.query(Work)
                .join(WorkContributor)
                .join(Author)
                .filter(_normalized_col(Work.title) == _normalize(title))
                .filter(_normalized_col(Author.name) == _normalize(author))
                .first()
            )
            if not work:
                return f"Work '{title}' by {author} not found in database."
            work_id = work.id
            # Reuse the work's own edition format when it's unambiguous (exactly one
            # edition); otherwise "Unknown" as before. Scalar captured before the session
            # closes to avoid a DetachedInstanceError on the ORM object.
            editions = session.query(Edition).filter(Edition.work_id == work_id).all()
            edition_fmt = editions[0].format if len(editions) == 1 else "Unknown"
            edition_fmt = edition_fmt or "Unknown"
        if canonical == "read":
            logged = two_phase.add_read_event(work_id, completed=completed, rating=None, notes=notes, fmt=edition_fmt)
            if logged["already_logged"]:
                return f"'{title}' is already logged as completed {completed.isoformat()}. No new entry written."
        note = (
            " (completion date assumed today — ask the user when they read it if it matters)" if assumed_today else ""
        )
        return f"Successfully updated status for '{title}' to {status}.{note}"
    except Exception as e:
        return f"Error updating status: {str(e)}"


@mcp.tool()
def add_book_to_history(
    title: str,
    author: str,
    date_completed: str | None = None,
    rating: int | None = None,
    format: str = "ebook",
    notes: str | None = None,
) -> str:
    """Add ONE book to the reading history (single-title import). Enriches + persists the
    work first if it isn't in the catalog (fast metadata in seconds; the deep trope/style
    analysis runs in the background — the return message says when that applies), then logs
    a READ EVENT. History is a log of read events: a re-read (different completion date)
    inserts a new row; the same work+date is a duplicate and is not double-logged.
    date_completed defaults to today (the Phase-4 UI will auto-fill it visibly)."""
    if not _valid_name(title):
        return "Error: title must be a non-empty string of at most 500 characters."
    if not _valid_name(author):
        return "Error: author must be a non-empty string of at most 500 characters."
    if date_completed is None:
        completed = date.today()
    else:
        try:
            completed = date.fromisoformat(str(date_completed))
        except ValueError:
            return f"Error: date_completed must be ISO YYYY-MM-DD; got {date_completed!r}."
        if completed > date.today():
            return f"Error: date_completed {completed.isoformat()} is in the future."
    # bool is an int subclass — reject it explicitly so rating=True can't slip in as 1.
    if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5):
        return f"Error: rating must be an integer from 1 to 5; got {rating!r}."
    format = (format or "ebook")[:50]
    notes = notes[:2000] if isinstance(notes, str) else None
    get_required_user_id()  # before any work: unset context must raise, not soft-fail (ADR-048)

    resolved = None
    try:
        resolved = two_phase.enrich_fast(title, author, format)
    except Exception as e:  # noqa: BLE001 - tool surface: report, don't crash the agent loop
        return f"Error enriching '{title}': {e}"
    if resolved is None:
        return f"Error: could not resolve '{title}' by {author} — check the spelling, or the scouts found nothing."
    work_id, created = resolved
    enqueued = False
    if created:
        try:
            enqueued = enqueue_enrichment(str(work_id), user_id=_current_user_id_or_none())
        except Exception:  # noqa: BLE001 - deep pass is best-effort
            logger.exception("deep-enrichment enqueue failed for work %s", work_id)

    try:
        logged = two_phase.add_read_event(work_id, completed=completed, rating=rating, notes=notes, fmt=format)
    except Exception as e:  # noqa: BLE001
        return f"Error adding to reading history: {e}"
    if logged["already_logged"]:
        return f"'{title}' is already logged as completed {completed.isoformat()}. No new entry written."
    msg = f"Added '{title}' to your reading history (work {work_id}, read #{logged['read_number']})."
    if created and enqueued:
        msg += (
            " I'm still analyzing this book in the background (~1-2 minutes) — its tropes and"
            " styles will be ready on your next turn, so tell the user that and don't draw"
            " trope-based conclusions about it yet."
        )
    return msg


@mcp.tool()
def log_suggestion(work_id: str, context: str, justification: str, conversation_id: str | None = None) -> str:
    """Logs a new recommendation to the Suggestions table."""
    uuid_obj = _parse_uuid(work_id)
    if uuid_obj is None:
        return f"Error: work_id must be a valid UUID, got {work_id!r}."
    user_id = get_required_user_id()  # before try: unset context must raise, not soft-fail (ADR-048)
    try:
        with db_manager.get_session() as session:
            # SEC-002 referent check: a suggestion must point at a real catalog work.
            if session.get(Work, uuid_obj) is None:
                return f"Error: no work exists with id {work_id}."
            # GH #95 (closes #88's root cause): dedup on the active-suggestion partial
            # unique (user_id, work_id) WHERE status='Suggested' — get_or_create backstops
            # the same race the old unconditional INSERT was vulnerable to.
            _suggestion, created = get_or_create(
                session,
                Suggestions,
                work_id=uuid_obj,
                user_id=user_id,
                status="Suggested",
                defaults={
                    "context": (context or "")[:200],
                    "justification": (justification or "")[:2000],
                    "conversation_id": _parse_uuid(conversation_id),
                },
            )
            if not created:
                return f"Already an active suggestion for work {work_id} — not duplicated."
            return f"Logged suggestion for work {work_id}."
    except Exception as e:
        return f"Error logging suggestion: {str(e)}"


_SUGGESTION_STATUSES = ("Accepted", "Dismissed", "Already Read", "Removed")


@mcp.tool()
def update_suggestion_status(work_id: str, status: str) -> str:
    """
    Updates the status of a suggestion (e.g. 'Accepted', 'Dismissed', 'Already Read', 'Removed'
    — 'Removed' = neutral take-it-off-my-list-for-now, not negative feedback).
    This ensures unacted suggestions are cleaned up based on feedback.
    """
    uuid_obj = _parse_uuid(work_id)
    if uuid_obj is None:
        return f"Error: work_id must be a valid UUID, got {work_id!r}."
    canonical = _normalize_status(status, _SUGGESTION_STATUSES)
    if canonical is None:
        return f"Error: status must be one of {', '.join(_SUGGESTION_STATUSES)}; got {status!r}."
    user_id = get_required_user_id()  # before try: unset context must raise, not soft-fail (ADR-048)
    try:
        with db_manager.get_session() as session:
            suggestion = (
                session.query(Suggestions)
                .filter_by(work_id=uuid_obj, status="Suggested", user_id=user_id)
                .order_by(Suggestions.suggested_at.desc())
                .first()
            )
            if not suggestion:
                last = (
                    session.query(Suggestions)
                    .filter_by(work_id=uuid_obj, user_id=user_id)
                    .order_by(Suggestions.suggested_at.desc())
                    .first()
                )
                if last:
                    return f"Suggestion for work {work_id} already resolved (status: {last.status}) — nothing to do."
                return f"No active suggestion found for work {work_id}."

            suggestion.status = canonical
            session.flush()
            return f"Updated suggestion for work {work_id} to status: {canonical}."
    except Exception as e:
        return f"Error updating suggestion status: {str(e)}"


def _lift_ranked(
    user_counts: dict[str, int], catalog_counts: dict[str, int], user_works: int, catalog_works: int, limit: int
) -> list[str]:
    """Rank tropes by lift: (user share) / (catalog share), +1-smoothed on the catalog
    side so a trope the catalog barely has can't divide by ~zero. Ties break by raw user
    count (desc), then name (asc) for determinism."""
    if user_works == 0 or catalog_works == 0:
        return []

    def lift(name: str, count: int) -> float:
        user_share = count / user_works
        catalog_share = (catalog_counts.get(name, 0) + 1) / (catalog_works + 1)
        return user_share / catalog_share

    ranked = sorted(user_counts.items(), key=lambda item: (-lift(item[0], item[1]), -item[1], item[0]))
    return [name for name, _ in ranked[:limit]]


@mcp.tool()
def get_user_trope_preferences(limit: int = 20) -> list[str]:
    """Aggregates the user's frequent tropes from REAL (justified) scout links only,
    ranked by lift vs the catalog baseline (how much the user over-indexes on a trope
    relative to how common it is catalog-wide) rather than raw link frequency. This keeps
    genuine over-indexed favorites at the top while deflating tropes that are merely
    ubiquitous (fallback-pollution attractors — see #125/#70).

    Known, accepted limitation: real scout links with NULL justification (saved before
    justification was tracked) are under-counted here PERMANENTLY — the #70 repair
    deliberately spares them (it deletes only links re-derivable from the work's
    genres/moods), so they remain NULL-justified and invisible to this aggregation.
    Acceptable for a preference ranking; revisit only if legacy-heavy profiles look thin.
    """
    user_id = get_required_user_id()
    with db_manager.get_session() as session:
        justified = WorkTrope.justification.isnot(None)

        # Tropes present in books the user has read, and how many DISTINCT works link
        # each one (a work read twice must not double-count).
        user_counts = dict(
            session.query(Trope.name, func.count(func.distinct(WorkTrope.work_id)))
            .join(WorkTrope, WorkTrope.trope_id == Trope.id)
            .join(Work, Work.id == WorkTrope.work_id)
            .join(Edition, Edition.work_id == Work.id)
            .join(ReadingHistory, ReadingHistory.edition_id == Edition.id)
            .filter(ReadingHistory.user_id == user_id, justified)
            .group_by(Trope.name)
            .all()
        )

        user_works = (
            session.query(func.count(func.distinct(Edition.work_id)))
            .join(ReadingHistory, ReadingHistory.edition_id == Edition.id)
            .filter(ReadingHistory.user_id == user_id)
            .scalar()
            or 0
        )

        # Catalog-wide baseline, restricted to tropes the user actually has (only those
        # can rank), over ALL justified links (not just this user's).
        catalog_counts = dict(
            session.query(Trope.name, func.count(func.distinct(WorkTrope.work_id)))
            .join(WorkTrope, WorkTrope.trope_id == Trope.id)
            .filter(justified, Trope.name.in_(user_counts.keys()))
            .group_by(Trope.name)
            .all()
        )

        catalog_works = session.query(func.count(func.distinct(WorkTrope.work_id))).filter(justified).scalar() or 0

        return _lift_ranked(user_counts, catalog_counts, user_works, catalog_works, limit)


@mcp.tool()
def get_work_details(work_id: str) -> dict:
    """Returns metadata, tropes, and merged style profile for a work."""
    # Web-discovered candidates have no DB id; an agent may pass a title instead of a
    # UUID. Guard the lookup so a bad work_id returns no details rather than crashing the
    # run (the psycopg2 UUID cast would otherwise raise). Resolving discoveries to DB
    # works / enriching new ones is Spec 4.
    uuid_obj = _parse_uuid(work_id)
    if uuid_obj is None:
        return {}

    with db_manager.get_session() as session:
        work = session.query(Work).filter_by(id=uuid_obj).first()
        if not work:
            return {}

        tropes = [
            {
                "name": wt.trope.name,
                "description": wt.trope.description,
                "relevance": wt.relevance_score,
                "justification": wt.justification,
            }
            for wt in work.tropes
        ]

        # Style Inheritance/Override Logic:
        # 1. Start with Work-specific styles
        merged_styles = {ws.attribute_type: ws.style.name for ws in work.styles}

        # 2. Inherit from Primary Author for missing attributes
        # Find primary author (role='Author' or first contributor)
        primary_contributor = next((c for c in work.contributors if c.role == "Author"), None)
        if not primary_contributor and work.contributors:
            primary_contributor = work.contributors[0]

        if primary_contributor:
            author = primary_contributor.author
            for ads in author.styles:
                if ads.attribute_type not in merged_styles:
                    merged_styles[ads.attribute_type] = ads.style.name

        return {
            "title": work.title,
            "description": work.description,
            "genres": work.genres,
            "tropes": tropes,
            "styles": merged_styles,
        }


@mcp.tool()
def enrich_and_persist_work(title: str, author: str, format: str = "ebook") -> str | None:
    """De-dup a discovered book against the catalog; if new, run the FAST scouts and
    persist immediately, then queue the deep pass (tropes/styles) via Cloud Tasks —
    the same two-phase path bulk import uses (GH #93/#94: the old all-scouts inline run
    blocked the event loop for minutes). Returns the work_id, or None if the title did
    not resolve. A NEWLY persisted work has no trope/style fingerprint until the deep
    pass lands (~1-2 min): tell the user you are still investigating it, and do not
    anchor trope-based recommendations on it this turn. This is the single write
    surface for discoveries — a future authorization layer (SEC-002) wraps here."""
    # SEC-002: this is a write path fed by web-derived strings — validate shape upfront.
    if not _valid_name(title):
        logger.warning("enrich_and_persist_work rejected invalid title %r", title)
        return None
    if not _valid_name(author):
        logger.warning("enrich_and_persist_work rejected invalid author %r", author)
        return None
    try:
        resolved = two_phase.enrich_fast(title, author, format or "ebook")
        if resolved is None:
            return None
        work_id, created = resolved
        if created:
            try:
                enqueue_enrichment(str(work_id), user_id=_current_user_id_or_none())
            except Exception:  # noqa: BLE001 - deep pass is best-effort; fast data already persisted
                logger.exception("deep-enrichment enqueue failed for work %s", work_id)
        return str(work_id)
    except Exception:  # noqa: BLE001 - degrade gracefully, never crash the agent loop
        logger.exception("enrich_and_persist_work failed for %r by %r", title, author)
        return None


if __name__ == "__main__":
    mcp.run()

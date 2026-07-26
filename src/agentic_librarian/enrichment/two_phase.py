"""Two-phase enrichment service (Lift 2 Stage 3).

Fast pass: API scouts only (seconds) — persist the Work + log the read immediately.
Deep pass: the slow LLM scouts, run later by the Cloud Tasks internal endpoint, which
re-persists (updates) the SAME Work. Reuses the shared persist_enriched_work so the
catalog is built identically to the ETL and discovery paths (DRY).

Both passes run their scouts (external HTTP/LLM calls) with NO database session held
(GH #94) — a short read session first, then the scouts, then a fresh write session that
re-checks dedup before persisting (the #95 TOCTOU window is back to milliseconds instead
of the scouts' full duration).

This is a parallel surface to mcp/server.py's enrich_and_persist_work (the all-scouts
discovery write tool). enrich_and_persist_work now ROUTES THROUGH enrich_fast (not left
untouched — corrected, final review Minor 6), so it is covered by enrich_fast's
pg_advisory_xact_lock dedup guard the same as every other fast-pass caller: same persist
core, tiered scouts."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy import text as sa_text  # aliased: a loop variable in _warm_embeddings shadows 'text' (F402)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from agentic_librarian.core.user_context import get_required_user_id
from agentic_librarian.db.get_or_create import get_or_create
from agentic_librarian.db.models import (
    Author,
    DetectedDuplicate,
    Edition,
    ReadingHistory,
    Suggestions,
    Work,
    WorkContributor,
)
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import collect_embedding_texts, merge_edition_and_narrators, persist_enriched_work
from agentic_librarian.orchestration.definitions import (
    create_completion_scout_manager,
    create_deep_scout_manager,
    create_fast_scout_manager,
)
from agentic_librarian.scouts.metadata_scout import StyleScout
from agentic_librarian.scouts.style_manager import StyleManager
from agentic_librarian.scouts.trope_manager import TropeManager
from agentic_librarian.scouts.utils import EMBED_MODEL, get_cached_embedding

logger = logging.getLogger(__name__)

# Module-level fallback pool for non-API processes; the API lifespan injects its shared
# manager via set_db_manager (GH #102 consolidation — the old "deferred to Stage 4" note was stale).
db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override the module db_manager (for tests) — the mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


def _normalize(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _normalized_col(col):
    """SQL-side equivalent of _normalize: lowercase, collapse whitespace, trim."""
    return func.trim(func.regexp_replace(func.lower(col), r"\s+", " ", "g"))


def _run_scouts(manager, *, title: str, author: str, fmt: str, write_fallback_tropes: bool = True) -> dict | None:
    """Run a scout tier with NO session held (GH #94 — the external HTTP/LLM calls used
    to pin a pooled connection idle-in-transaction for their whole duration). Returns the
    persist-ready row dict, or None if the scouts found nothing. date_completed=None so
    persist writes NO reading_history (the read-event is logged separately)."""
    enriched = manager.enrich(title=title, author=author, format=fmt)
    if not enriched:
        return None
    return {
        "Title": title,
        "Author_1": author,
        "format": fmt,
        "skip_enrichment": False,
        "date_completed": None,
        **enriched,
        "genres": list(enriched.get("genres") or []),
        "moods": list(enriched.get("moods") or []),
        # after **enriched so a scout payload can never clobber the caller's choice
        "write_fallback_tropes": write_fallback_tropes,
    }


def _persist_row(session, row: dict) -> Work | None:
    """Persist a scouted row via the shared function (the only part needing a session)."""
    return persist_enriched_work(session, row, TropeManager(session=session), StyleManager(session=session))


def _warm_embeddings(row: dict) -> None:
    """GH #123: warm the embedding LRU with every text the persist will standardize, so no
    network embed happens inside the write session (the pool's 5+2 sizing depends on it).
    Best-effort — a warm failure just means that item embeds in-session as before
    (_safe_standardize in etl/persist.py remains the net that degrades gracefully there)."""
    for text in collect_embedding_texts(row):
        try:
            get_cached_embedding(EMBED_MODEL, text)
        except Exception:  # noqa: BLE001 - warming is best-effort; _safe_standardize degrades in-session
            logger.warning("embed warm failed for %r — persist will retry in-session", text)


def enrich_fast(title: str, author: str, fmt: str = "ebook") -> tuple[UUID, bool] | None:
    """Fast pass: de-dup against the catalog; if new, run the API scouts (no session
    held, #94) and persist in a fresh session that RE-CHECKS the dedup (a concurrent
    import may have inserted the work while the scouts ran — the #95 TOCTOU window is
    back to milliseconds). Returns (work_id, created), or None if the scouts found
    nothing. Logs NO reading_history (see add_read_event)."""
    fmt = (fmt or "ebook")[:50]

    def _find_existing(session):
        return (
            session.query(Work)
            .join(WorkContributor)
            .join(Author)
            .filter(_normalized_col(Work.title) == _normalize(title))
            .filter(_normalized_col(Author.name) == _normalize(author))
            .first()
        )

    with db_manager.get_session() as session:
        existing = _find_existing(session)
        if existing:
            return existing.id, False  # UUID scalar — safe after close

    row = _run_scouts(create_fast_scout_manager(), title=title, author=author, fmt=fmt, write_fallback_tropes=False)
    if row is None:
        return None

    _warm_embeddings(row)

    with db_manager.get_session() as session:
        if session.get_bind().dialect.name == "postgresql":
            # GH #95: works can't carry a cross-table unique (title+author spans tables) —
            # serialize concurrent same-book creators instead. xact-scoped: released on commit.
            session.execute(
                sa_text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                {"k": f"{_normalize(title)}|{_normalize(author)}"},
            )
        existing = _find_existing(session)  # dedup re-check (#94/#95)
        if existing:
            return existing.id, False
        work = _persist_row(session, row)
        if work is None:
            return None
        session.flush()
        return work.id, True


def _resolve_active_pick(session, *, work_id: UUID, user_id: UUID) -> bool:
    """GH #130: flip the user's active 'Suggested' pick for work_id to 'Read'. The
    partial unique index (user_id, work_id) WHERE status='Suggested' guarantees at
    most one row; flipping OFF 'Suggested' cannot violate any constraint. Only
    'Suggested' rows are touched — resolved statuses are never rewritten."""
    pick = (
        session.query(Suggestions)
        .filter(
            Suggestions.work_id == work_id,
            Suggestions.user_id == user_id,
            Suggestions.status == "Suggested",
        )
        .first()
    )
    if pick is None:
        return False
    pick.status = "Read"
    return True


def add_read_event(work_id: UUID, *, completed, rating: int | None, notes: str | None, fmt: str) -> dict:
    """Log a read-event for the current user against work_id (the existing
    add_book_to_history semantics: a re-read on a new date is a new row; the same
    work+date is a no-op). Requires user context (as_user / the auth dependency).

    GH #130: adding to history resolves any pick active at that moment — any
    'Suggested' row for (user, work_id) is resolved to 'Read' in
    the same transaction, on BOTH branches (a duplicate add still clears a stale
    pick). All four history-writing paths (POST /books, both chat MCP tools, the
    CSV import worker) flow through here and inherit it."""
    user_id = get_required_user_id()
    with db_manager.get_session() as session:
        pick_resolved = _resolve_active_pick(session, work_id=work_id, user_id=user_id)
        prior_reads = (
            session.query(ReadingHistory)
            .join(Edition)
            .filter(Edition.work_id == work_id, ReadingHistory.user_id == user_id)
            .all()
        )
        if any(r.date_completed == completed for r in prior_reads):
            return {"read_number": len(prior_reads), "already_logged": True, "pick_resolved": pick_resolved}
        # GH #95: uq_editions_work_format backstops this get-then-create against a
        # concurrent add_read_event/persist race for the same (work_id, format).
        edition, _created = get_or_create(session, Edition, work_id=work_id, format=fmt)
        session.add(
            ReadingHistory(
                edition_id=edition.id,
                user_id=user_id,
                date_completed=completed,
                user_rating=rating,
                user_notes=notes,
            )
        )
        session.flush()
        return {"read_number": len(prior_reads) + 1, "already_logged": False, "pick_resolved": pick_resolved}


def complete_edition(work_id: UUID, fmt: str, api_key: str | None = None, key_source: str = "app") -> str:
    """Format-completion pass (history-format-edit spec): fill the (work_id, fmt)
    edition's metadata after a history-entry format change. Fast API scouts always
    (ISBN, pages/audio minutes, publication date); audiobook scouts + per-narrator
    style scouting only when fmt is an audiobook. Deliberately NEVER LLMTropeScout or
    author/work-style scouting — the Work is unchanged; this pass must not touch
    deep_enriched_at or the deep pass's retry gating.

    Same session discipline as the other passes here (#94): short read session, scouts
    with NO session held, fresh write session. Idempotent (all merges) — Cloud Tasks
    redelivery is safe.

    Returns:
      "missing" — work, its Author link, or the (work_id, fmt) edition no longer exists
                  (also when the work or edition vanished while scouts ran — re-checked in
                  the write session). Non-retryable.
      "empty"   — no scout contributed anything, INCLUDING the all-scouts-failed case: a
                  transient outage of every scout lands here too, because ScoutManager.enrich
                  swallows each scout's exception internally and returns {} when nobody
                  contributed (the GH #98 guard). This is a deliberate FINAL state, not a
                  transient error to retry-loop: the history entry is already saved, and a
                  later format change re-triggers completion. Unlike the deep pass there is no
                  requeue-sweep backstop economics to protect. A 500 → Cloud Tasks retry only
                  occurs for failures OUTSIDE the scout manager (persist/DB errors), which
                  propagate uncaught.
      "done"    — scouted values merged onto the edition.

    `api_key`/`key_source` (arc 3/3 BYOK) thread into the completion scout manager's LLM
    scouts and the narrator-style StyleScout call below — a byok user's own key, or the
    app key by default."""
    fmt = (fmt or "")[:50]

    with db_manager.get_session() as session:
        work = session.get(Work, work_id)
        if work is None:
            return "missing"
        author = next((c.author.name for c in work.contributors if c.role == "Author"), None)
        if author is None:
            return "missing"
        title = work.title  # scalars captured before close (detached-instance rule)
        edition = session.query(Edition).filter_by(work_id=work_id, format=fmt).first()
        if edition is None:
            return "missing"

    enriched = create_completion_scout_manager(api_key, key_source).enrich(title=title, author=author, format=fmt)
    if not enriched:
        return "empty"

    narrator_names = [n for n in (enriched.get("narrator_names") or []) if isinstance(n, str) and n.strip()]
    narrator_styles: dict[str, dict] = {}
    if "audiobook" in fmt.lower() and narrator_names:
        style_scout = StyleScout(api_key, key_source=key_source)
        for n_name in narrator_names:
            try:
                narrator_styles[n_name] = style_scout.scout_narrator_style(n_name)
            except Exception:  # noqa: BLE001 - style scouting is additive; narrators still merge without it
                logger.warning("narrator style scout failed for %r — merging narrator without styles", n_name)

    # GH #123: warm the embedding LRU for every style string the persist will standardize,
    # so no network embed happens inside the write session.
    for style_map in narrator_styles.values():
        for style_text in style_map.values():
            try:
                get_cached_embedding(EMBED_MODEL, style_text)
            except Exception:  # noqa: BLE001 - warming is best-effort; _safe_standardize degrades in-session
                logger.warning("embed warm failed for %r — persist will retry in-session", style_text)

    with db_manager.get_session() as session:
        if session.get(Work, work_id) is None:
            # Deleted while the scouts ran with no session held — same honesty rule as
            # enrich_deep's empty path.
            return "missing"
        if session.query(Edition).filter_by(work_id=work_id, format=fmt).first() is None:
            # The (work_id, fmt) edition was deleted mid-pass (operator cleanup tooling deletes
            # editions for real) — re-check it in the WRITE session too, else
            # merge_edition_and_narrators would silently RECREATE it. Same honesty rule as the
            # Work re-check above and enrich_deep's deleted-mid-pass cases.
            return "missing"
        merge_edition_and_narrators(
            session,
            work_id=work_id,
            fmt=fmt,
            isbn_13=enriched.get("isbn_13"),
            page_count=enriched.get("page_count"),
            audio_minutes=enriched.get("audio_minutes"),
            publication_date=enriched.get("publication_date"),
            narrator_names=narrator_names,
            narrator_styles=narrator_styles,
            style_manager=StyleManager(session=session),
        )
        session.flush()
    return "done"


def enrich_deep(work_id: UUID, api_key: str | None = None, key_source: str = "app") -> str:
    """Deep pass (Cloud Tasks target): read the Work's identity in a short session, run
    the slow LLM scouts with NO session held (#94 — previously minutes idle-in-transaction
    at enrich-queue concurrency 4, where a late transient failure also re-paid every LLM
    call), then re-persist in a fresh session.

    GH #141: the write session's persist_enriched_work re-checks dedup by SCOUT-CANONICAL
    identity (title + author as the scouts now report them), which can differ from the
    invoked work's own (possibly dirty) identity and resolve to a DIFFERENT existing Work
    — the invoked row's twin. This is not undone (the persist legitimately lands on the
    twin — it's the same book); refusing would require re-plumbing persist_enriched_work's
    many callers for zero data benefit. Instead the redirect is recorded in
    detected_duplicates (the works-merge tool's feed) and the INVOKED row is stamped, so
    the requeue sweep stops re-listing it (closing the paid-pass-burning loop).

    Returns one of:
      "missing"    — no Work has that id, or it has no linked Author (non-retryable). Also
                     returned (final-review Minor 7 honesty fix) if the write session's
                     persist_enriched_work returns None — nothing was persisted and
                     deep_enriched_at was never stamped, so "done" would be a lie; "missing"
                     tells the caller (api/internal.py) this is non-retryable the same way an
                     already-gone Work is, rather than falsely reporting success. Also
                     returned (PR #126 review) if the empty-path stamp session finds the Work
                     gone — it was deleted while the slow scouts were running with no session
                     held. Also returned (#141) if the redirect path's re-load of the invoked
                     row by id finds it gone — same deleted-mid-pass honesty rule.
      "empty"      — the scouts yielded nothing to add this pass, and the Work still exists. A
                     SHORT session stamps work.deep_enriched_at = now() anyway (GH #97): the
                     timestamp means "the deep pass COMPLETED", including confirmed-empty — the
                     caller (api/internal.py) decides retryability from the work's real-trope
                     state, not from this string alone. An exception raised before this point
                     propagates uncaught, leaving deep_enriched_at unstamped so Cloud Tasks
                     retries the whole pass.
      "done"       — the scouts found something AND the write session actually persisted +
                     stamped deep_enriched_at on the SAME Work in the SAME session.
      "redirected" — (#141) the scouts found something, but persist landed it on a DIFFERENT
                     existing Work (the twin) instead of the invoked one. The invoked row is
                     stamped deep_enriched_at (the pass DID complete) and a detected_duplicates
                     row is recorded; the twin's data is untouched by this function beyond
                     persist's own write. Non-retryable success — see api/internal.py.

    `api_key`/`key_source` (arc 3/3 BYOK) thread into the deep scout manager's LLM scouts —
    a byok user's own key, or the app key by default.

    Reviewer-found bug (fixed here): the redirect branch used to INSERT the detected_duplicates
    row (work_id_a=work_id) BEFORE re-loading the invoked work by id. If the invoked row had
    been deleted mid-pass, that insert FK-violated on work_id_a, and the raised exception rolled
    back the WHOLE write session — including the twin's already-`persist`ed pass data in the
    same transaction — even though the function documents a clean "missing" return for exactly
    this case. The existence check now runs FIRST, before any write, so a vanished invoked row
    takes the "missing" path without touching the twin's legitimately-persisted data. Residual:
    there is still a TOCTOU window between this get() and the session's eventual commit — the
    invoked row could be deleted in that gap and the FK violation is possible in principle, but
    it is now a millisecond window instead of spanning the full scout call, matching the same
    residual accepted elsewhere in this module (#94/#95)."""
    with db_manager.get_session() as session:
        work = session.get(Work, work_id)
        if work is None:
            return "missing"
        author = next((c.author.name for c in work.contributors if c.role == "Author"), None)
        if author is None:
            return "missing"
        title = work.title  # scalars captured before close (detached-instance rule)
        fmt = work.editions[0].format if work.editions else "ebook"

    row = _run_scouts(create_deep_scout_manager(api_key, key_source), title=title, author=author, fmt=fmt)
    if row is None:
        # scouts found nothing to add; the pass is done (not retryable on its own), but
        # stamp completion so the requeue sweep doesn't treat this work as never-attempted.
        with db_manager.get_session() as session:
            w = session.get(Work, work_id)
            if w is None:
                # The Work was deleted while the slow scouts were running. Falling through
                # to "empty" would make the internal endpoint 503 and buy a pointless Cloud
                # Tasks retry before the next pass 404s anyway -- report "missing" now, same
                # honesty rule as the other non-retryable cases in this function.
                return "missing"
            w.deep_enriched_at = datetime.now(UTC)
        return "empty"

    _warm_embeddings(row)

    with db_manager.get_session() as session:
        persisted = _persist_row(session, row)
        if persisted is None:
            # Nothing was persisted (e.g. the scouted row had no usable contributor to attach
            # to — persist_enriched_work's own "no work" case) and deep_enriched_at was never
            # stamped. Reporting "done" here would be dishonest: the caller would treat a
            # no-op as success. "missing" makes it non-retryable the same way an already-gone
            # Work is (final-review Minor 7).
            return "missing"
        if persisted.id == work_id:
            persisted.deep_enriched_at = datetime.now(UTC)
            session.flush()
            return "done"

        # #141: persist's dedup re-check resolved a DIFFERENT existing work (the twin) — same
        # book, dirty invoked-row identity. Do NOT undo the twin's write. Record the redirect
        # (upsert-or-ignore: a redelivered Cloud Tasks retry must not pile up duplicate rows)
        # and stamp the INVOKED row, re-loaded by id since `persisted` is the twin, not it.
        #
        # Existence check FIRST, before the detected_duplicates insert (reviewer finding): the
        # invoked row may have been deleted while the slow scouts ran with no session held. If
        # it's gone, work_id_a=work_id would FK-violate on insert and roll back this whole write
        # session — including the twin's persist above, in the SAME transaction — turning the
        # documented "missing" return into unreachable dead code and destroying the twin's
        # legitimate data as collateral damage. Checking first keeps the vanished-row case a
        # clean no-op read. A millisecond TOCTOU window remains between this get() and commit
        # (same residual accepted elsewhere in this module, #94/#95).
        invoked = session.get(Work, work_id)
        if invoked is None:
            # The invoked row was deleted while the slow scouts were running (same
            # deleted-mid-pass honesty rule as the other "missing" cases above).
            return "missing"
        session.execute(
            pg_insert(DetectedDuplicate)
            .values(work_id_a=work_id, work_id_b=persisted.id, source="deep_pass_redirect")
            .on_conflict_do_nothing(index_elements=["work_id_a", "work_id_b"])
        )
        invoked.deep_enriched_at = datetime.now(UTC)
        session.flush()
        return "redirected"

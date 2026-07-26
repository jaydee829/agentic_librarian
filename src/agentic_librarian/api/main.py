import asyncio
import concurrent.futures
import contextlib
import logging
import os
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from agentic_librarian.agents.runtime import LibrarianConversation, astart_conversation
from agentic_librarian.api import analysis, auth, recommendations
from agentic_librarian.api import availability as availability_api
from agentic_librarian.api import imports as imports_api
from agentic_librarian.api import kofi as kofi_api
from agentic_librarian.api import libraries as libraries_api
from agentic_librarian.api.analysis import router as analysis_router
from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.api.availability import router as availability_router
from agentic_librarian.api.books import router as books_router
from agentic_librarian.api.firebase_auth_proxy import router as firebase_auth_proxy_router
from agentic_librarian.api.imports import router as imports_router
from agentic_librarian.api.internal import router as internal_router
from agentic_librarian.api.kofi import router as kofi_router
from agentic_librarian.api.libraries import router as libraries_router
from agentic_librarian.api.recommendations import router as recommendations_router
from agentic_librarian.chat import stream, transcript
from agentic_librarian.core import budgets, tiers, usage
from agentic_librarian.core.user_context import as_user
from agentic_librarian.db.get_or_create import get_or_create
from agentic_librarian.db.migration_guard import check_migrations
from agentic_librarian.db.models import (
    Edition,
    ReadingHistory,
    User,
    Work,
    WorkContributor,
    WorkStyle,
    WorkTrope,
)
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.enrichment import two_phase
from agentic_librarian.enrichment.tasks import enqueue_edition_completion
from agentic_librarian.imports import worker as imports_worker
from agentic_librarian.mcp import server as mcp_server

logger = logging.getLogger(__name__)

db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override this module's db_manager (tests / the shared-pool lifespan) — mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


def _pin_default_executor() -> None:
    # GH #93: tool bodies, auth resolves, and enqueue loops all run via asyncio.to_thread.
    # Python's DEFAULT executor is min(32, cpu_count+4) ≈ 5-6 threads on Cloud Run's 1 vCPU —
    # five concurrent slow tool calls would queue every request's auth resolve behind them.
    # Pin 32 workers (I/O-bound work; threads are cheap) so capacity doesn't scale with vCPUs.
    asyncio.get_running_loop().set_default_executor(
        concurrent.futures.ThreadPoolExecutor(max_workers=32, thread_name_prefix="offloop")
    )


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Build ONE DatabaseManager at startup and inject it into the API-request-path
    modules that each owned a lazy pool (main, auth, transcript, usage, recommendations,
    analysis) — Lift 2 Stage 4 consolidation. mcp/server, enrichment/two_phase, and
    imports/worker join the fan-out (GH #102); their module-level managers remain as
    fallbacks for non-API processes (Dagster, CLI). Lazy construction means no DB
    connection happens here. Tests that use TestClient WITHOUT a `with` block skip
    lifespan and keep their own monkeypatched managers."""
    _pin_default_executor()
    shared = DatabaseManager()
    # ADR-058 (#92): refuse to serve when the DB schema is behind this code's migration
    # head — the failed revision keeps traffic on the previous one. Unreachable DB only
    # warns (cold-start protection); MIGRATION_GUARD=off is the emergency bypass.
    check_migrations(shared)
    app.state.db_manager = shared
    set_db_manager(shared)
    auth.set_db_manager(shared)
    transcript.set_db_manager(shared)
    usage.set_db_manager(shared)
    recommendations.set_db_manager(shared)
    analysis.set_db_manager(shared)
    imports_api.set_db_manager(shared)
    availability_api.set_db_manager(shared)
    libraries_api.set_db_manager(shared)
    # GH #102: the in-process chat tools (mcp/server), the enrichment paths
    # (two_phase), and the import worker previously each held their own lazy pool —
    # up to ~9 engines/process. One pool per process keeps the connection math sane.
    mcp_server.set_db_manager(shared)
    two_phase.set_db_manager(shared)
    imports_worker.set_db_manager(shared)
    budgets.set_db_manager(shared)
    kofi_api.set_db_manager(shared)
    yield


# Stage 4 opens the Cloud Run IAM gate, making this service publicly reachable (Firebase
# gates the data routes; /health and the SPA are intentionally public). Disable FastAPI's
# auto-docs so the full API schema isn't exposed unauthenticated. (GET /docs etc. now fall
# through to the SPA catch-all, which is harmless.)
app = FastAPI(title="Agentic Librarian API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

# User-facing data routes live under /api/* so they can never collide with an SPA client
# route (#151, follow-up to #150). The prefix lives in exactly one place.
api_router = APIRouter(prefix="/api")
api_router.include_router(recommendations_router)
api_router.include_router(analysis_router)
api_router.include_router(availability_router)
api_router.include_router(books_router)
api_router.include_router(imports_router)
api_router.include_router(libraries_router)

# Machine-only routers stay at root — never SPA-route collisions, and moving them would
# break fixed contracts (Firebase /__/auth/*) or Cloud Tasks target URLs (/internal/*).
app.include_router(firebase_auth_proxy_router)
app.include_router(internal_router)
app.include_router(kofi_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/health/db")
def db_health_check(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    try:
        with db_manager.get_session() as session:
            session.execute(text("SELECT 1"))
        return {"status": "connected"}
    except Exception as e:
        # 503 so platform health probes and monitors see the failure (HTTP status,
        # not body, is what they key on). Detail is safe: the service is IAM-gated.
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})


def _ranked_tropes(work_tropes):
    """Real scout tropes (justified) outrank genre/mood slug fallbacks (justification is
    NULL, default relevance 1.0) — otherwise slugs crowd every display list (#70).
    The sort touches wt.trope.name for EVERY link: callers must eager-load
    WorkTrope.trope (both current call sites do) or this becomes an N+1."""
    return sorted(work_tropes, key=lambda wt: (wt.justification is None, -wt.relevance_score, wt.trope.name.lower()))


def _history_item(h) -> dict:
    """Serialize one ReadingHistory row to the /history payload shape (shared by GET + PATCH)."""
    work = h.edition.work
    top = _ranked_tropes(work.tropes)[:3]
    return {
        "id": str(h.id),
        "title": work.title,
        "authors": [c.author.name for c in work.contributors if c.role == "Author"],
        "date_completed": h.date_completed.isoformat() if h.date_completed else None,
        "rating": h.user_rating,
        "format": h.edition.format,
        "notes": h.user_notes,
        "genre": work.genres[0] if work.genres else None,
        "tropes": [wt.trope.name for wt in top],
    }


def _history_options():
    """Shared eager-load options for ReadingHistory queries (GET + PATCH)."""
    return [
        joinedload(ReadingHistory.edition)
        .joinedload(Edition.work)
        .joinedload(Work.contributors)
        .joinedload(WorkContributor.author),
        joinedload(ReadingHistory.edition)
        .joinedload(Edition.work)
        .selectinload(Work.tropes)
        .joinedload(WorkTrope.trope),
    ]


@api_router.get("/history")
def get_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    """Paginated reading log, newest first (INF-029 — mirrors /works)."""
    with db_manager.get_session() as session:
        # Query reading history with eager loading for efficiency
        history_entries = (
            session.query(ReadingHistory)
            .join(Edition)
            .join(Work)
            .filter(ReadingHistory.user_id == user.id)  # my history, not the commons (ADR-048)
            # joinedload on the to-many Work.contributors is safe under LIMIT *here* (unlike
            # /works): the paginated root is ReadingHistory and the collection sits two to-one
            # hops below it, so SQLAlchemy subquery-wraps the LIMIT against ReadingHistory rows.
            # Work.tropes is a to-many so we use selectinload (separate IN query) to avoid
            # cartesian-multiplying rows with the contributors joinedload.
            .options(*_history_options())
            .order_by(ReadingHistory.date_completed.desc(), ReadingHistory.id)
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [_history_item(h) for h in history_entries]


HISTORY_FORMATS = {"ebook", "audiobook", "paperback", "hardcover"}


class HistoryUpdate(BaseModel):
    date_completed: date | None = None
    rating: int | None = None
    notes: str | None = None
    format: str | None = None

    @field_validator("rating", mode="before")
    @classmethod
    def _no_bool_rating(cls, v: object) -> object:
        if isinstance(v, bool):
            raise ValueError("rating must be an integer, not a boolean")
        return v

    @field_validator("rating")
    @classmethod
    def _rating_range(cls, v: int | None) -> int | None:
        if v is not None and not 1 <= v <= 5:
            raise ValueError("rating must be from 1 to 5")
        return v

    @field_validator("date_completed")
    @classmethod
    def _not_future(cls, v: date | None) -> date | None:
        if v is not None and v > date.today():
            raise ValueError("date_completed cannot be in the future")
        return v

    @field_validator("format")
    @classmethod
    def _format_vocab(cls, v: str | None) -> str | None:
        if v is None:
            return v
        normalized = v.strip().lower()
        if normalized not in HISTORY_FORMATS:
            raise ValueError("format must be one of: ebook, audiobook, paperback, hardcover")
        return normalized


@api_router.delete("/history/{entry_id}")
def delete_history(entry_id: UUID, user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with db_manager.get_session() as session:
        row = (
            session.query(ReadingHistory)
            .filter(ReadingHistory.id == entry_id, ReadingHistory.user_id == user.id)  # only mine (ADR-048)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="history entry not found")
        session.delete(row)
        session.flush()
    return {"id": str(entry_id), "deleted": True}


@api_router.patch("/history/{entry_id}")
def update_history(
    entry_id: UUID,
    req: HistoryUpdate,
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    fields = req.model_dump(exclude_unset=True)  # only what the client actually sent
    if "date_completed" in fields and fields["date_completed"] is None:
        raise HTTPException(status_code=422, detail="date_completed cannot be null")
    if "format" in fields and fields["format"] is None:
        raise HTTPException(status_code=422, detail="format cannot be null")

    needs_completion = False
    work_id_str = fmt_str = ""
    with db_manager.get_session() as session:
        row = (
            session.query(ReadingHistory)
            .filter(ReadingHistory.id == entry_id, ReadingHistory.user_id == user.id)
            .options(*_history_options())
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="history entry not found")

        # No ORM mutation until EVERY read/pre-check is done. Mutating row.date_completed (or
        # doing the edition repoint) before the dup pre-check query lets SQLAlchemy autoflush
        # emit the UPDATE while that query runs, so a real collision trips
        # uq_reading_history_user_edition_date as an IntegrityError raised OUTSIDE the flush
        # try/except below → 500 instead of 409. The sharp sibling is a combined date+format
        # edit that is valid at the FINAL (new_edition, new_date) but collides at the
        # intermediate (old_edition, new_date); and get_or_create runs its own internal flush,
        # so session.no_autoflush alone would NOT cover it. So we resolve the final target
        # values first and pre-check against them with the row still untouched.
        final_date = fields.get("date_completed", row.date_completed)

        target_edition = row.edition
        new_fmt = fields.get("format")
        if new_fmt is not None and new_fmt != (row.edition.format or "").strip().lower():
            # Format change = repoint to the sibling (work_id, format) edition — editions are
            # shared catalog objects, so the old one is never mutated or deleted. Reuse a
            # casing variant if one exists (uq_editions_work_format is case-sensitive);
            # get_or_create + the unique index backstop the concurrent-create race (#95).
            # order_by exact-format-match first, then Edition.id (M1): a duplicate-casing
            # catalog then can't flip the chosen target between successive edits.
            target_edition = (
                session.query(Edition)
                .filter(Edition.work_id == row.edition.work_id, func.lower(Edition.format) == new_fmt)
                .order_by((Edition.format != new_fmt), Edition.id)
                .first()
            )
            if target_edition is None:
                target_edition, _created = get_or_create(session, Edition, work_id=row.edition.work_id, format=new_fmt)

        collision_detail = (
            f"You already logged this book as {target_edition.format or 'the same format'} on {final_date.isoformat()}."
        )
        if target_edition.id != row.edition_id or "date_completed" in fields:
            # uq_reading_history_user_edition_date pre-check against the FINAL (edition, date).
            # final_date is a plain parameter because the row is still unmutated, so this query
            # cannot autoflush a premature UPDATE. The index itself backstops the millisecond
            # race below and maps to the same 409.
            dup = (
                session.query(ReadingHistory)
                .filter(
                    ReadingHistory.user_id == user.id,
                    ReadingHistory.edition_id == target_edition.id,
                    ReadingHistory.date_completed == final_date,
                    ReadingHistory.id != row.id,
                )
                .first()
            )
            if dup is not None:
                raise HTTPException(status_code=409, detail=collision_detail)

        if target_edition.id != row.edition_id:
            # Decide the async completion enqueue while the session is open (narrators is a
            # lazy relationship): missing ISBN, or an audiobook with no narrators yet. The
            # target_edition.narrators lazy-load is safe here precisely because nothing is
            # pending yet — it can't autoflush a premature UPDATE.
            needs_completion = target_edition.isbn_13 is None or (
                "audiobook" in (target_edition.format or "").lower() and not target_edition.narrators
            )
            work_id_str = str(target_edition.work_id)
            fmt_str = target_edition.format or ""

        # ONLY NOW mutate: scalar edits + the repoint. Every read above ran against final
        # values with the row untouched, so the flush below is the SINGLE UPDATE emission
        # point and its IntegrityError genuinely maps to the millisecond-race 409. Raising
        # inside the session context still rolls back EVERY field edit — a 409 must leave the
        # row exactly as it was.
        if "date_completed" in fields:
            row.date_completed = fields["date_completed"]
        if "rating" in fields:
            row.user_rating = fields["rating"]
        if "notes" in fields:
            row.user_notes = fields["notes"]
        if target_edition.id != row.edition_id:
            row.edition = target_edition
        try:
            session.flush()
        except IntegrityError as e:
            raise HTTPException(status_code=409, detail=collision_detail) from e
        payload = _history_item(row)

    # After commit: best-effort enqueue in the POST /books style — a Cloud Tasks failure
    # must never fail the edit (the completion sweep-of-one can be retriggered by any
    # later format edit; the entry itself is saved).
    enqueued = False
    if needs_completion:
        try:
            enqueued = enqueue_edition_completion(work_id_str, fmt_str, user_id=str(user.id))
        except Exception:  # noqa: BLE001 - enqueue is best-effort
            logger.exception("edition-completion enqueue failed for work %s", work_id_str)
    payload["enrichment_enqueued"] = enqueued
    return payload


@api_router.get("/works")
def get_works(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    """Enriched catalog listing — the walking skeleton's payload (Lift 0)."""
    with db_manager.get_session() as session:
        # selectinload for the collections: joinedload + LIMIT mis-paginates
        # (the limit would apply to joined rows, not works).
        works = (
            session.query(Work)
            .options(
                selectinload(Work.contributors).joinedload(WorkContributor.author),
                selectinload(Work.tropes).joinedload(WorkTrope.trope),
                selectinload(Work.styles).joinedload(WorkStyle.style),
            )
            .order_by(Work.title, Work.id)  # id tiebreaker: stable pages when titles collide
            .offset(offset)
            .limit(limit)
            .all()
        )

        return [
            {
                "id": str(w.id),
                "title": w.title,
                "authors": [
                    c.author.name for c in w.contributors if c.role == "Author"
                ],  # ETL always writes role="Author"
                "publication_year": w.original_publication_year,
                "genres": w.genres or [],
                "moods": w.moods or [],
                "tropes": [wt.trope.name for wt in _ranked_tropes(w.tropes)],
                "styles": [{"attribute": ws.attribute_type, "name": ws.style.name} for ws in w.styles],
            }
            for w in works
        ]


# ---------------------------------------------------------------------------
# Chat endpoints (Lift 2)
# ---------------------------------------------------------------------------


async def _open_conversation(*, user_id: str, session_id: str, history: list[dict], on_event) -> LibrarianConversation:
    """Open the mesh conversation for one turn (seam: tests replace this)."""
    return await astart_conversation(user_id=user_id, session_id=session_id, history=history, on_event=on_event)


class _SyncOpener:
    """The conversation object returned by the factory passed to sse_turn. Lazily opens
    the async mesh conversation on first asend — deferred so the open runs inside the
    event loop, not the sync endpoint frame. session_id = conversation_id.hex so usage
    rows (keyed off the ADK session uuid) FK to the transcript row."""

    def __init__(self, user_id, ctx, on_event):
        self._user_id = user_id
        self._ctx = ctx
        self._on_event = on_event
        self._conv = None  # opened lazily on first asend

    async def asend(self, message: str) -> str:
        if self._conv is None:
            self._conv = await _open_conversation(
                user_id=self._user_id,
                session_id=self._ctx.conversation_id.hex,
                history=self._ctx.history,
                on_event=self._on_event,
            )
        return await self._conv.asend(message)

    def close(self):
        if self._conv is not None:
            self._conv.close()


@api_router.get("/account")
def get_account(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    """Identity + entitlement summary for the avatar menu (#100 monetization arc 2/3).
    tier comes from tiers.effective_tier — the single source of truth; no second
    computation here."""
    with db_manager.get_session() as session:
        tier = tiers.effective_tier(session, user.id)
        row = session.get(User, user.id)
        until = row.subscriber_until.isoformat() if row and row.subscriber_until else None
        return {
            "email": row.email if row else None,
            "display_name": row.display_name if row else None,
            "tier": tier,
            "subscriber_until": until,
        }


@api_router.get("/conversations/current")
def get_current_conversation(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with as_user(user.id):
        ctx = transcript.get_or_create_active_conversation()
    return {"id": str(ctx.conversation_id), "messages": ctx.history}


@api_router.post("/conversations")
def new_conversation(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with as_user(user.id):
        ctx = transcript.start_new_conversation()
    return {"id": str(ctx.conversation_id), "messages": ctx.history}


@api_router.post("/chat")
def chat(user: AuthenticatedUser = Depends(get_current_user), message: str = Body(..., embed=True)):  # noqa: B008
    max_chars = tiers.chat_message_max_chars()
    if len(message) > max_chars:
        raise HTTPException(
            status_code=422,
            detail={"code": "message_too_long", "message": f"Message too long (max {max_chars} characters)."},
        )
    allowed, why = budgets.chat_turn_allowed(user.id)
    if not allowed:
        # Must reject BEFORE the StreamingResponse exists — SSE cannot carry an HTTP 429.
        raise HTTPException(status_code=429, detail={"code": "chat_quota", "message": why})
    with as_user(user.id):
        ctx = transcript.get_or_create_active_conversation()
    adk_user_id = str(user.id)
    return StreamingResponse(
        stream.sse_turn(
            message=message,
            conversation=lambda on_event: _SyncOpener(adk_user_id, ctx, on_event),
            on_persist=lambda role, content: transcript.append_message(ctx.conversation_id, role, content),
            user_id=user.id,
        ),
        media_type="text/event-stream",
    )


# Registered after all @api_router routes are declared and before the SPA catch-all so
# every /api/* route takes precedence over the greedy /{full_path:path} fallback.
app.include_router(api_router)


# ---------------------------------------------------------------------------
# SPA static serving (Lift 2 Stage 4) — served same-origin from this container.
# Registered LAST so every API route above takes precedence over the catch-all.
# ---------------------------------------------------------------------------


def _spa_dir() -> str:
    return os.environ.get("SPA_DIST_DIR", "/app/static")


# Cache policy (2026-07-19 mobile stale-bundle incident): index.html names the hashed
# bundles, so it must ALWAYS revalidate — without an explicit Cache-Control, mobile
# browsers heuristically cache it and keep serving a stale shell after deploys (the
# ETag makes revalidation a cheap 304). Vite content-hashes everything under /assets/,
# so those are immutable; unhashed root files (favicons) revalidate like the shell.
_SHELL_CACHE = "no-cache"
_HASHED_ASSET_CACHE = "public, max-age=31536000, immutable"


def _spa_index() -> FileResponse:
    return FileResponse(os.path.join(_spa_dir(), "index.html"), headers={"Cache-Control": _SHELL_CACHE})


# Private authed API JSON must never sit in a browser/proxy cache. The SPA shell and
# static assets set their own Cache-Control (below), so this only stamps responses that
# declared none — i.e. all API JSON. (The #150 Accept-sniffing navigation rewrite is gone:
# with data routes under /api/*, a bare /history is not an API route and reaches the SPA
# catch-all naturally — #151.)
@app.middleware("http")
async def _api_no_store_cache(request: Request, call_next):
    response = await call_next(request)
    if "cache-control" not in response.headers:
        response.headers["cache-control"] = "no-store"
    return response


@app.get("/")
def spa_root():
    return _spa_index()


@app.get("/{full_path:path}")
def spa_catch_all(full_path: str):
    """Serve a real built file when one exists; otherwise return the SPA shell so
    client-side routes (e.g. /add, /history) resolve. A genuinely-missing asset (e.g. a
    bad /assets/* path) therefore also returns the shell (200), not a 404 — standard SPA
    catch-all behavior. The realpath check is a path-traversal guard: a candidate that
    escapes the dist dir falls back to the shell."""
    root = os.path.realpath(_spa_dir())
    candidate = os.path.realpath(os.path.join(root, full_path))
    if (candidate == root or candidate.startswith(root + os.sep)) and os.path.isfile(candidate):
        hashed = candidate.startswith(os.path.join(root, "assets") + os.sep)
        cache = _HASHED_ASSET_CACHE if hashed else _SHELL_CACHE
        return FileResponse(candidate, headers={"Cache-Control": cache})
    return _spa_index()

"""Per-row bulk-import worker (Spec 2026-06-18). The ONLY place de-dup/shallow/route/
queue-deep happens. Keyed by import_row_id; status is the idempotency boundary.

Returns 'done' or 'not_found'. Raises LookupError when the row is gone (-> 404, non-retryable).
Any other exception propagates (-> 5xx -> Cloud Tasks redelivery), with error_detail recorded
and the row left in 'processing' so the stalled-row retry can recover it."""

from __future__ import annotations

import logging
from uuid import UUID

from agentic_librarian.core.user_context import as_user
from agentic_librarian.db.get_or_create import get_or_create
from agentic_librarian.db.models import ImportRow, Suggestions
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.enrichment import two_phase
from agentic_librarian.enrichment.tasks import enqueue_enrichment

logger = logging.getLogger(__name__)

db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override the module db_manager (tests / shared-pool lifespan) — mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


def _upsert_suggestion(session, *, work_id: UUID, user_id: UUID, context: str) -> bool:
    """Get-or-create the user's wishlist suggestion. Returns True if created, False if it
    already existed (re-import safe — mirrors add_read_event's idempotency).

    GH #95: uq_suggestions_active backstops this get-then-create against a concurrent
    duplicate-suggestion race (partial unique on (user_id, work_id) WHERE status='Suggested')."""
    _suggestion, created = get_or_create(
        session,
        Suggestions,
        work_id=work_id,
        user_id=user_id,
        status="Suggested",
        defaults={"context": context},
    )
    return created


def _finish(row_id: UUID, *, status: str, outcome: str, work_id: UUID | None = None) -> None:
    with db_manager.get_session() as session:
        row = session.get(ImportRow, row_id)
        if row is not None:
            row.status = status
            row.outcome = outcome
            if work_id is not None:
                row.work_id = work_id


def _record_error(row_id: UUID, detail: str) -> None:
    with db_manager.get_session() as session:
        row = session.get(ImportRow, row_id)
        if row is not None:
            row.error_detail = detail[:2000]  # stays in 'processing' for the stalled-row retry


def process_import_row(row_id: UUID) -> str:
    # 1. Load + idempotency guard + claim.
    with db_manager.get_session() as session:
        row = session.get(ImportRow, row_id)
        if row is None:
            raise LookupError("import row not found")
        if row.status == "done":
            return "done"
        # 'processing' is intentionally NOT short-circuited: re-claiming a row on a Cloud Tasks
        # redelivery is safe because add_read_event and _upsert_suggestion are both idempotent.
        row.status = "processing"
        data = {
            "title": row.raw_title or "",
            "author": row.raw_author or "",
            "fmt": row.raw_format or "ebook",
            "completed": row.date_completed,
            "rating": row.rating,
            "notes": row.notes,
            "destination": row.destination,
            "shelf": row.shelf or "",
            "user_id": row.user_id,
        }

    # 2-4 all run attributed to the row's user (#100): enrich_fast's grounded/embed calls,
    # the history/suggestion write, and the deep-enrichment enqueue all bill/attribute to
    # this user. OUTSIDE our claim session — enrich_fast owns its own session/pool.
    with as_user(data["user_id"]):
        # 2. Resolve the work (de-dup, else shallow scouts).
        try:
            fast = two_phase.enrich_fast(data["title"], data["author"], data["fmt"])
        except Exception as e:  # noqa: BLE001 - transient: record + re-raise so Cloud Tasks retries
            _record_error(row_id, f"{type(e).__name__}: {e}")
            raise
        if fast is None:
            _finish(row_id, status="failed", outcome="not_found")
            return "not_found"
        work_id, created = fast

        # 3. Write to the routed destination.
        try:
            if data["destination"] == "history":
                event = two_phase.add_read_event(
                    work_id,
                    completed=data["completed"],
                    rating=data["rating"],
                    notes=data["notes"],
                    fmt=data["fmt"],
                )
                outcome = "duplicate" if event["already_logged"] else ("created" if created else "linked")
            else:  # 'suggestion'. ('skip' rows are never enqueued — commit filters them — so they never reach here.)
                shelf = data["shelf"]
                context = f"imported:{shelf}" if shelf in ("to-read", "currently-reading") else "imported"
                with db_manager.get_session() as session:
                    is_new = _upsert_suggestion(session, work_id=work_id, user_id=data["user_id"], context=context)
                outcome = "created" if (is_new and created) else ("linked" if is_new else "duplicate")
        except Exception as e:  # noqa: BLE001 - transient: record + re-raise for retry
            _record_error(row_id, f"{type(e).__name__}: {e}")
            raise

        # 4. Queue the deep pass only for newly-created works (best-effort).
        if created:
            try:
                enqueue_enrichment(str(work_id), user_id=str(data["user_id"]))
            except Exception:  # noqa: BLE001 - deep pass can be retried later; never fail the row
                logger.exception("deep-enrichment enqueue failed for work %s", work_id)

    _finish(row_id, status="done", outcome=outcome, work_id=work_id)
    return "done"

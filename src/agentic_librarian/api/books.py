"""Add-a-book endpoint (Lift 2 Stage 3) — the fast pass of two-phase enrichment.

POST /books runs the API-only scouts (seconds), persists the Work + logs the read-event
immediately, and enqueues a Cloud Task for the deep LLM pass. Firebase-gated; the
read-event is scoped to the authenticated user (ADR-048)."""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.core.user_context import as_user
from agentic_librarian.enrichment import two_phase
from agentic_librarian.enrichment.tasks import enqueue_enrichment

logger = logging.getLogger(__name__)
router = APIRouter()


class AddBookRequest(BaseModel):
    title: str = Field(..., max_length=500)
    author: str = Field(..., max_length=500)
    format: str = Field("ebook", max_length=50)
    rating: int | None = Field(None, ge=1, le=5)
    notes: str | None = Field(None, max_length=2000)
    date_completed: date | None = None

    @field_validator("title", "author")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty string")
        return v.strip()

    @field_validator("rating", mode="before")
    @classmethod
    def _no_bool_rating(cls, v: object) -> object:
        # bool is an int subclass — reject it so rating=True can't slip in as 1 (parity with
        # the add_book_to_history MCP tool). Must run before Pydantic coerces bool→int.
        if isinstance(v, bool):
            raise ValueError("rating must be an integer, not a boolean")
        return v

    @field_validator("date_completed")
    @classmethod
    def _not_future(cls, v: date | None) -> date | None:
        if v is not None and v > date.today():
            raise ValueError("date_completed cannot be in the future")
        return v


@router.post("/books")
def add_book(req: AddBookRequest, user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    # as_user covers the fast pass so its embed cache-miss metering attributes to the
    # requester (#100 AC#1) — fast scouts make no grounded calls, so no budget effect.
    with as_user(user.id):
        fast = two_phase.enrich_fast(req.title, req.author, req.format)
    if fast is None:
        raise HTTPException(
            status_code=404,
            detail=f"Couldn't find '{req.title}' by {req.author}. Check the spelling and try again.",
        )
    work_id, created = fast
    completed = req.date_completed or date.today()
    with as_user(user.id):
        event = two_phase.add_read_event(
            work_id, completed=completed, rating=req.rating, notes=req.notes, fmt=req.format
        )

    enqueued = False
    if created:
        # A failed enqueue must not fail the add — the book is already saved.
        try:
            enqueued = enqueue_enrichment(str(work_id), user_id=str(user.id))
        except Exception:  # noqa: BLE001 - enqueue is best-effort; deep pass can be retried later
            logger.exception("deep-enrichment enqueue failed for work %s", work_id)

    return {
        "work_id": str(work_id),
        "title": req.title,
        "read_number": event["read_number"],
        "already_logged": event["already_logged"],
        "pick_resolved": event["pick_resolved"],
        "enrichment_enqueued": enqueued,
    }

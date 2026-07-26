"""Bulk reading-history import API (Spec 2026-06-18). Stateless preview/commit (the client
re-uploads the small CSV); per-row Cloud Tasks do the work. Firebase-gated like books.py."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from agentic_librarian.api.auth import AuthenticatedUser, get_current_user
from agentic_librarian.core import tiers
from agentic_librarian.db.models import ImportJob, ImportRow
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.imports import bucketing, parsing
from agentic_librarian.imports.tasks import enqueue_import_row

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_ROWS = 2000
STALLED_AFTER = timedelta(minutes=15)

db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override the module db_manager (tests / shared-pool lifespan) — mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


_REQUIRED_FIELDS = ("title", "author", "date_completed")


def _read_csv(raw: bytes) -> tuple[list[str], list[dict]]:
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    rows = list(reader)
    if not headers or not rows:
        raise HTTPException(status_code=422, detail="The file has no data rows.")
    if len(rows) > MAX_ROWS:
        raise HTTPException(status_code=422, detail=f"File has {len(rows)} rows; the limit is {MAX_ROWS}.")
    return list(headers), rows


def _counts(parsed: list[parsing.ParsedRow]) -> dict:
    c = {"read_dated": 0, "read_undated": 0, "bad_date": 0, "to_read": 0, "currently_reading": 0, "total": len(parsed)}
    for p in parsed:
        if p.shelf == "to-read":
            c["to_read"] += 1
        elif p.shelf == "currently-reading":
            c["currently_reading"] += 1
        elif p.date_completed is not None:
            c["read_dated"] += 1
        elif p.bad_date:
            c["bad_date"] += 1  # would be skipped at commit — surfaced so the UI can warn pre-import
        else:
            c["read_undated"] += 1
    return c


def _bad_date_example(parsed: list[parsing.ParsedRow]) -> str | None:
    """First raw date string that would skip as bad_date (shelved rows never skip on date)."""
    return next(
        (p.raw_date for p in parsed if p.bad_date and p.shelf not in ("to-read", "currently-reading")),
        None,
    )


def _preview_row(p: parsing.ParsedRow) -> dict:
    return {
        "title": p.raw_title,
        "author": p.raw_author,
        "format": p.raw_format,
        "date_completed": p.date_completed.isoformat() if p.date_completed else None,
        "rating": p.rating,
        "shelf": p.shelf,
    }


def _enqueue_rows(row_ids: list[str]) -> None:
    """Sequential Cloud Tasks enqueues — sync gRPC calls, so callers on the event loop
    run this via asyncio.to_thread (GH #93: up to MAX_ROWS calls blocked the loop for
    minutes). A failed enqueue leaves the row 'pending'; the stale-pending retry (#99)
    recovers it."""
    for rid in row_ids:
        try:
            enqueue_import_row(rid)
        except Exception:  # noqa: BLE001 - see docstring
            logger.exception("import-row enqueue failed for row %s", rid)


@router.post("/import/preview")
async def preview(
    file: UploadFile = File(...),  # noqa: B008
    mapping: str | None = Form(None),  # noqa: B008 - JSON override when the user edits the map
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    headers, rows = _read_csv(await file.read())
    source = parsing.sniff_source(headers)
    suggested = parsing.suggest_mapping(headers, source)
    effective = json.loads(mapping) if mapping else suggested
    parsed = parsing.parse_rows(rows, effective)
    return {
        "source": source,
        "headers": headers,
        "suggested_mapping": suggested,
        "preview_rows": [_preview_row(p) for p in parsed[:5]],
        "counts": _counts(parsed),
        "bad_date_example": _bad_date_example(parsed),
    }


@router.post("/import/commit")
async def commit(
    file: UploadFile = File(...),  # noqa: B008
    mapping: str = Form(...),  # noqa: B008
    import_to_read: bool = Form(False),  # noqa: B008
    import_currently_reading: bool = Form(False),  # noqa: B008
    original_filename: str | None = Form(None),  # noqa: B008
    user: AuthenticatedUser = Depends(get_current_user),  # noqa: B008
):
    parsed_mapping = json.loads(mapping)
    missing = [f for f in _REQUIRED_FIELDS if not parsed_mapping.get(f)]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required column mapping: {', '.join(missing)}")

    headers, rows = _read_csv(await file.read())
    source = parsing.sniff_source(headers)
    parsed = parsing.parse_rows(rows, parsed_mapping)

    enqueue_ids: list[str] = []
    with db_manager.get_session() as session:
        # Per-tier row cap (#100): the hard MAX_ROWS ceiling above is absolute (protects
        # the parser/DB regardless of tier); this is the tighter, tier-aware limit.
        tier = tiers.effective_tier(session, user.id)
        limit = min(MAX_ROWS, tiers.import_max_rows(tier))
        row_count = len(parsed)
        if row_count > limit:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "import_rows_limit",
                    "message": f"This import has {row_count} rows; your current limit is {limit}. "
                    "Split the file — or support Shelfwright for the full 2,000-row limit.",
                },
            )

        # One in-flight import at a time (#100): a prior commit's rows still pending/processing
        # blocks a new commit — avoids two jobs' Cloud Tasks racing on the same user's history.
        in_flight = (
            session.query(ImportRow.id)
            .join(ImportJob, ImportJob.id == ImportRow.import_job_id)
            .filter(ImportJob.user_id == user.id, ImportRow.status.in_(("pending", "processing")))
            .first()
        )
        if in_flight is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "import_in_flight",
                    "message": "Your previous import is still running — wait for it to finish before starting another.",
                },
            )

        job = ImportJob(user_id=user.id, source=source, original_filename=original_filename, total_rows=len(parsed))
        session.add(job)
        session.flush()  # populate job.id for the ImportRow FK
        to_enqueue: list[ImportRow] = []
        for p in parsed:
            destination, skip_reason = bucketing.bucket(
                p, import_to_read=import_to_read, import_currently_reading=import_currently_reading
            )
            row = ImportRow(
                import_job_id=job.id,
                user_id=user.id,
                raw_title=p.raw_title,
                raw_author=p.raw_author,
                raw_format=p.raw_format,
                raw_date=p.raw_date,
                date_completed=p.date_completed if destination == "history" else None,
                rating=p.rating,
                notes=p.notes,
                destination=destination,
                shelf=p.shelf,
                status="skipped" if destination == "skip" else "pending",
                skip_reason=skip_reason,
            )
            session.add(row)
            if destination != "skip":
                to_enqueue.append(row)
        session.flush()  # one flush populates all ImportRow.id values
        enqueue_ids = [str(row.id) for row in to_enqueue]
        job_id = str(job.id)

    await asyncio.to_thread(_enqueue_rows, enqueue_ids)

    return {"import_job_id": job_id, "total_rows": len(parsed), "enqueued": len(enqueue_ids)}


def _load_owned_job(session: Session, job_id: UUID, user_id: UUID) -> ImportJob:
    job = session.get(ImportJob, job_id)
    # 404 (not 403) for a job owned by another user: don't reveal it exists (ADR-048).
    if job is None or job.user_id != user_id:
        raise HTTPException(status_code=404, detail="import job not found")
    return job


@router.get("/import/{job_id}")
def get_status(job_id: UUID, user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    with db_manager.get_session() as session:
        job = _load_owned_job(session, job_id, user.id)
        counts = dict(
            session.query(ImportRow.status, func.count())
            .filter(ImportRow.import_job_id == job_id)
            .group_by(ImportRow.status)
            .all()
        )
        outcomes = dict(
            session.query(ImportRow.outcome, func.count())
            .filter(ImportRow.import_job_id == job_id, ImportRow.outcome.isnot(None))
            .group_by(ImportRow.outcome)
            .all()
        )
        report = [
            {
                "title": r.raw_title,
                "author": r.raw_author,
                "status": r.status,
                "outcome": r.outcome,
                "skip_reason": r.skip_reason,
                "error": r.error_detail,
            }
            for r in session.query(ImportRow)
            .filter(ImportRow.import_job_id == job_id, ImportRow.status.in_(("failed", "skipped")))
            .order_by(ImportRow.id)
            .all()
        ]
        stalled = (
            session.query(func.count())
            .select_from(ImportRow)
            .filter(
                ImportRow.import_job_id == job_id,
                # Rows a retry will re-drive (#99): stale 'processing' (worker died) AND
                # stale 'pending' (the enqueue RPC failed, so no task exists for the row).
                ImportRow.status.in_(("processing", "pending")),
                ImportRow.updated_at < datetime.now(UTC) - STALLED_AFTER,
            )
            .scalar()
        )
        active = counts.get("pending", 0) + counts.get("processing", 0)
        return {
            "import_job_id": str(job_id),
            "source": job.source,
            "total_rows": job.total_rows,
            "counts": counts,
            "outcomes": outcomes,
            "complete": active == 0,
            "stalled": stalled,
            "report": report,
        }


@router.post("/import/{job_id}/retry")
def retry(job_id: UUID, user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    cutoff = datetime.now(UTC) - STALLED_AFTER
    retry_ids: list[str] = []
    with db_manager.get_session() as session:
        _load_owned_job(session, job_id, user.id)
        rows = (
            session.query(ImportRow)
            .filter(
                ImportRow.import_job_id == job_id,
                (ImportRow.status == "failed")
                # Stale processing (worker died) or stale pending (enqueue failed, #99) —
                # re-enqueueing is safe: the worker's status machine is idempotent.
                | (ImportRow.status.in_(("processing", "pending")) & (ImportRow.updated_at < cutoff)),
            )
            .all()
        )
        for row in rows:
            row.status = "pending"
            row.error_detail = None
            retry_ids.append(str(row.id))

    _enqueue_rows(retry_ids)
    return {"retried": len(retry_ids)}

"""Internal deep-enrichment endpoint (Lift 2 Stage 3) — the Cloud Tasks target.

POST /internal/enrich/{work_id} runs the slow LLM scouts and updates the Work. It is NOT
Firebase-gated: it sits behind the (Stage-4) open IAM gate and is protected instead by the
OIDC token the Cloud Tasks queue attaches — only the queue's service account may call it.
Idempotent: Cloud Tasks may redeliver, and two_phase.enrich_deep is retry-safe."""

from __future__ import annotations

import contextlib
import logging
import os
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query

from agentic_librarian.core import budgets, byok
from agentic_librarian.core.user_context import as_user
from agentic_librarian.enrichment import two_phase
from agentic_librarian.enrichment.tasks import enqueue_edition_completion, enqueue_enrichment
from agentic_librarian.etl.trope_predicate import is_fallback_trope_name

logger = logging.getLogger(__name__)
router = APIRouter()


def _resolve_byok_key(user_id: UUID) -> tuple[str | None, str]:
    """Resolve a byok user's Gemini key for the internal enrichment endpoints (arc 3/3).
    Returns (api_key, key_source) — (None, "app") when the user has no byok credential
    (the common case). A short session, opened via two_phase's shared db_manager (the same
    DB-access pattern _has_real_trope below already uses) — resolution is not worth holding
    a session across the whole scout pass for. Raises ByokKeyError/ByokNotConfigured on
    decrypt/config failure so callers map it to the documented byok_key_error response
    (spec §3 error table) instead of silently falling back to the app key."""
    with two_phase.db_manager.get_session() as session:
        key = byok.resolve_gemini_key(session, user_id)
    if key is None:
        return None, "app"
    return key, "byok"


def _verify_oidc(token: str, audience: str) -> dict:
    """Seam for tests: monkeypatch THIS to fake the queue's OIDC token. Verifies the
    Google-signed ID token's signature, expiry, issuer, and audience, returning its claims."""
    from google.auth.transport import requests as ga_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(token, ga_requests.Request(), audience=audience)


def _require_queue_caller(authorization: str | None) -> None:
    """Fail-closed OIDC gate: 401 if no bearer token, 403 if it isn't a valid token from
    the configured queue service account."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    expected_sa = os.environ.get("ENRICH_INVOKER_SA")
    audience = os.environ.get("ENRICH_OIDC_AUDIENCE")
    if not expected_sa or not audience:
        # Misconfigured deployment — fail closed, never open. A missing audience would make
        # google-auth SKIP audience verification (defense-in-depth loss), so require it.
        logger.error("ENRICH_INVOKER_SA/ENRICH_OIDC_AUDIENCE unset; refusing internal enrichment call")
        raise HTTPException(status_code=403, detail="Internal endpoint not configured.")
    try:
        claims = _verify_oidc(token, audience)
    except Exception as e:  # noqa: BLE001 - any verification failure is a rejection
        logger.info("internal OIDC verification rejected: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=403, detail="Caller is not the enrichment queue.") from e
    if claims.get("email") != expected_sa or not claims.get("email_verified", False):
        logger.info("internal call from unexpected principal: %s", claims.get("email"))
        raise HTTPException(status_code=403, detail="Caller is not the enrichment queue.")


def _has_real_trope(work_id: UUID) -> bool:
    """True if work_id has >=1 genuine narrative trope link (shared #111 predicate over
    its linked trope names + its own genres/moods). False for zero links, or links that
    are ALL fallback (re-encoded genre/mood) or junk."""
    from agentic_librarian.db.models import Trope, Work, WorkTrope

    with two_phase.db_manager.get_session() as session:
        work = session.get(Work, work_id)
        if work is None:
            return False
        names = (
            session.query(Trope.name)
            .join(WorkTrope, WorkTrope.trope_id == Trope.id)
            .filter(WorkTrope.work_id == work_id)
        ).all()
        return any(is_fallback_trope_name(name, work.genres, work.moods) is False for (name,) in names)


# Cloud Tasks' own retry-count header (set by the queue on every redelivery, 0 on the first
# attempt) — https://cloud.google.com/tasks/docs/creating-http-target-tasks#handler. Cloud
# Tasks' default maxAttempts is 100 with backoff maxing out around an hour between attempts, so
# an unbounded empty-pass-503 loop on a genuinely poison book (bad title/author, scouts will
# NEVER find a real trope for it) would otherwise cost ~100 PAID deep-LLM passes before anyone
# notices. Giving up loudly at a bounded retry count is cheap insurance: the operator's
# --requeue-unenriched sweep (etl/enrichment_sweep.py) is the documented backstop for works that
# gave up here, so nothing is silently lost — it's just no longer an unbounded retry bill.
GIVE_UP_AFTER_RETRIES = 8


@router.post("/internal/enrich/{work_id}")
def enrich(
    work_id: UUID,
    authorization: str | None = Header(None),  # noqa: B008
    x_cloudtasks_taskretrycount: str | None = Header(None),  # noqa: B008
    user_id: UUID | None = Query(None),  # noqa: B008
):
    _require_queue_caller(authorization)
    # Pre-#100 tasks carry no user_id — run un-attributed exactly as before (metering
    # skips; nothing crashes). Attributed tasks bill the requesting user.
    ctx = as_user(user_id) if user_id is not None else contextlib.nullcontext()
    with ctx:
        api_key: str | None = None
        key_source = "app"
        if user_id is not None:
            # arc 3/3 BYOK: resolve the requesting user's Gemini key, if any, BEFORE the
            # budget gate below (task-2 review, spec AC#2) — a byok pass must never be
            # deferred by the tier-blind app-key governor. No credential row -> (None,
            # "app"), same as today. A decrypt/config failure must never fall back to the
            # app key (spec §"No silent fallback") — log the failure TYPE only (never key
            # material) and consume the task: retrying can't fix a revoked key, the
            # operator's requeue sweep is the backstop.
            try:
                api_key, key_source = _resolve_byok_key(user_id)
            except (byok.ByokKeyError, byok.ByokNotConfigured) as e:
                logger.warning(
                    "byok key resolution failed for enrich work_id=%s user_id=%s: %s",
                    work_id,
                    user_id,
                    type(e).__name__,
                )
                return {"work_id": str(work_id), "status": "byok_key_error"}
        if api_key is None:
            # App-key path only: a byok pass spends the user's own quota, so the tier-blind
            # global governor and per-user app budgets don't apply to it — its usage rows
            # are excluded from those counts by the key_source=='app' filter already.
            allowed, why = budgets.enrichment_allowed(user_id)
            if not allowed:
                when = budgets.next_utc_day_start_with_jitter()
                try:
                    requeued = enqueue_enrichment(
                        str(work_id), user_id=str(user_id) if user_id is not None else None, schedule_time=when
                    )
                except Exception:  # noqa: BLE001 - deferral is best-effort; the sweep is the backstop
                    logger.exception("deferral re-enqueue failed for work %s", work_id)
                    requeued = False
                if not requeued:
                    logger.warning(
                        "over budget and could not defer work %s (%s) — dropping to the requeue sweep", work_id, why
                    )
                    return {"work_id": str(work_id), "status": "deferred_enqueue_failed"}
                logger.info("deferred enrichment of work %s to %s (%s)", work_id, when.isoformat(), why)
                # 200 on purpose: the ORIGINAL task is consumed; the deferred copy is a NEW task,
                # so the #97 give-up retry count does not accumulate across days.
                return {"work_id": str(work_id), "status": "deferred", "until": when.isoformat()}
        result = (
            two_phase.enrich_deep(work_id, api_key=api_key, key_source=key_source)
            if api_key is not None
            else two_phase.enrich_deep(work_id)
        )
    if result == "missing":
        # Non-retryable: the work no longer exists. 404 stops Cloud Tasks from retrying.
        raise HTTPException(status_code=404, detail="work not found")
    if result == "redirected":
        # GH #141: the pass completed but persist landed its data on a different (twin) work
        # — a detected_duplicates row now records it for the works-merge tool. Non-retryable
        # success: the invoked work IS stamped, and retrying would only burn another paid
        # deep pass for data that already lives on the twin.
        return {"work_id": str(work_id), "status": "redirected"}
    if result == "empty":
        if _has_real_trope(work_id):
            # The work already has a real fingerprint from a prior pass; this empty pass
            # added nothing new but isn't a failure — don't make Cloud Tasks retry forever.
            return {"work_id": str(work_id), "status": "already_enriched"}
        try:
            retry_count = int(x_cloudtasks_taskretrycount) if x_cloudtasks_taskretrycount is not None else 0
        except ValueError:
            retry_count = 0
        if retry_count >= GIVE_UP_AFTER_RETRIES:
            # Retry bound reached with still no real trope: this is the poison-task end state,
            # not a transient failure. Return 200 (not 503) so Cloud Tasks STOPS retrying —
            # the --requeue-unenriched sweep is the operator's backstop for these, not another
            # 92 paid deep passes at ~hourly backoff.
            logger.warning("empty deep pass gave up after %d retries (no real trope): work_id=%s", retry_count, work_id)
            return {"work_id": str(work_id), "status": "empty_deep_pass_gave_up"}
        # No real trope AND this pass found nothing, and we haven't hit the give-up bound yet:
        # retryable. Cloud Tasks retries with backoff; the requeue sweep is also the backstop
        # for works that exhaust retries or were never queued at all.
        raise HTTPException(status_code=503, detail={"work_id": str(work_id), "status": "empty_deep_pass"})
    return {"work_id": str(work_id), "status": "enriched"}


@router.post("/internal/complete-edition/{work_id}")
def complete_edition(
    work_id: UUID,
    format: str = Query(..., max_length=50),  # noqa: B008
    authorization: str | None = Header(None),  # noqa: B008
    user_id: UUID | None = Query(None),  # noqa: B008
):
    """Format-completion pass target (history-format-edit spec). Same OIDC gate as
    /internal/enrich. 'missing' → 404 (non-retryable: work/edition gone); 'empty' and
    'done' → 200 (final — no retry economics here, the history edit is already saved).

    'empty' INCLUDES the all-scouts-failed case: ScoutManager.enrich swallows each scout's
    exception internally and returns {} when nobody contributed (the GH #98 guard), so a
    transient outage of every scout resolves to 'empty' → 200, NOT to a retry. That is
    deliberate — the entry is already saved and a later format change re-triggers completion.
    A 500 → normal Cloud Tasks retry only fires for a failure OUTSIDE the scout manager
    (persist/DB error) propagating uncaught, never for the scouts merely finding nothing."""
    _require_queue_caller(authorization)
    # Pre-#100 tasks carry no user_id — run un-attributed exactly as before.
    ctx = as_user(user_id) if user_id is not None else contextlib.nullcontext()
    with ctx:
        api_key: str | None = None
        key_source = "app"
        if user_id is not None:
            # arc 3/3 BYOK — same resolution + no-silent-fallback rule as /internal/enrich,
            # resolved BEFORE the budget gate below (task-2 review, spec AC#2).
            try:
                api_key, key_source = _resolve_byok_key(user_id)
            except (byok.ByokKeyError, byok.ByokNotConfigured) as e:
                logger.warning(
                    "byok key resolution failed for complete-edition work_id=%s format=%s user_id=%s: %s",
                    work_id,
                    format,
                    user_id,
                    type(e).__name__,
                )
                return {"work_id": str(work_id), "format": format, "status": "byok_key_error"}
        if api_key is None:
            # App-key path only: a byok pass spends the user's own quota, so the tier-blind
            # global governor and per-user app budgets don't apply to it — its usage rows
            # are excluded from those counts by the key_source=='app' filter already.
            allowed, why = budgets.enrichment_allowed(user_id)
            if not allowed:
                when = budgets.next_utc_day_start_with_jitter()
                try:
                    requeued = enqueue_edition_completion(
                        str(work_id), format, user_id=str(user_id) if user_id is not None else None, schedule_time=when
                    )
                except Exception:  # noqa: BLE001 - deferral is best-effort; the sweep is the backstop
                    logger.exception("deferral re-enqueue failed for work %s format %s", work_id, format)
                    requeued = False
                if not requeued:
                    logger.warning(
                        "over budget and could not defer completion of work %s format %s (%s) — dropping to the "
                        "requeue sweep",
                        work_id,
                        format,
                        why,
                    )
                    return {"work_id": str(work_id), "format": format, "status": "deferred_enqueue_failed"}
                logger.info(
                    "deferred edition completion of work %s format %s to %s (%s)",
                    work_id,
                    format,
                    when.isoformat(),
                    why,
                )
                return {"work_id": str(work_id), "format": format, "status": "deferred", "until": when.isoformat()}
        result = (
            two_phase.complete_edition(work_id, format, api_key=api_key, key_source=key_source)
            if api_key is not None
            else two_phase.complete_edition(work_id, format)
        )
    if result == "missing":
        raise HTTPException(status_code=404, detail="work or edition not found")
    return {"work_id": str(work_id), "format": format, "status": result}


@router.post("/internal/import-row/{row_id}")
def import_row(row_id: UUID, authorization: str | None = Header(None)):  # noqa: B008
    _require_queue_caller(authorization)
    from agentic_librarian.imports import worker

    try:
        result = worker.process_import_row(row_id)
    except LookupError as e:
        # Non-retryable: the row is gone. 404 stops Cloud Tasks from retrying.
        raise HTTPException(status_code=404, detail="import row not found") from e
    return {"row_id": str(row_id), "result": result}

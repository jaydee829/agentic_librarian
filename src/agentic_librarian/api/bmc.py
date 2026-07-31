"""Buy Me a Coffee webhook (monetization arc 2/3, BMC revector) — root-level machine
route, like /internal/*: NOT under /api (purpose-scoped namespacing, #151).
Authenticity = HMAC-SHA256 of the RAW body with BMC_WEBHOOK_SECRET
(x-signature-sha256 header); fail-closed when unset. The raw body is read before any
parsing — the signature covers exact bytes. Always 200 once the event is durably
stored: BMC retries failures and AUTO-DISABLES the endpoint after 10 consecutive
non-2xx, so an unmatched payment is an operator task (CLI `payments match`), never a
delivery failure. Idempotent on (provider, provider_event_id) = envelope event_id.
Grant events never shrink subscriber_until; cancel/pause caps never extend it."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException, Request

from agentic_librarian.core import entitlements
from agentic_librarian.db.models import Payment, User
from agentic_librarian.db.session import DatabaseManager

logger = logging.getLogger(__name__)
router = APIRouter()

db_manager = DatabaseManager()

_CAP_EVENTS = {"membership.cancelled", "membership.paused", "recurring_donation.cancelled"}


def set_db_manager(new_manager: DatabaseManager):
    global db_manager
    db_manager = new_manager


def _truthy(value: object) -> bool:
    # BMC booleans arrive as real bools OR the string enums "true"/"false".
    return value is True or str(value).strip().casefold() == "true"


@router.post("/webhooks/bmc")
async def bmc_webhook(request: Request):
    body = await request.body()
    secret = os.environ.get("BMC_WEBHOOK_SECRET")
    supplied = request.headers.get("x-signature-sha256", "").strip().casefold()
    if not secret:
        # Unset env fails CLOSED (matches _require_queue_caller's posture).
        raise HTTPException(status_code=403, detail="verification failed")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="verification failed")

    try:
        envelope = json.loads(body)
        if not isinstance(envelope, dict):
            raise ValueError("payload is not an object")
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
        raise HTTPException(status_code=400, detail="malformed payload") from e
    event_id = str(envelope.get("event_id") or "").strip()
    event_type = str(envelope.get("type") or "").strip()
    if not event_id or not event_type:
        raise HTTPException(status_code=400, detail="missing event_id or type")
    data = envelope.get("data")
    if not isinstance(data, dict):
        data = {}
    test_marker = "" if _truthy(envelope.get("live_mode", True)) else " [TEST]"

    email = str(data.get("supporter_email") or "").strip().lower()
    try:
        amount = Decimal(str(data.get("amount") or "0"))
    except InvalidOperation:
        amount = Decimal(0)
    if not amount.is_finite():
        amount = Decimal(0)
    raw_level = data.get("membership_level_name")
    level_name = str(raw_level) if raw_level else None
    raw_duration = data.get("duration_type")
    duration_type = str(raw_duration) if raw_duration else None
    raw_sub_id = data.get("id") if event_type.startswith(("membership.", "recurring_donation.")) else None
    subscription_id = str(raw_sub_id) if raw_sub_id is not None else None

    kind = entitlements.classify(event_type, duration_type)
    period_end = entitlements.ts_to_dt(data.get("current_period_end"))
    status_active = str(data.get("status") or "active").strip().casefold() == "active"

    with db_manager.get_session() as session:
        exists = (
            session.query(Payment.id).filter(Payment.provider == "bmc", Payment.provider_event_id == event_id).first()
        )
        if exists is not None:
            return {"status": "duplicate"}
        user = session.query(User).filter(User.email == email).first() if email else None
        payment = Payment(
            provider="bmc",
            provider_event_id=event_id,
            event_type=event_type,
            email=email,
            amount=amount,
            currency=str(data.get("currency") or ""),
            level_name=level_name,
            duration_type=duration_type,
            subscription_id=subscription_id,
            payload=envelope,
            matched_user_id=user.id if user else None,
            granted_until=None,
        )
        session.add(payment)

        if user is not None and event_type in _CAP_EVENTS:
            cap_base = (
                period_end
                if _truthy(data.get("cancel_at_period_end"))
                else entitlements.ts_to_dt(data.get("canceled_at"))
                or entitlements.ts_to_dt(data.get("paused_at"))
                or period_end
                or datetime.now(UTC)
            )
            cap = entitlements.horizon(cap_base)
            user.subscriber_until = entitlements.apply_cap(user.subscriber_until, cap)
            payment.granted_until = user.subscriber_until
            session.flush()
            logger.info(
                "bmc%s %s: capped %s -> %s",
                test_marker,
                event_type,
                email,
                user.subscriber_until.isoformat() if user.subscriber_until else "none",
            )
            return {"status": "capped", "kind": kind}

        if user is not None and kind in ("monthly", "annual") and status_active:
            new_until = entitlements.horizon(period_end) or entitlements.extend(
                user.subscriber_until, entitlements.grant_days(kind)
            )
            user.subscriber_until = entitlements.apply_grant(user.subscriber_until, new_until)
            payment.granted_until = user.subscriber_until
            session.flush()
            logger.info(
                "bmc%s %s: %s %s -> %s", test_marker, event_type, kind, email, user.subscriber_until.isoformat()
            )
            return {"status": "applied", "kind": kind}
        session.flush()

    if user is not None:
        logger.info("bmc%s %s recorded for %s", test_marker, event_type, email)
        return {"status": "recorded", "kind": kind}
    if kind == "ignore" and not email:
        return {"status": "ignored", "kind": kind}
    logger.warning(
        "bmc%s payment UNMATCHED (email %s, event %s) — resolve via `payments match`", test_marker, email, event_id
    )
    return {"status": "unmatched", "kind": kind}

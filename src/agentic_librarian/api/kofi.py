"""Ko-fi payment webhook (monetization arc 2/3) — root-level machine route, like
/internal/*: NOT under /api (purpose-scoped namespacing, #151). Authenticity is Ko-fi's
shared verification_token (no HMAC exists); fail-closed when unset. Always 200 once the
event is durably stored — Ko-fi retries non-2xx, and an unmatched payment is an operator
task (CLI `payments match`), not a delivery failure. Idempotent on kofi_transaction_id."""

from __future__ import annotations

import hmac
import json
import logging
import os
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Form, HTTPException

from agentic_librarian.core import entitlements
from agentic_librarian.db.models import Payment, User
from agentic_librarian.db.session import DatabaseManager

logger = logging.getLogger(__name__)
router = APIRouter()

db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager):
    global db_manager
    db_manager = new_manager


@router.post("/webhooks/kofi")
def kofi_webhook(data: str = Form(...)):  # noqa: B008
    try:
        event = json.loads(data)
        if not isinstance(event, dict):
            raise ValueError("payload is not an object")
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=400, detail="malformed payload") from e

    expected = os.environ.get("KOFI_VERIFICATION_TOKEN")
    supplied = str(event.get("verification_token") or "")
    if not expected or not hmac.compare_digest(supplied, expected):
        # Unset env fails CLOSED (matches _require_queue_caller's posture).
        raise HTTPException(status_code=403, detail="verification failed")

    txn_id = str(event.get("kofi_transaction_id") or "").strip()
    if not txn_id:
        raise HTTPException(status_code=400, detail="missing kofi_transaction_id")
    email = str(event.get("email") or "").strip().lower()
    try:
        amount = Decimal(str(event.get("amount") or "0"))
    except InvalidOperation:
        amount = Decimal(0)
    if not amount.is_finite():
        # "nan"/"-nan"/"Infinity" parse successfully (no InvalidOperation above) but crash
        # entitlements.classify()'s `amount >= threshold` ordering comparison.
        amount = Decimal(0)
    # str-coerce like the sibling fields: a non-string tier_name would crash classify()'s
    # .strip().casefold() before the event is persisted (same class as the amount guard).
    raw_tier = event.get("tier_name")
    tier_name = str(raw_tier) if raw_tier else None
    is_sub = bool(event.get("is_subscription_payment", False))
    kofi_type = str(event.get("type") or "")

    kind = entitlements.classify(kofi_type, is_sub, tier_name, amount)
    days = entitlements.grant_days(kind)

    with db_manager.get_session() as session:
        if session.query(Payment.id).filter(Payment.provider_event_id == txn_id).first() is not None:
            return {"status": "duplicate"}
        user = session.query(User).filter(User.email == email).first() if email else None
        payment = Payment(
            provider_event_id=txn_id,
            event_type=kofi_type,
            email=email,
            amount=amount,
            currency=str(event.get("currency") or ""),
            level_name=tier_name,
            payload=event,
            matched_user_id=user.id if user else None,
            granted_until=None,
        )
        session.add(payment)
        if user is not None and days > 0:
            user.subscriber_until = entitlements.extend(user.subscriber_until, days)
            session.flush()
            logger.info("kofi %s: %s +%dd -> %s", kind, email, days, user.subscriber_until.isoformat())
            return {"status": "applied", "kind": kind}
        session.flush()
    if user is not None:
        logger.info("kofi tip recorded for %s", email)
        return {"status": "recorded", "kind": kind}
    logger.warning("kofi payment UNMATCHED (email %s, txn %s) — resolve via `payments match`", email, txn_id)
    return {"status": "unmatched", "kind": kind}

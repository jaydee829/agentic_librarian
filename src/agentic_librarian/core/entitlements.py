"""Ko-fi payment → entitlement rules (monetization arc 2/3). Pure and DB-free: the
webhook and the CLI both call these so grant math exists exactly once. Env knobs are
read per call (prod-tunable): KOFI_ANNUAL_TIER_NAMES (csv of tier/product names that
mean 'annual', default 'annual'; exact case-folded match — substring matching would
misfire on e.g. 'Not Annual'), KOFI_ANNUAL_MIN_AMOUNT (one-off donations at/over this
classify as annual, default 25). Grace is baked into the grant: 33/370 days cover
Ko-fi's missing cancellation events and late renewals."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

Kind = Literal["monthly", "annual", "tip"]

_GRANT_DAYS: dict[Kind, int] = {"monthly": 33, "annual": 370, "tip": 0}
_DEFAULT_ANNUAL_MIN = Decimal(25)


def _annual_tier_names() -> set[str]:
    raw = os.environ.get("KOFI_ANNUAL_TIER_NAMES", "annual")
    return {part.strip().casefold() for part in raw.split(",") if part.strip()}


def _annual_min_amount() -> Decimal:
    raw = os.environ.get("KOFI_ANNUAL_MIN_AMOUNT", "")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return _DEFAULT_ANNUAL_MIN
    return value if value > 0 else _DEFAULT_ANNUAL_MIN


def classify(kofi_type: str, is_subscription_payment: bool, tier_name: str | None, amount: Decimal) -> Kind:
    if tier_name is not None and tier_name.strip().casefold() in _annual_tier_names():
        return "annual"
    if is_subscription_payment:
        return "monthly"
    if amount >= _annual_min_amount():
        return "annual"
    return "tip"


def grant_days(kind: Kind) -> int:
    return _GRANT_DAYS[kind]


def extend(current: datetime | None, days: int, now: datetime | None = None) -> datetime:
    """New subscriber_until: active subs stack; lapsed subs restart from now."""
    now = now or datetime.now(UTC)
    base = current if (current is not None and current > now) else now
    return base + timedelta(days=days)

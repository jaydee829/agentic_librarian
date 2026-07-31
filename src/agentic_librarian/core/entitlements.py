"""BMC payment → entitlement rules (monetization arc 2/3, BMC revector). Pure and
DB-free: the webhook and the CLI both call these so grant math exists exactly once.
BMC's membership events carry duration_type ("month"|"year") and current_period_end
directly, so classification is structural and the horizon is provider-truth + grace —
no tier-name or amount heuristics. Grant events never shrink subscriber_until
(out-of-order deliveries); cancel/pause caps never extend it. BMC_GRACE_DAYS (default
5) covers renewal-charge retries and webhook delivery lag; extend() remains for the
CLI and as the fallback when current_period_end is absent from a payload."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Literal

Kind = Literal["monthly", "annual", "tip", "ignore"]

_GRANT_DAYS: dict[Kind, int] = {"monthly": 33, "annual": 370, "tip": 0, "ignore": 0}
_DEFAULT_GRACE_DAYS = 5
# Unix-seconds sanity ceiling (year 3000) — absurd values become None, not a grant.
_MAX_UNIX_TS = 32503680000

_GRANT_EVENTS = {
    "membership.started",
    "membership.updated",
    "recurring_donation.started",
    "recurring_donation.updated",
}


def classify(event_type: str, duration_type: str | None) -> Kind:
    if event_type in _GRANT_EVENTS:
        if (duration_type or "").strip().casefold() == "year":
            return "annual"
        return "monthly"
    if event_type == "donation.created":
        return "tip"
    return "ignore"


def grant_days(kind: Kind) -> int:
    return _GRANT_DAYS[kind]


def grace_days() -> int:
    raw = os.environ.get("BMC_GRACE_DAYS", "")
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_GRACE_DAYS
    return value if value > 0 else _DEFAULT_GRACE_DAYS


def ts_to_dt(value: object) -> datetime | None:
    try:
        ts = int(str(value))
    except (TypeError, ValueError):
        return None
    if ts <= 0 or ts > _MAX_UNIX_TS:
        return None
    return datetime.fromtimestamp(ts, tz=UTC)


def horizon(current_period_end: datetime | None) -> datetime | None:
    if current_period_end is None:
        return None
    return current_period_end + timedelta(days=grace_days())


def apply_grant(current: datetime | None, new_horizon: datetime) -> datetime:
    """Grant-path events never SHRINK standing (out-of-order webhook deliveries)."""
    if current is not None and current > new_horizon:
        return current
    return new_horizon


def apply_cap(current: datetime | None, cap: datetime) -> datetime | None:
    """Cancel/pause events never EXTEND standing."""
    if current is None or current <= cap:
        return current
    return cap


def extend(current: datetime | None, days: int, now: datetime | None = None) -> datetime:
    """New subscriber_until: active subs stack; lapsed subs restart from now."""
    now = now or datetime.now(UTC)
    base = current if (current is not None and current > now) else now
    return base + timedelta(days=days)

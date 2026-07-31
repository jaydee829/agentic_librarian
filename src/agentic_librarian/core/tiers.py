"""Effective tier + env-tunable budget knobs (#100, monetization arc 1/3; spec
2026-07-25-metering-tiers-budgets-design.md).

Tier semantics: 'byok' requires a user_credentials row for vendor 'gemini' — that table
has NO writers until arc PR3 lands, so today the branch is inert but the enum is stable
across the arc. 'supporter' = subscriber_until in the future (granted via the BMC
subscriptions webhook, monetization arc 2/3). Knobs are read per call (prod-tunable
without redeploy); invalid or non-positive values fall back to the defaults below."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

Tier = Literal["free", "supporter", "byok"]

_DEFAULTS = {
    "CHAT_TURNS_PER_DAY_FREE": 20,
    "CHAT_TURNS_PER_DAY_SUPPORTER": 200,
    "CHAT_MESSAGE_MAX_CHARS": 4000,
    "IMPORT_MAX_ROWS_FREE": 300,
    "IMPORT_MAX_ROWS_SUPPORTER": 2000,
    "GROUNDED_CALLS_PER_DAY_FREE": 300,
    "GROUNDED_CALLS_PER_DAY_SUPPORTER": 1500,
    "GROUNDED_CALLS_PER_DAY_GLOBAL": 1400,
}

# byok budgets are structural sanity bounds, not cost protection (their key, their bill).
_BYOK_MULTIPLIER = 10


def _env_int(name: str) -> int:
    raw = os.environ.get(name, "")
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULTS[name]
    return value if value > 0 else _DEFAULTS[name]


def tier_for(subscriber_until: datetime | None, has_byok_credential: bool, now: datetime | None = None) -> Tier:
    if has_byok_credential:
        return "byok"
    now = now or datetime.now(UTC)
    if subscriber_until is not None and subscriber_until > now:
        return "supporter"
    return "free"


def effective_tier(session, user_id: UUID) -> Tier:
    from agentic_librarian.db.models import User, UserCredential

    user = session.get(User, user_id)
    has_cred = (
        session.query(UserCredential.user_id)
        .filter(UserCredential.user_id == user_id, UserCredential.vendor == "gemini")
        .first()
        is not None
    )
    return tier_for(user.subscriber_until if user else None, has_cred)


def _tiered(free_var: str, supporter_var: str, tier: Tier) -> int:
    if tier == "free":
        return _env_int(free_var)
    supporter = _env_int(supporter_var)
    return supporter * _BYOK_MULTIPLIER if tier == "byok" else supporter


def chat_turns_per_day(tier: Tier) -> int:
    return _tiered("CHAT_TURNS_PER_DAY_FREE", "CHAT_TURNS_PER_DAY_SUPPORTER", tier)


def chat_message_max_chars() -> int:
    return _env_int("CHAT_MESSAGE_MAX_CHARS")


def import_max_rows(tier: Tier) -> int:
    return _tiered("IMPORT_MAX_ROWS_FREE", "IMPORT_MAX_ROWS_SUPPORTER", tier)


def grounded_calls_per_day(tier: Tier) -> int:
    return _tiered("GROUNDED_CALLS_PER_DAY_FREE", "GROUNDED_CALLS_PER_DAY_SUPPORTER", tier)


def grounded_calls_per_day_global() -> int:
    return _env_int("GROUNDED_CALLS_PER_DAY_GLOBAL")


def grounding_model_name() -> str:
    """Single source of truth for the grounded-scout model id — budgets count usage rows
    by this name, and scouts/grounded_llm.py resolves its model from it."""
    return os.environ.get("GROUNDING_MODEL") or os.environ.get("EXPLORER_MODEL") or "gemini-2.5-flash"

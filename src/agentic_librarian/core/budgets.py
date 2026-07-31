"""Budget decisions for #100 — counting queries over existing tables (messages, usage);
no new counters, no Redis. Checks FAIL OPEN: a broken budget query logs and allows (the
budget protects cost, not correctness). Module-level db_manager + set_db_manager seam
mirrors core/usage.py."""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, time, timedelta
from uuid import UUID

from agentic_librarian.core import tiers
from agentic_librarian.db.session import DatabaseManager

logger = logging.getLogger(__name__)

db_manager = DatabaseManager()

_DEFER_JITTER_MAX_SECONDS = 1800  # spread the next-midnight thundering herd over 30 min


def set_db_manager(new_manager: DatabaseManager):
    global db_manager
    db_manager = new_manager


def _utc_day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return datetime.combine(now.date(), time.min, tzinfo=UTC)


def chat_turns_today(session, user_id: UUID) -> int:
    from agentic_librarian.db.models import Conversation, Message

    return (
        session.query(Message.id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(
            Conversation.user_id == user_id,
            Message.role == "user",
            Message.created_at >= _utc_day_start(),
        )
        .count()
    )


def grounded_calls_today(session, user_id: UUID | None) -> int:
    """App-key grounded-model usage rows today — per-user, or global when user_id is None."""
    from agentic_librarian.db.models import Usage

    q = session.query(Usage.id).filter(
        Usage.model == tiers.grounding_model_name(),
        Usage.key_source == "app",
        Usage.created_at >= _utc_day_start(),
    )
    if user_id is not None:
        q = q.filter(Usage.user_id == user_id)
    return q.count()


def chat_turn_allowed(user_id: UUID) -> tuple[bool, str]:
    """(allowed, human_message). Fail-open on any error."""
    try:
        with db_manager.get_session() as session:
            tier = tiers.effective_tier(session, user_id)
            limit = tiers.chat_turns_per_day(tier)
            used = chat_turns_today(session, user_id)
        if used >= limit:
            return False, (
                f"You've reached today's chat limit ({limit} messages). "
                "It resets at midnight UTC — or support Shelfwright for a higher limit."
            )
        return True, ""
    except Exception:
        logger.warning("chat budget check failed open", exc_info=True)
        return True, ""


def enrichment_allowed(user_id: UUID | None) -> tuple[bool, str]:
    """Per-user grounded budget (when attributed) AND the global governor. Un-attributed
    (pre-deploy) tasks check only the governor. Fail-open on any error."""
    try:
        with db_manager.get_session() as session:
            if grounded_calls_today(session, None) >= tiers.grounded_calls_per_day_global():
                return False, "global grounded-call governor reached"
            if user_id is not None:
                tier = tiers.effective_tier(session, user_id)
                if grounded_calls_today(session, user_id) >= tiers.grounded_calls_per_day(tier):
                    return False, f"per-user grounded budget reached (tier {tier})"
        return True, ""
    except Exception:
        logger.warning("enrichment budget check failed open", exc_info=True)
        return True, ""


def next_utc_day_start_with_jitter(now: datetime | None = None, jitter_seconds: int | None = None) -> datetime:
    if jitter_seconds is None:
        jitter_seconds = random.randint(0, _DEFER_JITTER_MAX_SECONDS)
    return _utc_day_start(now) + timedelta(days=1, seconds=jitter_seconds)

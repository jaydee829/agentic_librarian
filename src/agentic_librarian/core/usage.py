"""Per-LLM-call usage metering (Lift 1, ADR-048). One row per model invocation —
the raw material for Lift 3 quotas/billing/BYOK attribution.

Best-effort BY DESIGN in Lift 1: a metering failure logs a warning and the
conversation continues; hardening to billing-grade is a Lift 3 decision.

Latency note (INF-030, Lift 2 Stage 4): this INSERT is synchronous, so its call sites run
it off the event loop via asyncio.to_thread (runtime._record_event_usage) — to_thread copies
the context, so the user identity still resolves in the worker thread."""

from __future__ import annotations

import logging
from uuid import UUID

from agentic_librarian.core.user_context import get_required_user_id
from agentic_librarian.db.models import Usage
from agentic_librarian.db.session import DatabaseManager

logger = logging.getLogger(__name__)

db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager):
    """Override the global db_manager (primarily for testing) — mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


def record_llm_call(
    vendor: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    conversation_id: UUID | None = None,
    key_source: str = "app",
) -> None:
    """Write one usage row for the current context user. key_source is 'app' unless
    the caller threaded a byok-resolved key through (arc 3/3) — this is the ONE place
    key_source is written. Never raises."""
    try:
        user_id = get_required_user_id()
        with db_manager.get_session() as session:
            session.add(
                Usage(
                    user_id=user_id,
                    key_source=key_source,
                    vendor=vendor,
                    model=model,
                    input_tokens=int(input_tokens or 0),
                    output_tokens=int(output_tokens or 0),
                    conversation_id=conversation_id,
                )
            )
            session.flush()
    except Exception:
        logger.warning("usage metering failed (conversation continues)", exc_info=True)

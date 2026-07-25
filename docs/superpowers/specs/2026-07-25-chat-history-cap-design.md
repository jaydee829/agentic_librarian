# Design: Cap seeded chat history (#113)

**Date:** 2026-07-25
**Issue:** #113 — "Chat: unbounded conversation history reloaded and reseeded into the mesh every turn"
**Phase:** 6.4 cost & abuse guards (first item; #100 metering is separate and still open)
**Type:** Small cost/latency fix, single PR, no migration, no API-shape change.

## Problem

The active conversation never rotates, `transcript._history` loads the ENTIRE message
history each turn, and `runtime.astart_conversation` replays all of it into the LLM
session as events. `/conversations/current` also returns the full list. A long-lived
thread grows per-turn token cost and latency without bound.

## Scope decision: cap-only, no auto-rotation

There is no conversation-list UI or endpoint — `/conversations/current` is the only
reader. Auto-rotating past a threshold would silently orphan the user's visible history
mid-session. Rotation is deferred until a conversations-browser feature exists; capping
the seed at the single choke point fully addresses the cost/latency finding.

## Change

One function: `chat/transcript.py::_history()`.

- New module helper `_seed_limit() -> int`: reads `CHAT_HISTORY_SEED_LIMIT`, default
  **30**; invalid or ≤ 0 falls back to 30 (no "0 = unlimited" special case — YAGNI).
  Read per call (not import time) so tests and prod tuning don't fight import order.
- `_history` queries `order_by(Message.created_at.desc(), Message.id.desc())
  .limit(_seed_limit())`, then reverses the rows in Python. The reverse of the desc pair
  is exactly the previous `(created_at asc, id asc)` ordering — stable tiebreak preserved,
  and the result is the LAST N messages oldest-first.

Both consumers are covered by this one edit (they flow through `_history`):
- the mesh seed (`main.py` `_SyncOpener` → `astart_conversation` event replay), and
- `/conversations/current`'s payload (the issue explicitly wants it capped at the same N).

Untouched: `start_new_conversation`, `append_message` (writes are unbounded — the DB keeps
full history; only the *seeded/returned window* is capped), frontend (renders whatever
list arrives), Claude-backend path (consumes the same `TurnContext`).

## Visible effect

A user with a 200-message thread sees the last 30 messages after reload and the mesh
seeds the last 30 per turn. Older rows remain in the DB, readable when a conversations
UI lands.

## Tests

Models are Postgres-only, so DB-backed cases are `db_integration` (CI-gated); the
env-parsing helper is DB-free and unit-tested locally (the un-gate-DB-free-paths lesson).
All parametrized — no loops in test bodies.

- `test/unit/test_chat_transcript_limit.py` (new, local): `_seed_limit()` — unset → 30;
  valid override (e.g. "5" → 5); invalid ("abc", "-5", "0") → 30.
- `test/integration/test_transcript_store.py` (extend, CI): with the limit monkeypatched
  small (e.g. `CHAT_HISTORY_SEED_LIMIT=4` to keep seeding cheap) — over-cap: seed 7
  messages, expect exactly the last 4, oldest-first (assert content AND ordering);
  exactly-at-cap: seed 4, all 4 returned; under-cap: seed 2, both returned unchanged.
- `test/integration/test_chat_api.py` (extend, CI): `/conversations/current` reflects the
  cap through the endpoint (seed > limit via `append_message`, assert payload length ==
  limit and it is the tail).

## Acceptance criteria

1. `_history` returns at most `CHAT_HISTORY_SEED_LIMIT` (default 30) messages — the most
   recent ones, oldest-first.
2. `/conversations/current` payload and the mesh seed both reflect the cap (single choke
   point — no second code path).
3. Full local unit suite green; `db_integration` additions green in CI.
4. No migration, no API-shape change, no frontend change.

## Non-goals

- Auto-rotation / conversation list (deferred with the UI).
- Message-length caps, metering, budgets (#100).
- Summarizing/compacting older context (future enhancement if 30 proves too thin).

# Chat History Seed Cap Implementation Plan (#113)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cap the chat history seeded per turn (and returned by `/conversations/current`) to the last N messages, N = `CHAT_HISTORY_SEED_LIMIT` (default 30).

**Architecture:** One choke point — `chat/transcript.py::_history()` — gains a desc-limit-reverse query. Both consumers (mesh seed and `/conversations/current`) flow through it, so a single edit caps both. New `_seed_limit()` helper reads the env var per call.

**Tech Stack:** Python 3.14, SQLAlchemy, pytest. Postgres-only models → DB-backed tests are `db_integration` (CI); the env helper is DB-free and unit-tested locally.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-25-chat-history-cap-design.md`.
- Default limit **30**; invalid or ≤ 0 env values fall back to 30. No "0 = unlimited".
- Read the env var **per call** (inside `_seed_limit()`), not at import time.
- Returned order must remain **oldest-first with the existing stable tiebreak**: query `(created_at desc, id desc)` + Python reverse == previous `(created_at asc, id asc)`.
- Writes stay unbounded (`append_message` untouched); only the read window is capped.
- **Flake guard:** DB cap tests insert `Message` rows with EXPLICIT distinct `created_at` values (the #147 lesson: same-timestamp seeds → UUID-tiebreak coin flip). Do not rely on wall-clock spacing from sequential `append_message` calls.
- Tests: parametrized cases, never loops inside a test body.
- `db_integration` tests are CI-only locally — write them by inspection; local gate is `test/unit`.
- Before commit: `uvx ruff check <files>` AND `uvx ruff format <files>`. No `[skip ci]`.
- Commit trailer: end with
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_011hyNHf8LCF6Gs8gUeKfxh3`.
- Tests run via `.venv/Scripts/python -m pytest ...` from repo root.

---

### Task 1: `_seed_limit()` + capped `_history` + tests

**Files:**
- Modify: `src/agentic_librarian/chat/transcript.py`
- Create: `test/unit/test_chat_transcript_limit.py`
- Modify: `test/integration/test_transcript_store.py`
- Modify: `test/integration/test_chat_api.py`

**Interfaces:**
- Produces: `transcript._seed_limit() -> int` (env-driven, default 30); `_history` returns at most `_seed_limit()` messages, most recent, oldest-first. Public API of the module unchanged.

- [ ] **Step 1: Write the failing unit tests for `_seed_limit`**

Create `test/unit/test_chat_transcript_limit.py`:

```python
"""#113: the seeded-history window is env-tunable (CHAT_HISTORY_SEED_LIMIT, default 30).
DB-free — the parsing helper must be testable without Postgres (db_integration lesson:
un-gate DB-free paths so they run locally)."""

import pytest

from agentic_librarian.chat.transcript import _seed_limit


def test_default_is_30(monkeypatch):
    monkeypatch.delenv("CHAT_HISTORY_SEED_LIMIT", raising=False)
    assert _seed_limit() == 30


@pytest.mark.parametrize("raw,expected", [("5", 5), ("30", 30), ("100", 100)])
def test_valid_override(monkeypatch, raw, expected):
    monkeypatch.setenv("CHAT_HISTORY_SEED_LIMIT", raw)
    assert _seed_limit() == expected


@pytest.mark.parametrize("raw", ["abc", "", "-5", "0", "3.5"])
def test_invalid_or_nonpositive_falls_back_to_default(monkeypatch, raw):
    monkeypatch.setenv("CHAT_HISTORY_SEED_LIMIT", raw)
    assert _seed_limit() == 30
```

- [ ] **Step 2: Run them — verify they fail**

Run: `.venv/Scripts/python -m pytest test/unit/test_chat_transcript_limit.py -v`
Expected: FAIL at import — `cannot import name '_seed_limit'`.

- [ ] **Step 3: Implement `_seed_limit` and the capped `_history`**

In `src/agentic_librarian/chat/transcript.py`, add `import os` to the imports, then add the helper above `_history` and rewrite `_history`:

```python
_DEFAULT_SEED_LIMIT = 30


def _seed_limit() -> int:
    """#113: how many recent messages to seed per turn (and return from
    /conversations/current). Env-tunable without a deploy; read per call so tests and
    prod tuning don't depend on import order. Invalid or non-positive -> default
    (no "0 = unlimited" mode — the cap is the point)."""
    raw = os.environ.get("CHAT_HISTORY_SEED_LIMIT", "")
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_SEED_LIMIT
    return value if value > 0 else _DEFAULT_SEED_LIMIT


def _history(session: Session, conversation_id: UUID) -> list[dict]:
    rows = (
        session.query(Message)
        # Last N messages only (#113): desc + limit, then reverse — the reverse of
        # (created_at desc, id desc) is exactly the old (created_at asc, id asc), so
        # ordering and the stable UUID tiebreak are preserved while the window is
        # capped. Writes are unbounded; only this read window is capped, and BOTH
        # consumers (mesh seed + /conversations/current) flow through here.
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(_seed_limit())
        .all()
    )
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]
```

- [ ] **Step 4: Run the unit tests — verify they pass**

Run: `.venv/Scripts/python -m pytest test/unit/test_chat_transcript_limit.py -v`
Expected: PASS (9 cases).

- [ ] **Step 5: Extend the integration store tests (CI-only — by inspection)**

Append to `test/integration/test_transcript_store.py` (uses the existing `store_db`
fixture; insert rows directly with explicit `created_at` so ordering is deterministic —
never rely on wall-clock spacing):

```python
def _seed_numbered_messages(store_db, conversation_id, count):
    """Insert messages 'm1'..'m{count}' with explicit, strictly-increasing created_at
    (the #147 flake lesson: same-timestamp rows are ordered by a UUID coin flip)."""
    from datetime import UTC, datetime, timedelta

    from agentic_librarian.db.models import Message

    base = datetime(2026, 1, 1, tzinfo=UTC)
    with store_db.get_session() as s:
        for i in range(1, count + 1):
            s.add(
                Message(
                    conversation_id=conversation_id,
                    role="user" if i % 2 else "assistant",
                    content=f"m{i}",
                    created_at=base + timedelta(seconds=i),
                )
            )


@pytest.mark.parametrize(
    "seeded,expected_contents",
    [
        (7, ["m4", "m5", "m6", "m7"]),  # over-cap: exactly the LAST 4, oldest-first
        (4, ["m1", "m2", "m3", "m4"]),  # exactly-at-cap: all 4
        (2, ["m1", "m2"]),  # under-cap: unchanged
    ],
)
def test_history_is_capped_to_last_n_oldest_first(store_db, monkeypatch, seeded, expected_contents):
    monkeypatch.setenv("CHAT_HISTORY_SEED_LIMIT", "4")
    ctx = transcript.get_or_create_active_conversation()
    _seed_numbered_messages(store_db, ctx.conversation_id, seeded)
    reloaded = transcript.get_or_create_active_conversation()
    assert [m["content"] for m in reloaded.history] == expected_contents
```

Note: `_seed_numbered_messages` is a helper function, not a test — the loop inside it does
not violate the no-loops-in-test-bodies rule.

- [ ] **Step 6: Extend the chat API integration test (CI-only — by inspection)**

Append to `test/integration/test_chat_api.py` (uses its existing `client` fixture; check
the file's imports — it already imports `transcript`):

```python
def test_conversations_current_payload_is_capped(client, monkeypatch):
    # #113: the endpoint returns the same capped window the mesh seeds — one choke point.
    monkeypatch.setenv("CHAT_HISTORY_SEED_LIMIT", "3")
    ctx = transcript.get_or_create_active_conversation()
    for i in range(1, 6):
        transcript.append_message(ctx.conversation_id, "user", f"m{i}")
    resp = client.get("/api/conversations/current")
    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assert len(messages) == 3
    assert [m["content"] for m in messages] == ["m3", "m4", "m5"]
```

Caveat: this test seeds via `append_message` (5 sequential transactions → distinct
`created_at` at Postgres microsecond precision; the tail-window assertion tolerates no
ties because each insert commits separately). If the reviewer prefers, switch to the
explicit-`created_at` helper pattern from Step 5 — either is acceptable; explicit
timestamps are the stricter flake guard. The seeding loop lives before the request; the
assertions are loop-free.

- [ ] **Step 7: Collect-check the integration files locally**

Run: `.venv/Scripts/python -m pytest test/integration/test_transcript_store.py test/integration/test_chat_api.py --collect-only -q`
Expected: all tests collect with no import errors (they deselect/skip locally without Postgres).

- [ ] **Step 8: Full local unit suite**

Run: `.venv/Scripts/python -m pytest test/unit -q`
Expected: green (0 failed; the suite is green-by-default per ADR-063).

- [ ] **Step 9: Lint, format, commit**

```bash
uvx ruff check src/agentic_librarian/chat/transcript.py test/unit/test_chat_transcript_limit.py test/integration/test_transcript_store.py test/integration/test_chat_api.py
uvx ruff format src/agentic_librarian/chat/transcript.py test/unit/test_chat_transcript_limit.py test/integration/test_transcript_store.py test/integration/test_chat_api.py
git add src/agentic_librarian/chat/transcript.py test/unit/test_chat_transcript_limit.py test/integration/test_transcript_store.py test/integration/test_chat_api.py
git commit -m "feat(chat): cap seeded history to CHAT_HISTORY_SEED_LIMIT (default 30) (#113)"
```
(Full trailer per Global Constraints.)

---

## Post-implementation verification

- [ ] Full local unit suite green.
- [ ] CI green on the PR — the `db_integration` additions execute there first (gate #5).
- [ ] Optional runtime sanity: with a local uvicorn + `CHAT_HISTORY_SEED_LIMIT=2`, confirm `/api/conversations/current` returns at most 2 messages.

## Self-review notes (coverage against spec)

- Spec "Change" (helper + desc-limit-reverse) → Step 3, code verbatim. ✓
- Spec "both consumers via one choke point" → no second code path exists to edit; Step 6 proves the endpoint path. ✓
- Spec test matrix (unit env-parsing; over/at/under-cap; endpoint cap) → Steps 1, 5, 6. ✓
- Spec non-goals (no rotation, no metering) → no task touches them. ✓
- Flake guard (explicit created_at) → Step 5 helper; Step 6 caveat notes the alternative. ✓

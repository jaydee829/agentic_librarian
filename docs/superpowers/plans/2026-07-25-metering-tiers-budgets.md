# Metering, Tiers & Budgets Implementation Plan (#100 — monetization arc 1/3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every LLM/embedding call writes an attributed `usage` row; per-tier budgets (free/supporter/byok) are enforced at the chat, import, and enrichment chokepoints; over-budget background enrichment defers to the next UTC day via Cloud Tasks `schedule_time`.

**Architecture:** New `core/tiers.py` (tier decision + env-tunable knobs) and `core/budgets.py` (counting queries + allow/defer decisions, own `db_manager` + `set_db_manager` seam like `core/usage.py`). Metering wires the existing `record_llm_call` into the grounded scouts and the embedding cache-miss path; user attribution reaches background tasks by threading `user_id` through the Cloud Tasks payload into `as_user(...)` in the handlers. Enforcement returns 429/413/409 with `{"code", "message"}` details interactively and re-enqueues with `schedule_time` in the queue handlers.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy (Postgres-only models), Alembic, google-genai, Cloud Tasks, React/TS (vitest).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-25-metering-tiers-budgets-design.md` — its budget table is normative: defaults 20/200 chat turns per day, 4000 chars, 300/2000 import rows, 300/1500 grounded calls per day, 1400 global; byok = supporter × 10 for chat/import/grounded.
- Env knobs are read **per call** (never at import time); invalid or non-positive values fall back to the default (the `_seed_limit()` pattern from `chat/transcript.py`).
- Metering is best-effort and NEVER blocks or breaks the user path; enforcement checks fail OPEN with a logged warning.
- Meter at the **response object** (SDK-level HTTP retries must not double-count); scout-level retries are genuinely separate billable calls and record separately.
- Budget windows are UTC days (`created_at >= UTC midnight today`).
- Pre-deploy Cloud Tasks (no `user_id` in URL) MUST still work: handlers treat `user_id` as optional everywhere.
- Deferral must NOT burn the #97 give-up retry count: defer = re-enqueue a NEW task with `schedule_time`, return 200.
- Postgres-only models: DB-backed tests are `db_integration` (CI-gated); DB-free logic gets local unit tests. All tests parametrized — never loops inside a test body.
- DB cap/count tests insert rows with EXPLICIT distinct `created_at` values (the #147 same-timestamp flake lesson).
- Tests run via `.venv/Scripts/python -m pytest ...` from repo root; local `test/unit` is green-by-default (ADR-063) — any failure is real.
- Before each commit: `uvx ruff check <files>` AND `uvx ruff format <files>`. No `[skip ci]`.
- Commit trailer — end every commit message with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_011hyNHf8LCF6Gs8gUeKfxh3`

## File Structure

- `alembic/versions/<gen>_users_subscriber_until.py` — new migration (down_revision `f871fd59415e`)
- `src/agentic_librarian/db/models.py` — `User.subscriber_until`
- `src/agentic_librarian/core/tiers.py` — NEW: `Tier`, `tier_for`, `effective_tier`, knob accessors
- `src/agentic_librarian/core/budgets.py` — NEW: counting queries + decisions + deferral time
- `src/agentic_librarian/scouts/grounded_llm.py` — meter both providers
- `src/agentic_librarian/scouts/utils.py` — meter embed cache misses
- `src/agentic_librarian/enrichment/tasks.py` — `user_id` + `schedule_time` on both enqueue helpers
- `src/agentic_librarian/api/internal.py` — `as_user` wrap + budget check + deferral
- `src/agentic_librarian/imports/worker.py` — widen `as_user` scope; pass user to deep enqueue
- `src/agentic_librarian/api/books.py`, `src/agentic_librarian/api/main.py` — pass user to enqueues; chat enforcement
- `src/agentic_librarian/api/imports.py` — per-tier rows cap + one-in-flight rule
- `frontend/src/api/client.ts`, `frontend/src/views/ChatView.tsx` (only if needed) — 429/422 detail surfacing

---

### Task 1: Tier model, budget knobs, counting queries, migration

**Files:**
- Create: `src/agentic_librarian/core/tiers.py`
- Create: `src/agentic_librarian/core/budgets.py`
- Modify: `src/agentic_librarian/db/models.py` (User, after `display_name`)
- Create: `alembic/versions/<gen>_users_subscriber_until.py`
- Create: `test/unit/test_tiers.py`
- Create: `test/integration/test_budgets_db.py`

**Interfaces (later tasks rely on these exact names):**
- `tiers.Tier = Literal["free", "supporter", "byok"]`
- `tiers.tier_for(subscriber_until: datetime | None, has_byok_credential: bool, now: datetime | None = None) -> Tier` (pure)
- `tiers.effective_tier(session, user_id: UUID) -> Tier` (DB wrapper)
- `tiers.chat_turns_per_day(tier) -> int`, `tiers.chat_message_max_chars() -> int`, `tiers.import_max_rows(tier) -> int`, `tiers.grounded_calls_per_day(tier) -> int`, `tiers.grounded_calls_per_day_global() -> int`, `tiers.grounding_model_name() -> str`
- `budgets.set_db_manager(m)`, `budgets.chat_turn_allowed(user_id) -> tuple[bool, str]`, `budgets.enrichment_allowed(user_id: UUID | None) -> tuple[bool, str]`, `budgets.next_utc_day_start_with_jitter(now: datetime | None = None, jitter_seconds: int | None = None) -> datetime`

- [ ] **Step 1: Failing unit tests for the pure tier/knob logic**

Create `test/unit/test_tiers.py`:

```python
"""#100: tier decision + env-tunable budget knobs are DB-free and unit-testable
(the un-gate-DB-free-paths lesson)."""

from datetime import UTC, datetime, timedelta

import pytest

from agentic_librarian.core import tiers

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "subscriber_until,has_cred,expected",
    [
        (None, False, "free"),
        (NOW - timedelta(days=1), False, "free"),          # lapsed
        (NOW + timedelta(days=1), False, "supporter"),
        (None, True, "byok"),
        (NOW + timedelta(days=1), True, "byok"),           # byok wins over supporter
    ],
)
def test_tier_for(subscriber_until, has_cred, expected):
    assert tiers.tier_for(subscriber_until, has_cred, now=NOW) == expected


@pytest.mark.parametrize(
    "fn,tier,expected",
    [
        (tiers.chat_turns_per_day, "free", 20),
        (tiers.chat_turns_per_day, "supporter", 200),
        (tiers.chat_turns_per_day, "byok", 2000),
        (tiers.import_max_rows, "free", 300),
        (tiers.import_max_rows, "supporter", 2000),
        (tiers.grounded_calls_per_day, "free", 300),
        (tiers.grounded_calls_per_day, "supporter", 1500),
        (tiers.grounded_calls_per_day, "byok", 15000),
    ],
)
def test_default_budgets(monkeypatch, fn, tier, expected):
    for var in tiers._DEFAULTS:
        monkeypatch.delenv(var, raising=False)
    assert fn(tier) == expected


def test_global_governor_default(monkeypatch):
    monkeypatch.delenv("GROUNDED_CALLS_PER_DAY_GLOBAL", raising=False)
    assert tiers.grounded_calls_per_day_global() == 1400


@pytest.mark.parametrize("raw,expected", [("50", 50), ("abc", 20), ("", 20), ("-5", 20), ("0", 20)])
def test_env_override_and_fallback(monkeypatch, raw, expected):
    monkeypatch.setenv("CHAT_TURNS_PER_DAY_FREE", raw)
    assert tiers.chat_turns_per_day("free") == expected


def test_chat_message_max_chars_default(monkeypatch):
    monkeypatch.delenv("CHAT_MESSAGE_MAX_CHARS", raising=False)
    assert tiers.chat_message_max_chars() == 4000


def test_grounding_model_name_default(monkeypatch):
    monkeypatch.delenv("GROUNDING_MODEL", raising=False)
    monkeypatch.delenv("EXPLORER_MODEL", raising=False)
    assert tiers.grounding_model_name() == "gemini-2.5-flash"
```

Also add (same file) the deferral-time tests:

```python
def test_next_utc_day_start_is_next_midnight_plus_jitter():
    from agentic_librarian.core import budgets

    now = datetime(2026, 7, 25, 23, 50, tzinfo=UTC)
    when = budgets.next_utc_day_start_with_jitter(now=now, jitter_seconds=600)
    assert when == datetime(2026, 7, 26, 0, 10, tzinfo=UTC)


def test_next_utc_day_start_jitter_bounds():
    now = datetime(2026, 7, 25, 3, 0, tzinfo=UTC)
    when = budgets.next_utc_day_start_with_jitter(now=now)
    midnight = datetime(2026, 7, 26, tzinfo=UTC)
    assert midnight <= when <= midnight + timedelta(seconds=1800)
```

(import `budgets` at module top with `tiers`.)

- [ ] **Step 2: Run — verify FAIL** (`cannot import name 'tiers'`):
`.venv/Scripts/python -m pytest test/unit/test_tiers.py -v`

- [ ] **Step 3: Implement `core/tiers.py`**

```python
"""Effective tier + env-tunable budget knobs (#100, monetization arc 1/3; spec
2026-07-25-metering-tiers-budgets-design.md).

Tier semantics: 'byok' requires a user_credentials row for vendor 'gemini' — that table
has NO writers until arc PR3 lands, so today the branch is inert but the enum is stable
across the arc. 'supporter' = subscriber_until in the future (Ko-fi entitlements are
PR2). Knobs are read per call (prod-tunable without redeploy); invalid or non-positive
values fall back to the defaults below."""

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
```

- [ ] **Step 4: Implement `core/budgets.py`**

```python
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
```

NOTE for the implementer: check the actual `conversations` model class name and its
user column in `db/models.py` (~line 266) and adjust the import/filters if the names
differ from `Conversation.user_id` — the QUERY SEMANTICS in the spec are normative, the
names here are best-effort.

- [ ] **Step 5: Model + migration**

In `db/models.py`, add to `User` after `display_name`:

```python
    # #100 monetization: Ko-fi entitlement horizon (PR2 writes it; NULL/past = free tier).
    subscriber_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Generate the migration file manually (do NOT run `alembic revision --autogenerate` — no
local Postgres). Create `alembic/versions/a1b2c3d4e5f6_users_subscriber_until.py`
following the style of `f871fd59415e_detected_duplicates.py` (read it first):
`revision = "a1b2c3d4e5f6"`, `down_revision = "f871fd59415e"`, upgrade =
`op.add_column("users", sa.Column("subscriber_until", sa.DateTime(timezone=True), nullable=True))`,
downgrade = `op.drop_column("users", "subscriber_until")`.

- [ ] **Step 6: Run unit tests — verify PASS**
`.venv/Scripts/python -m pytest test/unit/test_tiers.py -v`

- [ ] **Step 7: db_integration tests (CI-only — write by inspection)**

Create `test/integration/test_budgets_db.py` marked `@pytest.mark.db_integration`
(follow the fixture pattern of an existing integration DB test file, e.g.
`test/integration/test_transcript_store.py`). Cover, all parametrized, all rows with
EXPLICIT distinct `created_at`:

- `chat_turns_today`: user messages today count; yesterday's rows and OTHER users'
  conversations excluded; assistant-role rows excluded.
- `grounded_calls_today`: counts only rows with the grounding model name +
  `key_source='app'` + today; per-user filter vs global (None) behavior.
- `effective_tier` DB wrapper: user with future `subscriber_until` → supporter; with a
  `user_credentials` row (vendor gemini) → byok; plain → free.

- [ ] **Step 8: Full local unit suite, lint, format, commit**

```bash
.venv/Scripts/python -m pytest test/unit -q
uvx ruff check src/agentic_librarian/core/tiers.py src/agentic_librarian/core/budgets.py src/agentic_librarian/db/models.py alembic/versions/a1b2c3d4e5f6_users_subscriber_until.py test/unit/test_tiers.py test/integration/test_budgets_db.py
uvx ruff format <same files>
git add <same files>
git commit -m "feat(core): tier model, budget knobs, counting queries + subscriber_until migration (#100)"
```

---

### Task 2: Meter scouts + embeddings; thread user attribution into background tasks

**Files:**
- Modify: `src/agentic_librarian/scouts/grounded_llm.py`
- Modify: `src/agentic_librarian/scouts/utils.py`
- Modify: `src/agentic_librarian/enrichment/tasks.py`
- Modify: `src/agentic_librarian/api/internal.py`
- Modify: `src/agentic_librarian/imports/worker.py`
- Modify: `src/agentic_librarian/api/books.py` (enqueue call ~line 75)
- Modify: `src/agentic_librarian/api/main.py` (PATCH /history enqueue call ~line 376)
- Create: `test/unit/test_scout_metering.py`
- Modify: `test/integration/` (extend the existing internal-endpoint/import-worker test files you find via `grep -rl "internal/enrich\|process_import_row" test/`)

**Interfaces:**
- Consumes: `record_llm_call` (core/usage.py), `as_user` (core/user_context.py), `tiers.grounding_model_name()`.
- Produces: `enqueue_enrichment(work_id: str, user_id: str | None = None, schedule_time: datetime | None = None) -> bool` and `enqueue_edition_completion(work_id: str, fmt: str, user_id: str | None = None, schedule_time: datetime | None = None) -> bool` (schedule_time is USED in Task 4 but added here so tasks.py is touched once). `/internal/enrich/{work_id}` and `/internal/complete-edition/{work_id}` accept optional `user_id` query param.

- [ ] **Step 1: Failing unit tests for metering capture**

Create `test/unit/test_scout_metering.py`:

```python
"""#100: grounded-scout + embedding metering. Fake response objects — no network, no DB
(record_llm_call is monkeypatched to a recorder)."""

from types import SimpleNamespace

import pytest

from agentic_librarian.scouts import grounded_llm


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, vendor, model, input_tokens, output_tokens, conversation_id=None):
        self.calls.append((vendor, model, input_tokens, output_tokens))


@pytest.mark.parametrize(
    "usage,expected",
    [
        (SimpleNamespace(prompt_token_count=120, candidates_token_count=45), [("gemini", "gemini-2.5-flash", 120, 45)]),
        (SimpleNamespace(prompt_token_count=None, candidates_token_count=None), [("gemini", "gemini-2.5-flash", 0, 0)]),
        (None, []),  # no usage_metadata -> no row, no crash
    ],
)
def test_record_gemini_usage(monkeypatch, usage, expected):
    rec = _Recorder()
    monkeypatch.setattr(grounded_llm, "record_llm_call", rec)
    response = SimpleNamespace(text="ok", usage_metadata=usage)
    grounded_llm._record_gemini_usage("gemini-2.5-flash", response)
    assert rec.calls == expected
```

And the embed-metering test (same file):

```python
def test_embed_metering_records_only_on_cache_miss(monkeypatch):
    from agentic_librarian.scouts import utils

    rec = _Recorder()
    monkeypatch.setattr(utils, "record_llm_call", rec)

    fake_resp = SimpleNamespace(embeddings=[SimpleNamespace(values=[0.0] * 4)])
    client = SimpleNamespace(models=SimpleNamespace(embed_content=lambda **kw: fake_resp))
    monkeypatch.setattr(utils, "get_shared_genai_client", lambda: client)

    utils.get_cached_embedding.cache_clear()
    utils.get_cached_embedding("m", "some tag text!!")  # miss -> one row
    utils.get_cached_embedding("m", "some tag text!!")  # hit -> no new row
    assert rec.calls == [("gemini", "m", len("some tag text!!") // 4, 0)]
```

- [ ] **Step 2: Run — verify FAIL** (`no attribute '_record_gemini_usage'` / no `record_llm_call` in utils):
`.venv/Scripts/python -m pytest test/unit/test_scout_metering.py -v`

- [ ] **Step 3: Implement metering in `grounded_llm.py`**

Add imports: `from agentic_librarian.core.usage import record_llm_call` and
`from agentic_librarian.core.tiers import grounding_model_name`. Replace the
`GeminiGroundedLLM.__init__` model resolution `os.environ.get("GROUNDING_MODEL") or
os.environ.get("EXPLORER_MODEL") or "gemini-2.5-flash"` with `grounding_model_name()`
(single source of truth — budgets count by this name). Add:

```python
def _record_gemini_usage(model_name: str, response) -> None:
    """Meter at the RESPONSE object (#100): the SDK's HTTP-level 429/5xx retry must not
    double-count; scout-level retries are genuinely separate billable calls and each
    records via its own response. record_llm_call never raises (warns + drops when no
    user is in context, e.g. pre-#100 queued tasks)."""
    um = getattr(response, "usage_metadata", None)
    if um is None:
        return
    record_llm_call(
        "gemini",
        model_name,
        int(getattr(um, "prompt_token_count", 0) or 0),
        int(getattr(um, "candidates_token_count", 0) or 0),
    )
```

In `GeminiGroundedLLM.generate`, between the `generate_content` call and the return:
`_record_gemini_usage(self.model_name, response)`.

In `ClaudeGroundedLLM._agenerate`, extend the message loop (mirrors
`agents/backends/claude.py` — read its usage-recording lines first and match the
attribute access it uses):

```python
        async for message in query(prompt=prompt, options=options):
            result_val = getattr(message, "result", None)
            if result_val and isinstance(result_val, str):
                text = result_val
            usage = getattr(message, "usage", None)
            if usage:
                record_llm_call(
                    "anthropic",
                    self.model,
                    int(usage.get("input_tokens", 0) or 0),
                    int(usage.get("output_tokens", 0) or 0),
                )
```

(Known limit, do not fix: the ThreadPoolExecutor fallback path in `generate` does not
propagate ContextVars, so metering warns+drops there — that path only runs under async
test runners, never in the scout worker. Note this in the docstring.)

- [ ] **Step 4: Implement embed metering in `scouts/utils.py`**

Import `from agentic_librarian.core.usage import record_llm_call` (no import cycle:
core.usage pulls only db + user_context). In `get_cached_embedding`, after the
`if not response or not response.embeddings: raise` guard, before the return:

```python
    # #100: embed responses expose no token counts and lru_cache means only MISSES reach
    # here — record per network call with a documented chars//4 estimate (visibility, not
    # billing-grade; embeddings are $0.15/M).
    record_llm_call("gemini", model_name, len(text) // 4, 0)
    return response.embeddings[0].values
```

- [ ] **Step 5: Thread `user_id` through the enqueue helpers**

In `enrichment/tasks.py`, change both signatures and URLs (audience handling UNCHANGED —
it already deliberately excludes query strings; extend that comment to cover user_id):

```python
def enqueue_enrichment(work_id: str, user_id: str | None = None, schedule_time: datetime | None = None) -> bool:
```
URL: `url = f"{base.rstrip('/')}/internal/enrich/{work_id}"`, then
`if user_id: url += f"?user_id={quote(user_id)}"`; audience stays the PATH url (bind it
to a variable before appending the query). After building `task`, add:

```python
    if schedule_time is not None:
        task["schedule_time"] = schedule_time  # proto-plus coerces datetime -> Timestamp
```

Same for `enqueue_edition_completion` (its URL already has `?format=`, so append
`&user_id=` there), same schedule_time block. Add `from datetime import datetime` import.

- [ ] **Step 6: Accept + use `user_id` in the internal handlers**

In `api/internal.py`: add `import contextlib` and
`from agentic_librarian.core.user_context import as_user`. In `enrich(...)` add parameter
`user_id: UUID | None = Query(None)` and wrap the work:

```python
    _require_queue_caller(authorization)
    # Pre-#100 tasks carry no user_id — run un-attributed exactly as before (metering
    # skips; nothing crashes). Attributed tasks bill the requesting user.
    ctx = as_user(user_id) if user_id is not None else contextlib.nullcontext()
    with ctx:
        result = two_phase.enrich_deep(work_id)
```

Same pattern in `complete_edition` around `two_phase.complete_edition(work_id, format)`.

- [ ] **Step 7: Attribute the import worker + caller enqueues**

In `imports/worker.py process_import_row`: lift the `as_user(...)` so steps 2–4 (the
`enrich_fast` call through `enqueue_enrichment`) all run inside
`with as_user(data["user_id"]):` — remove the now-nested inner `with as_user(...)` in the
history branch (it would just re-set the same ContextVar). Change the enqueue to
`enqueue_enrichment(str(work_id), user_id=str(data["user_id"]))`.

In `api/books.py` (~line 75): `enqueue_enrichment(str(work_id), user_id=str(user.id))`.
In `api/main.py` PATCH /history (~line 376): pass `user_id=str(user.id)` to
`enqueue_edition_completion` (match the actual local variable names at the call site).

- [ ] **Step 8: Run unit tests — verify PASS**; also run any existing unit tests
covering these modules:
`.venv/Scripts/python -m pytest test/unit/test_scout_metering.py test/unit -q`

- [ ] **Step 9: Extend integration tests (CI-only — by inspection)**

Find the existing files: `grep -rl "internal/enrich\|process_import_row\|enqueue_enrichment" test/`.
Add parametrized cases:
- `/internal/enrich/{id}?user_id=<uuid>` runs `enrich_deep` with the user set — assert
  by monkeypatching a probe inside the handler's scope (e.g. patch `two_phase.enrich_deep`
  to capture `get_required_user_id()`); and WITHOUT the param it still succeeds with no
  user in context (pre-deploy task shape — the back-compat guarantee).
- `process_import_row` calls `enqueue_enrichment` with `user_id=str(row.user_id)`
  (patch the enqueue seam the existing tests already patch — they exist per the old-seam
  CI lesson: search for patches of `enqueue_enrichment` and update ALL of them to the
  new signature expectation).
- Any existing test that patches/asserts `enqueue_enrichment(str(work_id))` (books flow)
  must be updated for the new kwargs — grep the integration suites for the OLD seam
  (CLAUDE.md gate #5).

- [ ] **Step 10: Full unit suite, lint, format, commit**
(as Task 1 Step 8, listing this task's files)
`git commit -m "feat(metering): record scout + embedding usage with task-context user attribution (#100)"`

---

### Task 3: Chat enforcement (length cap, daily turn budget, 429) + frontend surfacing

**Files:**
- Modify: `src/agentic_librarian/api/main.py` (the `/chat` handler, ~line 474)
- Modify: `frontend/src/api/client.ts` (`streamChat`, ~line 246)
- Modify: `test/integration/test_chat_api.py` (or the file that tests POST /api/chat — grep)
- Modify: `frontend/src/api/client.test.ts` (or wherever streamChat is tested — grep `streamChat`)

**Interfaces:**
- Consumes: `tiers.chat_message_max_chars()`, `budgets.chat_turn_allowed(user_id)`.
- Produces: POST /api/chat → 422 `{"detail": {"code": "message_too_long", "message": ...}}` when over-length; 429 `{"detail": {"code": "chat_quota", "message": ...}}` when over budget. SSE contract unchanged otherwise.

- [ ] **Step 1: Write failing API tests** (extend the chat API integration file; follow
its fixture pattern; parametrized):
- over-length message (len = cap+1, monkeypatch `CHAT_MESSAGE_MAX_CHARS=50` for cheap
  strings) → 422 with `code == "message_too_long"`.
- budget: monkeypatch `CHAT_TURNS_PER_DAY_FREE=2`, seed 2 user messages TODAY (explicit
  created_at, #147 lesson), POST /api/chat → 429 with `code == "chat_quota"`; with only
  1 seeded → not 429.
- If this file is `db_integration`/CI-only, write by inspection and verify collection:
  `--collect-only -q`.

- [ ] **Step 2: Implement in the chat handler**

Add imports `from agentic_librarian.core import budgets, tiers` in main.py. New handler body
(before the existing `with as_user(...)` block):

```python
@api_router.post("/chat")
def chat(user: AuthenticatedUser = Depends(get_current_user), message: str = Body(..., embed=True)):  # noqa: B008
    max_chars = tiers.chat_message_max_chars()
    if len(message) > max_chars:
        raise HTTPException(
            status_code=422,
            detail={"code": "message_too_long", "message": f"Message too long (max {max_chars} characters)."},
        )
    allowed, why = budgets.chat_turn_allowed(user.id)
    if not allowed:
        # Must reject BEFORE the StreamingResponse exists — SSE cannot carry an HTTP 429.
        raise HTTPException(status_code=429, detail={"code": "chat_quota", "message": why})
    ...existing body unchanged...
```

- [ ] **Step 3: Frontend — surface the 429/422 detail**

In `client.ts` `streamChat`, replace the `if (!res.ok || !res.body)` block:

```ts
  if (!res.ok || !res.body) {
    if (res.status === 429 || res.status === 422) {
      let message = ''
      try {
        const body = await res.json()
        message = body?.detail?.message ?? ''
      } catch {
        // fall through to the generic copy
      }
      handlers.onError(message || GENERIC_CHAT_ERROR)
      return
    }
    handlers.onError(GENERIC_CHAT_ERROR)
    return
  }
```

- [ ] **Step 4: Frontend test** (extend the existing streamChat/vitest suite; use
`...Once` mock variants — the vitest#1692 lesson): a 429 response with
`{"detail": {"code": "chat_quota", "message": "Daily limit reached."}}` → `onError`
receives `"Daily limit reached."`; a 500 → generic copy unchanged.
Run: `npm test -- --run` from `frontend/` (match the repo's script).

- [ ] **Step 5: Local suites, lint, format, commit**
Backend: `.venv/Scripts/python -m pytest test/unit -q` (+ `--collect-only` on touched
integration files). Frontend: the repo's vitest command.
`git commit -m "feat(chat): per-tier daily turn budget + message length cap, 429 surfaced in UI (#100)"`

---

### Task 4: Import caps + enrichment budget deferral

**Files:**
- Modify: `src/agentic_librarian/api/imports.py` (preview/commit, MAX_ROWS at line 27)
- Modify: `src/agentic_librarian/api/internal.py` (enrich + complete_edition budget gate)
- Modify: `frontend/src/api/client.ts` (import commit error detail — only if the current
  generic `Error` hides the server message; follow the `updateHistory` ApiError pattern)
- Modify: the imports API integration test file + internal-endpoint test file (grep as in Task 2)
- Create/extend: `test/unit/` for any new pure helpers

**Interfaces:**
- Consumes: `tiers.import_max_rows`, `tiers.effective_tier`, `budgets.enrichment_allowed`, `budgets.next_utc_day_start_with_jitter`, `enqueue_enrichment(..., user_id=, schedule_time=)`.
- Produces: import commit → 413 `{"code": "import_rows_limit", ...}` over tier cap; 409 `{"code": "import_in_flight", ...}` when the user already has a pending/processing import. `/internal/enrich` responds `{"status": "deferred"}` (HTTP 200) when over budget.

- [ ] **Step 1: Failing tests for import caps** (extend the imports API test file;
parametrized): free-tier user commits 301 rows → 413 with `code == "import_rows_limit"`
(monkeypatch `IMPORT_MAX_ROWS_FREE` small, e.g. 5, and build a 6-row CSV to keep the
test cheap); supporter (seed `subscriber_until` future) passes the same file; a second
commit while rows from the first are still `pending` → 409 `import_in_flight`; after
all rows reach a terminal status → commit allowed again.

- [ ] **Step 2: Implement import caps** in `api/imports.py`: in `commit` (and `preview`
if it validates row counts — mirror wherever `MAX_ROWS` is enforced today, line ~49):

```python
    with db_manager.get_session() as session:
        tier = tiers.effective_tier(session, user.id)
    limit = min(MAX_ROWS, tiers.import_max_rows(tier))
    if row_count > limit:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "import_rows_limit",
                "message": f"This import has {row_count} rows; your current limit is {limit}. "
                "Split the file — or support Shelfwright for the full 2,000-row limit.",
            },
        )
```

(match the function's actual local names/session handling — read the file first). In-flight
check in `commit` before writing the new job:

```python
    in_flight = (
        session.query(ImportRow.id)
        .join(ImportJob, ImportJob.id == ImportRow.job_id)
        .filter(ImportJob.user_id == user.id, ImportRow.status.in_(("pending", "processing")))
        .first()
    )
    if in_flight is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "import_in_flight", "message": "Your previous import is still running — wait for it to finish before starting another."},
        )
```

(verify the actual status strings + model/column names against `db/models.py` and the worker.)

- [ ] **Step 3: Failing tests for deferral** (internal-endpoint test file): monkeypatch
`budgets.enrichment_allowed` → `(False, "global grounded-call governor reached")`, patch the
enqueue seam, POST `/internal/enrich/{id}?user_id=...` → 200 with `status == "deferred"`,
enqueue called once with same work_id/user_id and a `schedule_time` strictly after now;
`enrichment_allowed` → `(True, "")` → normal path unchanged. Same shape for
`/internal/complete-edition`. And: deferral with enqueue returning False → 200
`status == "deferred_enqueue_failed"` (the requeue sweep is the backstop — no retry storm).

- [ ] **Step 4: Implement the budget gate** in `api/internal.py` — in `enrich(...)`,
after `_require_queue_caller`, before `enrich_deep`:

```python
    allowed, why = budgets.enrichment_allowed(user_id)
    if not allowed:
        when = budgets.next_utc_day_start_with_jitter()
        try:
            requeued = enqueue_enrichment(str(work_id), user_id=str(user_id) if user_id else None, schedule_time=when)
        except Exception:  # noqa: BLE001 - deferral is best-effort; the sweep is the backstop
            logger.exception("deferral re-enqueue failed for work %s", work_id)
            requeued = False
        if not requeued:
            logger.warning("over budget and could not defer work %s (%s) — dropping to the requeue sweep", work_id, why)
            return {"work_id": str(work_id), "status": "deferred_enqueue_failed"}
        logger.info("deferred enrichment of work %s to %s (%s)", work_id, when.isoformat(), why)
        # 200 on purpose: the ORIGINAL task is consumed; the deferred copy is a NEW task,
        # so the #97 give-up retry count does not accumulate across days.
        return {"work_id": str(work_id), "status": "deferred", "until": when.isoformat()}
```

Mirror in `complete_edition` with `enqueue_edition_completion(str(work_id), format, user_id=..., schedule_time=when)`.
Add the needed imports (`budgets`, the enqueue helpers).

- [ ] **Step 5: Frontend import-error check**: read how the import view surfaces commit
errors today; if the 413/409 `detail.message` doesn't reach the user (client.ts throws
generic `Error`), extend `commitImport` to raise `ApiError` (the existing class,
`updateHistory` pattern) and render `.detail.message` in the view's error slot. If it
already surfaces server detail, skip this step and say so in the report.

- [ ] **Step 6: Suites, lint, format, commit**
`git commit -m "feat(budgets): per-tier import caps + enrichment budget deferral via schedule_time (#100)"`

---

## Post-implementation verification

- [ ] Full local unit suite green; frontend vitest green.
- [ ] `--collect-only` clean on every touched integration file.
- [ ] CI green on the PR (db_integration executes there FIRST — gate #5); grep confirmed
      no stale patches of the old `enqueue_enrichment(work_id)` seam remain.
- [ ] Optional runtime sanity (no DB): uvicorn boots; POST /api/chat with an over-length
      body → 422 JSON.

## Self-review notes (coverage against spec)

- Spec §1 tier model/migration → Task 1. §2 metering incl. Claude scout + embed
  estimate + attribution threading/back-compat → Task 2. §3 chat → Task 3; imports +
  governor/deferral → Task 4. ✓
- Spec error posture (meter best-effort, enforce fail-open, 429 bodies with code+message)
  → budgets.py design + handler code. ✓
- Spec non-goals (Ko-fi, BYOK, queue fairness) → no task touches them. ✓
- Names consistent across tasks (checked: `chat_turn_allowed`, `enrichment_allowed`,
  `next_utc_day_start_with_jitter`, enqueue kwargs). ✓

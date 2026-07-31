# BMC Revector Implementation Plan (monetization arc 2/3 rework)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Ko-fi payment integration with Buy Me a Coffee on `feat/kofi-subscriptions`, per spec `docs/superpowers/specs/2026-07-31-bmc-subscriptions-design.md`.

**Architecture:** The provider-neutral core (payments audit table, pure entitlement math, CLI fallback, `/api/account`, AccountMenu) stays; the provider surface swaps: `api/kofi.py` → `api/bmc.py` (raw-body HMAC-SHA256 verification, JSON envelope, membership lifecycle events incl. cancellation), entitlements move from flat grant-days to period-end + grace with never-shrink grants and never-extend caps, and the unmerged `payments` migration is edited in place to provider-neutral columns.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic (Postgres-only models), pytest (parametrized only), React/vitest, existing house patterns from the parent arc.

## Global Constraints

- Spec of record: `docs/superpowers/specs/2026-07-31-bmc-subscriptions-design.md`. Parent spec `2026-07-26-kofi-subscriptions-design.md` still governs everything the amendment doesn't touch.
- BMC envelope: `{event_id, type, live_mode, created, attempt, data}`; idempotency key = `str(event_id)` with `provider="bmc"`; signature = HMAC-SHA256 hex of the **raw request body** with `BMC_WEBHOOK_SECRET`, header `x-signature-sha256`, `hmac.compare_digest`, **403 fail-closed when env unset**. Raw body MUST be read before any parsing (no `Form`/Pydantic body models on this route).
- Webhook returns **200 with a status JSON for every processed outcome** (`applied|capped|recorded|unmatched|duplicate|ignored`); only malformed → 400, bad/missing signature → 403, DB failure → 500. BMC auto-disables after 10 consecutive failures — never invent new error paths.
- Grant events never shrink `subscriber_until` (`max`); cancel/pause events never extend it (`min`). Grace = `BMC_GRACE_DAYS` env, default 5, `_env_int`-style fallback (invalid/non-positive → default), read per call.
- Ko-fi heuristics (`KOFI_ANNUAL_TIER_NAMES`, `KOFI_ANNUAL_MIN_AMOUNT`), `/webhooks/kofi`, and `api/kofi.py` must be fully gone at the end (grep must find no `kofi` outside docs/ and alembic history comments).
- Migration `b2c3d4e5f6a7` is UNMERGED — edit it in place; revision id and down_revision (`a1b2c3d4e5f6`) must not change.
- Pricing/copy: **$3 / month**, **$30 / year**, tips; BMC page URL `https://buymeacoffee.com/shelfwrighw`.
- Tests: parametrized cases only (no loops in test bodies); `filterwarnings` error policy stands; db_integration tests run locally with `POSTGRES_HOST=localhost` (container `agentic_librarian_db` must be up) — run per-file, not all in one session.
- Never log the webhook secret, the signature header value, or full payloads at info level.
- Lint AND format before every commit: `uvx ruff check <files>` and `uvx ruff format <files>`. Full local unit suite before each commit. No `[skip ci]`. Commit trailers:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` + `Claude-Session: https://claude.ai/code/session_011hyNHf8LCF6Gs8gUeKfxh3`.

---

### Task 1: Entitlements rework (pure module + unit tests)

**Files:**
- Modify: `src/agentic_librarian/core/entitlements.py` (full rewrite below)
- Modify: `test/unit/test_entitlements.py` (rewrite to the new API)

**Interfaces:**
- Consumes: nothing (pure, DB-free).
- Produces (Tasks 2–3 rely on these exact signatures):
  - `Kind = Literal["monthly", "annual", "tip", "ignore"]`
  - `classify(event_type: str, duration_type: str | None) -> Kind`
  - `grant_days(kind: Kind) -> int` (fallback grants: monthly 33, annual 370, else 0)
  - `grace_days() -> int` (env `BMC_GRACE_DAYS`, default 5)
  - `ts_to_dt(value: object) -> datetime | None` (unix-ts guard)
  - `horizon(current_period_end: datetime | None) -> datetime | None` (AS BUILT in Task 1 — no `now` parameter)
  - `apply_grant(current: datetime | None, new_horizon: datetime) -> datetime`
  - `apply_cap(current: datetime | None, cap: datetime) -> datetime | None`
  - `extend(current: datetime | None, days: int, now: datetime | None = None) -> datetime` (unchanged semantics — CLI + fallback path)

- [ ] **Step 1: Rewrite the failing tests** — replace `test/unit/test_entitlements.py` content with parametrized suites covering:
  - `classify`: (`"membership.started"`, `"month"`)→monthly; (`"membership.started"`, `"year"`)→annual; (`"membership.updated"`, `"year"`)→annual; (`"recurring_donation.started"`, `"month"`)→monthly; (`"membership.started"`, `None`)→monthly (unknown cadence defaults to the smallest grant); (`"donation.created"`, `None`)→tip; (`"donation.refunded"`, `None`)→ignore; (`"membership.cancelled"`, `"month"`)→ignore (caps are routed by event type, not classify); (`"shop_order.created"`, `None`)→ignore; (`""`, `None`)→ignore.
  - `grant_days`: monthly→33, annual→370, tip→0, ignore→0.
  - `grace_days`: unset→5; `"10"`→10; `"0"`→5; `"-3"`→5; `"abc"`→5 (monkeypatched env).
  - `ts_to_dt`: `1719825600`→datetime(2024,7,1,8,40, tzinfo=UTC) (verify exact); `"1719825600"` (string digits)→same; `None`→None; `0`→None; `-5`→None; `"nope"`→None; `40000000000`→None (past year-3000 guard).
  - `horizon`: period_end + 5 days (default grace); `None`→None; env grace 10 respected.
  - `apply_grant`: current None→horizon; current earlier→horizon; current LATER→current (never shrink).
  - `apply_cap`: current None→None; current later than cap→cap; current earlier→current (never extend).
  - `extend`: unchanged matrix (active stacks from current, lapsed restarts from now) — keep the existing cases, they still bind.

- [ ] **Step 2: Run to verify failure** — `.venv/Scripts/python -m pytest test/unit/test_entitlements.py -q` → import/attribute errors (new API absent).

- [ ] **Step 3: Rewrite `core/entitlements.py`:**

```python
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


def horizon(current_period_end: datetime | None, now: datetime | None = None) -> datetime | None:
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
```

  (Resolved during Task 1: `horizon()` shipped WITHOUT a `now` parameter — the
  Interfaces list above is the as-built contract.)

- [ ] **Step 4: Run to verify pass** — `.venv/Scripts/python -m pytest test/unit/test_entitlements.py -q` → all green.
- [ ] **Step 5: Lint, format, full unit suite, commit** — `uvx ruff check` + `uvx ruff format` on both files; `.venv/Scripts/python -m pytest test/unit -q`; commit `feat(entitlements): BMC period-end+grace model replaces Ko-fi grant heuristics (revector 1/4)`.

---

### Task 2: Provider-neutral payments schema + CLI rework

**Files:**
- Modify: `alembic/versions/b2c3d4e5f6a7_payments_table.py` (in place — same revision id)
- Modify: `src/agentic_librarian/db/models.py` (Payment class, ~line 342)
- Modify: `src/agentic_librarian/cli.py` (parser ~lines 39–56, `_run_payments_list` ~218, `_run_payments_match` ~242)
- Modify: `test/unit/test_cli.py` (subscribe/payments arg tests → new arg/column names)

**Interfaces:**
- Consumes (Task 1): `classify`, `grant_days`, `grace_days`, `ts_to_dt`, `horizon`, `apply_grant`, `extend`.
- Produces (Task 3 relies on): `Payment` model columns — `provider: str` (default `"bmc"`), `provider_event_id: str`, `event_type: str`, `email: str`, `amount: Decimal`, `currency: str`, `level_name: str | None`, `duration_type: str | None`, `subscription_id: str | None` (index), `payload: dict`, `matched_user_id: UUID | None`, `granted_until: datetime | None`, `created_at`; table-level `UniqueConstraint("provider", "provider_event_id", name="uq_payments_provider_event")`.

- [ ] **Step 1: Edit the migration in place.** Same revision/down_revision. New body:

```python
def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(), nullable=False, server_default="bmc"),
        sa.Column("provider_event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("level_name", sa.String(), nullable=True),
        sa.Column("duration_type", sa.String(), nullable=True),
        sa.Column("subscription_id", sa.String(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("matched_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("granted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["matched_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_payments_provider_event"),
    )
    op.create_index("ix_payments_email", "payments", ["email"])
    op.create_index("ix_payments_matched_user_id", "payments", ["matched_user_id"])
    op.create_index("ix_payments_subscription_id", "payments", ["subscription_id"])
```

  Downgrade drops the extra index then table (mirror existing). Update the module
  docstring: "provider-neutral payment event audit trail (BMC)". Rule-11 note stands.

- [ ] **Step 2: Rewrite the `Payment` model to match** (same column set; `provider: Mapped[str] = mapped_column(String, default="bmc", server_default="bmc", nullable=False)`; `granted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)`; `__table_args__ = (UniqueConstraint("provider", "provider_event_id", name="uq_payments_provider_event"),)`; drop `Boolean`/`Integer` imports if now unused). Docstring: one row per BMC webhook delivery; idempotency = (provider, provider_event_id); granted_until = the horizon/cap this event produced (null = tip/ignore/unmatched).

- [ ] **Step 3: CLI rework.**
  - Parser: `match_parser.add_argument("provider_event_id")` (replaces `kofi_transaction_id`); help strings say BMC; `user subscribe` help: "(comp or BMC mismatch fix)".
  - `_run_payments_list`: columns → `provider_event_id`, date, email, amount, `event_type`/`level_name`, `duration_type or "-"`, matched, `granted_until` (date or `-`).
  - `_run_payments_match`: recompute **from the stored row** (single source of truth, as before):

```python
kind = entitlements.classify(payment.event_type, payment.duration_type)
if kind in ("monthly", "annual"):
    period_end = entitlements.ts_to_dt((payment.payload.get("data") or {}).get("current_period_end"))
    new_until = entitlements.horizon(period_end) or entitlements.extend(
        user.subscriber_until, entitlements.grant_days(kind)
    )
    user.subscriber_until = entitlements.apply_grant(user.subscriber_until, new_until)
    payment.granted_until = user.subscriber_until
payment.matched_user_id = user.id
```

  Print `-> subscriber_until {…}` as before (`unchanged` when kind is tip/ignore).
- [ ] **Step 4: Update `test/unit/test_cli.py`** payments cases to the new argument and output columns (parametrized; keep the non-positive `--months/--days` rejection cases exactly as they are — that guardrail is untouched).
- [ ] **Step 5: Run** `.venv/Scripts/python -m pytest test/unit/test_cli.py -q` → green; full unit suite (expect Task-3 webhook tests still red-on-old-API only if touched — they are not; the old `api/kofi.py` still imports the old model fields, so **fix compile breakage minimally**: this task may leave `api/kofi.py` failing imports ONLY if fields it references were renamed — to keep the suite green commit-by-commit, update `api/kofi.py`'s field references mechanically (Payment kwargs) without changing its behavior; Task 3 deletes it).
- [ ] **Step 6: Lint, format, full unit suite, commit** — `refactor(payments): provider-neutral payment columns + CLI (BMC revector 2/4)`.

---

### Task 3: BMC webhook adapter (replaces Ko-fi) + wiring + tests

**Files:**
- Create: `src/agentic_librarian/api/bmc.py`
- Delete: `src/agentic_librarian/api/kofi.py`
- Modify: `src/agentic_librarian/api/main.py` (lines 20, 29, 105, 129 — swap kofi imports/wiring for bmc)
- Rename+rewrite: `test/unit/test_kofi_webhook.py` → `test/unit/test_bmc_webhook.py`
- Rename+rewrite: `test/integration/test_kofi_webhook_db.py` → `test/integration/test_bmc_webhook_db.py`
- Modify: `test/unit/test_db_pool_consolidation.py` (the lifespan-pool probe references `kofi` — repoint to `bmc`)

**Interfaces:**
- Consumes: Task 1 functions; Task 2 `Payment` columns.
- Produces: `POST /webhooks/bmc` (root machine route); module-level `db_manager` + `set_db_manager(new_manager)` seam (same GH-#102 lifespan-pool contract as every router).

- [ ] **Step 1: Write failing unit tests** (`test_bmc_webhook.py`, db seam patched, FastAPI TestClient, raw-body posts). Signature helper in the test module:

```python
def _signed(body: bytes, secret: str) -> dict[str, str]:
    return {"x-signature-sha256": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}
```

  Parametrized cases:
  - env unset → 403 (even with a "valid-looking" signature)
  - missing header / wrong signature / signature of a DIFFERENT body (tamper) → 403
  - valid signature + non-JSON body → 400; JSON non-object → 400; missing `event_id` or `type` → 400
  - `membership.started` (month, active, `current_period_end` set, matched email) → 200 `{"status": "applied", "kind": "monthly"}`
  - same envelope re-posted (same `event_id`) → `{"status": "duplicate"}`
  - `membership.started` year → applied annual
  - `membership.updated` with LOWER `current_period_end` than current standing → applied but `subscriber_until` unchanged (never-shrink; assert via seam-visible session mock or move to db test — unit asserts handler status only where the fake session allows)
  - `membership.started` with `status: "canceled"` → `{"status": "recorded"}` (grant only when active; missing `status` counts as active)
  - `membership.cancelled` `cancel_at_period_end: "true"` → `{"status": "capped"}`
  - `membership.paused` → capped
  - `donation.created` matched → recorded; unknown email → unmatched; `donation.refunded` → recorded; `shop_order.created` → ignored
  - amount `"nan"` / missing → processed with amount 0 (no 500)
  - `live_mode: false` → still processed (assert status unchanged)
- [ ] **Step 2: Run to verify failure** — module doesn't exist yet.
- [ ] **Step 3: Implement `api/bmc.py`:**

```python
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

_CAP_EVENTS = {"membership.cancelled", "membership.paused",
               "recurring_donation.cancelled"}


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
            session.query(Payment.id)
            .filter(Payment.provider == "bmc", Payment.provider_event_id == event_id)
            .first()
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
            logger.info("bmc%s %s: capped %s -> %s", test_marker, event_type, email,
                        user.subscriber_until.isoformat() if user.subscriber_until else "none")
            return {"status": "capped", "kind": kind}

        if user is not None and kind in ("monthly", "annual") and status_active:
            new_until = entitlements.horizon(period_end) or entitlements.extend(
                user.subscriber_until, entitlements.grant_days(kind)
            )
            user.subscriber_until = entitlements.apply_grant(user.subscriber_until, new_until)
            payment.granted_until = user.subscriber_until
            session.flush()
            logger.info("bmc%s %s: %s %s -> %s", test_marker, event_type, kind, email,
                        user.subscriber_until.isoformat())
            return {"status": "applied", "kind": kind}
        session.flush()

    if user is not None:
        logger.info("bmc%s %s recorded for %s", test_marker, event_type, email)
        return {"status": "recorded", "kind": kind}
    if kind == "ignore" and not email:
        return {"status": "ignored", "kind": kind}
    logger.warning("bmc%s payment UNMATCHED (email %s, event %s) — resolve via `payments match`",
                   test_marker, email, event_id)
    return {"status": "unmatched", "kind": kind}
```

  (`horizon(cap_base)` adds the same grace to caps so cancelled members keep their
  paid-through window + grace — matches spec AC#2.)
- [ ] **Step 4: Wire `main.py`** — replace the four `kofi` references with `bmc` equivalents (import module as `bmc_api`, router import, `bmc_api.set_db_manager(shared)` in lifespan, `app.include_router(bmc_router)`); delete `api/kofi.py`; repoint `test_db_pool_consolidation.py`'s kofi probe at `agentic_librarian.api.bmc`.
- [ ] **Step 5: Rewrite `test_bmc_webhook_db.py`** (db_integration, parametrized): end-to-end against real Postgres — started (applied, `subscriber_until == period_end + 5d`), updated with later period (advances), updated with earlier period (unchanged — never shrink), cancelled `cancel_at_period_end` (capped at `period_end + 5d`), immediate cancelled via `canceled_at` (capped there), duplicate event_id no-op, unmatched stored then `payments match` applies the payload horizon, tip recorded `granted_until IS NULL`, refund recorded. Follow the existing file's session/fixture pattern.
- [ ] **Step 6: Run** unit webhook file green; db_integration file locally with `POSTGRES_HOST=localhost` (container up) → green; `grep -ri kofi src/ frontend/src/ test/ --include='*.py' --include='*.tsx' --include='*.ts'` → no hits.
- [ ] **Step 7: Lint, format, full unit suite, commit** — `feat(payments): BMC webhook with HMAC verification + lifecycle caps, Ko-fi adapter removed (revector 3/4)`.

---

### Task 4: Frontend links/copy + deploy config

**Files:**
- Modify: `frontend/src/components/AccountMenu.tsx` (URL constant + link copy + nudge)
- Modify: `frontend/src/components/AccountMenu.test.tsx` (hrefs/copy assertions)
- Modify: `.github/workflows/deploy.yml` (line 153 secrets list)

**Interfaces:**
- Consumes: nothing new. Produces: final user-facing surface.

- [ ] **Step 1: Update failing frontend tests** — links assert `href="https://buymeacoffee.com/shelfwrighw"`, texts `$3 / month`, `$30 / year`, `Leave a tip`, nudge mentions "Buy Me a Coffee". Run `npm test -- AccountMenu` (from `frontend/`) → red.
- [ ] **Step 2: Update `AccountMenu.tsx`** — `const BMC_URL = 'https://buymeacoffee.com/shelfwrighw'` (rename constant + all three hrefs), `$30 / year`, nudge: `Use your Shelfwright sign-in email on Buy Me a Coffee so your support links up automatically.`
- [ ] **Step 3: Run frontend suite** (`npm test` from `frontend/`) → green.
- [ ] **Step 4: deploy.yml** — in the `--set-secrets` list replace `KOFI_VERIFICATION_TOKEN=librarian-kofi-verification-token:latest` with `BMC_WEBHOOK_SECRET=librarian-bmc-webhook-secret:latest`.
- [ ] **Step 5: Lint/format touched frontend files per repo conventions (prettier via test tooling only — match existing), full local unit suite for safety, commit** — `feat(account): BMC support links + deploy secret swap (revector 4/4)`.

---

## Final gate (controller)

- Whole-branch review package (`merge-base main HEAD`), opus adversarial reviewer per house convention (concurrency/time-window charter: webhook signature bytes vs proxy re-encoding, out-of-order delivery races, unix-ts edge cases, cap/grant interleavings, migration-edit consistency with model).
- Push branch, update PR #156 title/body ("feat: Buy Me a Coffee subscriber tracking + account menu (monetization arc 2/3)"), note the provider revector and the ops steps (secret creation, test events post-deploy, first-renewal log check ~2026-09-01).
- Ledger updates in `.superpowers/sdd/progress.md` throughout.

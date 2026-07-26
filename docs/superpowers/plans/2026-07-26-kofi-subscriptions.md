# Ko-fi Subscriptions + Account Menu Implementation Plan (monetization arc 2/3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ko-fi payments (webhook + CLI fallback) grant/extend `subscriber_until`; a top-bar avatar dropdown shows account status + Ko-fi support links; `GET /api/account` exposes the effective tier.

**Architecture:** Pure entitlement rules in `core/entitlements.py`; a `payments` audit table; a root-level machine route `POST /webhooks/kofi` (token-verified, idempotent); argparse CLI extensions; one new React component (`AccountMenu`) replacing the standalone sign-out button.

**Tech Stack:** Python 3.14/FastAPI/SQLAlchemy/Alembic, React+TS/vitest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-26-kofi-subscriptions-design.md` — normative for entitlement rules (33/370 days, `extend = max(now, current or now) + days`), webhook status codes (400 malformed / 403 token / 200 everything processed incl. unmatched+duplicate / 500 only on DB failure), and menu behavior.
- Env knobs read per call with fallback defaults (PR-1 `_env_int` pattern): `KOFI_ANNUAL_TIER_NAMES` (csv, default `"annual"`), `KOFI_ANNUAL_MIN_AMOUNT` (default 25). `KOFI_VERIFICATION_TOKEN` has NO default — unset means the webhook 403s (fail closed).
- Token comparison via `hmac.compare_digest`. Never log the token or full payload at info level.
- Ko-fi sends `application/x-www-form-urlencoded`, field `data` = JSON string.
- The webhook route lives on `app` (root), NOT under `/api` (purpose-scoped namespacing, #151).
- Entitlement write is transactional with the payment insert (same session).
- Tests parametrized (no loops in test bodies); Postgres-only models → DB-backed tests `db_integration` (CI-first); DB-free guards unit-tested locally; frontend rejection paths use `...Once` mocks (vitest#1692); seeded rows get EXPLICIT distinct `created_at` (#147).
- Local `test/unit` green-by-default (ADR-063); `.venv/Scripts/python -m pytest ...` from repo root; frontend `npm test -- --run` from `frontend/`.
- `uvx ruff check` AND `uvx ruff format` before each commit; no `[skip ci]`.
- Commit trailer — end every commit with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_011hyNHf8LCF6Gs8gUeKfxh3`

---

### Task 1: Entitlement rules + Payment model + migration

**Files:**
- Create: `src/agentic_librarian/core/entitlements.py`
- Modify: `src/agentic_librarian/db/models.py` (Payment class after Usage)
- Create: `alembic/versions/b2c3d4e5f6a7_payments_table.py` (down_revision `a1b2c3d4e5f6`)
- Create: `test/unit/test_entitlements.py`

**Interfaces (produced):**
- `entitlements.classify(kofi_type: str, is_subscription_payment: bool, tier_name: str | None, amount: Decimal) -> Literal["monthly", "annual", "tip"]`
- `entitlements.grant_days(kind) -> int` (monthly 33, annual 370, tip 0)
- `entitlements.extend(current: datetime | None, days: int, now: datetime | None = None) -> datetime`
- `db/models.py` `Payment` exactly per the spec's column table.

- [ ] **Step 1: Failing unit tests** — create `test/unit/test_entitlements.py`:

```python
"""Monetization arc 2/3: entitlement classification is pure and DB-free."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from agentic_librarian.core import entitlements

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "kofi_type,is_sub,tier_name,amount,expected",
    [
        ("Subscription", True, "Supporter", Decimal("3.00"), "monthly"),
        ("Subscription", True, None, Decimal("3.00"), "monthly"),
        ("Shop Order", False, "Annual", Decimal("25.00"), "annual"),
        ("Shop Order", False, "ANNUAL", Decimal("25.00"), "annual"),      # case-insensitive
        ("Subscription", True, "Annual Supporter", Decimal("25.00"), "annual"),  # tier name wins over is_sub — but exact-name match only, see below
        ("Donation", False, None, Decimal("25.00"), "annual"),            # one-off >= threshold
        ("Donation", False, None, Decimal("30.00"), "annual"),
        ("Donation", False, None, Decimal("24.99"), "tip"),
        ("Donation", False, None, Decimal("3.00"), "tip"),
        ("Commission", False, None, Decimal("10.00"), "tip"),
    ],
)
def test_classify_defaults(monkeypatch, kofi_type, is_sub, tier_name, amount, expected):
    monkeypatch.delenv("KOFI_ANNUAL_TIER_NAMES", raising=False)
    monkeypatch.delenv("KOFI_ANNUAL_MIN_AMOUNT", raising=False)
    assert entitlements.classify(kofi_type, is_sub, tier_name, amount) == expected


def test_classify_env_tier_names(monkeypatch):
    monkeypatch.setenv("KOFI_ANNUAL_TIER_NAMES", "yearly, gold ")
    assert entitlements.classify("Shop Order", False, "Gold", Decimal("25.00")) == "annual"
    assert entitlements.classify("Shop Order", False, "Annual", Decimal("10.00")) == "tip"


@pytest.mark.parametrize("kind,days", [("monthly", 33), ("annual", 370), ("tip", 0)])
def test_grant_days(kind, days):
    assert entitlements.grant_days(kind) == days


@pytest.mark.parametrize(
    "current,days,expected",
    [
        (None, 33, NOW + timedelta(days=33)),                              # first sub
        (NOW - timedelta(days=10), 33, NOW + timedelta(days=33)),          # lapsed: restart from now
        (NOW + timedelta(days=5), 33, NOW + timedelta(days=38)),           # active: stack
        (NOW + timedelta(days=100), 370, NOW + timedelta(days=470)),       # annual stacks too
    ],
)
def test_extend(current, days, expected):
    assert entitlements.extend(current, days, now=NOW) == expected
```

NOTE on the "Annual Supporter" case: `classify` matches tier names by exact
(case-folded, stripped) equality against the csv entries — "Annual Supporter" only
classifies annual if an env entry equals it. With the DEFAULT list ("annual") it does
NOT match, so with `is_sub=True` that row's expected value is "monthly". **Fix the test
table accordingly** (expected "monthly" for that row) — this note exists so you don't
"fix" the implementation to substring-match instead; substring matching would misfire
on names like "Not Annual".

- [ ] **Step 2: Run — verify FAIL** (`.venv/Scripts/python -m pytest test/unit/test_entitlements.py -v`).

- [ ] **Step 3: Implement `core/entitlements.py`**:

```python
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
```

- [ ] **Step 4: Run unit tests — PASS.**

- [ ] **Step 5: Model + migration.** Add to `db/models.py` (after `Usage`; import `Numeric`, `JSONB` is already imported for other models — verify):

```python
class Payment(Base):
    """One row per Ko-fi webhook event (monetization arc 2/3) — the audit trail behind
    users.subscriber_until. Idempotency key = kofi_transaction_id (Ko-fi retries).
    matched_user_id NULL = payer email didn't match a user (CLI `payments match` fixes)."""

    __tablename__ = "payments"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    kofi_transaction_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    kofi_type: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)  # lowercased at ingest
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String, nullable=False)
    tier_name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_subscription_payment: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    matched_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    entitlement_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
```

(match the file's existing import style; add `from decimal import Decimal` and `Boolean`/`Numeric` to the sqlalchemy imports as needed.) Hand-write
`alembic/versions/b2c3d4e5f6a7_payments_table.py` mirroring the newest migration's
style: `create_table("payments", ...)` with the unique constraint on
kofi_transaction_id and indexes on email + matched_user_id; downgrade drops the table.

- [ ] **Step 6: Full unit suite, lint, format, commit**
`git commit -m "feat(payments): entitlement rules + payments table (Ko-fi arc 2/3)"`

---

### Task 2: `POST /webhooks/kofi` + deploy wiring

**Files:**
- Create: `src/agentic_librarian/api/kofi.py`
- Modify: `src/agentic_librarian/api/main.py` (register router next to `internal_router`, line ~123)
- Modify: `.github/workflows/deploy.yml` (append `KOFI_VERIFICATION_TOKEN=librarian-kofi-verification-token:latest` to `--set-secrets`, line ~153)
- Create: `test/unit/test_kofi_webhook.py`
- Create: `test/integration/test_kofi_webhook_db.py`

**Interfaces:** consumes `entitlements.*`, `Payment`, `User`. Produces route `POST /webhooks/kofi` on `app` root.

- [ ] **Step 1: Failing unit tests (DB-free guards)** — `test/unit/test_kofi_webhook.py`: build a tiny FastAPI app with just the router (the `test_firebase_auth_proxy.py` pattern: `app.include_router(router)`); parametrized:
  - no `data` field → 400; `data` not JSON → 400.
  - `KOFI_VERIFICATION_TOKEN` unset → 403 (even with a token in payload).
  - token mismatch → 403.
  - Token OK but DB layer raises (patch the module's `db_manager` with a stub whose `get_session` raises) → 500 (Ko-fi will retry; idempotency makes that safe).

- [ ] **Step 2: Run — FAIL.** Then implement `api/kofi.py`:

```python
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
    tier_name = event.get("tier_name") or None
    is_sub = bool(event.get("is_subscription_payment", False))
    kofi_type = str(event.get("type") or "")

    kind = entitlements.classify(kofi_type, is_sub, tier_name, amount)
    days = entitlements.grant_days(kind)

    with db_manager.get_session() as session:
        if session.query(Payment.id).filter(Payment.kofi_transaction_id == txn_id).first() is not None:
            return {"status": "duplicate"}
        user = session.query(User).filter(User.email == email).first() if email else None
        payment = Payment(
            kofi_transaction_id=txn_id,
            kofi_type=kofi_type,
            email=email,
            amount=amount,
            currency=str(event.get("currency") or ""),
            tier_name=tier_name,
            is_subscription_payment=is_sub,
            payload=event,
            matched_user_id=user.id if user else None,
            entitlement_days=days if user else 0,
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
```

Register in `api/main.py` next to `internal_router` (~line 123):
`from agentic_librarian.api.kofi import router as kofi_router` + `app.include_router(kofi_router)`
(root registration — must stay OUTSIDE `api_router`).

- [ ] **Step 3: deploy.yml** — append `,KOFI_VERIFICATION_TOKEN=librarian-kofi-verification-token:latest` to the `--set-secrets` list (line ~153). Do not touch anything else in the workflow.

- [ ] **Step 4: db_integration tests (by inspection)** — `test/integration/test_kofi_webhook_db.py` (marker + fixture pattern from `test_budgets_db.py`; patch this module's `db_manager` via monkeypatch + `KOFI_VERIFICATION_TOKEN`): parametrized end-to-end matrix:
  - membership payment, matched email → 200 applied; `subscriber_until` == extend(prior, 33) (seed prior both lapsed and active — assert exact datetimes with a frozen `now` is impossible through HTTP, so assert range: `>= before_call + 33d - small_slack` and `payments` row fields).
  - annual (tier_name "Annual") → +370 path.
  - tip (< threshold) → 200 recorded, entitlement_days 0, subscriber_until unchanged.
  - unknown email → 200 unmatched, matched_user_id NULL.
  - same txn replay → 200 duplicate, no second row, no double-extend.

- [ ] **Step 5: Unit suite + collect-only, lint, format, commit**
`git commit -m "feat(payments): Ko-fi webhook -> payments + subscriber_until (arc 2/3)"`

---

### Task 3: CLI — `user subscribe`, `payments list/match`

**Files:**
- Modify: `src/agentic_librarian/cli.py` (extend `_parse_args` subparsers + `_run_user`, add `_run_payments`; follow the existing `user invite` shape at cli.py:108-128 and its `_invite_db_manager` seam)
- Modify/extend: the existing CLI test file (grep `test/` for the file testing `user invite`)

**Interfaces:** consumes `entitlements.extend`/`grant_days`, `Payment`, `User`.

- [ ] **Step 1: Failing tests** (extend the existing CLI test file, mirroring its invite-test style — it patches the db-manager seam; parametrized):
  - `user subscribe x@y.com` (default) → subscriber_until ≈ now+33d printed; `--months 3` → +99d; `--days 45` → +45; `--until 2027-01-01` → exact; unknown email → exit 2 with error.
  - `payments list --unmatched` prints only unmatched rows; `payments match <txn> <email>` sets matched_user_id + applies entitlement_days per stored classification, refuses (exit 2) when already matched or txn/email unknown.
- [ ] **Step 2: Implement.** Parser additions in `_parse_args`:

```python
    sub_parser = user_sub.add_parser("subscribe", help="grant/extend supporter entitlement (comp or Ko-fi mismatch fix)")
    sub_parser.add_argument("email")
    group = sub_parser.add_mutually_exclusive_group()
    group.add_argument("--months", type=int, default=None, help="N x 33-day grants (default 1)")
    group.add_argument("--days", type=int, default=None)
    group.add_argument("--until", default=None, help="YYYY-MM-DD (absolute)")

    payments_parser = subparsers.add_parser("payments", help="Ko-fi payment records (operator)")
    payments_sub = payments_parser.add_subparsers(dest="payments_command")
    list_parser = payments_sub.add_parser("list", help="list payments")
    list_parser.add_argument("--unmatched", action="store_true")
    match_parser = payments_sub.add_parser("match", help="link an unmatched payment to a user and apply its entitlement")
    match_parser.add_argument("kofi_transaction_id")
    match_parser.add_argument("email")
```

`_run_user` grows a `subscribe` branch (validate email format like invite; load user;
compute `days = months*33 | days | until-date` — for `--until`, set
`subscriber_until` directly to that date at UTC midnight; else
`user.subscriber_until = entitlements.extend(user.subscriber_until, days)`; print the
result). New `_run_payments(args)` wired in `_dispatch`; `match` recomputes
`classify`/`grant_days` from the stored row's fields (`kofi_type`,
`is_subscription_payment`, `tier_name`, `amount`) so webhook and CLI grant identically,
then applies `extend` and stamps `matched_user_id`/`entitlement_days`.
Use the same db-manager seam pattern `_invite_db_manager` uses (reuse or mirror it).

- [ ] **Step 3: Full unit suite, lint, format, commit**
`git commit -m "feat(cli): user subscribe + payments list/match operator fallback (arc 2/3)"`

---

### Task 4: `GET /api/account` + avatar dropdown (AccountMenu)

**Files:**
- Modify: `src/agentic_librarian/api/main.py` (new `@api_router.get("/account")` near the `/conversations/current` route ~line 460)
- Create: `frontend/src/components/AccountMenu.tsx`
- Modify: `frontend/src/components/TopBar.tsx` (avatar → menu trigger; remove standalone sign-out)
- Modify: `frontend/src/components/AppShell.css` (menu styles — follow existing token/theme variables)
- Modify: `frontend/src/api/client.ts` (add `getAccount`)
- Create: `frontend/src/components/AccountMenu.test.tsx`; Modify: `frontend/src/components/TopBar.test.tsx`
- Modify/extend: the chat API integration test file OR a small new `test/integration/test_account_api.py` for the endpoint

**Interfaces:** consumes `tiers.effective_tier`. Produces `GET /api/account` → `{"email", "display_name", "tier", "subscriber_until"}` (subscriber_until ISO string or null); `client.ts` `getAccount(): Promise<Account>`.

- [ ] **Step 1: Backend endpoint** (+ failing integration test first, by inspection if db_integration):

```python
@api_router.get("/account")
def get_account(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    from agentic_librarian.core import tiers as tiers_mod
    from agentic_librarian.db.models import User

    with db_manager.get_session() as session:
        tier = tiers_mod.effective_tier(session, user.id)
        row = session.get(User, user.id)
        until = row.subscriber_until.isoformat() if row and row.subscriber_until else None
        return {
            "email": row.email if row else None,
            "display_name": row.display_name if row else None,
            "tier": tier,
            "subscriber_until": until,
        }
```

(match main.py's actual db-session idiom — grep how other main.py routes open sessions; keep imports at module top per file style.)
Integration test: free user → tier "free", null until; seeded future subscriber_until → "supporter" + ISO date.

- [ ] **Step 2: client.ts** —

```ts
export interface Account {
  email: string
  display_name: string | null
  tier: 'free' | 'supporter' | 'byok'
  subscriber_until: string | null
}

export function getAccount(): Promise<Account> {
  return getJson<Account>('/account')
}
```

- [ ] **Step 3: Failing frontend tests** — `AccountMenu.test.tsx` (mock `getAccount` via `vi.mock('../api/client')`; `...Once` for the rejection case): opens on avatar click; shows email + "Free plan" for tier free; "Supporter until <formatted date>" for supporter; three Ko-fi links all `href="https://ko-fi.com/shelfwright"` with `target="_blank"` + `rel` containing `noopener`; Escape and outside-click close it; sign-out button calls the auth context's signOut even when `getAccount` rejects. Update `TopBar.test.tsx`: standalone sign-out button gone; avatar has `aria-expanded`.

- [ ] **Step 4: Implement `AccountMenu.tsx`** (and TopBar changes):

```tsx
import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../auth/AuthContext'
import { getAccount, type Account } from '../api/client'

const KOFI_URL = 'https://ko-fi.com/shelfwright'

/** Account dropdown off the top-bar avatar. Future home of username change and the
 *  BYOK entry (arc PR 3). Sign-out must never depend on the API: account fetch failure
 *  just hides the status line. */
export default function AccountMenu() {
  const { user, signOut } = useAuth()
  const [open, setOpen] = useState(false)
  const [account, setAccount] = useState<Account | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const initial = (user?.displayName || user?.email || '?').charAt(0).toUpperCase()

  useEffect(() => {
    if (!open) return
    if (account === null) {
      getAccount().then(setAccount).catch(() => setAccount(null))
    }
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, account])

  const status =
    account?.tier === 'supporter' && account.subscriber_until
      ? `Supporter until ${new Date(account.subscriber_until).toLocaleDateString()}`
      : account?.tier === 'free'
        ? 'Free plan'
        : null

  return (
    <div className="account-menu" ref={rootRef}>
      <button
        className="avatar avatar-button"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="Account menu"
        onClick={() => setOpen((v) => !v)}
      >
        {initial}
      </button>
      {open && (
        <div className="account-menu-panel" role="menu">
          <div className="account-menu-identity">
            <div className="account-menu-name">{user?.displayName || user?.email}</div>
            {user?.displayName && <div className="account-menu-email">{user?.email}</div>}
            {status && <div className="account-menu-status">{status}</div>}
          </div>
          <div className="account-menu-support">
            <div className="account-menu-heading">Support Shelfwright ♥</div>
            <a href={KOFI_URL} target="_blank" rel="noopener noreferrer" role="menuitem">$3 / month</a>
            <a href={KOFI_URL} target="_blank" rel="noopener noreferrer" role="menuitem">$25 / year</a>
            <a href={KOFI_URL} target="_blank" rel="noopener noreferrer" role="menuitem">Leave a tip</a>
            <div className="account-menu-nudge">
              Use your Shelfwright sign-in email on Ko-fi so your support links up automatically.
            </div>
          </div>
          <hr />
          <button role="menuitem" onClick={() => void signOut()}>Sign out</button>
        </div>
      )}
    </div>
  )
}
```

`TopBar.tsx`: replace the `<span className="avatar">` + sign-out button with
`<AccountMenu />` (keep the theme toggle). Style the panel in `AppShell.css` using the
existing CSS custom properties/tokens (absolute-positioned under the bar, right-aligned,
themed background/border like existing surfaces — follow the `.topbar` block's variables;
check both light and dark themes).

- [ ] **Step 5: Suites** — frontend `npm test -- --run` + `tsc --noEmit`; backend unit suite + `--collect-only` on touched integration files; lint/format; commit
`git commit -m "feat(account): /api/account + avatar dropdown with Ko-fi support links (arc 2/3)"`

---

## Post-implementation verification

- [ ] Local unit + frontend suites green; collect-only clean.
- [ ] CI green on the PR (db_integration first — gate #5).
- [ ] Visual QC of the menu (the qc-harness pattern) is OPTIONAL here; note in the PR if skipped.

## Self-review notes (coverage against spec)

- Spec §1 table → Task 1 model/migration. §2 rules → Task 1 module (incl. exact-match tier-name note). §3 webhook semantics/status codes → Task 2 code verbatim. §4 CLI → Task 3. §5 endpoint → Task 4 Step 1. §6 menu behavior incl. lazy fetch + fetch-failure sign-out guarantee → Task 4 component. §7 deploy wiring → Task 2 Step 3 (secret creation + Ko-fi dashboard = operator steps, PR body). ✓
- Names cross-checked: `entitlements.classify/grant_days/extend`, `Payment` columns, `getAccount`/`Account`, `set_db_manager` seam. ✓

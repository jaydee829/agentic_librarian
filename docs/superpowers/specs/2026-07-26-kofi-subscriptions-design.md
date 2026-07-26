# Design: Ko-fi subscriber tracking + account menu (monetization arc 2 of 3)

**Date:** 2026-07-26
**Arc:** Monetization (PR 1 = #100 metering/tiers/budgets, merged first; this PR stacks on it; PR 3 = BYOK)
**Branch:** `feat/kofi-subscriptions` (base: `feat/metering-tiers-100` / PR #155)

## Product decisions (user, 2026-07-25)

- Payments run through **Ko-fi** (page: **ko-fi.com/shelfwright**, currently bare-bones;
  Stripe under the hood on Ko-fi's side — we never touch card data).
- Offers: **$3/month** membership, **$25/year**, and one-off **tips**.
- Linkage: **webhook + CLI fallback** (auto-match by email; operator commands for
  mismatches, comps, corrections).
- Supporters get the generous env-tunable budgets defined in PR 1 (`supporter` tier).
- Account UI is a **dropdown from the user bubble** in the top bar — NOT a Settings
  card. It seeds the future home of username change etc. "Sign out" moves into it.

## Ko-fi integration facts (constraints we design around)

- Ko-fi POSTs `application/x-www-form-urlencoded` with a single field `data` whose
  value is a JSON object: `verification_token`, `message_id`, `kofi_transaction_id`,
  `type` ("Donation" | "Subscription" | "Shop Order" | "Commission"), `email`,
  `amount` (string, e.g. "3.00"), `currency`, `tier_name`, `is_subscription_payment`,
  `is_first_subscription_payment`, `timestamp`, and more. Authenticity = the shared
  `verification_token` (no HMAC signature exists).
- **No cancellation/expiry event** — Ko-fi only reports payments. Entitlement must be
  an expiry horizon each payment extends; lapse is silent (tier computation in PR 1
  already treats past `subscriber_until` as free).
- Memberships are monthly-centric; the $25 annual will likely arrive as a Shop Order
  or one-off Donation → classification must be configurable, not hardcoded on `type`.
- The payer's Ko-fi email may differ from their Shelfwright sign-in email → unmatched
  payments must be stored and operator-fixable, and the UI nudges users to pay with
  their sign-in email.

## Architecture

### 1. `payments` table (one migration, head after PR 1's `a1b2c3d4e5f6`)

`db/models.py` `Payment`:

| column | type | notes |
|---|---|---|
| id | UUID pk | |
| kofi_transaction_id | String, **unique**, not null | idempotency key (Ko-fi retries) |
| kofi_type | String, not null | raw `type` |
| email | String, not null, index | lowercased at ingest |
| amount | Numeric(10,2), not null | parsed from Ko-fi's string |
| currency | String, not null | |
| tier_name | String, null | |
| is_subscription_payment | Boolean, not null, default false | |
| payload | JSONB, not null | full raw event (audit/forensics) |
| matched_user_id | FK users.id, null, index | null = unmatched |
| entitlement_days | Integer, not null, default 0 | what was granted (0 = tip/none) |
| created_at | timestamptz, not null | our clock |

### 2. Entitlement rules (pure module `core/entitlements.py`, DB-free)

- `classify(kofi_type, is_subscription_payment, tier_name, amount) -> "monthly" | "annual" | "tip"`:
  - **annual** if `tier_name` case-insensitively matches one of
    `KOFI_ANNUAL_TIER_NAMES` (csv env, default `"annual"`), OR if it is NOT a
    subscription payment and `amount >= KOFI_ANNUAL_MIN_AMOUNT` (default `25`).
  - else **monthly** if `is_subscription_payment` is true.
  - else **tip** (recorded, thanked, no entitlement).
- `grant_days(kind) -> int`: monthly → **33**, annual → **370**, tip → **0**. The +3/+5
  padding is the grace covering Ko-fi's no-cancellation-event gap and late renewals.
- `extend(current: datetime | None, days: int, now) -> datetime`:
  `max(now, current or now) + timedelta(days=days)` — stacking payments extends, a
  lapsed sub restarts from now.
- Env knobs read per call, `_env_int`-style fallbacks (PR 1 pattern; the tier-name list
  is a string csv, whitespace-trimmed, case-folded).

### 3. Webhook — `POST /webhooks/kofi` (root-level machine route)

New `api/kofi.py` router registered on `app` next to `internal_router` (deliberately
NOT under `/api` — purpose-scoped namespacing from #151; this is a machine route like
`/internal/*`). Handler:

1. Read form field `data`; `json.loads` it. Malformed/missing → **400**.
2. Verify `payload["verification_token"] == KOFI_VERIFICATION_TOKEN` env. Env unset →
   **403** fail-closed (like `_require_queue_caller`'s posture); mismatch → **403**.
   Compare with `hmac.compare_digest`.
3. Idempotency: existing `kofi_transaction_id` → **200** `{"status": "duplicate"}`,
   nothing re-applied.
4. Insert the `Payment` row (email lowercased); match `users.email`; if matched and
   `grant_days > 0`, update `subscriber_until = extend(...)` **in the same session** and
   stamp `matched_user_id` + `entitlement_days`.
5. Always **200** with `{"status": "applied" | "recorded" | "unmatched"}` — an unmatched
   payment is not an error (Ko-fi must not retry it); it's stored for the CLI. Log at
   info (matched) / warning (unmatched, with the email).

Never log the verification token or the full payload at info level.

### 4. CLI fallback (extends the argparse `user` subcommands in `cli.py`)

- `librarian user subscribe <email> [--months N | --days N | --until YYYY-MM-DD]`
  (default `--months 1`; `--months N` = N×33 days via `extend`) — comps, corrections,
  Ko-fi-email mismatch remedies. Prints the resulting `subscriber_until`. Unknown
  email → error listing nothing (no user enumeration beyond the operator's own DB).
- `librarian payments list [--unmatched]` — table: txn id, date, email, amount,
  type/tier, matched?, entitlement_days.
- `librarian payments match <kofi_transaction_id> <email>` — links a stored payment to
  a user and applies its entitlement (idempotent: refuses if already matched).

### 5. Account API — `GET /api/account`

Returns `{"email", "display_name", "tier", "subscriber_until"}` using
`tiers.effective_tier` (one query path — no second tier computation). Authed like every
`/api` route.

### 6. Frontend — avatar dropdown (`AccountMenu`)

- `TopBar.tsx`: the `avatar` span becomes a button toggling a new
  `components/AccountMenu.tsx` anchored top-right; the standalone "Sign out" button
  moves inside. Theme toggle stays in the bar.
- Menu content: identity header (avatar initial, display name, email); status line —
  "Free plan" / "Supporter until {local date}" / (byok label arrives in PR 3);
  "Support Shelfwright ♥" block with links to `https://ko-fi.com/shelfwright`
  ($3/mo · $25/yr · tip — all land on the Ko-fi page; `target="_blank"
  rel="noopener noreferrer"`), and the nudge line: "Use your Shelfwright sign-in email
  on Ko-fi so your support links up automatically."; divider; Sign out.
- Behavior: click toggles; click-outside and Escape close; `aria-expanded` +
  `role="menu"`. Account data fetched lazily on first open via `client.ts
  getAccount()`; fetch failure shows the identity from the auth context and hides the
  status line (menu still works — sign-out must never depend on the API).
- Future home (documented in the component): username change, BYOK entry (PR 3).

### 7. Config & ops (operator steps at rollout)

- `KOFI_VERIFICATION_TOKEN` → Secret Manager, wired in deploy.yml like existing
  secrets (this PR edits deploy.yml; operator creates the secret).
- Optional: `KOFI_ANNUAL_TIER_NAMES`, `KOFI_ANNUAL_MIN_AMOUNT` (defaults fine).
- Ko-fi dashboard: set webhook URL `https://shelfwright.app/webhooks/kofi`; create the
  $3/mo membership and the $25 annual product **named to match** an entry in
  `KOFI_ANNUAL_TIER_NAMES` (e.g. "Annual").

## Error handling

- Webhook: 400 malformed, 403 bad/missing token, 200 for every processed outcome
  (applied / recorded / unmatched / duplicate) — Ko-fi retry semantics demand success
  once we've durably stored the event. A DB failure → 500 (Ko-fi retries; idempotency
  key makes the retry safe).
- Entitlement writes are transactional with the payment insert — no payment row without
  its grant outcome recorded.
- CLI: clear errors for unknown email / unknown txn / already matched.

## Testing

- Unit (local): `classify`/`grant_days`/`extend` full matrix (subscription, shop-order
  annual by tier name, donation ≥/< threshold, case-insensitive tier names, env
  overrides, lapsed-vs-active extend); webhook DB-free guards (missing `data`, bad
  JSON, missing/mismatched/unset token) with the db seam patched; CLI arg parsing.
- db_integration (CI): webhook end-to-end — applied (matched email, subscriber_until
  extended exactly +33/+370 from max(now, current)), unmatched stored, duplicate
  ignored, tip recorded with 0 days; CLI subscribe/match against real rows.
- Frontend (vitest): menu open/close (click, outside, Escape), status rendering per
  tier payload, Ko-fi links present with correct href/rel, sign-out still works when
  `getAccount` rejects (`...Once` mocks), TopBar no longer renders the standalone
  sign-out button.

## Acceptance criteria

1. A Ko-fi membership payment for a known email sets/extends `subscriber_until` by 33
   days from `max(now, current)`; an annual product by 370; tips record with no grant.
2. Replayed webhook deliveries (same `kofi_transaction_id`) are no-ops.
3. Wrong-email payments are stored and fully resolvable via
   `payments list --unmatched` + `payments match`; comps via `user subscribe`.
4. `GET /api/account` reflects the tier PR 1's budgets actually enforce (single code
   path through `effective_tier`).
5. The avatar dropdown shows identity, status, Ko-fi links, and sign-out; the old
   standalone sign-out button is gone; menu is keyboard/outside-click dismissible.
6. Local unit + frontend suites green; db_integration green in CI.

## Non-goals

- BYOK (PR 3). Username change (future menu item). Refund/chargeback handling
  (operator uses `user subscribe --until` to adjust). Automated dunning/expiry
  notices (silent downgrade to free is the designed lapse behavior). Webhook IP
  allow-listing (token is Ko-fi's documented mechanism).

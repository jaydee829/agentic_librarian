# Design: BMC revector — Buy Me a Coffee replaces Ko-fi (monetization arc 2/3, amendment)

**Date:** 2026-07-31
**Amends:** `2026-07-26-kofi-subscriptions-design.md` (the neutral core it defined is
built and reviewed; this spec swaps the provider-specific surface)
**Branch:** `feat/kofi-subscriptions` (rebased onto main post-#155; PR #156 retitled)

## Product decisions (user, 2026-07-31)

- **Provider: Buy Me a Coffee only** (over Polar / Ko-fi / hybrid) — chosen after a
  three-way research pass because BMC fixes both Ko-fi dealbreakers (no annual
  auto-renew, no cancellation events) at Ko-fi-level fees, and preserves most of the
  reviewed #156 branch. Polar rejected: 5%+50¢ (~22% take at $3/mo) + seed-stage risk
  outweigh merchant-of-record tax handling at this scale.
- **Pricing: $3/month + $30/year** (annual raised from $25 for net parity: ~$2.25/mo
  vs ~$2.44/mo after fees), plus one-off tips. Enable BMC's "supporter covers
  processing fees" option on the page.
- Everything else from the parent spec stands: supporter budgets from #155, avatar
  dropdown, CLI fallback, webhook-first linkage.

## BMC integration facts (verified 2026-07-31, official docs + OpenAPI spec)

Source of truth: `https://cdn.buymeacoffee.com/assets/integrations/bmc-webhooks-openapi.json`
(OpenAPI 3.1, all 16 events).

- **Envelope** (JSON POST): `{event_id, type, live_mode, created, attempt, data}`.
  `event_id` is the delivery's unique id (`attempt` increments on retries) →
  idempotency key. `live_mode: false` marks dashboard test events.
- **Signature:** HMAC-SHA256 of the **raw request body** with the endpoint's signing
  secret, sent in `x-signature-sha256`. Constant-time compare. (A real signature —
  stronger than Ko-fi's shared token.)
- **Membership events** (`membership.started/updated/cancelled/paused`), `data` incl.:
  `supporter_email`, `supporter_name`, `id` (subscription id), `psp_id` (Stripe sub id),
  `membership_level_name`, **`duration_type`: "month" | "year"** (structural cadence —
  kills the tier-name/amount-threshold classification heuristics), `amount`, `currency`,
  `status` ("active"|"canceled"|"paused"), **`current_period_start/end`** (unix ts),
  `started_at`, `canceled_at`, `cancel_at_period_end`, `paused_at/until/by`.
- **Donation events** (`donation.created/refunded`), `data` incl.: `supporter_email`,
  `amount`, `currency`, `transaction_id` (Stripe PaymentIntent), `status`
  ("succeeded"|"refunded"), `refunded`, `refunded_at`.
- Retries: up to 4 more deliveries, exponential backoff; endpoint auto-disables after
  10+ consecutive failures (→ handler must 200 aggressively once stored, like Ko-fi).
- `recurring_donation.*` ("monthly support" without a tier) also exists — treat like
  membership with `duration_type="month"` where fields allow, else record-only.
- **Verified-unknown:** whether `membership.updated` fires on each renewal cycle
  (schema mirrors Stripe subscriptions, whose `current_period_end` advances per cycle —
  very likely yes, but undocumented), and no pull/reconciliation API is documented.
  Design must not brick on either answer (see entitlement model).

## What changes vs the parent spec

### 1. Entitlement model: period-end + grace (replaces flat grant-days)

BMC tells us the paid-through horizon directly, so `core/entitlements.py` becomes:

- `classify(event_type, duration_type) -> "monthly" | "annual" | "tip" | "ignore"`:
  membership/recurring events with `duration_type "month"` → monthly, `"year"` →
  annual; `donation.created` → tip; anything else (shop, wishlist, refund handled
  separately) → ignore. **Delete** `KOFI_ANNUAL_TIER_NAMES` / `KOFI_ANNUAL_MIN_AMOUNT`
  and the amount/tier-name heuristics.
- `horizon(current_period_end: datetime, kind) -> datetime`:
  `current_period_end + GRACE_DAYS` (env `BMC_GRACE_DAYS`, default **5**, `_env_int`
  pattern). Applied on `membership.started` and `membership.updated` when
  `status == "active"`: `subscriber_until = max(existing, horizon)` (never shrink on a
  grant-path event — out-of-order deliveries must not truncate).
- Fallback when `current_period_end` is missing/invalid: previous behavior —
  `extend(current, days)` with 33/370 (kept as dead-simple insurance; `extend` also
  still serves the CLI's `--months/--days`).
- **Revocation (new, the reason we switched):**
  - `membership.cancelled`: if `cancel_at_period_end` truthy → cap:
    `subscriber_until = min(existing, current_period_end + GRACE_DAYS)` (member keeps
    what they paid for). Else (immediate) → cap at `canceled_at + GRACE_DAYS` falling
    back to now. Never extend on a cancel event.
  - `membership.paused`: same cap-at-period-end semantics (auto-resume will send a
    fresh started/updated).
  - `donation.refunded`: tips grant 0 days, so this is a record-only row (audit trail).
    Membership refunds have no BMC event type — operator adjusts via
    `user subscribe --until` if one ever matters.
- Failure-mode posture (explicit): if renewals turn out NOT to fire `updated`, monthly
  supporters lapse at period_end+5d and we flip `BMC_GRACE_DAYS` up while adding a
  renewal source — favoring under-grant of pennies over silent forever-grants from
  missed cancel events (webhook delivery is at-most-5-tries with no pull API).
  First real renewal (~2026-09-01) must be checked in logs — ops step below.

### 2. `payments` table goes provider-neutral (edit migration `b2c3d4e5f6a7` in place — unmerged)

| column | change |
|---|---|
| `kofi_transaction_id` | → `provider_event_id` (String, not null; idempotency key = envelope `event_id` as str) |
| `kofi_type` | → `event_type` (raw envelope `type`, e.g. "membership.started") |
| NEW `provider` | String, not null, default `"bmc"`; unique constraint becomes **(provider, provider_event_id)** |
| `tier_name` | → `level_name` (from `membership_level_name`, null for donations) |
| `is_subscription_payment` | → `duration_type` (String, null: "month"/"year"/null) |
| NEW `subscription_id` | String, null, index — BMC subscription `id` (str-coerced); groups a membership's lifecycle rows |
| `entitlement_days` | → `granted_until` (timestamptz, null — the resulting horizon; null = tip/ignore/cap events record their cap) |
| kept | id, email (lowercased `supporter_email`), amount, currency, payload JSONB, matched_user_id, created_at |

Model/CLI columns rename accordingly. `payments list` shows event_type, duration_type,
granted_until; `payments match <provider_event_id> <email>` recomputes from the stored
payload (single-source grant math, as before).

### 3. Webhook — `POST /webhooks/bmc` (replaces `/webhooks/kofi`)

Same root-level machine-route placement. Handler order:

1. Read **raw body** (`await request.body()` — signature is over raw bytes, so no
   Pydantic/Form parsing before verification).
2. Verify `x-signature-sha256` = HMAC-SHA256(body, `BMC_WEBHOOK_SECRET` env) via
   `hmac.compare_digest`. Env unset → **403 fail-closed**; missing/mismatched header →
   **403**. Never log the secret or signature.
3. `json.loads` body; envelope must be a dict with `event_id` and `type` → else **400**.
4. Idempotency: existing `(provider='bmc', provider_event_id)` → **200 duplicate**.
   (Retries reuse `event_id` with bumped `attempt`.)
5. `live_mode: false` events: process normally but log at info with a `[TEST]` marker —
   they exercise the full path in BMC's dashboard test mode; test grants against a
   matched email are acceptable in our pre-launch window (operator can revoke via
   `user subscribe --until`).
6. Classify → insert Payment row → match `users.email` == lowercased
   `supporter_email` → apply grant/cap in the same session (transactional, as parent).
7. Always **200** `{"status": "applied"|"capped"|"recorded"|"unmatched"|"duplicate"|"ignored"}`.
   DB failure → 500 (BMC retries; idempotency makes it safe). 10-consecutive-failure
   auto-disable is why nothing else may 4xx/5xx.

Decimal/None coercion guards carry over from the Ko-fi handler verbatim posture
(`is_finite()`, str-coercion of names) — non-finite/absent amounts → 0, never a 500
pre-persist.

### 4. Frontend + copy

- AccountMenu support links → the BMC page (constant `https://buymeacoffee.com/shelfwright`
  — confirm final handle with the operator at rollout); copy: "$3/mo · $30/yr · tip".
  Nudge line: "Use your Shelfwright sign-in email on Buy Me a Coffee so your support
  links up automatically."
- No structural component changes (dropdown built and reviewed in the parent arc).

### 5. Config & ops

- Env: `BMC_WEBHOOK_SECRET` (Secret Manager `librarian-bmc-webhook-secret`, wired in
  deploy.yml — replaces the parent spec's `KOFI_VERIFICATION_TOKEN` edit); optional
  `BMC_GRACE_DAYS`.
- Operator at rollout: create the BMC page; one membership level at **$3/mo with
  annual enabled at $30/yr**; enable "supporters pay processing fees"; register
  webhook endpoint `https://shelfwright.app/webhooks/bmc`, copy the signing secret;
  fire dashboard **test events** for every membership/donation type and verify
  handler statuses; **check logs at first real renewal (~1 month post-launch)** to
  confirm `membership.updated` fires per cycle (if not: raise `BMC_GRACE_DAYS`,
  open an issue for a renewal source).
- Payload JSONB stores supporter emails (no token/secret this time — HMAC key never
  appears in payloads); the DB-dump-leak⇒rotate note from the parent spec downgrades
  to ordinary PII handling.

## Unchanged from parent spec

Payments-table concept & transactional grant; CLI `user subscribe` /
`payments list|match` shapes; `GET /api/account`; AccountMenu structure/behavior/a11y;
error-handling posture; `hmac.compare_digest` everywhere; no user enumeration.

## Testing (delta)

- Unit: classify matrix over (event_type × duration_type); horizon/cap math incl.
  max-on-grant, min-on-cancel, out-of-order deliveries, missing period_end fallback,
  `cancel_at_period_end` string-"true"/"false" coercion; raw-body HMAC verification
  (good/bad/missing header, unset env, tampered body) with db seam patched; unix-ts
  parsing guards (absent/absurd values).
- db_integration: end-to-end started→updated (horizon advances)→cancelled (capped);
  unmatched stored + `payments match` applies horizon from payload; duplicate
  event_id no-op; refund records; tip records with null granted_until.
- Frontend: link hrefs/copy updated; everything else already covered.

## Acceptance criteria (delta from parent)

1. `membership.started`/`updated` (active, month|year) sets `subscriber_until` to
   `current_period_end + grace`, never shrinking it on grant-path events.
2. `membership.cancelled`/`paused` caps `subscriber_until` at the paid-through horizon;
   immediate cancels cap at cancellation time; caps never extend.
3. Signature verification rejects tampered/unsigned bodies and fails closed on unset
   secret; duplicate `event_id` deliveries are no-ops.
4. Ko-fi heuristics (`KOFI_ANNUAL_TIER_NAMES`, `KOFI_ANNUAL_MIN_AMOUNT`) and the
   `/webhooks/kofi` route are fully gone.
5. Parent ACs 3–6 (CLI, /api/account, AccountMenu, suites green) still hold on the
   reworked columns.

## Non-goals

Parent list stands, plus: `recurring_donation.*` first-class support beyond
best-effort monthly mapping; shop/wishlist/commission events (ignored, recorded);
automated reconciliation against a BMC pull API (none documented — operator CLI is
the backstop).

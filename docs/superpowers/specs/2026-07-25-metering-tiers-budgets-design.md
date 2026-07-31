# Design: Metering, tiers, and budgets (#100 — sub-project 1 of the monetization arc)

**Date:** 2026-07-25
**Issue:** #100 — per-user quotas, rate limiting, and enrichment metering
**Arc:** Monetization (1 of 3: this → Ko-fi subscriber tracking → BYOK)
**Branch:** `feat/metering-tiers-100` (PRs stack; user merges in order at end of arc)

## Product decisions (user, 2026-07-25)

- Scope: meter everything + per-tier budgets; the shared Gemini key flips to **paid tier**
  at rollout (operator step). Pricing data measured for a month before any price tuning.
- Tiers: **free** = trial quotas (~20 chat turns/day, ~300-row import); **supporter**
  (Ko-fi $3/mo / $25/yr, PR2) = generous env-tunable safety budgets, not unlimited;
  **byok** (PR3) = own key, bypasses app-key budgets.
- Cost model measured from prod: chat turn ≈ 0.7¢; deep-enrich ≈ 2¢/work under the paid
  tier's free 1,500 grounded-prompts/day, ≈ 13¢ past it ($35/1k). The **global grounding
  governor** below is what keeps the worst-case bill at "fixed floor + tokens".

## Problem

`record_llm_call` is wired into the chat mesh backends only. The most expensive path —
grounded scouts + embeddings (every one of prod's 1,127 works) — writes zero usage rows,
and nothing anywhere is enforced: no chat rate limit, no message length cap, no per-user
import/enrichment budget. One 2,000-row import ≈ 6k grounded calls, unmetered.

## Architecture

Three layers, all in this PR:

1. **Tier model** — schema + one pure function.
2. **Metering** — every Gemini/Claude call site records a `usage` row, including
   background tasks (requires threading `user_id` into task payloads).
3. **Enforcement** — pre-LLM chokepoint checks against the `usage`/domain tables
   (no Redis), returning 429 to interactive callers and *deferring* background work
   via Cloud Tasks `schedule_time`.

### 1. Tier model

- Migration: `users.subscriber_until` (timestamptz, nullable). Comps = far-future value
  (CLI to set it lands in PR2; until then operator SQL or nothing — acceptable, merge
  order is 1→2→3).
- New `core/tiers.py`:
  - `effective_tier(session, user_id) -> Literal["free", "supporter", "byok"]`:
    `byok` if a `user_credentials` row exists for vendor `gemini` (table has NO writers
    until PR3, so this branch is inert but the enum is stable across the arc);
    else `supporter` if `subscriber_until` is set and `> now()`; else `free`.
  - Budget lookups, all env-tunable with defaults (the `_seed_limit()` parse pattern —
    invalid/non-positive → default, read per call):

    | Env var | Default | Meaning |
    |---|---|---|
    | `CHAT_TURNS_PER_DAY_FREE` | 20 | user messages / UTC day |
    | `CHAT_TURNS_PER_DAY_SUPPORTER` | 200 | |
    | `CHAT_MESSAGE_MAX_CHARS` | 4000 | all tiers (structural) |
    | `IMPORT_MAX_ROWS_FREE` | 300 | per import file |
    | `IMPORT_MAX_ROWS_SUPPORTER` | 2000 | = existing absolute `MAX_ROWS` |
    | `GROUNDED_CALLS_PER_DAY_FREE` | 300 | ≈100 works/day |
    | `GROUNDED_CALLS_PER_DAY_SUPPORTER` | 1500 | can consume the whole free allowance |
    | `GROUNDED_CALLS_PER_DAY_GLOBAL` | 1400 | governor: all users, headroom under Google's free 1,500/day |

  - `byok` tier: chat/import budgets use the supporter values ×10 (structural sanity,
    not cost protection); grounded budgets don't apply once PR3 routes to their key —
    in THIS PR byok cannot occur (no credential writers), so no special-casing beyond
    the enum.
- Budget *counting* queries the existing tables — no new counters to keep consistent:
  - chat turns today = `messages` rows with `role='user'`, joined via `conversations.user_id`, `created_at >= UTC midnight`.
  - grounded calls today (per-user / global) = `usage` rows with `model = <grounding model>`, `key_source='app'`, same window.

### 2. Metering

- **Gemini grounded scout** (`scouts/grounded_llm.py` `GeminiGroundedLLM.generate`):
  the response already carries `usage_metadata` (prompt/candidates token counts) and
  `_extract_text` discards it. Capture it and call `record_llm_call("gemini", model,
  prompt_tokens, candidates_tokens)`. Meter **at the response object** — the SDK-level
  429/5xx retry (`genai_http_options`) must not double-count; scout-level retries are
  genuinely second billable calls and record again (correct).
- **Claude grounded scout** (`ClaudeGroundedLLM._agenerate`): read the SDK
  `ResultMessage.usage` the same way `agents/backends/claude.py` already does; record
  with vendor `anthropic`.
- **Embeddings** (`scouts/utils.py get_cached_embedding`): embed responses expose no
  token counts, and the `@lru_cache` means only cache misses hit the network. Record
  **inside the cached function body** (misses only), `input_tokens = len(text) // 4`
  (documented estimate — embeddings are $0.15/M, this is visibility, not billing-grade),
  `output_tokens = 0`, model `gemini-embedding-001`. This transparently covers all
  callers (trope/style managers, MCP warming, two-phase warming, analysis_style).
- **Failure posture unchanged:** `record_llm_call` never raises; a metering failure
  logs and the call proceeds (ADR-048 best-effort stands until billing-grade is needed).

#### User attribution in background tasks (the prerequisite)

`record_llm_call` → `get_required_user_id()` fails closed, and today the enrichment
handlers have **no user in context**, so metering there would silently drop every row:

- `enqueue_enrichment(work_id)` and `enqueue_edition_completion(work_id, fmt)` gain a
  `user_id: str | None = None` parameter, appended to the task URL as a query parameter.
  Callers pass the acting user: `books.py` add-book and `main.py` PATCH /history (current
  user), `imports/worker.py` (the row's `user_id`).
- `/internal/enrich/{work_id}`, `/internal/complete-edition/{work_id}` accept the
  optional `user_id` query param and wrap their work in `as_user(user_id)` when present.
  **Tasks enqueued before this deploy carry no user_id — handlers must tolerate its
  absence** (run un-attributed exactly as today; metering skips, nothing crashes).
- `imports/worker.py process_import_row`: the `as_user(...)` block moves up to also
  cover `enrich_fast(...)` (today it covers only the history/suggestion writes), so
  import-triggered fast-scout calls are attributed.
- Enrichment for a shared work bills to whoever requested it (catalog sharing means
  later users of the same work pay nothing — by design).

### 3. Enforcement

**Chat** (`api/main.py /chat`):
- Length cap as an explicit handler check (`len(message) > chat_message_max_chars()`),
  NOT a Pydantic `max_length` constant — the env value must be tunable without redeploy
  and read per call. Over-length → 422 with a clear detail.
- After `get_current_user`, before the `StreamingResponse` is created (the map confirms
  429 is impossible once streaming starts): count today's user messages; at/over the
  tier budget → `HTTPException(429, detail={"code": "chat_quota", "message": ..., "tier": ...})`.
- Frontend `streamChat` (client.ts): today any non-OK collapses to a generic error.
  Add: `res.status === 429` → parse detail → show its message (friendly copy: daily
  limit reached — support Shelfwright or bring your own key soon). No toast system is
  built (none exists; per-view error slots are the house pattern).

**Imports** (`api/imports.py`):
- Per-tier rows/import: preview/commit reject files over the tier's `IMPORT_MAX_ROWS_*`
  (existing absolute `MAX_ROWS=2000` stays as the ceiling). 413/422-style JSON detail the
  ImportView already renders; include the tier and the limit in the message.
- **One in-flight import per user** (any tier): commit refuses (409) while the user has
  a job with pending/running rows (query `import_jobs` + `import_rows` status; no new
  columns).

**Enrichment budget + global governor** (`api/internal.py` enrich / complete-edition):
- At task execution, BEFORE running scouts: if the acting user's grounded calls today
  ≥ tier budget, OR the global count ≥ `GROUNDED_CALLS_PER_DAY_GLOBAL`, **defer**: the
  handler re-enqueues the same task with `schedule_time` = next UTC midnight + jitter
  (0–30 min, spreads the morning thundering herd) and returns 200 (task consumed —
  deferral must NOT burn the #97 give-up retry count).
- Un-attributed tasks (pre-deploy backlog, no user_id) check only the global governor.
- `enrichment/tasks.py` + `imports/tasks.py` gain optional `schedule_time` support on
  the Task dict (protobuf timestamp) — the missing knob the map identified.
- `/books` gets no separate in-flight cap: the daily grounded budget bounds the same
  cost (the issue's suggestion is subsumed).

**Queue fairness** (issue item 4): NOT in this PR. The governor + per-user budgets bound
the cost; FIFO fairness between a bulk import and an interactive add remains a 6.5/6.6
follow-up. Noted here so the deferral is deliberate.

## Data flow (enforcement read path)

Every check is one indexed COUNT against existing tables (`messages`, `usage`,
`import_rows`) scoped to a UTC day — no new state, no Redis, consistent-enough (a
racing pair of requests can each pass at N-1; budgets are safety nets, not invoicing).
`usage.created_at` has no index today; `usage(user_id)` is indexed — the per-day scan
per user is trivial at this scale. Add a composite index only if CI's query plans say so
(YAGNI now).

## Error handling

- Metering: best-effort, never blocks the user path (unchanged posture).
- Enforcement failures fail OPEN with a logged warning (a broken budget query must not
  take chat down — the budget protects cost, not correctness).
- 429 bodies always carry `{"code", "message"}` so the frontend renders a human
  sentence, never a bare status.

## Testing

Per house rules: parametrized cases only; Postgres-only models → counting/attribution
tests are `db_integration` (CI-first gate); DB-free logic (env parsing, tier decision
with injected rows, usage-capture with fake response objects) unit-tests locally.

- Unit: `effective_tier` matrix (subscriber_until past/future/null × credential
  present/absent); budget env parsing (valid/invalid/non-positive); Gemini scout
  metering captures usage_metadata off a fake response (and records once despite
  `_extract_text` retry paths); embed metering records only on cache miss with the
  documented estimate; deferral schedule_time computation (next-UTC-midnight + jitter
  bounds).
- db_integration: chat-turn counting query (today vs yesterday rows, other users'
  rows excluded); grounded-call counting (per-user and global, key_source filter);
  worker attribution — `process_import_row` writes usage rows with the row's user_id;
  enrich handler defers (returns 200, re-enqueues with schedule_time) when over budget
  — enqueue helper faked, assert the schedule_time argument.
- API integration: /chat 429 at the budget boundary (seed N user messages today, N+1th
  turn refused; under-budget passes); over-length message 422; import commit 409 with
  an in-flight job; per-tier rows/import rejection.
- Frontend (vitest): streamChat surfaces the 429 detail message; generic errors
  unchanged.

## Acceptance criteria

1. Every Gemini/Claude LLM call and every embedding cache-miss writes a `usage` row
   with correct user attribution, including Cloud-Tasks-driven enrichment.
2. Free tier: 21st chat turn of the day → 429 with human-readable detail; 301-row
   import rejected; supporter limits are the higher numbers; all env-tunable.
3. Over-budget/over-governor enrichment tasks defer to next UTC day via
   `schedule_time` without burning give-up retries; pre-deploy tasks (no user_id)
   still complete.
4. Frontend shows the quota message on chat 429 (not the generic error).
5. Full local unit suite green; db_integration/API additions green in CI; no frontend
   regression.

## Non-goals (deferred within the arc or later)

- Ko-fi webhook, payments table, subscribe CLI, avatar-dropdown UI → PR2.
- BYOK storage/routing/walkthrough → PR3.
- Queue fairness (bulk vs interactive), summarizing old chat context, billing-grade
  metering, `usage` table rotation → later phases.

## Ops at rollout (operator, with sub-project 1's deploy)

Flip the shared Gemini key's project to the paid tier (billing on). Until then the
governor still helps (free tier's 500 grounded RPD << 1,500) — set
`GROUNDED_CALLS_PER_DAY_GLOBAL=450` in the interim if the flip lags the deploy.

# Design: BYOK — bring your own Gemini key (monetization arc 3 of 3)

**Date:** 2026-07-26
**Arc:** Monetization (PR 1 = #100 metering/tiers/budgets; PR 2 = Ko-fi tracking; this PR stacks on both)
**Branch:** `feat/byok` (base: `feat/kofi-subscriptions` / PR #156)

## Product decisions (user, 2026-07-25)

BYOK is the **free tier's escape valve**: users who bring their own (free-tier) Google
AI Studio key ride at no marginal cost to the operator, are **tracked**
(`usage.key_source='byok'`, tier `byok`), and get a **guided walkthrough**. The
`user_credentials` table (KMS ciphertext, Lift-1 placeholder) and `usage.key_source`
were built for exactly this; PR 1's tier logic already returns `byok` when a
credential row exists — this PR creates the first writer and the actual routing.

## Scope boundaries (from the code survey)

1. **Embeddings stay on the app key — deliberately NOT routed per-user.** The embed
   path is a process-wide client + `lru_cache` keyed `(model, text)` with miss-only
   metering: per-user routing would misattribute costs across users via cache hits and
   buy nothing (embeddings are $0.15/M and have separate, higher quota). Documented in
   code where the decision bites.
2. **BYOK is a *Gemini* key.** On `AGENT_BACKEND=claude` deployments the generation
   path ignores it (Max subscription, no API key at all). Prod runs `adk`, so routing
   covers: the chat mesh (ADK `Gemini` models) and the grounded scouts
   (`GeminiGroundedLLM`). The claude branch is untouched.
3. **Never route a key by mutating `os.environ`** (`_ensure_adk_credentials` is
   global state — a per-request env swap races across concurrent turns). The key
   threads as a **parameter**: the mesh is rebuilt per conversation turn and ADK
   `Gemini(api_key=...)` accepts one; scouts already take `api_key` per instance.
4. **No silent fallback to the app key.** A byok user whose key fails gets an error
   telling them to check Settings — not a quiet bill for the operator.

## Architecture

### 1. `core/byok.py` — KMS crypto + key resolution

- New dependency `google-cloud-kms`. Env `KMS_KEY_NAME` (full resource name
  `projects/agentic-librarian-prod/locations/us-central1/keyRings/librarian/cryptoKeys/byok-credentials`).
- `encrypt_key(plaintext: str) -> bytes` / `decrypt_key(ciphertext: bytes) -> str` via
  `kms.KeyManagementServiceClient` (cached client, `_client()` seam like
  `enrichment/tasks.py`; lazy import so the dependency is only needed where used).
  `KMS_KEY_NAME` unset → `ByokNotConfigured` (API layer maps to 503).
- `resolve_gemini_key(session, user_id) -> str | None`: load the
  `UserCredential(user_id, vendor='gemini')` row; None when absent; decrypt when
  present. Decrypt failure raises `ByokKeyError` (callers surface it — no fallback).
  Plaintext keys are NEVER logged, never stored, and live only in call scope.
- Module `db_manager`-free: takes a session (callers own sessions), keeping crypto
  pure of pool concerns.

### 2. Credentials API — `api/credentials.py` (mirrors `api/libraries.py` shape)

- `GET /api/me/credentials` → `{"configured": bool, "updated_at": iso-or-null}`
  (never the key, never a fragment — the table stores ciphertext only).
- `PUT /api/me/credentials` `{"api_key": "..."}`:
  1. Shape check (non-empty, trimmed, sane length ≤ 200).
  2. **Live validation**: one `count_tokens` call on `gemini-3.1-flash-lite` with a
     fresh `genai.Client(api_key=candidate)` (free, fast). Auth failure → 422
     `{"code": "invalid_api_key", "message": ...}` (seam-injectable for tests).
  3. `encrypt_key` → upsert `UserCredential` (`kms_key_name` = env value). → 200
     `{"configured": true}`. The user's tier flips to `byok` automatically (PR 1's
     `effective_tier` reads row existence).
- `DELETE /api/me/credentials` → remove the row (idempotent 200; tier reverts).
- `KMS_KEY_NAME` unset → 503 `{"code": "byok_unavailable"}` on PUT; GET still answers.

### 3. Key routing

- **`core/usage.py`**: `record_llm_call(..., key_source: str = "app")` — the one
  place `key_source` is written stays one place. PR 1's budgets already filter
  `key_source == 'app'`, so byok calls fall out of the app-key governor automatically,
  and `tier == 'byok'` already bypasses/relaxes budgets.
- **Chat mesh**: `astart_conversation(..., api_key: str | None = None)` →
  `build_runner(api_key)` → `create_agent_mesh(api_key)` → `_gemini(model_name,
  api_key)` → `Gemini(model=..., api_key=..., retry_options=...)`. The chat handler
  (`api/main.py`) resolves the key once per turn (inside its existing session usage)
  and passes it through `_SyncOpener`; `stream.sse_turn` needs the key_source so
  runtime's `_record_event_usage` records `'byok'` — thread a `key_source` value
  alongside (default `'app'`). `ByokKeyError` at resolution → 409
  `{"code": "byok_key_error", "message": "Your API key failed — check Settings."}`
  raised BEFORE the stream starts (same pattern as the 429).
- **Grounded scouts (deep enrichment)**: `/internal/enrich` + `/internal/complete-edition`
  handlers (which already `as_user(user_id)`) resolve the key and pass
  `api_key`/`key_source` into `two_phase.enrich_deep(work_id, api_key=None,
  key_source="app")` (and `complete_edition`), which threads them into the
  `LLMScout(api_key=...)`/`GeminiGroundedLLM(api_key=...)` constructions it makes.
  `grounded_llm` metering records the threaded `key_source`. `ByokKeyError` in a task
  → log + 200 `{"status": "byok_key_error"}` (task consumed — retrying can't fix a
  revoked key; the user re-adds enrichment by fixing the key and using the requeue
  sweep / next add). Key revoked at Google mid-call → the scout call 401s → existing
  scout-exception path (retry → give-up bound) — acceptable, documented residual.
- **Fast pass** (`enrich_fast` — API-only scouts, no LLM): untouched.
- **Embeddings**: untouched (app key, decision #1) — byok users' embed rows stay
  `key_source='app'` and are the only app-key spend they generate (fractions of a cent).

### 4. Frontend

- **Settings** (`SettingsView.tsx` gains a second section — "Your API key"): the
  walkthrough stepper copy — (1) create a free key at aistudio.google.com/apikey
  (external link), (2) paste it here, (3) Save validates it live. Status line when
  configured ("Using your own key since <date>") + Remove button. Errors surface the
  server's `message` (invalid key / byok unavailable). Follows the existing section's
  save-button + `status` aria-live pattern.
- **AccountMenu**: `tier === 'byok'` status renders "Using your own key"; new
  menuitem link "API key settings" → `/settings` (the earmarked BYOK entry).
- `client.ts`: `getCredentials()`, `putCredentials(apiKey)`, `deleteCredentials()`.
- Chat 409 `byok_key_error` surfaces via the same streamChat non-OK detail path as
  429/422 (extend the status allowlist).

### 5. Config & ops (operator steps, PR body)

- New `infra/09-kms.sh`: create keyring `librarian` + key `byok-credentials`
  (us-central1, symmetric encrypt/decrypt), grant
  `roles/cloudkms.cryptoKeyEncrypterDecrypter` to
  `librarian-api-runtime@agentic-librarian-prod.iam.gserviceaccount.com`.
- deploy.yml: add `KMS_KEY_NAME` to `--set-env-vars` (a resource name, not a secret).
- `pyproject.toml`: `google-cloud-kms` dependency.

## Error handling summary

| Failure | Where | Behavior |
|---|---|---|
| KMS_KEY_NAME unset | PUT credentials | 503 byok_unavailable; GET works; nothing else affected |
| Invalid key at save | PUT validation | 422 invalid_api_key, nothing stored |
| Decrypt fails / KMS down at use | chat | 409 byok_key_error pre-stream ("check Settings") |
| Decrypt fails / KMS down at use | enrich task | log + 200 byok_key_error (task consumed, no retry storm) |
| Key revoked at Google mid-call | scouts | existing scout-exception → retry → give-up path (residual) |
| Key revoked at Google mid-call | mesh | ADK call fails → existing SSE generic error (residual: not byok-specific copy) |

## Testing

- Unit (local): byok crypto seams (fake KMS client — encrypt/decrypt round-trip,
  ByokNotConfigured on unset env); credentials API guards (shape 422, byok_unavailable
  503, validation-seam auth-fail 422, success upsert flow with patched session/KMS);
  key threading — `_gemini`/`create_agent_mesh`/`build_runner` accept and pass
  `api_key` (fake ADK objects or introspection of constructed `Gemini` args);
  `two_phase` threads `api_key` into `LLMScout` (existing scout-construction seams);
  `record_llm_call` key_source parameter; chat handler 409 pre-stream on ByokKeyError.
- db_integration (EXECUTE locally — Postgres is up): credentials PUT/GET/DELETE
  round-trip with a fake-KMS seam (ciphertext bytes stored, never plaintext); tier
  flips free→byok→free across PUT/DELETE (through `/api/account`); enrich handler
  passes a byok user's key into `enrich_deep` (probe seam) and usage rows record
  `key_source='byok'`.
- Frontend (vitest): Settings section (save success/invalid-key error/remove;
  `...Once` mocks), AccountMenu byok status + settings link, streamChat 409 detail.

## Acceptance criteria

1. A user can save a validated Gemini key (stored as KMS ciphertext only), see
   "configured" status, and remove it; their tier reads `byok` while configured.
2. Chat turns and deep-enrichment scouts for a byok user run on THEIR key and write
   `usage` rows with `key_source='byok'`; app-key budgets/governor ignore those rows.
3. A failing/unconfigured-at-use key produces the defined errors — never a silent
   fallback to the app key.
4. Embeddings remain app-keyed (documented), and non-byok users' behavior is
   byte-identical to before this PR.
5. Settings walkthrough + AccountMenu status/link work; suites green (frontend, unit,
   db_integration executed locally + CI).

## Non-goals

- Anthropic-vendor BYOK (schema supports it; no routing — the claude backend is
  subscription-auth). Key health dashboards/notifications. Plaintext key display
  after save. Per-key usage export. Migrating existing users.

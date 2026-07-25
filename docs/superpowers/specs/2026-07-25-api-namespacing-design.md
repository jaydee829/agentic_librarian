# Design: Namespace the API under `/api/*` (#151)

**Date:** 2026-07-25
**Issue:** #151 — "Namespace the API under /api/* to make SPA/API route collisions impossible"
**Follow-up to:** #150 (refresh-on-a-tab rendered `{"detail":"Missing bearer token."}` as the page)
**Type:** Deliberate refactor, single PR, no data/schema/infra changes.

## Problem

The SPA and the API are served same-origin from one container. Several browser client
routes (`/history`, `/recommendations`, `/analysis`) are *also* authed API GET paths, and
the API router wins — so a browser refresh on a tab rendered raw API JSON as the page.
#150 patched this with an `Accept`-header-sniffing middleware that rewrites HTML
navigations to the shell. That covers today's surface but is a band-aid: any new API path
that happens to match a future client route re-opens the hole.

The structural cure is to prefix every **user-facing** API route with `/api/`, so client
routes and API paths live in disjoint namespaces and can never collide.

## Scope decision: purpose-scoped, not fully uniform

A collision can only occur between an API path and a **browser client route** (the SPA tab
routes: `/history`, `/recommendations`, `/analysis`, `/add`, `/import`, `/settings`).
Machine-only endpoints can never be SPA routes, so prefixing them buys zero collision
safety while adding real transition hazard. Therefore:

**Prefixed → `/api/*`** (all user-facing data routes):

| Location | Routes |
|---|---|
| `main.py` inline | `/history`, `/history/{entry_id}` (DELETE + PATCH), `/works`, `/conversations/current`, `/conversations`, `/chat` |
| `recommendations.py` | `/recommendations`, `/recommendations/{suggestion_id}/status` |
| `analysis.py` | `/analysis` |
| `availability.py` | `/availability` |
| `books.py` | `/books` |
| `imports.py` | `/import/preview`, `/import/commit`, `/import/{job_id}`, `/import/{job_id}/retry` |
| `libraries.py` | `/libraries/search`, `/me/libraries` (GET + PUT) |

**Stays at root** (machine-only — deliberately un-prefixed):

- `/health`, `/health/db` — Cloud Run probes + deploy smoke checks key on these.
- `/internal/*` (`internal.py`) — Cloud Tasks targets. Their URLs **and OIDC audience** are
  composed in `enrichment/tasks.py` from `ENRICH_TARGET_BASE_URL`; moving them would force
  a URL + audience change and a compat window for in-flight tasks, for no collision benefit.
- `/__/auth/*` (`firebase_auth_proxy.py`) — a **fixed** Firebase contract path; the Firebase
  SDK loads its sign-in handler from exactly this path and it must not move.
- `/` and the SPA catch-all `/{full_path:path}`.

## Backend mechanism (chosen: single parent router)

In `main.py`, introduce one `api_router = APIRouter(prefix="/api")`:

1. `api_router.include_router(...)` for each user-facing sub-router (recommendations,
   analysis, availability, books, imports, libraries).
2. Change the inline user-facing routes from `@app.<verb>("/history")` to
   `@api_router.<verb>("/history")` — **the decorator path strings stay literally the
   same**; only the object changes and the single `prefix="/api"` prepends `/api`.
3. `app.include_router(api_router)` **after** all `@api_router` decorations and **before**
   the SPA root/catch-all (which must remain registered last).
4. `firebase_auth_proxy_router` and `internal_router` keep `app.include_router(...)` at
   root. `/health`, `/health/db`, `/`, and the catch-all keep `@app`.

The prefix lives in exactly one place, mirroring the frontend's single choke point.

**Rejected alternatives:** (B) `prefix="/api"` on each `include_router` + rewriting each
inline decorator — repeats `/api` ~17 times, easy to miss one. (C) mounting a sub-app at
`/api` — separate middleware/exception/lifespan scope, overkill.

## Middleware cleanup (`main.py`, currently ~L506–531)

Once data routes live under `/api/*`, a browser navigation to `/history` no longer matches
any API route — it falls straight through to the SPA catch-all, which already serves the
shell. So:

- **Delete** the `_SPA_CLIENT_ROUTES` allowlist and the navigation-rewrite branch of
  `_spa_navigation_and_api_cache` (the `scope["path"]` rewrite).
- **Keep** the `no-store` stamping on responses that set no cache policy of their own —
  private authed API JSON must never be cacheable. The middleware is retained solely for
  this; if reduced to only the stamping, rename it accordingly (e.g. `_api_no_store_cache`).

## Frontend (`frontend/src/api/client.ts`)

Single change in the choke point `authedFetchRaw` (~L117): prepend an `API_PREFIX = '/api'`
constant to `path` before `fetch`. All ~15 call sites keep their literal relative paths
(`/history`, `/recommendations`, …). `getJson` routes through `authedFetchRaw`, so it is
covered. The Firebase SDK's `/__/auth/*` requests are issued by the SDK, not `client.ts`,
so they are unaffected.

## Tests

- Update all API test paths (unit + integration) from `/x` to `/api/x`. `test_firebase_auth_proxy`
  stays at `/__/auth`. Internal-endpoint tests stay at `/internal/*`. Health tests stay at
  `/health`.
- Add a regression test asserting a bare `GET /history` (no `/api` prefix) now returns the
  **SPA shell** (200, HTML containing `id="root"`) rather than API JSON — proving the
  collision is structurally impossible, and locking in the deleted middleware's former job.
- Follow the repo test convention: parametrized cases, no loops inside a single test body.

## Deploy (`.github/workflows/deploy.yml`)

- Container smoke (`/health`, `/` → shell): **unchanged**.
- Live smoke auth probe (~L177): `curl … "${URL}/history"` expecting 401 → change to
  `"${URL}/api/history"`. Post-refactor, un-prefixed `/history` returns the SPA shell (200),
  so the probe must target the prefixed path to still exercise auth enforcement.

## Deploy behavior (no compat shim)

A browser tab open across the deploy runs old JS calling old un-prefixed paths; after
deploy those return the SPA shell (200 HTML) instead of JSON, breaking in-flight fetches
until the user refreshes, which self-heals. Accepted: small user base, infrequent deploys,
and the app already tolerates cold-start hiccups. Old un-prefixed paths are simply gone.

## Non-goals

- No Cloud Tasks / OIDC / `enrichment/tasks.py` changes (internal routes stay at root).
- No health-probe or `/health` shim.
- No API versioning (`/api/v1`) — out of scope; `/api` is the namespace boundary.
- No route logic, payload, or auth changes — paths only.

## Acceptance criteria

1. Every user-facing route responds under `/api/*`; the same paths without `/api` fall
   through to the SPA shell.
2. `/health`, `/health/db`, `/internal/*`, `/__/auth/*` unchanged.
3. Frontend reaches the API through `/api/*` via the single `client.ts` change; the app
   works end-to-end (history loads, chat streams, import, recommendations, analysis).
4. The `_SPA_CLIENT_ROUTES` navigation-rewrite is gone; `no-store` stamping remains.
5. Full unit suite green; `deploy.yml` live smoke updated to `/api/history`.

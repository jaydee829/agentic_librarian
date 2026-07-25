# API `/api/*` Namespacing Implementation Plan (#151)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prefix every user-facing API route with `/api/` so SPA client routes and API paths live in disjoint namespaces and can never collide (structural cure for #150).

**Architecture:** A single parent `APIRouter(prefix="/api")` in `main.py` carries all user-facing routes (inline routes switch from `@app` to `@api_router`; sub-routers are `include_router`'d into it). Machine-only endpoints (`/health`, `/internal/*`, `/__/auth/*`) stay at root. The frontend reaches everything through one `'/api'` prefix added in the `client.ts` fetch choke point. The now-redundant SPA navigation-rewrite middleware is deleted; a bare `/history` falls through to the SPA catch-all naturally.

**Tech Stack:** FastAPI (Python 3.14), pytest, React + Vite + TypeScript + Vitest.

## Global Constraints

- **Purpose-scoped prefix only.** Prefix user-facing data routes. NEVER prefix `/health`, `/health/db`, `/internal/*`, `/__/auth/*`, `/`, or the SPA catch-all.
- **`/import` ≠ `/internal/import-row`.** The user-facing import routes (`/import/preview`, `/import/commit`, `/import/{job_id}`, `/import/{job_id}/retry`) get prefixed. The Cloud Tasks endpoint `/internal/import-row/{row_id}` does NOT. No blind find-replace on the string "import".
- **Router files do not change.** `recommendations.py`, `analysis.py`, `availability.py`, `books.py`, `imports.py`, `libraries.py`, `internal.py`, `firebase_auth_proxy.py` keep their existing decorator paths — the `/api` prefix comes from the parent `include_router`.
- **No route logic, payload, auth, schema, or infra changes.** Paths only.
- **Test style (repo + user pref):** parametrized cases, never loops inside a single test body; each case atomic.
- **`db_integration` integration tests run in CI only** (no local Postgres) — get them right by inspection; they are the first CI gate.
- **Before every commit:** `uvx ruff check <files>` AND `uvx ruff format <files>`. No `[skip ci]` in messages.
- **Commit trailer:** end every commit message with
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_011hyNHf8LCF6Gs8gUeKfxh3`.
- Run tests with `.venv/Scripts/python -m pytest ...` from repo root.

---

### Task 1: Backend — move user-facing routes under `/api`, delete the navigation-rewrite middleware

**Files:**
- Modify: `src/agentic_librarian/api/main.py`
- Modify (test path updates): `test/unit/test_api_history.py`, `test/unit/test_api_works.py`, `test/unit/test_api_import_commit.py`, `test/unit/test_api_import_preview.py`, `test/unit/test_api_import_routes_wired.py`, `test/unit/test_api_requires_auth.py`
- Modify (behavior-coupled tests): `test/unit/test_spa_serving.py`
- Modify (CI-only integration path updates): `test/integration/test_analysis_api.py`, `test/integration/test_api_history_db.py`, `test/integration/test_api_import_status.py`, `test/integration/test_api_works_db.py`, `test/integration/test_availability_api.py`, `test/integration/test_books_api.py`, `test/integration/test_chat_api.py`, `test/integration/test_libraries_api.py`, `test/integration/test_recommendations_api.py`, `test/integration/test_recommendations_read_status.py`
- Modify: `.github/workflows/deploy.yml` (live-smoke auth probe)
- **Do NOT touch:** `test/unit/test_internal_import_row.py`, `test/unit/test_enqueue_import_row.py`, `test/unit/test_firebase_auth_proxy.py`, and any `/health`, `/health/db`, `/internal/*` assertions.

**Interfaces:**
- Produces: all user-facing routes served under `/api/*`; bare (un-prefixed) user-facing paths fall through to the SPA shell. Consumed by Task 2 (frontend must call `/api/*`).

- [ ] **Step 1: Rewrite the behavior-coupled SPA-serving test to the new contract (write the failing test first)**

In `test/unit/test_spa_serving.py`, **replace** `test_fetch_style_request_still_reaches_the_api` (currently L104–112) with the two tests below. The old test asserted the API wins at bare `/history`; post-namespacing the API lives only at `/api/history`, so a bare path returns the shell for ANY `Accept`, and the gated API answers under `/api`.

```python
@pytest.mark.parametrize("path", ["/history", "/recommendations", "/analysis"])
def test_bare_api_path_now_serves_the_shell(tmp_path, monkeypatch, path):
    # Post-#151: user-facing routes live under /api/*, so an un-prefixed path can never
    # be an API route — it falls through to the SPA catch-all shell regardless of Accept.
    # This is the structural cure: no client route can collide with an API path anymore.
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get(path, headers={"Accept": "*/*"})
    assert r.status_code == 200
    assert 'id="root"' in r.text


@pytest.mark.parametrize("path", ["/api/history", "/api/recommendations", "/api/analysis"])
def test_prefixed_api_path_is_gated(tmp_path, monkeypatch, path):
    # The API moved under /api and is still auth-gated (401 without a token, not the shell).
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get(path, headers={"Accept": "*/*"})
    assert r.status_code == 401
    assert r.json() == {"detail": "Missing bearer token."}
```

The parametrized `test_browser_navigation_to_spa_route_serves_shell` (L91–101) stays as-is — those paths still serve the shell (now via the catch-all rather than the rewrite). Update the two module comments at L1–3 and L84–88 to describe the namespace design instead of the Accept discriminator.

- [ ] **Step 2: Run the new tests — verify they FAIL**

Run: `.venv/Scripts/python -m pytest test/unit/test_spa_serving.py -v`
Expected: `test_prefixed_api_path_is_gated` FAILS (no `/api/history` route yet → catch-all shell 200, not 401). `test_bare_api_path_now_serves_the_shell` currently FAILS too (bare `/history` with `*/*` still hits the API → 401).

- [ ] **Step 3: Update `test_api_requires_auth.py` to the prefixed paths**

In `test/unit/test_api_requires_auth.py`, change the two data-route tests to the `/api` paths (leave `test_health_stays_open` and `test_health_db_requires_auth` untouched — health stays at root):

```python
def test_history_requires_auth():
    assert client.get("/api/history").status_code == 401


def test_works_requires_auth():
    assert client.get("/api/works").status_code == 401
```

- [ ] **Step 4: Add `APIRouter` import and build the parent router in `main.py`**

In `src/agentic_librarian/api/main.py`, add `APIRouter` to the FastAPI import (L9):

```python
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request
```

Replace the user-facing `app.include_router(...)` block (currently L109–116) with a parent router; keep the two machine-only routers at root:

```python
# User-facing data routes live under /api/* so they can never collide with an SPA client
# route (#151, follow-up to #150). The prefix lives in exactly one place.
api_router = APIRouter(prefix="/api")
api_router.include_router(recommendations_router)
api_router.include_router(analysis_router)
api_router.include_router(availability_router)
api_router.include_router(books_router)
api_router.include_router(imports_router)
api_router.include_router(libraries_router)

# Machine-only routers stay at root — never SPA-route collisions, and moving them would
# break fixed contracts (Firebase /__/auth/*) or Cloud Tasks target URLs (/internal/*).
app.include_router(firebase_auth_proxy_router)
app.include_router(internal_router)
```

Note: `firebase_auth_proxy_router` was previously included first (L109); registration order among these disjoint-path routers does not matter, only that all are registered before the greedy catch-all.

- [ ] **Step 5: Switch the inline user-facing routes from `@app` to `@api_router`**

In `main.py`, change ONLY these decorators (paths stay identical; the `/api` prefix is applied by `api_router`):

- L175 `@app.get("/history")` → `@api_router.get("/history")`
- L244 `@app.delete("/history/{entry_id}")` → `@api_router.delete("/history/{entry_id}")`
- L259 `@app.patch("/history/{entry_id}")` → `@api_router.patch("/history/{entry_id}")`
- L376 `@app.get("/works")` → `@api_router.get("/works")`
- L453 `@app.get("/conversations/current")` → `@api_router.get("/conversations/current")`
- L460 `@app.post("/conversations")` → `@api_router.post("/conversations")`
- L467 `@app.post("/chat")` → `@api_router.post("/chat")`

Leave `@app.get("/health")` (L119), `@app.get("/health/db")` (L124), `@app.get("/")` (L534), and `@app.get("/{full_path:path}")` (L539) on `@app`.

- [ ] **Step 6: Register the parent router before the SPA section**

In `main.py`, immediately before the `# SPA static serving` comment block (currently ~L483, after the `/chat` endpoint), add:

```python
# Registered after all @api_router routes are declared and before the SPA catch-all so
# every /api/* route takes precedence over the greedy /{full_path:path} fallback.
app.include_router(api_router)
```

- [ ] **Step 7: Delete the navigation-rewrite middleware branch, keep the no-store stamping**

In `main.py`, remove the `_SPA_CLIENT_ROUTES` constant (L514) and its explanatory comment (L506–513). Replace the middleware (L517–531) with a stamping-only version:

```python
# Private authed API JSON must never sit in a browser/proxy cache. The SPA shell and
# static assets set their own Cache-Control (below), so this only stamps responses that
# declared none — i.e. all API JSON. (The #150 Accept-sniffing navigation rewrite is gone:
# with data routes under /api/*, a bare /history is not an API route and reaches the SPA
# catch-all naturally — #151.)
@app.middleware("http")
async def _api_no_store_cache(request: Request, call_next):
    response = await call_next(request)
    if "cache-control" not in response.headers:
        response.headers["cache-control"] = "no-store"
    return response
```

- [ ] **Step 8: Run the SPA-serving + auth suites — verify they PASS**

Run: `.venv/Scripts/python -m pytest test/unit/test_spa_serving.py test/unit/test_api_requires_auth.py -v`
Expected: PASS (including both new tests from Step 1 and the updated auth tests). `test_api_json_responses_are_no_store` and the navigation/traversal tests still pass unchanged.

- [ ] **Step 9: Update the remaining local unit test paths**

Prefix `/api` on the user-facing paths in these files (each file's paths, verbatim rule below). Do NOT touch `/internal/*`, `/health`, or `/__/auth`.

- `test/unit/test_api_history.py`: `/history` and `/history/{...}` → `/api/history...`
- `test/unit/test_api_works.py`: `/works` → `/api/works`
- `test/unit/test_api_import_preview.py`: `/import/preview` → `/api/import/preview`
- `test/unit/test_api_import_commit.py`: `/import/commit` → `/api/import/commit`
- `test/unit/test_api_import_routes_wired.py`: `/import/...` → `/api/import/...` (verify it does not assert on `/internal/import-row`; if it does, leave that line unchanged)

- [ ] **Step 10: Run the full local unit suite — verify green**

Run: `.venv/Scripts/python -m pytest test/unit -q`
Expected: PASS (all collected unit tests; `db_integration`-marked tests are deselected locally). If any unit test still references a bare user-facing path, fix it to `/api/*`.

- [ ] **Step 11: Update the CI-only integration test paths (by inspection)**

Prefix `/api` on user-facing paths in each integration file. These execute in CI (Postgres), not locally, so read each edit carefully.

- `test/integration/test_api_history_db.py`: `/history`, `/history/{id}` → `/api/history...` (~36 refs — check every one)
- `test/integration/test_api_works_db.py`: `/works` → `/api/works`
- `test/integration/test_analysis_api.py`: `/analysis` → `/api/analysis`
- `test/integration/test_availability_api.py`: `/availability` → `/api/availability`
- `test/integration/test_books_api.py`: `/books` → `/api/books`
- `test/integration/test_chat_api.py`: `/chat`, `/conversations...` → `/api/chat`, `/api/conversations...`
- `test/integration/test_libraries_api.py`: `/libraries/search`, `/me/libraries` → `/api/libraries/search`, `/api/me/libraries`
- `test/integration/test_recommendations_api.py`: `/recommendations`, `/recommendations/{id}/status` → `/api/recommendations...`
- `test/integration/test_recommendations_read_status.py`: `/recommendations...` → `/api/recommendations...`
- `test/integration/test_api_import_status.py`: `/import/{job_id}...` → `/api/import/{job_id}...` (verify none are `/internal/import-row`; leave those)

- [ ] **Step 12: Sanity-grep for any missed bare user-facing paths in tests**

Run:
```bash
grep -rnE "(get|post|put|patch|delete)\(\s*f?[\"']/(history|works|recommendations|analysis|availability|books|import/|import\"|libraries|me/|conversations|chat)" test/ | grep -v "/api/"
```
Expected: no output. Any hit is a missed path — prefix it (unless it is genuinely `/internal/...`, which won't match this pattern).

- [ ] **Step 13: Update the deploy live-smoke auth probe**

In `.github/workflows/deploy.yml`, the live-smoke step (~L173–182) curls `"${URL}/history"` expecting 401. Change that path to `/api/history` (post-refactor bare `/history` returns the SPA shell 200, so the probe must target the prefixed path to exercise auth). Update the adjacent comment referencing `/history` to `/api/history`. Leave the `/health` and `/` smoke checks unchanged.

```yaml
          code=$(curl -s -o /dev/null -w '%{http_code}' \
            -H "X-Serverless-Authorization: Bearer ${TOKEN}" "${URL}/api/history")
```

- [ ] **Step 14: Lint, format, and commit**

Run:
```bash
uvx ruff check src/agentic_librarian/api/main.py test/
uvx ruff format src/agentic_librarian/api/main.py test/
.venv/Scripts/python -m pytest test/unit -q
```
Expected: clean lint/format, green unit suite. Then commit:
```bash
git add src/agentic_librarian/api/main.py test/ .github/workflows/deploy.yml
git commit -m "refactor(api): namespace user-facing routes under /api/* (#151)"
```
(Full trailer per Global Constraints.)

---

### Task 2: Frontend — route all API calls through the `/api` prefix

**Files:**
- Modify: `frontend/src/api/client.ts` (the `authedFetchRaw` choke point, ~L117–121)
- Modify (if present): the client's test file asserting fetched URLs (locate in Step 1)

**Interfaces:**
- Consumes: backend serves user-facing routes at `/api/*` (Task 1).
- Produces: every `client.ts` call hits `/api/<path>`; the app works end-to-end.

- [ ] **Step 1: Locate any frontend test that asserts fetched URLs**

Run:
```bash
grep -rnE "'/(history|works|recommendations|analysis|availability|books|import|libraries|me|conversations|chat)|toHaveBeenCalledWith" frontend/src --include="*.test.ts" --include="*.test.tsx"
```
Note which test files assert on the un-prefixed path so Step 4 can update them. (If none, Step 4 is a no-op.)

- [ ] **Step 2: Add the `/api` prefix in the fetch choke point**

In `frontend/src/api/client.ts`, add a module-level constant near the top (after imports):

```typescript
// All user-facing API routes are served under /api/* (#151) so they can never collide
// with an SPA client route. This is the single place the prefix is applied — every call
// site keeps its bare literal path (e.g. '/history').
const API_PREFIX = '/api'
```

Change `authedFetchRaw` (~L117–121) to prepend it:

```typescript
async function authedFetchRaw(path: string, init: RequestInit = {}): Promise<Response> {
  const token = await getIdToken()
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return fetch(`${API_PREFIX}${path}`, { ...init, headers })
}
```

(Preserve the existing body of `authedFetchRaw` exactly — only the `fetch(...)` URL argument changes. `getJson` calls through `authedFetchRaw`, so it is covered automatically. The Firebase SDK's `/__/auth/*` requests do not go through `client.ts` and are unaffected.)

- [ ] **Step 3: Run the frontend build/type-check and tests**

Run (from `frontend/`): `npm run test` (or the repo's configured vitest command).
Expected: PASS, except any URL-asserting tests found in Step 1, which now expect the un-prefixed URL — those FAIL and are fixed in Step 4.

- [ ] **Step 4: Update URL-asserting frontend tests (if any)**

For each test found in Step 1 that asserts a fetch URL, change the expected URL from `'/history'` to `'/api/history'` (etc.). Re-run `npm run test`.
Expected: PASS.

- [ ] **Step 5: Lint, format, and commit**

Run the frontend lint/format (e.g. `npm run lint` / prettier per repo config), then:
```bash
git add frontend/src/api/client.ts frontend/src
git commit -m "refactor(frontend): call the API under /api/* (#151)"
```
(Full trailer per Global Constraints.)

---

## Post-implementation verification (before opening the PR)

- [ ] Full local unit suite green: `.venv/Scripts/python -m pytest test/unit -q`
- [ ] Frontend tests green.
- [ ] Drive the app end-to-end (superpowers/verify): history loads, chat streams, recommendations, analysis, import preview/commit, availability/libraries — all via `/api/*`; a browser refresh on `/history` renders the app (not JSON). Confirm `/health` still returns `{"status":"ok"}` and a bare `/history` fetch returns the shell.
- [ ] Open PR to `main`; the CI `db_integration` run is the real merge gate for the integration path updates (Step 11) — treat its first run as the gate.

## Self-review notes (coverage against spec)

- Spec "Prefixed → /api/*" table → Task 1 Steps 4–6 (routing) + Steps 9,11 (tests). ✓
- Spec "Stays at root" → Task 1 Step 4 keeps `internal`/`firebase_auth_proxy` at root; Steps 5 keeps `/health*`, `/`, catch-all on `@app`. ✓
- Spec "Middleware cleanup" → Task 1 Step 7 (delete `_SPA_CLIENT_ROUTES` + rewrite, keep no-store, rename). ✓
- Spec "Frontend" → Task 2 Step 2. ✓
- Spec "Tests" (path updates + bare-path regression) → Task 1 Steps 1,3,9,11,12. ✓
- Spec "Deploy" → Task 1 Step 13. ✓
- Spec "Non-goals" (no Cloud Tasks/OIDC/health changes) → Global Constraints + do-not-touch lists. ✓

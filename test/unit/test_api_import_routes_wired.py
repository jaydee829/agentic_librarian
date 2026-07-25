"""#151: user-facing import routes live under /api/*; the Cloud Tasks endpoint
/internal/import-row stays at root.

Asserted by route RESOLUTION (behavior) rather than by walking app.routes internals. The
previous `_all_paths` helper traversed a FastAPI `original_router` attribute whose shape
differs across FastAPI versions — it passed on the pinned local FastAPI but failed in CI
(freshly resolved from pyproject) even though runtime routing was correct there (the
`/api/import/*` integration tests pass in the same CI run). Resolution-based checks are
version-robust and also auth-state-robust (they assert a route exists / is gone, not a
specific auth code that a leaked dependency override could change).
"""

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api.main import app

client = TestClient(app)


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/import/preview"),
        ("post", "/api/import/commit"),
        ("get", "/api/import/any-job-id"),
        ("post", "/api/import/any-job-id/retry"),
    ],
)
def test_user_facing_import_routes_registered_under_api(method, path):
    # A route IS registered at this /api path+method: the request reaches the import router
    # (auth/validation response), never the GET-only SPA catch-all (which 405s a POST) and
    # never a 404. We assert only that a route exists here (not the SPA shell), so the check
    # is independent of both the exact auth code and the FastAPI version.
    resp = client.request(method, path)
    assert resp.status_code not in (404, 405)


@pytest.mark.parametrize("path", ["/import/preview", "/import/commit"])
def test_bare_import_paths_are_no_longer_api_routes(path):
    # #151 structural cure: the un-prefixed /import/* paths are gone. A POST matches only the
    # GET-only SPA catch-all (405/404), so it can never reach the import handler.
    assert client.post(path).status_code in (404, 405)


def test_internal_import_row_stays_at_root():
    # The Cloud Tasks target is deliberately NOT under /api; a POST reaches its handler at the
    # root path (registered -> not 404/405), unlike the user-facing import routes above.
    assert client.post("/internal/import-row/not-a-uuid").status_code not in (404, 405)

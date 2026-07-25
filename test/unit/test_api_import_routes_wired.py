from agentic_librarian.api.main import app


def _all_paths(routes) -> set[str]:
    """Recursively collect route paths from the app route tree (FastAPI wraps
    sub-routers in _IncludedRouter objects whose individual routes live on
    original_router.routes, not directly on app.routes)."""
    paths: set[str] = set()
    for route in routes:
        if hasattr(route, "path"):
            paths.add(route.path)
        if hasattr(route, "original_router"):
            paths |= _all_paths(route.original_router.routes)
    return paths


def test_import_routes_are_registered():
    paths = _all_paths(app.routes)
    assert "/api/import/preview" in paths
    assert "/api/import/commit" in paths
    assert "/api/import/{job_id}" in paths
    assert "/internal/import-row/{row_id}" in paths

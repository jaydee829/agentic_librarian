"""Lift 2 Stage 4: FastAPI serves the built SPA same-origin, with an index.html fallback
for client-side routes, real built files served as-is, and a path-traversal guard on the
catch-all. Post-#151, all user-facing API routes live under /api/*, so bare client routes
can never collide with an API path — the catch-all serves the shell for any Accept."""

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api.main import app


def _build_dist(root):
    (root / "assets").mkdir()
    (root / "index.html").write_text('<!doctype html><div id="root"></div>')
    (root / "assets" / "app.js").write_text('console.log("spa")')
    (root / "favicon.svg").write_text("<svg/>")
    return root


def test_root_serves_index(tmp_path, monkeypatch):
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert 'id="root"' in r.text


def test_unknown_path_falls_back_to_index(tmp_path, monkeypatch):
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/add")  # a client route, not a file
    assert r.status_code == 200
    assert 'id="root"' in r.text


def test_real_asset_is_served(tmp_path, monkeypatch):
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/assets/app.js")
    assert r.status_code == 200
    assert 'console.log("spa")' in r.text


# Cache policy (2026-07-19 mobile stale-bundle incident): without an explicit
# Cache-Control, mobile browsers heuristically cache index.html and keep serving a
# stale shell that points at old hashed bundles. index.html (and any unhashed root
# file) must always revalidate; the content-hashed /assets/* never change and are
# immutable.
@pytest.mark.parametrize("path", ["/", "/add", "/index.html"])
def test_index_and_fallback_are_no_cache(tmp_path, monkeypatch, path):
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get(path)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
    assert 'id="root"' in r.text


def test_hashed_asset_is_immutable(tmp_path, monkeypatch):
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/assets/app.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_unhashed_root_file_is_no_cache(tmp_path, monkeypatch):
    # favicon.svg comes from frontend/public/ unhashed — a new one must be picked up.
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/favicon.svg")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
    assert "<svg/>" in r.text


def test_api_route_wins_over_spa_catch_all(tmp_path, monkeypatch):
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/health")  # unauthenticated API route
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# Refresh-on-a-tab collision (2026-07-19), structurally cured by #151: /history,
# /recommendations, /analysis are SPA client routes; the same-named API GETs now live only
# under /api/*, so a bare path is never an API route regardless of Accept — it always falls
# through to the catch-all shell. The Accept header no longer needs to discriminate.
_NAV_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


@pytest.mark.parametrize(
    "path",
    ["/history", "/recommendations", "/analysis", "/add", "/import", "/settings", "/history/abc/edit"],
)
def test_browser_navigation_to_spa_route_serves_shell(tmp_path, monkeypatch, path):
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get(path, headers={"Accept": _NAV_ACCEPT})
    assert r.status_code == 200
    assert 'id="root"' in r.text
    assert r.headers["cache-control"] == "no-cache"


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


def test_navigation_to_a_real_static_file_is_not_hijacked(tmp_path, monkeypatch):
    # A navigation whose path IS a built file (e.g. the favicon opened directly) still
    # serves the file, not the shell.
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/favicon.svg", headers={"Accept": _NAV_ACCEPT})
    assert r.status_code == 200
    assert "<svg/>" in r.text


def test_api_json_responses_are_no_store(tmp_path, monkeypatch):
    # Private API JSON must never sit in a browser/proxy cache — this is also what let a
    # stale authed /api/history body render as a page after the route collision.
    dist = _build_dist(tmp_path)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))
    r = TestClient(app).get("/health")
    assert r.headers["cache-control"] == "no-store"


def test_path_traversal_is_blocked(tmp_path, monkeypatch):
    # Secret lives OUTSIDE the dist dir. Call the handler directly — the HTTP client would
    # normalize the `..` away before routing, so a direct call is what exercises the guard.
    dist = tmp_path / "dist"
    dist.mkdir()
    _build_dist(dist)
    (tmp_path / "secret.txt").write_text("TOP-SECRET")
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))

    from agentic_librarian.api.main import spa_catch_all

    resp = spa_catch_all("../secret.txt")
    # The escaped path is refused → falls back to the SPA shell (index.html), not the secret.
    assert resp.path.endswith("index.html")


def test_sibling_prefix_collision_is_blocked(tmp_path, monkeypatch):
    # A sibling dir whose path shares the dist prefix (dist vs dist-secret) must NOT be
    # reachable — this is why the guard compares against root + os.sep, not a bare prefix.
    dist = tmp_path / "dist"
    dist.mkdir()
    _build_dist(dist)
    sibling = tmp_path / "dist-secret"
    sibling.mkdir()
    (sibling / "leak.txt").write_text("SIBLING-SECRET")
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))

    from agentic_librarian.api.main import spa_catch_all

    resp = spa_catch_all("../dist-secret/leak.txt")
    assert resp.path.endswith("index.html")  # refused → SPA shell, not the sibling file


def test_absolute_path_input_is_blocked(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    _build_dist(dist)
    monkeypatch.setenv("SPA_DIST_DIR", str(dist))

    from agentic_librarian.api.main import spa_catch_all

    resp = spa_catch_all("/etc/hostname")
    assert resp.path.endswith("index.html")

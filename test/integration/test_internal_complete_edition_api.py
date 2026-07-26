"""OIDC gate + status mapping for the edition-completion internal endpoint.

Mirrors test_internal_enrich_api.py: db_integration because the FastAPI app import
chain needs real settings, but complete_edition itself is monkeypatched — the pass's
own behavior is covered by test/unit/test_edition_completion.py."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import internal as internal_mod
from agentic_librarian.api import main as api_main
from agentic_librarian.core.user_context import get_required_user_id
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration

VALID_AUD = "https://librarian.example.run.app/internal/enrich/x"
QUEUE_SA = "queue-invoker@p.iam.gserviceaccount.com"


@pytest.fixture
def client(db_url, monkeypatch):
    # #100: point the budget gate at the isolated test DB, same reasoning as
    # test_internal_enrich_api.py's client fixture.
    monkeypatch.setattr(internal_mod.budgets, "db_manager", DatabaseManager(db_url))
    monkeypatch.setenv("ENRICH_INVOKER_SA", QUEUE_SA)
    monkeypatch.setenv("ENRICH_OIDC_AUDIENCE", VALID_AUD)
    yield TestClient(api_main.app)


def _as_queue(monkeypatch):
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )


def test_valid_queue_token_runs_completion(client, monkeypatch):
    _as_queue(monkeypatch)
    called = {}

    def fake_complete(wid, fmt):
        called["args"] = (wid, fmt)
        return "done"

    monkeypatch.setattr(internal_mod.two_phase, "complete_edition", fake_complete)
    wid = uuid4()
    resp = client.post(f"/internal/complete-edition/{wid}?format=audiobook", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 200
    assert resp.json() == {"work_id": str(wid), "format": "audiobook", "status": "done"}
    assert called["args"] == (wid, "audiobook")


def test_valid_queue_token_with_user_id_attributes_completion(client, monkeypatch):
    """#100: ?user_id=<uuid> attributes the completion pass's LLM usage to the requester."""
    _as_queue(monkeypatch)
    seen = {}

    def fake_complete(wid, fmt):
        seen["user_id"] = get_required_user_id()
        return "done"

    monkeypatch.setattr(internal_mod.two_phase, "complete_edition", fake_complete)
    wid = uuid4()
    caller_id = uuid4()
    resp = client.post(
        f"/internal/complete-edition/{wid}?format=audiobook&user_id={caller_id}",
        headers={"Authorization": "Bearer ok"},
    )
    assert resp.status_code == 200
    assert seen["user_id"] == caller_id


def test_valid_queue_token_without_user_id_completion_unattributed(client, monkeypatch):
    """Back-compat: no user_id -> no user in context, and the pass still succeeds."""
    _as_queue(monkeypatch)
    seen = {}

    def fake_complete(wid, fmt):
        seen["raised"] = False
        try:
            get_required_user_id()
        except RuntimeError:
            seen["raised"] = True
        return "done"

    monkeypatch.setattr(internal_mod.two_phase, "complete_edition", fake_complete)
    wid = uuid4()
    resp = client.post(f"/internal/complete-edition/{wid}?format=audiobook", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 200
    assert seen["raised"] is True


def test_missing_work_is_404_non_retryable(client, monkeypatch):
    _as_queue(monkeypatch)
    monkeypatch.setattr(internal_mod.two_phase, "complete_edition", lambda wid, fmt: "missing")
    resp = client.post(f"/internal/complete-edition/{uuid4()}?format=ebook", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 404


def test_empty_scouts_is_200_final(client, monkeypatch):
    _as_queue(monkeypatch)
    monkeypatch.setattr(internal_mod.two_phase, "complete_edition", lambda wid, fmt: "empty")
    resp = client.post(f"/internal/complete-edition/{uuid4()}?format=ebook", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "empty"


def test_missing_token_is_401(client):
    assert client.post(f"/internal/complete-edition/{uuid4()}?format=ebook").status_code == 401


def test_wrong_service_account_is_403(client, monkeypatch):
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": "attacker@evil.com", "email_verified": True}
    )
    resp = client.post(f"/internal/complete-edition/{uuid4()}?format=ebook", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_missing_format_param_is_422(client, monkeypatch):
    _as_queue(monkeypatch)
    resp = client.post(f"/internal/complete-edition/{uuid4()}", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 422


def test_over_budget_defers_with_new_scheduled_task_and_never_calls_complete_edition(client, monkeypatch):
    """#100: mirrors test_internal_enrich_api.py's deferral tests for the completion pass."""
    _as_queue(monkeypatch)
    monkeypatch.setattr(
        internal_mod.budgets, "enrichment_allowed", lambda uid: (False, "global grounded-call governor reached")
    )
    ran_complete = {"called": False}
    monkeypatch.setattr(
        internal_mod.two_phase,
        "complete_edition",
        lambda wid, fmt: ran_complete.__setitem__("called", True) or "done",
    )
    calls = []

    def fake_enqueue(wid, fmt, user_id=None, schedule_time=None):
        calls.append((wid, fmt, user_id, schedule_time))
        return True

    monkeypatch.setattr(internal_mod, "enqueue_edition_completion", fake_enqueue)

    wid = uuid4()
    caller_id = uuid4()
    before = datetime.now(UTC)
    resp = client.post(
        f"/internal/complete-edition/{wid}?format=audiobook&user_id={caller_id}",
        headers={"Authorization": "Bearer ok"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["work_id"] == str(wid)
    assert body["format"] == "audiobook"
    assert body["status"] == "deferred"
    assert "until" in body
    assert ran_complete["called"] is False
    assert len(calls) == 1
    call_wid, call_fmt, call_uid, when = calls[0]
    assert call_wid == str(wid)
    assert call_fmt == "audiobook"
    assert call_uid == str(caller_id)
    assert when > before


def test_under_budget_runs_the_normal_completion_path_unchanged(client, monkeypatch):
    _as_queue(monkeypatch)
    monkeypatch.setattr(internal_mod.budgets, "enrichment_allowed", lambda uid: (True, ""))
    called = {}

    def fake_complete(wid, fmt):
        called["args"] = (wid, fmt)
        return "done"

    monkeypatch.setattr(internal_mod.two_phase, "complete_edition", fake_complete)
    wid = uuid4()
    resp = client.post(f"/internal/complete-edition/{wid}?format=audiobook", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 200
    assert resp.json() == {"work_id": str(wid), "format": "audiobook", "status": "done"}
    assert called["args"] == (wid, "audiobook")


def test_over_budget_and_enqueue_returns_false_is_deferred_enqueue_failed(client, monkeypatch):
    _as_queue(monkeypatch)
    monkeypatch.setattr(internal_mod.budgets, "enrichment_allowed", lambda uid: (False, "global governor reached"))
    monkeypatch.setattr(
        internal_mod, "enqueue_edition_completion", lambda wid, fmt, user_id=None, schedule_time=None: False
    )
    wid = uuid4()
    resp = client.post(f"/internal/complete-edition/{wid}?format=ebook", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 200
    assert resp.json() == {"work_id": str(wid), "format": "ebook", "status": "deferred_enqueue_failed"}


def test_over_budget_and_enqueue_raises_is_deferred_enqueue_failed(client, monkeypatch):
    _as_queue(monkeypatch)
    monkeypatch.setattr(internal_mod.budgets, "enrichment_allowed", lambda uid: (False, "global governor reached"))

    def _boom(wid, fmt, user_id=None, schedule_time=None):
        raise RuntimeError("cloud tasks unavailable")

    monkeypatch.setattr(internal_mod, "enqueue_edition_completion", _boom)
    wid = uuid4()
    resp = client.post(f"/internal/complete-edition/{wid}?format=ebook", headers={"Authorization": "Bearer ok"})
    assert resp.status_code == 200
    assert resp.json() == {"work_id": str(wid), "format": "ebook", "status": "deferred_enqueue_failed"}

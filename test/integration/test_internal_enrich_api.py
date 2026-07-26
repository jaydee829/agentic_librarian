from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import internal as internal_mod
from agentic_librarian.api import main as api_main
from agentic_librarian.core.user_context import get_required_user_id
from agentic_librarian.db.models import Author, Edition, Trope, Work, WorkContributor, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.enrichment import two_phase

pytestmark = pytest.mark.db_integration

VALID_AUD = "https://librarian.example.run.app/internal/enrich/x"
QUEUE_SA = "queue-invoker@p.iam.gserviceaccount.com"


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    monkeypatch.setenv("ENRICH_INVOKER_SA", QUEUE_SA)
    monkeypatch.setenv("ENRICH_OIDC_AUDIENCE", VALID_AUD)
    yield TestClient(api_main.app)


def _seed_work(manager, *, with_real_trope=False):
    with manager.get_session() as s:
        work = Work(title="Dune")
        s.add(work)
        s.flush()
        a = Author(name="Frank Herbert")
        s.add(a)
        s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        s.add(Edition(work_id=work.id, format="ebook"))
        if with_real_trope:
            # "Found Family" cleans to something outside this work's (empty) genres/moods, so
            # is_fallback_trope_name returns False — a genuine narrative trope.
            trope = Trope(name="Found Family")
            s.add(trope)
            s.flush()
            s.add(WorkTrope(work_id=work.id, trope_id=trope.id))
        s.flush()
        return work.id


def test_valid_queue_token_runs_deep_enrich(client, db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager)
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )
    called = {}

    def _fake_enrich_deep(wid):
        called["wid"] = wid
        return "done"

    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep", _fake_enrich_deep)

    resp = client.post(f"/internal/enrich/{work_id}", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200
    assert resp.json() == {"work_id": str(work_id), "status": "enriched"}
    assert str(called["wid"]) == str(work_id)


def test_valid_queue_token_with_user_id_runs_deep_enrich_attributed(client, db_url, monkeypatch):
    """#100: the queue may pass ?user_id=<uuid> so the deep pass's LLM usage bills the
    requesting user. as_user(...) wraps the call, so get_required_user_id() resolves inside
    enrich_deep — this is the probe (patched at the handler's call site, per house pattern)."""
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager)
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )
    seen = {}

    def _fake_enrich_deep(wid):
        seen["user_id"] = get_required_user_id()
        return "done"

    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep", _fake_enrich_deep)
    caller_id = uuid4()

    resp = client.post(f"/internal/enrich/{work_id}?user_id={caller_id}", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200
    assert seen["user_id"] == caller_id


def test_valid_queue_token_without_user_id_runs_deep_enrich_unattributed(client, db_url, monkeypatch):
    """Back-compat guarantee: pre-#100 (or requeue-sweep) tasks carry no user_id and must
    still succeed, with no user identity in context (metering skips, nothing crashes)."""
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager)
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )
    seen = {}

    def _fake_enrich_deep(wid):
        seen["raised"] = False
        try:
            get_required_user_id()
        except RuntimeError:
            seen["raised"] = True
        return "done"

    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep", _fake_enrich_deep)

    resp = client.post(f"/internal/enrich/{work_id}", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200
    assert seen["raised"] is True  # no user in context, exactly as before #100


def test_missing_token_is_rejected(client):
    resp = client.post(f"/internal/enrich/{uuid4()}")
    assert resp.status_code == 401


def test_wrong_service_account_is_forbidden(client, monkeypatch):
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": "attacker@evil.com", "email_verified": True}
    )
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_bad_token_signature_is_forbidden(client, monkeypatch):
    def _boom(token, audience):
        raise ValueError("invalid signature")

    monkeypatch.setattr(internal_mod, "_verify_oidc", _boom)
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_redirected_deep_pass_returns_200_non_retryable(client, db_url, monkeypatch):
    """GH #141: a "redirected" pass completed and stamped the invoked work — its data
    lives on the twin, recorded in detected_duplicates. Retrying would burn another paid
    deep pass for nothing, so this must be a 2xx, not a 5xx."""
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager)
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )
    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep", lambda wid: "redirected")

    resp = client.post(f"/internal/enrich/{work_id}", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200
    assert resp.json() == {"work_id": str(work_id), "status": "redirected"}


def test_unknown_work_returns_404(client, monkeypatch):
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )
    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep", lambda wid: "missing")
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 404


def test_empty_deep_pass_on_tropeless_work_returns_503(client, db_url, monkeypatch):
    """GH #97: a work with no real trope after an empty deep pass is a retryable poison
    task — Cloud Tasks must see a 5xx so it retries with backoff."""
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager, with_real_trope=False)
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )
    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep", lambda wid: "empty")

    resp = client.post(f"/internal/enrich/{work_id}", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 503
    assert resp.json() == {"detail": {"work_id": str(work_id), "status": "empty_deep_pass"}}


def test_empty_deep_pass_on_work_with_real_trope_returns_200(client, db_url, monkeypatch):
    """GH #97: an empty pass on a work that already has a real trope from a prior pass is
    NOT a failure — it just means this pass added nothing new. Don't retry forever."""
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager, with_real_trope=True)
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )
    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep", lambda wid: "empty")

    resp = client.post(f"/internal/enrich/{work_id}", headers={"Authorization": "Bearer good"})
    assert resp.status_code == 200
    assert resp.json() == {"work_id": str(work_id), "status": "already_enriched"}


def test_empty_deep_pass_gives_up_after_8_retries_returns_200(client, db_url, monkeypatch):
    """Important (final review): Cloud Tasks default maxAttempts=100 with ~hourly backoff would
    otherwise mean ~100 PAID deep passes per poison book. Once Cloud Tasks' own
    X-CloudTasks-TaskRetryCount header reports >=8 retries AND the work still has no real
    trope, give up loudly with 200 (not 503) so Cloud Tasks stops retrying — the documented
    backstop is the operator's --requeue-unenriched sweep, not unbounded paid retries."""
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager, with_real_trope=False)
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )
    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep", lambda wid: "empty")

    resp = client.post(
        f"/internal/enrich/{work_id}",
        headers={"Authorization": "Bearer good", "X-CloudTasks-TaskRetryCount": "8"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"work_id": str(work_id), "status": "empty_deep_pass_gave_up"}


def test_empty_deep_pass_still_retries_below_give_up_threshold(client, db_url, monkeypatch):
    """The give-up threshold is >=8, not "any retry count present" — below it, the ordinary
    retryable 503 path still fires so Cloud Tasks keeps retrying with backoff."""
    manager = DatabaseManager(db_url)
    work_id = _seed_work(manager, with_real_trope=False)
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )
    monkeypatch.setattr(internal_mod.two_phase, "enrich_deep", lambda wid: "empty")

    resp = client.post(
        f"/internal/enrich/{work_id}",
        headers={"Authorization": "Bearer good", "X-CloudTasks-TaskRetryCount": "3"},
    )
    assert resp.status_code == 503
    assert resp.json() == {"detail": {"work_id": str(work_id), "status": "empty_deep_pass"}}


def test_unverified_email_is_forbidden(client, monkeypatch):
    # Mutation guard: a token with the correct SA email but email_verified=False must still 403.
    # Deleting the `not claims.get("email_verified")` check would let this through.
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": False}
    )
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_unconfigured_audience_is_forbidden(client, monkeypatch):
    # Fail-closed: without ENRICH_OIDC_AUDIENCE, google-auth would skip audience verification.
    # Mutation guard: _verify_oidc is mocked to SUCCEED, so if the `or not audience` config guard
    # were removed, the call would pass verification and reach enrich_deep → 404. A 403 here proves
    # the config guard fired BEFORE verification.
    monkeypatch.delenv("ENRICH_OIDC_AUDIENCE", raising=False)
    monkeypatch.setattr(
        internal_mod, "_verify_oidc", lambda token, audience: {"email": QUEUE_SA, "email_verified": True}
    )
    resp = client.post(f"/internal/enrich/{uuid4()}", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403

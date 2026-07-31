from datetime import UTC, datetime

import pytest

from agentic_librarian.enrichment import tasks


class _FakeClient:
    def __init__(self):
        self.created = []

    def create_task(self, *, parent, task):
        self.created.append((parent, task))
        return object()


def _set_env(monkeypatch):
    monkeypatch.setenv("CLOUD_TASKS_QUEUE", "projects/p/locations/us-central1/queues/enrich")
    monkeypatch.setenv("ENRICH_TARGET_BASE_URL", "https://librarian.example.run.app")
    monkeypatch.setenv("ENRICH_INVOKER_SA", "queue-invoker@p.iam.gserviceaccount.com")


def test_enqueue_builds_oidc_task_targeting_the_internal_route(monkeypatch):
    _set_env(monkeypatch)
    fake = _FakeClient()
    monkeypatch.setattr(tasks, "_client", lambda: fake)

    assert tasks.enqueue_enrichment("11111111-1111-4111-8111-111111111111") is True

    parent, task = fake.created[0]
    assert parent == "projects/p/locations/us-central1/queues/enrich"
    http = task["http_request"]
    assert http["url"] == "https://librarian.example.run.app/internal/enrich/11111111-1111-4111-8111-111111111111"
    assert http["oidc_token"]["service_account_email"] == "queue-invoker@p.iam.gserviceaccount.com"


def test_enqueue_skips_when_queue_not_configured(monkeypatch):
    monkeypatch.delenv("CLOUD_TASKS_QUEUE", raising=False)
    called = {"n": 0}
    monkeypatch.setattr(tasks, "_client", lambda: called.__setitem__("n", called["n"] + 1))

    assert tasks.enqueue_enrichment("abc") is False  # local dev: no queue, fast pass still succeeds
    assert called["n"] == 0  # client never constructed


def test_enqueue_edition_completion_targets_the_completion_route(monkeypatch):
    _set_env(monkeypatch)
    fake = _FakeClient()
    monkeypatch.setattr(tasks, "_client", lambda: fake)

    assert tasks.enqueue_edition_completion("11111111-1111-4111-8111-111111111111", "audiobook") is True

    parent, task = fake.created[0]
    assert parent == "projects/p/locations/us-central1/queues/enrich"
    http = task["http_request"]
    assert http["url"] == (
        "https://librarian.example.run.app/internal/complete-edition/"
        "11111111-1111-4111-8111-111111111111?format=audiobook"
    )
    assert http["oidc_token"]["service_account_email"] == "queue-invoker@p.iam.gserviceaccount.com"
    # Audience must NOT include the query string (a per-task audience would break a fixed
    # receiver-side ENRICH_OIDC_AUDIENCE check).
    assert http["oidc_token"]["audience"] == (
        "https://librarian.example.run.app/internal/complete-edition/11111111-1111-4111-8111-111111111111"
    )


def test_enqueue_edition_completion_skips_when_not_configured(monkeypatch):
    monkeypatch.delenv("CLOUD_TASKS_QUEUE", raising=False)
    called = {"n": 0}
    monkeypatch.setattr(tasks, "_client", lambda: called.__setitem__("n", called["n"] + 1))

    assert tasks.enqueue_edition_completion("abc", "audiobook") is False
    assert called["n"] == 0


@pytest.mark.parametrize(
    "user_id,expected_url",
    [
        (
            "22222222-2222-4222-8222-222222222222",
            "https://librarian.example.run.app/internal/enrich/"
            "11111111-1111-4111-8111-111111111111?user_id=22222222-2222-4222-8222-222222222222",
        ),
        (None, "https://librarian.example.run.app/internal/enrich/11111111-1111-4111-8111-111111111111"),
    ],
)
def test_enqueue_enrichment_appends_user_id_query_when_given(monkeypatch, user_id, expected_url):
    _set_env(monkeypatch)
    fake = _FakeClient()
    monkeypatch.setattr(tasks, "_client", lambda: fake)

    assert tasks.enqueue_enrichment("11111111-1111-4111-8111-111111111111", user_id=user_id) is True

    _parent, task = fake.created[0]
    http = task["http_request"]
    assert http["url"] == expected_url
    # Audience is the bare path url regardless of user_id — a per-user audience would never
    # match the receiver's single fixed ENRICH_OIDC_AUDIENCE.
    assert http["oidc_token"]["audience"] == (
        "https://librarian.example.run.app/internal/enrich/11111111-1111-4111-8111-111111111111"
    )


@pytest.mark.parametrize(
    "user_id,expected_url",
    [
        (
            "22222222-2222-4222-8222-222222222222",
            "https://librarian.example.run.app/internal/complete-edition/"
            "11111111-1111-4111-8111-111111111111?format=audiobook"
            "&user_id=22222222-2222-4222-8222-222222222222",
        ),
        (
            None,
            "https://librarian.example.run.app/internal/complete-edition/"
            "11111111-1111-4111-8111-111111111111?format=audiobook",
        ),
    ],
)
def test_enqueue_edition_completion_appends_user_id_query_when_given(monkeypatch, user_id, expected_url):
    _set_env(monkeypatch)
    fake = _FakeClient()
    monkeypatch.setattr(tasks, "_client", lambda: fake)

    assert (
        tasks.enqueue_edition_completion("11111111-1111-4111-8111-111111111111", "audiobook", user_id=user_id) is True
    )

    _parent, task = fake.created[0]
    http = task["http_request"]
    assert http["url"] == expected_url
    assert http["oidc_token"]["audience"] == (
        "https://librarian.example.run.app/internal/complete-edition/11111111-1111-4111-8111-111111111111"
    )


def test_enqueue_enrichment_sets_schedule_time_on_task(monkeypatch):
    _set_env(monkeypatch)
    fake = _FakeClient()
    monkeypatch.setattr(tasks, "_client", lambda: fake)
    when = datetime(2026, 8, 1, tzinfo=UTC)

    assert tasks.enqueue_enrichment("11111111-1111-4111-8111-111111111111", schedule_time=when) is True

    _parent, task = fake.created[0]
    assert task["schedule_time"] == when


def test_enqueue_edition_completion_sets_schedule_time_on_task(monkeypatch):
    _set_env(monkeypatch)
    fake = _FakeClient()
    monkeypatch.setattr(tasks, "_client", lambda: fake)
    when = datetime(2026, 8, 1, tzinfo=UTC)

    assert (
        tasks.enqueue_edition_completion("11111111-1111-4111-8111-111111111111", "audiobook", schedule_time=when)
        is True
    )

    _parent, task = fake.created[0]
    assert task["schedule_time"] == when

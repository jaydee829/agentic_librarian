from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import auth
from agentic_librarian.api import main as api_main
from agentic_librarian.chat import transcript
from agentic_librarian.core import budgets
from agentic_librarian.core import usage as usage_mod
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.models import Conversation, Message
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    monkeypatch.setattr(transcript, "db_manager", manager)  # chat endpoints use the store's manager
    monkeypatch.setattr(usage_mod, "db_manager", manager)  # usage recorder writes to the test DB
    monkeypatch.setattr(budgets, "db_manager", manager)  # chat quota check reads the store's manager
    # Endpoints wrap store calls in as_user(user.id), so a plain user object suffices.
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        auth.get_current_user,
        lambda: auth.AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL),
    )

    class _FakeConv:
        async def asend(self, message):
            return f"echo:{message}"

        def close(self): ...

    async def _fake_open(**kwargs):
        return _FakeConv()

    monkeypatch.setattr(api_main, "_open_conversation", _fake_open)
    yield TestClient(api_main.app)


def test_current_conversation_then_chat_then_resume(client):
    current = client.get("/api/conversations/current").json()
    assert current["messages"] == []
    cid = current["id"]

    with client.stream("POST", "/api/chat", json={"message": "hi"}) as r:
        body = "".join(r.iter_text())
    assert "echo:hi" in body
    assert body.rstrip().endswith("event: done\ndata: {}")

    resumed = client.get("/api/conversations/current").json()
    assert resumed["id"] == cid
    assert resumed["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "echo:hi"},
    ]


def test_new_conversation_starts_empty(client):
    current = client.get("/api/conversations/current").json()
    fresh = client.post("/api/conversations").json()
    assert fresh["messages"] == []
    assert fresh["id"] != current["id"]  # New chat is a distinct conversation


def test_usage_rows_reference_the_conversation(client, db_url, monkeypatch):
    from uuid import UUID

    from agentic_librarian.core import usage
    from agentic_librarian.db.models import Usage
    from agentic_librarian.db.session import DatabaseManager

    current = client.get("/api/conversations/current").json()
    cid = UUID(current["id"])

    class _UsingConv:
        async def asend(self, message):
            # mirrors runtime._record_event_usage: meter against the conversation id
            usage.record_llm_call(vendor="gemini", model="test", input_tokens=1, output_tokens=1, conversation_id=cid)
            return "ok"

        def close(self): ...

    async def _using_open(**kwargs):
        return _UsingConv()

    monkeypatch.setattr(api_main, "_open_conversation", _using_open)

    with client.stream("POST", "/api/chat", json={"message": "go"}) as r:
        "".join(r.iter_text())

    with DatabaseManager(db_url).get_session() as s:
        row = s.query(Usage).filter(Usage.conversation_id == cid).first()
        assert row is not None  # FK held: the conversation existed when usage was written


def test_conversations_current_payload_is_capped(client, monkeypatch):
    # #113: the endpoint returns the same capped window the mesh seeds — one choke point.
    monkeypatch.setenv("CHAT_HISTORY_SEED_LIMIT", "3")
    ctx = transcript.get_or_create_active_conversation()
    for i in range(1, 6):
        transcript.append_message(ctx.conversation_id, "user", f"m{i}")
    resp = client.get("/api/conversations/current")
    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assert len(messages) == 3
    assert [m["content"] for m in messages] == ["m3", "m4", "m5"]


def test_chat_message_over_length_returns_422(client, monkeypatch):
    # #100: cheap-string cap check — no need for a real 4000-char default here.
    monkeypatch.setenv("CHAT_MESSAGE_MAX_CHARS", "50")
    resp = client.post("/api/chat", json={"message": "x" * 51})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "message_too_long"


def test_chat_byok_key_error_returns_409_pre_stream(client, monkeypatch):
    # arc 3/3: a decrypt/config failure at resolution time must surface as a 409 before
    # any streaming starts — never a silent fallback to the app key. The client fixture
    # already points api_main.db_manager at the test DB, so this exercises the real
    # resolve_gemini_key seam (patched only at the KMS-decrypt boundary).
    from agentic_librarian.core import byok

    def _raise(session, user_id):
        raise byok.ByokKeyError("decrypt failed")

    monkeypatch.setattr(byok, "resolve_gemini_key", _raise)
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "byok_key_error"


@pytest.mark.parametrize(
    "seeded_today,expect_quota_blocked",
    [
        pytest.param(2, True, id="at_daily_limit_blocked"),
        pytest.param(1, False, id="under_daily_limit_allowed"),
    ],
)
def test_chat_quota_enforced_at_daily_limit(client, db_url, monkeypatch, seeded_today, expect_quota_blocked):
    # #100 / #147 lesson: seed with an explicit, real "today" created_at rather than
    # relying on the row default (avoids the same-timestamp/tie-break flake).
    monkeypatch.setenv("CHAT_TURNS_PER_DAY_FREE", "2")
    today = datetime.now(UTC)
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        convo = Conversation(user_id=DEFAULT_USER_ID)
        session.add(convo)
        session.flush()
        for i in range(seeded_today):
            session.add(Message(conversation_id=convo.id, role="user", content=f"seed-{i}", created_at=today))

    resp = client.post("/api/chat", json={"message": "one more, please"})
    if expect_quota_blocked:
        assert resp.status_code == 429
        assert resp.json()["detail"]["code"] == "chat_quota"
    else:
        assert resp.status_code != 429

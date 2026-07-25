import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import auth
from agentic_librarian.api import main as api_main
from agentic_librarian.chat import transcript
from agentic_librarian.core import usage as usage_mod
from agentic_librarian.core.user_context import DEFAULT_USER_EMAIL, DEFAULT_USER_ID
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


@pytest.fixture
def client(db_url, monkeypatch):
    manager = DatabaseManager(db_url)
    monkeypatch.setattr(api_main, "db_manager", manager)
    monkeypatch.setattr(transcript, "db_manager", manager)  # chat endpoints use the store's manager
    monkeypatch.setattr(usage_mod, "db_manager", manager)  # usage recorder writes to the test DB
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

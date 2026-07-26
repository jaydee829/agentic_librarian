"""Chat mesh byok key routing (arc 3/3): the handler resolves the caller's Gemini key
once per turn, BEFORE the StreamingResponse exists (SSE cannot carry an HTTP error status
once streaming has started — the same reason the 429 quota check above it is pre-stream).
A decrypt/config failure must surface as a 409, never a silent fallback to the app key."""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from agentic_librarian.api import main as api_main
from agentic_librarian.api.auth import AuthenticatedUser
from agentic_librarian.core import byok


class _FakeCtx:
    """Stand-in for transcript.ConversationContext — chat() only reads these two fields
    before returning (the StreamingResponse body is never iterated by a direct call)."""

    def __init__(self):
        self.conversation_id = uuid4()
        self.history = []


@pytest.fixture(autouse=True)
def _allow_chat_turn(monkeypatch):
    """Bypass the (DB-backed) daily quota check — irrelevant to byok key resolution."""
    monkeypatch.setattr(api_main.budgets, "chat_turn_allowed", lambda user_id: (True, None))


@pytest.fixture
def _user():
    return AuthenticatedUser(id=uuid4(), email="reader@example.com")


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(byok.ByokKeyError("decrypt failed"), id="decrypt_failure"),
        pytest.param(byok.ByokNotConfigured("KMS_KEY_NAME unset"), id="kms_not_configured"),
    ],
)
def test_chat_byok_resolution_failure_maps_to_409_pre_stream(monkeypatch, _user, error):
    def _raise(user_id):
        raise error

    monkeypatch.setattr(api_main, "_resolve_byok_chat_key", _raise)

    with pytest.raises(HTTPException) as exc_info:
        api_main.chat(user=_user, message="hi")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "byok_key_error",
        "message": "Your API key failed — check Settings.",
    }


def test_chat_without_byok_credential_behaves_as_before(monkeypatch, _user):
    """No credential row -> (None, "app"), same as pre-arc-3 behavior: the handler
    proceeds to build the streaming chat response instead of raising."""
    monkeypatch.setattr(api_main, "_resolve_byok_chat_key", lambda user_id: (None, "app"))
    monkeypatch.setattr(api_main.transcript, "get_or_create_active_conversation", lambda: _FakeCtx())

    response = api_main.chat(user=_user, message="hi")

    assert isinstance(response, StreamingResponse)


def test_chat_with_byok_credential_threads_key_into_the_sync_opener(monkeypatch, _user):
    """A resolved byok key reaches _SyncOpener (and, from there, astart_conversation) —
    verified via the conversation factory sse_turn would call, not by draining the
    stream (draining requires the async event loop; that's covered by the runtime and
    integration tests)."""
    monkeypatch.setattr(api_main, "_resolve_byok_chat_key", lambda user_id: ("user-secret-key", "byok"))
    monkeypatch.setattr(api_main.transcript, "get_or_create_active_conversation", lambda: _FakeCtx())

    captured = {}
    real_sse_turn = api_main.stream.sse_turn

    def _capturing_sse_turn(*, message, conversation, on_persist, user_id):
        opener = conversation(on_event=None)
        captured["api_key"] = opener._api_key
        captured["key_source"] = opener._key_source
        return real_sse_turn(message=message, conversation=conversation, on_persist=on_persist, user_id=user_id)

    monkeypatch.setattr(api_main.stream, "sse_turn", _capturing_sse_turn)

    api_main.chat(user=_user, message="hi")

    assert captured == {"api_key": "user-secret-key", "key_source": "byok"}

import asyncio
import os

import pytest
from google.genai import types

from agentic_librarian.agents import runtime, services
from agentic_librarian.agents.services import create_agent_mesh


@pytest.fixture(autouse=True)
def _adk_key(monkeypatch, request):
    """ADK's Gemini model reads GOOGLE_API_KEY. Set a dummy for offline tests so
    agent/runner construction never needs a real key. Live tests opt out."""
    if "api_dependent" not in request.keywords:
        monkeypatch.setenv("GOOGLE_API_KEY", "test-adk-key")


def test_all_mesh_agents_have_a_model():
    mesh = create_agent_mesh()
    for name in ("librarian", "analyst", "explorer", "critic"):
        assert mesh[name].model, f"{name} agent has no model"


def test_ensure_adk_credentials_falls_back(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "fallback-key-123")
    runtime._ensure_adk_credentials()
    assert os.environ["GOOGLE_API_KEY"] == "fallback-key-123"


def test_build_runner_constructs():
    r = runtime.build_runner()
    assert r is not None
    assert r.app_name == runtime.APP_NAME


class _FakeEvent:
    def __init__(self, text: str):
        self.content = types.Content(role="model", parts=[types.Part(text=text)])

    def is_final_response(self) -> bool:
        return True


class _FakeSessionService:
    def __init__(self):
        self.created = []

    async def create_session(self, app_name, user_id, session_id):
        self.created.append((app_name, user_id, session_id))
        return None


class _FakeRunner:
    def __init__(self, reply="Recommended: Dune"):
        self.app_name = runtime.APP_NAME
        self.session_service = _FakeSessionService()
        self.calls = []
        self._reply = reply

    async def run_async(self, user_id, session_id, new_message):
        self.calls.append((user_id, session_id, new_message.parts[0].text))
        yield _FakeEvent(self._reply)


def test_send_returns_final_response_text():
    conv = runtime.LibrarianConversation(_FakeRunner(reply="Try Hyperion"), "u", "s")
    assert conv.send("recommend sci-fi") == "Try Hyperion"


def test_asend_concatenates_multiple_text_parts():
    class _MultiPartRunner(_FakeRunner):
        async def run_async(self, user_id, session_id, new_message):
            event = _FakeEvent("")
            event.content = types.Content(role="model", parts=[types.Part(text="Hello "), types.Part(text="world")])
            yield event

    conv = runtime.LibrarianConversation(_MultiPartRunner(), "u", "s")
    assert conv.send("hi") == "Hello world"


def test_two_sends_reuse_the_same_session():
    fake = _FakeRunner()
    conv = runtime.LibrarianConversation(fake, "u", "sess-1")
    conv.send("first")
    conv.send("second")
    assert [sid for (_, sid, _) in fake.calls] == ["sess-1", "sess-1"]
    assert [msg for (_, _, msg) in fake.calls] == ["first", "second"]


def test_start_conversation_creates_a_session():
    fake = _FakeRunner()
    conv = runtime.start_conversation(user_id="alice", runner=fake)
    assert conv.user_id == "alice"
    assert fake.session_service.created
    assert fake.session_service.created[0][1] == "alice"


def test_run_recommendation_one_shot(monkeypatch):
    received = []

    class _FakeBackend:
        name = "fake"

        def run_recommendation(self, prompt, user_id="local"):
            received.append(prompt)
            return "Recommended: Dune"

    monkeypatch.setattr(runtime, "get_backend", lambda: _FakeBackend())
    assert runtime.run_recommendation("something like Dune") == "Recommended: Dune"
    assert received[0] == "something like Dune"


@pytest.mark.api_dependent
def test_live_conversation_runs():
    conv = runtime.start_conversation()
    first = conv.send("Recommend a sci-fi novel like Dune in one sentence.")
    assert isinstance(first, str) and first.strip()
    # Second turn shares the session (memory).
    second = conv.send("Actually, something more recent.")
    assert isinstance(second, str) and second.strip()


def test_explorer_uses_explorer_model_env(monkeypatch):
    # Each agent's model is an ADK Gemini object; its id is `.model.model`.
    monkeypatch.delenv("GROUNDING_MODEL", raising=False)
    monkeypatch.setenv("EXPLORER_MODEL", "gemini-test-explorer")
    mesh = create_agent_mesh()
    assert mesh["explorer"].model.model == "gemini-test-explorer"


def test_explorer_model_defaults_to_flash(monkeypatch):
    monkeypatch.delenv("GROUNDING_MODEL", raising=False)
    monkeypatch.delenv("EXPLORER_MODEL", raising=False)
    mesh = create_agent_mesh()
    assert mesh["explorer"].model.model == "gemini-2.5-flash"


def test_grounding_model_env_overrides_explorer_model(monkeypatch):
    monkeypatch.setenv("GROUNDING_MODEL", "gemini-grounded")
    monkeypatch.setenv("EXPLORER_MODEL", "gemini-legacy")
    mesh = create_agent_mesh()
    assert mesh["explorer"].model.model == "gemini-grounded"


def test_nongrounding_agents_default_to_flash_lite_3_1(monkeypatch):
    # Analyst/Critic/Librarian don't ground -> high-throughput gemini-3.1-flash-lite by default,
    # off the squeezed gemini-2.5 capacity. The grounding Explorer stays on a grounding model.
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GROUNDING_MODEL", raising=False)
    monkeypatch.delenv("EXPLORER_MODEL", raising=False)
    mesh = create_agent_mesh()
    for role in ("analyst", "critic", "librarian"):
        assert mesh[role].model.model == "gemini-3.1-flash-lite", role
    assert mesh["explorer"].model.model == "gemini-2.5-flash"


def test_every_agent_model_carries_transient_retry(monkeypatch):
    from agentic_librarian.llm_retry import RETRY_OPTIONS

    mesh = create_agent_mesh()
    for role, agent in mesh.items():
        assert agent.model.retry_options is RETRY_OPTIONS, role


def test_explorer_has_a_google_search_tool():
    mesh = create_agent_mesh()
    tool_types = [type(t).__name__ for t in mesh["explorer"].tools]
    assert any("GoogleSearch" in name for name in tool_types), tool_types


class _FakeFunctionCall:
    def __init__(self, name):
        self.name = name


class _FakeToolEvent(_FakeEvent):
    """An intermediate event carrying tool calls from a named agent."""

    def __init__(self, author, tool_names):
        super().__init__("")
        self.author = author
        self._tool_names = tool_names

    def is_final_response(self) -> bool:
        return False

    def get_function_calls(self):
        return [_FakeFunctionCall(n) for n in self._tool_names]


def test_asend_fires_on_event_for_tools_and_agents():
    class _EventfulRunner(_FakeRunner):
        async def run_async(self, user_id, session_id, new_message):
            yield _FakeToolEvent("Librarian", ["get_unacted_suggestions"])
            yield _FakeToolEvent("Explorer", ["google_search"])
            final = _FakeEvent("Try Hyperion")
            final.author = "Librarian"
            yield final

    seen = []
    conv = runtime.LibrarianConversation(
        _EventfulRunner(), "u", "s", on_event=lambda kind, detail: seen.append((kind, detail))
    )
    assert conv.send("recommend sci-fi") == "Try Hyperion"
    assert ("tool", "get_unacted_suggestions") in seen
    assert ("tool", "google_search") in seen
    assert ("agent", "Explorer") in seen


def test_asend_without_on_event_is_unchanged():
    conv = runtime.LibrarianConversation(_FakeRunner(reply="Try Dune"), "u", "s")
    assert conv.send("recommend sci-fi") == "Try Dune"
    assert conv.close() is None  # close() exists and is a no-op


@pytest.mark.api_dependent
def test_explorer_discovers_real_books():
    # The Explorer in isolation: its grounded google_search should return a
    # substantive, book-naming response for a recent query. This verifies Spec 2's
    # deliverable (grounded web discovery). Strict grounding correctness is a manual
    # check (results vary). The full Librarian orchestration is non-deterministic
    # (clarify vs delegate vs no-response) and is covered by Spec 4.
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    runtime._ensure_adk_credentials()
    explorer = create_agent_mesh()["explorer"]
    runner = Runner(agent=explorer, app_name=runtime.APP_NAME, session_service=InMemorySessionService())
    conv = runtime.start_conversation(runner=runner)
    response = conv.send("Find grimdark fantasy novels published in 2024. List each title and author.")
    assert isinstance(response, str)
    assert len(response.strip()) > 30


# --- arc 3/3 BYOK: key threading through the chat mesh ---


def test_gemini_without_api_key_constructs_a_plain_gemini():
    # Unchanged from before this arc: no api_key kwarg means the model reads the app's
    # process-wide GOOGLE_API_KEY (set by the autouse _adk_key fixture in tests).
    model = services._gemini("model-x")
    assert type(model) is services.Gemini
    assert model.model == "model-x"


def test_gemini_with_api_key_binds_a_client_to_that_key():
    # ADK's Gemini (google-adk 2.2.0) has no api_key field of its own — a plain
    # `Gemini(api_key=...)` kwarg is silently dropped. _gemini must route through the
    # documented api_client-override extension point (_ByokGemini) instead, or the byok
    # key never actually reaches google.genai.Client.
    model = services._gemini("model-x", api_key="user-secret-key")
    assert isinstance(model, services._ByokGemini)
    assert model.model == "model-x"
    assert model.byok_api_key == "user-secret-key"
    assert model.api_client.models._api_client.api_key == "user-secret-key"


def test_gemini_byok_generate_content_async_actually_uses_the_byok_client(monkeypatch):
    """Behavioral canary (task-3 review): the construction-level test above proves
    `.api_client` is BOUND to the byok key, but that alone doesn't prove ADK's REAL call
    path reads `self.api_client` at call time — a future google-adk (pinned
    `>=2.1.0`, unbounded — CI resolves fresh) could route `generate_content_async`
    through a different attribute or a module-level client, and this would silently
    fall back to the app key while the construction test above kept passing. This drives
    the actual entry point the mesh calls (`generate_content_async`), mocking only the
    network boundary (`AsyncModels.generate_content`), and fails loudly the moment a
    future ADK version stops consulting `self.api_client` for the real call."""
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types as genai_types

    model = services._gemini("model-x", api_key="canary-byok-key")
    client = model.api_client  # forces construction; api_client is cached, so the real
    # call below reuses this exact instance — not a fresh one.
    assert client.models._api_client.api_key == "canary-byok-key"

    captured: dict = {}

    async def fake_generate_content(*, model, contents, config):
        # The real network boundary. Read the key off the client instance that is
        # actually executing the call — not off `model`/`_gemini`'s return value — so
        # this fails if a future ADK routes the call through some OTHER client.
        captured["key"] = client.models._api_client.api_key
        return genai_types.GenerateContentResponse()

    monkeypatch.setattr(client.aio.models, "generate_content", fake_generate_content)

    llm_request = LlmRequest(
        model="model-x", contents=[genai_types.Content(role="user", parts=[genai_types.Part(text="hi")])]
    )

    async def _drive():
        return [r async for r in model.generate_content_async(llm_request)]

    responses = asyncio.run(_drive())

    assert captured.get("key") == "canary-byok-key"
    assert responses  # the fake response was consumed all the way to an LlmResponse


@pytest.mark.parametrize(
    "api_key",
    [pytest.param("user-secret-key", id="byok_key_threaded"), pytest.param(None, id="app_key_default")],
)
def test_create_agent_mesh_threads_api_key_to_every_agent(monkeypatch, api_key):
    calls = []

    def fake_gemini(model_name, key=None):
        calls.append((model_name, key))
        return services.Gemini(model=model_name)

    monkeypatch.setattr(services, "_gemini", fake_gemini)
    services.create_agent_mesh(api_key=api_key)
    # Analyst, Explorer (the grounding model too), Critic, Librarian.
    assert len(calls) == 4
    assert all(key == api_key for _, key in calls)


def test_build_runner_threads_api_key_into_create_agent_mesh(monkeypatch):
    captured = {}

    def fake_create_agent_mesh(api_key=None):
        captured["api_key"] = api_key
        return create_agent_mesh()

    monkeypatch.setattr(runtime, "create_agent_mesh", fake_create_agent_mesh)
    runtime.build_runner(api_key="byok-key")
    assert captured["api_key"] == "byok-key"


def test_astart_conversation_threads_api_key_into_build_runner_when_no_runner_given(monkeypatch):
    captured = {}

    def fake_build_runner(api_key=None):
        captured["api_key"] = api_key
        return _FakeRunner()

    monkeypatch.setattr(runtime, "build_runner", fake_build_runner)
    asyncio.run(runtime.astart_conversation(user_id="u", session_id="s", api_key="byok-key"))
    assert captured["api_key"] == "byok-key"


def test_astart_conversation_does_not_rebuild_an_explicitly_passed_runner(monkeypatch):
    # A caller-supplied runner's mesh is already fixed — api_key threading only applies
    # when astart_conversation builds the runner itself.
    def fail_build_runner(api_key=None):
        raise AssertionError("build_runner must not be called when a runner is passed")

    monkeypatch.setattr(runtime, "build_runner", fail_build_runner)
    fake = _FakeRunner()
    conv = asyncio.run(runtime.astart_conversation(user_id="u", runner=fake, session_id="s", api_key="byok-key"))
    assert conv is not None


def test_astart_conversation_carries_key_source_onto_the_conversation():
    fake = _FakeRunner()
    conv = asyncio.run(runtime.astart_conversation(user_id="u", runner=fake, session_id="s", key_source="byok"))
    assert conv.key_source == "byok"


def test_astart_conversation_key_source_defaults_to_app():
    fake = _FakeRunner()
    conv = asyncio.run(runtime.astart_conversation(user_id="u", runner=fake, session_id="s"))
    assert conv.key_source == "app"

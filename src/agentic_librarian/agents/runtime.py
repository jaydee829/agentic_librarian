"""Runtime for the recommendation agent mesh: host the Librarian in an ADK Runner
and expose a multi-turn conversation API (ADR-035 Spec 1, ADR-036)."""

import asyncio
import logging
import os
import uuid

from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agentic_librarian.agents.backends import get_backend
from agentic_librarian.agents.services import create_agent_mesh
from agentic_librarian.core.usage import record_llm_call

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

APP_NAME = "agentic_librarian"

logger = logging.getLogger(__name__)


def _ensure_adk_credentials() -> None:
    """ADK's Gemini model authenticates via GOOGLE_API_KEY. Populate it from the
    project's existing keys if it isn't set (GOOGLE_SEARCH_API_KEY has access to both
    Custom Search and the Gemini API in this GCP project)."""
    if not os.environ.get("GOOGLE_API_KEY"):
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_SEARCH_API_KEY")
        if key:
            os.environ["GOOGLE_API_KEY"] = key


def build_runner(api_key: str | None = None) -> Runner:
    """Build a Runner hosting the Librarian (root of the agent mesh).

    api_key (arc 3/3 BYOK): threaded into create_agent_mesh so every agent in this
    Runner's mesh rides the byok user's key. `_ensure_adk_credentials` still runs
    unconditionally — it only sets the APP-key env default (GOOGLE_API_KEY), which the
    per-model `api_key` override takes precedence over; it is never mutated per-request."""
    _ensure_adk_credentials()
    mesh = create_agent_mesh(api_key)
    return Runner(
        agent=mesh["librarian"],
        app_name=APP_NAME,
        session_service=InMemorySessionService(),
    )


async def _record_event_usage(event, conversation_id: uuid.UUID | None, key_source: str = "app") -> None:
    """Meter one ADK event if it carries usage (duck-typed: unit-test fakes and
    non-LLM events simply lack usage_metadata).

    key_source (arc 3/3 BYOK): passed as a parameter (not module-level state) because
    concurrent turns from different users can be in flight in the same process — a
    module global would race between them."""
    # NOTE: with StreamingMode.SSE each partial event would carry usage; run_async is
    # used in default (non-streaming) mode here — one usage-bearing event per LLM call.
    um = getattr(event, "usage_metadata", None)
    if um is None:
        return
    # INF-030: the metering INSERT runs off the event loop. to_thread copies the context,
    # so record_llm_call's get_required_user_id still resolves the turn's user.
    await asyncio.to_thread(
        record_llm_call,
        vendor="gemini",
        model=getattr(event, "model_version", None) or os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
        input_tokens=getattr(um, "prompt_token_count", 0) or 0,
        output_tokens=getattr(um, "candidates_token_count", 0) or 0,
        conversation_id=conversation_id,
        key_source=key_source,
    )


class LibrarianConversation:
    """A multi-turn conversation with the Librarian. Reusing one session across
    sends is what gives the agent conversational memory (ADR-036)."""

    def __init__(self, runner: Runner, user_id: str, session_id: str, on_event=None, key_source: str = "app"):
        self._runner = runner
        self.user_id = user_id
        self.session_id = session_id
        # arc 3/3 BYOK: carried as an instance attribute (not a module global) so
        # concurrent conversations from different users never race on it — each turn
        # rebuilds its own mesh + conversation (astart_conversation), so this is
        # resolved once and fixed for the conversation's lifetime.
        self.key_source = key_source
        try:
            self.conversation_id = uuid.UUID(session_id)  # session ids are uuid4().hex
        except (ValueError, AttributeError):
            # A manufactured id would look joinable but join to nothing — NULL is the
            # honest encoding for "unknown" (T8 review). Production always passes hex
            # ids (astart_conversation); this is reachable only by hand-built callers.
            logger.warning("session_id %r is not a UUID; usage rows will have NULL conversation_id", session_id)
            self.conversation_id = None
        # Optional visibility hook (ADR-045): on_event(kind, detail) for ("tool", name) /
        # ("agent", author). Duck-typed event introspection so unit-test fakes keep working.
        self.on_event = on_event

    async def asend(self, message: str) -> str:
        content = types.Content(role="user", parts=[types.Part(text=message)])
        final = ""
        last_author = None
        async for event in self._runner.run_async(
            user_id=self.user_id, session_id=self.session_id, new_message=content
        ):
            await _record_event_usage(event, self.conversation_id, self.key_source)
            if self.on_event:
                author = getattr(event, "author", None)
                if author and author != last_author:
                    self.on_event("agent", author)
                    last_author = author
                get_calls = getattr(event, "get_function_calls", None)
                for fc in (get_calls() if callable(get_calls) else []) or []:
                    name = getattr(fc, "name", None)
                    if name:
                        self.on_event("tool", name)
            if event.is_final_response() and event.content and event.content.parts:
                parts_text = [p.text for p in event.content.parts if p.text]
                if parts_text:
                    final = "".join(parts_text)
        return final or "(no response)"

    def send(self, message: str) -> str:
        return asyncio.run(self.asend(message))

    def close(self) -> None:
        """No session resources to release (InMemorySessionService); exists for
        BackendConversation conformance (ADR-045)."""


async def astart_conversation(
    user_id: str = "local",
    runner: Runner | None = None,
    on_event=None,
    session_id: str | None = None,
    history: list[dict] | None = None,
    api_key: str | None = None,
    key_source: str = "app",
) -> LibrarianConversation:
    """Open a conversation. `session_id` lets the caller pin the ADK session id to a
    stored conversation id (so usage rows line up). `history` (oldest first, each
    {'role': 'user'|'assistant', 'content': str}) is seeded into the session as events
    so the mesh has prior context WITHOUT re-running earlier turns (Lift 2).

    api_key/key_source (arc 3/3 BYOK): when a `runner` is passed explicitly (tests, or a
    caller that already built one), `api_key` is NOT re-threaded into it — the runner's
    mesh is already fixed; only `key_source` is carried onto the returned conversation so
    usage rows record it correctly. The chat handler never passes both `runner` and
    `api_key` together — it lets this function build the runner via `build_runner`."""
    runner = runner or build_runner(api_key)
    session_id = session_id or uuid.uuid4().hex
    session = await runner.session_service.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
    for turn in history or []:
        role = "user" if turn["role"] == "user" else "model"
        author = "user" if turn["role"] == "user" else "librarian"
        content = types.Content(role=role, parts=[types.Part(text=turn["content"])])
        await runner.session_service.append_event(session, Event(author=author, content=content))
    return LibrarianConversation(runner, user_id, session_id, on_event=on_event, key_source=key_source)


def start_conversation(
    user_id: str = "local",
    runner: Runner | None = None,
    on_event=None,
    session_id: str | None = None,
    history: list[dict] | None = None,
    api_key: str | None = None,
    key_source: str = "app",
) -> LibrarianConversation:
    return asyncio.run(
        astart_conversation(
            user_id=user_id,
            runner=runner,
            on_event=on_event,
            session_id=session_id,
            history=history,
            api_key=api_key,
            key_source=key_source,
        )
    )


def run_recommendation(prompt: str, user_id: str = "local") -> str:
    """One-shot recommendation via the configured backend (AGENT_BACKEND)."""
    return get_backend().run_recommendation(prompt, user_id)

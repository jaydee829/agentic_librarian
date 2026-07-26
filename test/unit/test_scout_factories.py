from unittest.mock import MagicMock

import pytest

from agentic_librarian.orchestration.definitions import (
    create_completion_scout_manager,
    create_deep_scout_manager,
    create_fast_scout_manager,
)
from agentic_librarian.scouts.metadata_scout import (
    AudiobookScout,
    DirectKnowledgeScout,
    GoogleBooksScout,
    HardcoverScout,
    LLMTropeScout,
    StyleScout,
)


def test_fast_manager_has_only_api_scouts_in_priority_order():
    mgr = create_fast_scout_manager()
    types = [type(s) for s, _ in mgr.scouts]
    assert types == [HardcoverScout, GoogleBooksScout]


def test_deep_manager_has_only_llm_scouts_in_priority_order(monkeypatch):
    # LLM scouts require a Google key at construction.
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    mgr = create_deep_scout_manager()
    types = [type(s) for s, _ in mgr.scouts]
    assert types == [AudiobookScout, DirectKnowledgeScout, StyleScout, LLMTropeScout]


def test_completion_manager_composition(monkeypatch):
    """Format-completion pass (history-format-edit spec): fast API scouts + audiobook
    scouts ONLY — never LLMTropeScout (paid trope pass) or StyleScout (author/work
    styles); narrator styles are scouted directly by two_phase.complete_edition."""
    # AudiobookScout/DirectKnowledgeScout are LLMScouts — the base raises without a key.
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    manager = create_completion_scout_manager()
    assert [type(s) for s, _priority in manager.scouts] == [
        HardcoverScout,
        GoogleBooksScout,
        AudiobookScout,
        DirectKnowledgeScout,
    ]


@pytest.fixture
def _no_network_gemini_client(monkeypatch):
    """GeminiGroundedLLM's __init__ builds a real genai.Client — stub it so constructing
    LLM scouts in these threading tests never attempts a network call. Also pins
    AGENT_BACKEND to the default Gemini provider regardless of the ambient env."""
    from agentic_librarian.scouts import grounded_llm

    monkeypatch.setattr(grounded_llm.genai, "Client", lambda *a, **k: MagicMock())
    monkeypatch.delenv("AGENT_BACKEND", raising=False)


@pytest.mark.parametrize(
    ("factory", "llm_scout_types"),
    [
        pytest.param(
            create_deep_scout_manager, {AudiobookScout, DirectKnowledgeScout, StyleScout, LLMTropeScout}, id="deep"
        ),
        pytest.param(create_completion_scout_manager, {AudiobookScout, DirectKnowledgeScout}, id="completion"),
    ],
)
def test_factory_threads_byok_key_into_every_llm_scout(
    monkeypatch, _no_network_gemini_client, factory, llm_scout_types
):
    """arc 3/3: a byok user's api_key/key_source must reach every LLM scout's underlying
    GeminiGroundedLLM (so its usage rows record key_source='byok'), never the non-LLM
    API scouts (Hardcover/GoogleBooks, which use their own unrelated keys)."""
    monkeypatch.delenv("GOOGLE_SEARCH_API_KEY", raising=False)
    manager = factory("byok-users-gemini-key", "byok")

    seen_llm_scouts = [s for s, _priority in manager.scouts if type(s) in llm_scout_types]
    assert len(seen_llm_scouts) == len(llm_scout_types)
    for scout in seen_llm_scouts:
        assert scout.api_key == "byok-users-gemini-key"
        assert scout._llm.api_key == "byok-users-gemini-key"
        assert scout._llm.key_source == "byok"

    non_llm_scouts = [s for s, _priority in manager.scouts if type(s) not in llm_scout_types]
    for scout in non_llm_scouts:
        assert type(scout) in (HardcoverScout, GoogleBooksScout)
        # HardcoverScout/GoogleBooksScout read their OWN env-keyed credentials — the byok
        # Gemini key must never leak into them.
        assert scout.api_key != "byok-users-gemini-key"


def test_factory_defaults_are_app_keyed(monkeypatch, _no_network_gemini_client):
    """No api_key/key_source passed -> every LLM scout stays on the app key, byte-identical
    to pre-BYOK behavior."""
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    manager = create_deep_scout_manager()
    llm_scouts = [
        s
        for s, _priority in manager.scouts
        if isinstance(s, AudiobookScout | DirectKnowledgeScout | StyleScout | LLMTropeScout)
    ]
    assert len(llm_scouts) == 4
    for scout in llm_scouts:
        assert scout._llm.key_source == "app"

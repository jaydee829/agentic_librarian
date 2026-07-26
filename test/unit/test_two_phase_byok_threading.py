"""arc 3/3 BYOK: two_phase.enrich_deep / complete_edition thread api_key/key_source into
the scout factories they call (and complete_edition's bare narrator-style StyleScout call),
so a byok user's deep-enrichment pass genuinely runs on their own Gemini key. Session
doubles follow the house #94 pattern (test_two_phase_sessions.py / test_edition_completion.py)
— no real DB, no real scout network calls."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from agentic_librarian.enrichment import two_phase


class _Session:
    """Stands in for both the read and write `with db_manager.get_session()` blocks."""

    def __init__(self, work=None, edition=None):
        self._work = work
        self._edition = edition

    def __enter__(self):
        m = MagicMock()
        m.get.return_value = self._work
        m.query.return_value.filter_by.return_value.first.return_value = self._edition
        return m

    def __exit__(self, *a):
        return False


def _work_double(title="T", author="A", editions=None):
    work = MagicMock()
    work.title = title
    contrib = MagicMock(role="Author")
    contrib.author.name = author
    work.contributors = [contrib]
    work.editions = editions or []
    return work


@pytest.mark.parametrize(
    ("api_key", "key_source", "call_with_kwargs"),
    [
        pytest.param("users-gemini-key", "byok", True, id="byok"),
        pytest.param(None, "app", False, id="app-default"),
    ],
)
def test_enrich_deep_threads_key_into_deep_scout_factory(monkeypatch, api_key, key_source, call_with_kwargs):
    work = _work_double()
    sessions = [_Session(work=work), _Session(work=work)]
    fake_manager = MagicMock()
    fake_manager.get_session = lambda: sessions.pop(0)
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)

    captured = {}

    def fake_factory(factory_api_key=None, factory_key_source="app"):
        captured["args"] = (factory_api_key, factory_key_source)
        mgr = MagicMock()
        mgr.enrich.return_value = None  # scouts found nothing -> "empty" path, no persist needed
        return mgr

    monkeypatch.setattr(two_phase, "create_deep_scout_manager", fake_factory)

    work_id = uuid4()
    if call_with_kwargs:
        result = two_phase.enrich_deep(work_id, api_key=api_key, key_source=key_source)
    else:
        result = two_phase.enrich_deep(work_id)

    assert result == "empty"
    assert captured["args"] == (api_key, key_source)


@pytest.mark.parametrize(
    ("api_key", "key_source", "call_with_kwargs"),
    [
        pytest.param("users-gemini-key", "byok", True, id="byok"),
        pytest.param(None, "app", False, id="app-default"),
    ],
)
def test_complete_edition_threads_key_into_completion_factory_and_style_scout(
    monkeypatch, api_key, key_source, call_with_kwargs
):
    work = _work_double()
    edition = MagicMock()
    sessions = [_Session(work=work, edition=edition), _Session(work=work, edition=edition)]
    fake_manager = MagicMock()
    fake_manager.get_session = lambda: sessions.pop(0)
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)

    captured = {}
    scout_mgr = MagicMock()
    scout_mgr.enrich.return_value = {"narrator_names": ["Ray Porter"], "source_priority": ["Hardcover"]}

    def fake_completion_factory(factory_api_key=None, factory_key_source="app"):
        captured["factory_args"] = (factory_api_key, factory_key_source)
        return scout_mgr

    monkeypatch.setattr(two_phase, "create_completion_scout_manager", fake_completion_factory)

    style_scout = MagicMock()
    style_scout.scout_narrator_style.return_value = {}

    def fake_style_scout(style_api_key=None, key_source="app"):
        captured["style_scout_args"] = (style_api_key, key_source)
        return style_scout

    monkeypatch.setattr(two_phase, "StyleScout", fake_style_scout)
    monkeypatch.setattr(two_phase, "get_cached_embedding", lambda *a, **k: [0.0])
    monkeypatch.setattr(two_phase, "merge_edition_and_narrators", lambda session, **kw: MagicMock())

    work_id = uuid4()
    if call_with_kwargs:
        result = two_phase.complete_edition(work_id, "audiobook", api_key=api_key, key_source=key_source)
    else:
        result = two_phase.complete_edition(work_id, "audiobook")

    assert result == "done"
    assert captured["factory_args"] == (api_key, key_source)
    assert captured["style_scout_args"] == (api_key, key_source)
    style_scout.scout_narrator_style.assert_called_once_with("Ray Porter")

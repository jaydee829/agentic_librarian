"""complete_edition (history-format-edit): targeted format-completion pass.

Session discipline (#94), status contract, and scout composition — narrator styles are
scouted ONLY for audiobook formats, and only via scout_narrator_style (never
StyleScout.search, which would re-scout author/work styles)."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agentic_librarian.enrichment import two_phase


class _FakeSession:
    """Counting session double (the house #94 pattern, test_two_phase_sessions.py)."""

    def __init__(self, state, work, edition):
        self._state = state
        self._work = work
        self._edition = edition

    def __enter__(self):
        self._state["open"] += 1
        m = MagicMock()
        m.get.return_value = self._work
        m.query.return_value.filter_by.return_value.first.return_value = self._edition
        return m

    def __exit__(self, *a):
        self._state["open"] -= 1
        return False


def _work_double(title="T", author="A"):
    work = MagicMock()
    work.title = title
    contrib = MagicMock(role="Author")
    contrib.author.name = author
    work.contributors = [contrib]
    return work


def _wire(monkeypatch, *, work, edition, enriched, state=None):
    state = state if state is not None else {"open": 0}
    fake_manager = MagicMock()
    fake_manager.get_session = lambda: _FakeSession(state, work, edition)
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)
    scout_mgr = MagicMock()
    scout_mgr.enrich.return_value = enriched
    monkeypatch.setattr(two_phase, "create_completion_scout_manager", lambda *a, **k: scout_mgr)
    return state, scout_mgr


def test_scouts_run_outside_any_session(monkeypatch):
    state, scout_mgr = _wire(monkeypatch, work=_work_double(), edition=MagicMock(), enriched={})
    seen = {}
    scout_mgr.enrich.side_effect = lambda **kw: seen.setdefault("open_during_scout", state["open"]) or {}
    assert two_phase.complete_edition(uuid4(), "ebook") == "empty"
    assert seen["open_during_scout"] == 0  # THE #94 assertion


@pytest.mark.parametrize(
    ("work", "edition"),
    [
        pytest.param(None, MagicMock(), id="work_gone"),
        pytest.param(_work_double(), None, id="edition_gone"),
        pytest.param(MagicMock(title="T", contributors=[]), MagicMock(), id="no_author"),
    ],
)
def test_missing_paths(monkeypatch, work, edition):
    _wire(monkeypatch, work=work, edition=edition, enriched={})
    assert two_phase.complete_edition(uuid4(), "ebook") == "missing"


def test_empty_scouts_is_final(monkeypatch):
    _wire(monkeypatch, work=_work_double(), edition=MagicMock(), enriched={})
    assert two_phase.complete_edition(uuid4(), "audiobook") == "empty"


def test_done_merges_scouted_values_for_audiobook(monkeypatch):
    enriched = {
        "isbn_13": "9780000000000",
        "page_count": 300,
        "audio_minutes": 600,
        "publication_date": "2020-01-01",
        "narrator_names": ["Ray Porter"],
        "source_priority": ["Hardcover"],
    }
    _wire(monkeypatch, work=_work_double(), edition=MagicMock(), enriched=enriched)
    monkeypatch.setattr(two_phase, "get_cached_embedding", lambda *a, **k: [0.0])
    style_scout = MagicMock()
    style_scout.scout_narrator_style.return_value = {"pacing": "brisk"}
    monkeypatch.setattr(two_phase, "StyleScout", lambda *a, **k: style_scout)
    merged = {}

    def fake_merge(session, **kwargs):
        merged.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(two_phase, "merge_edition_and_narrators", fake_merge)

    assert two_phase.complete_edition(uuid4(), "audiobook") == "done"
    style_scout.scout_narrator_style.assert_called_once_with("Ray Porter")
    assert merged["fmt"] == "audiobook"
    assert merged["isbn_13"] == "9780000000000"
    assert merged["audio_minutes"] == 600
    assert merged["narrator_names"] == ["Ray Porter"]
    assert merged["narrator_styles"] == {"Ray Porter": {"pacing": "brisk"}}


def test_non_audiobook_never_scouts_narrator_styles(monkeypatch):
    enriched = {"isbn_13": "9780000000001", "narrator_names": [], "source_priority": ["Hardcover"]}
    _wire(monkeypatch, work=_work_double(), edition=MagicMock(), enriched=enriched)
    monkeypatch.setattr(two_phase, "merge_edition_and_narrators", lambda session, **kw: MagicMock())
    with patch.object(two_phase, "StyleScout") as style_cls:
        assert two_phase.complete_edition(uuid4(), "paperback") == "done"
    style_cls.assert_not_called()


def test_style_scout_failure_degrades_to_no_styles(monkeypatch):
    enriched = {"narrator_names": ["Ray Porter"], "source_priority": ["Audible"]}
    _wire(monkeypatch, work=_work_double(), edition=MagicMock(), enriched=enriched)
    monkeypatch.setattr(two_phase, "get_cached_embedding", lambda *a, **k: [0.0])
    broken = MagicMock()
    broken.scout_narrator_style.side_effect = RuntimeError("LLM down")
    monkeypatch.setattr(two_phase, "StyleScout", lambda *a, **k: broken)
    merged = {}
    monkeypatch.setattr(
        two_phase, "merge_edition_and_narrators", lambda session, **kw: merged.update(kw) or MagicMock()
    )
    assert two_phase.complete_edition(uuid4(), "audiobook") == "done"
    assert merged["narrator_styles"] == {}  # narrators still merge; styles degrade


def test_work_deleted_mid_pass_returns_missing(monkeypatch):
    """The write session re-checks existence (same honesty rule as enrich_deep)."""
    enriched = {"isbn_13": "9780000000002", "narrator_names": [], "source_priority": ["Hardcover"]}
    state = {"open": 0, "calls": 0}

    work = _work_double()

    class _VanishingSession(_FakeSession):
        def __enter__(self):
            self._state["open"] += 1
            self._state["calls"] += 1
            m = MagicMock()
            # First (read) session sees the work; second (write) session finds it gone.
            m.get.return_value = work if self._state["calls"] == 1 else None
            m.query.return_value.filter_by.return_value.first.return_value = MagicMock()
            return m

    fake_manager = MagicMock()
    fake_manager.get_session = lambda: _VanishingSession(state, work, MagicMock())
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)
    scout_mgr = MagicMock()
    scout_mgr.enrich.return_value = enriched
    monkeypatch.setattr(two_phase, "create_completion_scout_manager", lambda *a, **k: scout_mgr)
    assert two_phase.complete_edition(uuid4(), "ebook") == "missing"


def test_edition_deleted_mid_pass_returns_missing_without_merge(monkeypatch):
    """The write session re-checks the EDITION too: the read session sees it, but an operator
    edition delete mid-pass makes the write session's edition query return None → "missing",
    and merge_edition_and_narrators must never run (it would silently recreate the edition)."""
    enriched = {"isbn_13": "9780000000003", "narrator_names": [], "source_priority": ["Hardcover"]}
    state = {"open": 0, "calls": 0}
    work = _work_double()

    class _EditionVanishesSession(_FakeSession):
        def __enter__(self):
            self._state["open"] += 1
            self._state["calls"] += 1
            m = MagicMock()
            m.get.return_value = work  # the Work survives both sessions
            # First (read) session sees the edition; second (write) session finds it gone.
            edition = MagicMock() if self._state["calls"] == 1 else None
            m.query.return_value.filter_by.return_value.first.return_value = edition
            return m

    fake_manager = MagicMock()
    fake_manager.get_session = lambda: _EditionVanishesSession(state, work, MagicMock())
    monkeypatch.setattr(two_phase, "db_manager", fake_manager)
    scout_mgr = MagicMock()
    scout_mgr.enrich.return_value = enriched
    monkeypatch.setattr(two_phase, "create_completion_scout_manager", lambda *a, **k: scout_mgr)
    merge_calls = []
    monkeypatch.setattr(
        two_phase, "merge_edition_and_narrators", lambda session, **kw: merge_calls.append(kw) or MagicMock()
    )
    assert two_phase.complete_edition(uuid4(), "ebook") == "missing"
    assert merge_calls == []  # the edition re-check short-circuits before any merge

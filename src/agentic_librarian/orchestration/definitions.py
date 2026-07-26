from dagster import Definitions, load_assets_from_modules

from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.orchestration import assets, jobs, sensors
from agentic_librarian.scouts.metadata_scout import (
    AudiobookScout,
    DirectKnowledgeScout,
    GoogleBooksScout,
    HardcoverScout,
    LLMTropeScout,
    ScoutManager,
    StyleScout,
)

all_assets = load_assets_from_modules([assets])


def create_scout_manager() -> ScoutManager:
    """Initialize and configure the ScoutManager with active scouts."""
    manager = ScoutManager()
    manager.register_scout(HardcoverScout(), priority=1)
    manager.register_scout(GoogleBooksScout(), priority=2)
    manager.register_scout(AudiobookScout(), priority=3)
    manager.register_scout(DirectKnowledgeScout(), priority=4)
    # StyleScout runs after the audiobook scouts so narrator_names is populated
    # before it scouts narrator styles. LLMTropeScout extracts deep tropes.
    manager.register_scout(StyleScout(), priority=5)
    manager.register_scout(LLMTropeScout(), priority=6)
    return manager


def create_fast_scout_manager() -> ScoutManager:
    """Fast tier (Lift 2 Stage 3): API scouts only (Hardcover, Google Books) — no LLM,
    so the add-a-book request returns in seconds and needs no Google API key."""
    manager = ScoutManager()
    manager.register_scout(HardcoverScout(), priority=1)
    manager.register_scout(GoogleBooksScout(), priority=2)
    return manager


def create_deep_scout_manager(api_key: str | None = None, key_source: str = "app") -> ScoutManager:
    """Deep tier (Lift 2 Stage 3): the slow LLM scouts (audiobook, style, tropes) run later
    via the Cloud Tasks internal endpoint. Audiobook/DirectKnowledge self-skip on non-audiobook
    formats; StyleScout (5) runs after them so narrator_names is populated; LLMTropeScout (6) last.

    `api_key`/`key_source` (arc 3/3 BYOK) thread into every LLM scout here — the ones that
    call Gemini via GroundedLLM — so a byok user's deep-enrichment pass runs on their own
    key and its usage rows record key_source='byok'."""
    manager = ScoutManager()
    manager.register_scout(AudiobookScout(api_key, key_source=key_source), priority=3)
    manager.register_scout(DirectKnowledgeScout(api_key, key_source=key_source), priority=4)
    manager.register_scout(StyleScout(api_key, key_source=key_source), priority=5)
    manager.register_scout(LLMTropeScout(api_key, key_source=key_source), priority=6)
    return manager


def create_completion_scout_manager(api_key: str | None = None, key_source: str = "app") -> ScoutManager:
    """Format-completion pass (history-format-edit): the fast API scouts fetch the new
    format's edition metadata (ISBN, pages/audio minutes, publication date); the audiobook
    scouts (which self-skip on non-audiobook formats) add narrators. Deliberately NO
    LLMTropeScout and NO StyleScout — tropes and author/work styles belong to the Work,
    which a format change does not touch; narrator styles are scouted directly by
    two_phase.complete_edition.

    `api_key`/`key_source` (arc 3/3 BYOK) thread ONLY into the two LLM scouts (Audiobook,
    DirectKnowledge) — HardcoverScout/GoogleBooksScout use their own, unrelated API keys."""
    manager = ScoutManager()
    manager.register_scout(HardcoverScout(), priority=1)
    manager.register_scout(GoogleBooksScout(), priority=2)
    manager.register_scout(AudiobookScout(api_key, key_source=key_source), priority=3)
    manager.register_scout(DirectKnowledgeScout(api_key, key_source=key_source), priority=4)
    return manager


defs = Definitions(
    assets=all_assets,
    jobs=[jobs.enhance_job],
    sensors=[sensors.new_file_sensor],
    resources={
        "db_manager": DatabaseManager(),
        "scout_manager": create_scout_manager(),
    },
)

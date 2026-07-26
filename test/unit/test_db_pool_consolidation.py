"""Lift 2 Stage 4: startup consolidates the four lazy DatabaseManager pools into one
shared, lifespan-injected manager (INF-030 companion / Stage 1 review note)."""

import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import analysis as analysis_mod
from agentic_librarian.api import auth as auth_mod
from agentic_librarian.api import main as main_mod
from agentic_librarian.api import recommendations as recommendations_mod
from agentic_librarian.chat import transcript as transcript_mod
from agentic_librarian.core import budgets as budgets_mod
from agentic_librarian.core import usage as usage_mod


@pytest.fixture()
def _restore_db_managers():
    """lifespan mutates module globals; snapshot + restore so test order can't leak."""
    saved = (
        main_mod.db_manager,
        auth_mod.db_manager,
        transcript_mod.db_manager,
        usage_mod.db_manager,
        recommendations_mod.db_manager,
        analysis_mod.db_manager,
        budgets_mod.db_manager,
    )
    yield
    (
        main_mod.db_manager,
        auth_mod.db_manager,
        transcript_mod.db_manager,
        usage_mod.db_manager,
        recommendations_mod.db_manager,
        analysis_mod.db_manager,
        budgets_mod.db_manager,
    ) = saved


def test_startup_consolidates_api_pools(_restore_db_managers):
    # Entering the TestClient context runs the lifespan startup. DatabaseManager() is
    # lazy (no connection until first get_session), so this is offline-safe.
    with TestClient(main_mod.app):
        shared = main_mod.app.state.db_manager
        assert main_mod.db_manager is shared
        assert auth_mod.db_manager is shared
        assert transcript_mod.db_manager is shared
        assert usage_mod.db_manager is shared
        assert recommendations_mod.db_manager is shared
        assert analysis_mod.db_manager is shared
        assert budgets_mod.db_manager is shared

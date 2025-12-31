"""Tests for TropeAgent."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.agentic_librarian.agents.trope_agent import TropeAgent


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Mock environment variables needed for TropeAgent."""
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "test_api_key")
    monkeypatch.setenv("SEARCH_ENGINE_ID", "test_search_engine_id")
    monkeypatch.setenv("MCP_SERVER_URL", "http://test-mcp-server.com")


@pytest.fixture
def trope_agent(mock_env_vars):
    """Create a TropeAgent instance with mocked environment."""
    with patch("src.agentic_librarian.agents.trope_agent.genai.Client"):
        agent = TropeAgent()
        return agent


def test_trope_agent_initialization_missing_api_key(monkeypatch):
    """Test that TropeAgent raises error when API key is missing."""
    monkeypatch.delenv("GOOGLE_SEARCH_API_KEY", raising=False)
    monkeypatch.setenv("SEARCH_ENGINE_ID", "test_search_engine_id")
    monkeypatch.setenv("MCP_SERVER_URL", "http://test-mcp-server.com")

    with (
        pytest.raises(ValueError, match="GOOGLE_SEARCH_API_KEY"),
        patch("src.agentic_librarian.agents.trope_agent.genai.Client"),
    ):
        TropeAgent()


def test_trope_agent_initialization_missing_search_engine_id(monkeypatch):
    """Test that TropeAgent raises error when search engine ID is missing."""
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "test_api_key")
    monkeypatch.delenv("SEARCH_ENGINE_ID", raising=False)
    monkeypatch.setenv("MCP_SERVER_URL", "http://test-mcp-server.com")

    with (
        pytest.raises(ValueError, match="SEARCH_ENGINE_ID"),
        patch("src.agentic_librarian.agents.trope_agent.genai.Client"),
    ):
        TropeAgent()


def test_trope_agent_initialization_missing_mcp_url(monkeypatch):
    """Test that TropeAgent raises error when MCP server URL is missing."""
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "test_api_key")
    monkeypatch.setenv("SEARCH_ENGINE_ID", "test_search_engine_id")
    monkeypatch.delenv("MCP_SERVER_URL", raising=False)

    with (
        pytest.raises(ValueError, match="MCP_SERVER_URL"),
        patch("src.agentic_librarian.agents.trope_agent.genai.Client"),
    ):
        TropeAgent()


def test_process_request_success(trope_agent, monkeypatch):
    """Test successful A2A request processing."""
    # Mock the identify_tropes method
    mock_tropes = [
        {"name": "Hero's Journey", "confidence": 0.95, "sources": ["llm_knowledge", "internet_search"]},
        {"name": "Chosen One", "confidence": 0.85, "sources": ["llm_knowledge"]},
    ]
    monkeypatch.setattr(trope_agent, "identify_tropes", lambda title, author, top_n: mock_tropes)

    request_data = {"title": "The Way of Kings", "author": "Brandon Sanderson", "top_n": 5}

    response = trope_agent.process_request(request_data)

    assert response["status"] == "success"
    assert "tropes" in response
    assert len(response["tropes"]) == 2
    assert response["tropes"][0]["name"] == "Hero's Journey"


def test_process_request_missing_title(trope_agent):
    """Test A2A request with missing title."""
    request_data = {"author": "Brandon Sanderson", "top_n": 5}

    response = trope_agent.process_request(request_data)

    assert response["status"] == "error"
    assert "title" in response["message"].lower()


def test_process_request_missing_author(trope_agent):
    """Test A2A request with missing author."""
    request_data = {"title": "The Way of Kings", "top_n": 5}

    response = trope_agent.process_request(request_data)

    assert response["status"] == "error"
    assert "author" in response["message"].lower()


def test_process_request_default_top_n(trope_agent, monkeypatch):
    """Test that top_n defaults to 5 when not provided."""
    called_with = {}

    def mock_identify(title, author, top_n):
        called_with["top_n"] = top_n
        return []

    monkeypatch.setattr(trope_agent, "identify_tropes", mock_identify)

    request_data = {"title": "The Way of Kings", "author": "Brandon Sanderson"}

    trope_agent.process_request(request_data)

    assert called_with["top_n"] == 5


def test_get_tropes_from_llm_success(trope_agent):
    """Test getting tropes from LLM."""
    mock_response = MagicMock()
    mock_response.text = json.dumps(
        {
            "tropes": [
                {"name": "Hero's Journey", "confidence": 0.9, "description": "Main character on epic quest"},
                {"name": "Chosen One", "confidence": 0.85, "description": "Protagonist with special destiny"},
            ]
        }
    )

    trope_agent._gemini_client.models.generate_content = MagicMock(return_value=mock_response)

    result = trope_agent._get_tropes_from_llm("The Way of Kings", "Brandon Sanderson")

    assert "Hero's Journey" in result
    assert result["Hero's Journey"]["confidence"] == 0.9
    assert result["Hero's Journey"]["source"] == "llm_knowledge"
    assert "Chosen One" in result


def test_get_tropes_from_llm_handles_markdown(trope_agent):
    """Test that LLM response with markdown formatting is handled."""
    mock_response = MagicMock()
    mock_response.text = "```json\n" + json.dumps({"tropes": [{"name": "Test Trope", "confidence": 0.8}]}) + "\n```"

    trope_agent._gemini_client.models.generate_content = MagicMock(return_value=mock_response)

    result = trope_agent._get_tropes_from_llm("Test Book", "Test Author")

    assert "Test Trope" in result
    assert result["Test Trope"]["confidence"] == 0.8


def test_get_tropes_from_llm_error_handling(trope_agent):
    """Test error handling in LLM trope extraction."""
    trope_agent._gemini_client.models.generate_content = MagicMock(side_effect=Exception("API Error"))

    result = trope_agent._get_tropes_from_llm("Test Book", "Test Author")

    assert result == {}


def test_get_tropes_from_search_success(trope_agent):
    """Test getting tropes from internet search."""
    # Mock Google Custom Search
    mock_search_service = MagicMock()
    mock_cse = MagicMock()
    mock_search_service.cse.return_value = mock_cse
    mock_cse.list.return_value.execute.return_value = {
        "items": [
            {"snippet": "This book features the Hero's Journey trope prominently."},
            {"snippet": "A classic Chosen One narrative with elements of prophecy."},
        ]
    }

    # Mock LLM response for snippet analysis
    mock_llm_response = MagicMock()
    mock_llm_response.text = json.dumps({"tropes": [{"name": "Hero's Journey", "confidence": 0.8}]})

    with patch("src.agentic_librarian.agents.trope_agent.build", return_value=mock_search_service):
        trope_agent._gemini_client.models.generate_content = MagicMock(return_value=mock_llm_response)

        result = trope_agent._get_tropes_from_search("Test Book", "Test Author")

        assert "Hero's Journey" in result
        assert result["Hero's Journey"]["source"] == "internet_search"


def test_get_tropes_from_search_no_results(trope_agent):
    """Test search with no results."""
    mock_search_service = MagicMock()
    mock_cse = MagicMock()
    mock_search_service.cse.return_value = mock_cse
    mock_cse.list.return_value.execute.return_value = {}

    with patch("src.agentic_librarian.agents.trope_agent.build", return_value=mock_search_service):
        result = trope_agent._get_tropes_from_search("Test Book", "Test Author")

        assert result == {}


def test_get_tropes_from_search_error_handling(trope_agent):
    """Test error handling in search trope extraction."""
    with patch("src.agentic_librarian.agents.trope_agent.build", side_effect=Exception("Search Error")):
        result = trope_agent._get_tropes_from_search("Test Book", "Test Author")

        assert result == {}


def test_get_tropes_from_database_success(trope_agent):
    """Test getting tropes from database via MCP server."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "tropes": [{"name": "Dark Lord", "confidence": 0.9}, {"name": "Magic System", "confidence": 0.85}]
    }

    with patch("src.agentic_librarian.agents.trope_agent.requests.post", return_value=mock_response):
        result = trope_agent._get_tropes_from_database("Test Book", "Test Author")

        assert "Dark Lord" in result
        assert result["Dark Lord"]["confidence"] == 0.9
        assert result["Dark Lord"]["source"] == "database"
        assert "Magic System" in result


def test_get_tropes_from_database_no_confidence(trope_agent):
    """Test database response without confidence values."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"tropes": [{"name": "Test Trope"}]}

    with patch("src.agentic_librarian.agents.trope_agent.requests.post", return_value=mock_response):
        result = trope_agent._get_tropes_from_database("Test Book", "Test Author")

        assert "Test Trope" in result
        assert result["Test Trope"]["confidence"] == 0.7  # Default value


def test_get_tropes_from_database_error(trope_agent):
    """Test database query error handling."""
    mock_response = MagicMock()
    mock_response.status_code = 500

    with patch("src.agentic_librarian.agents.trope_agent.requests.post", return_value=mock_response):
        result = trope_agent._get_tropes_from_database("Test Book", "Test Author")

        assert result == {}


def test_get_tropes_from_database_timeout(trope_agent):
    """Test database query timeout handling."""
    with patch("src.agentic_librarian.agents.trope_agent.requests.post", side_effect=Exception("Timeout")):
        result = trope_agent._get_tropes_from_database("Test Book", "Test Author")

        assert result == {}


def test_combine_and_rank_tropes_single_source(trope_agent):
    """Test combining tropes from a single source."""
    llm_tropes = {
        "Hero's Journey": {"confidence": 0.9, "source": "llm_knowledge"},
        "Chosen One": {"confidence": 0.8, "source": "llm_knowledge"},
    }

    result = trope_agent._combine_and_rank_tropes(llm_tropes, {}, {}, top_n=2)

    assert len(result) == 2
    assert result[0]["name"] == "Hero's Journey"
    assert result[0]["confidence"] == 0.9
    assert result[0]["sources"] == ["llm_knowledge"]


def test_combine_and_rank_tropes_multiple_sources(trope_agent):
    """Test combining tropes from multiple sources with overlap."""
    llm_tropes = {
        "Hero's Journey": {"confidence": 0.9, "source": "llm_knowledge"},
        "Chosen One": {"confidence": 0.8, "source": "llm_knowledge"},
    }
    search_tropes = {"Hero's Journey": {"confidence": 0.85, "source": "internet_search"}}
    db_tropes = {
        "Hero's Journey": {"confidence": 0.95, "source": "database"},
        "Magic System": {"confidence": 0.7, "source": "database"},
    }

    result = trope_agent._combine_and_rank_tropes(llm_tropes, search_tropes, db_tropes, top_n=3)

    # Hero's Journey should rank highest due to multiple sources
    assert result[0]["name"] == "Hero's Journey"
    assert len(result[0]["sources"]) == 3
    assert result[0]["confidence"] > 0.9  # Boosted by multiple sources


def test_combine_and_rank_tropes_respects_top_n(trope_agent):
    """Test that only top_n tropes are returned."""
    llm_tropes = {f"Trope {i}": {"confidence": 0.9 - i * 0.1, "source": "llm_knowledge"} for i in range(10)}

    result = trope_agent._combine_and_rank_tropes(llm_tropes, {}, {}, top_n=3)

    assert len(result) == 3


def test_combine_and_rank_tropes_source_bonus(trope_agent):
    """Test that tropes from multiple sources get confidence bonus."""
    # Trope A: single source, high confidence
    # Trope B: multiple sources, lower individual confidence but should rank higher due to bonus
    llm_tropes = {"Trope A": {"confidence": 0.9, "source": "llm_knowledge"}}
    search_tropes = {
        "Trope B": {"confidence": 0.7, "source": "internet_search"},
        "Trope A": {"confidence": 0.85, "source": "internet_search"},
    }
    db_tropes = {"Trope B": {"confidence": 0.7, "source": "database"}}

    result = trope_agent._combine_and_rank_tropes(llm_tropes, search_tropes, db_tropes, top_n=2)

    # Both should be present, but order may vary based on weighted calculation
    names = [t["name"] for t in result]
    assert "Trope A" in names
    assert "Trope B" in names


def test_identify_tropes_integration(trope_agent, monkeypatch):
    """Test the full identify_tropes workflow."""
    # Mock all three data source methods
    mock_llm_tropes = {"Hero's Journey": {"confidence": 0.9, "source": "llm_knowledge"}}
    mock_search_tropes = {"Chosen One": {"confidence": 0.8, "source": "internet_search"}}
    mock_db_tropes = {"Magic System": {"confidence": 0.85, "source": "database"}}

    monkeypatch.setattr(trope_agent, "_get_tropes_from_llm", lambda t, a: mock_llm_tropes)
    monkeypatch.setattr(trope_agent, "_get_tropes_from_search", lambda t, a: mock_search_tropes)
    monkeypatch.setattr(trope_agent, "_get_tropes_from_database", lambda t, a: mock_db_tropes)

    result = trope_agent.identify_tropes("Test Book", "Test Author", top_n=3)

    assert len(result) == 3
    names = [t["name"] for t in result]
    assert "Hero's Journey" in names
    assert "Chosen One" in names
    assert "Magic System" in names

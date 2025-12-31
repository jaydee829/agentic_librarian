"""
Simple integration test to verify TropeAgent works end-to-end.

This test requires environment variables to be set:
- GOOGLE_SEARCH_API_KEY
- SEARCH_ENGINE_ID
- MCP_SERVER_URL

Run with: python -m pytest test/test_trope_agent_integration.py -v -s
"""

import json
import os

import pytest

# Skip all tests in this file if integration tests are disabled
pytestmark = pytest.mark.integration


@pytest.fixture
def check_env_vars():
    """Check if required environment variables are set."""
    required_vars = ["GOOGLE_SEARCH_API_KEY", "SEARCH_ENGINE_ID", "MCP_SERVER_URL"]
    missing = [var for var in required_vars if not os.environ.get(var)]

    if missing:
        pytest.skip(f"Missing required environment variables: {', '.join(missing)}")


def test_trope_agent_a2a_protocol_integration(check_env_vars):
    """Test TropeAgent with real A2A protocol request (requires live APIs)."""
    from src.agentic_librarian.agents.trope_agent import TropeAgent

    # Create agent
    agent = TropeAgent()

    # Create A2A protocol request
    request = {"title": "The Hobbit", "author": "J.R.R. Tolkien", "top_n": 3}

    print("\n\nA2A Request:")
    print(json.dumps(request, indent=2))

    # Process request
    response = agent.process_request(request)

    print("\nA2A Response:")
    print(json.dumps(response, indent=2))

    # Verify response structure
    assert response is not None
    assert "status" in response
    assert response["status"] == "success"
    assert "tropes" in response
    assert isinstance(response["tropes"], list)
    assert len(response["tropes"]) <= 3

    # Verify trope structure
    for trope in response["tropes"]:
        assert "name" in trope
        assert "confidence" in trope
        assert "sources" in trope
        assert isinstance(trope["name"], str)
        assert isinstance(trope["confidence"], float)
        assert isinstance(trope["sources"], list)
        assert 0.0 <= trope["confidence"] <= 1.0
        assert len(trope["sources"]) > 0

    print(f"\n✓ Successfully identified {len(response['tropes'])} tropes")
    for trope in response["tropes"]:
        print(f"  - {trope['name']}: {trope['confidence']:.2%} (from {len(trope['sources'])} sources)")


def test_trope_agent_error_handling_integration(check_env_vars):
    """Test TropeAgent error handling with A2A protocol (requires live APIs)."""
    from src.agentic_librarian.agents.trope_agent import TropeAgent

    agent = TropeAgent()

    # Test missing title
    request = {"author": "J.R.R. Tolkien"}
    response = agent.process_request(request)

    assert response["status"] == "error"
    assert "message" in response
    assert "title" in response["message"].lower()

    print("\n✓ Error handling works correctly")


def test_json_roundtrip():
    """Test that requests and responses can be serialized/deserialized as JSON."""
    # Test request serialization
    request = {"title": "Test Book", "author": "Test Author", "top_n": 5}

    json_str = json.dumps(request)
    parsed_request = json.loads(json_str)

    assert parsed_request == request

    # Test response serialization
    response = {
        "status": "success",
        "tropes": [
            {"name": "Hero's Journey", "confidence": 0.95, "sources": ["llm_knowledge", "internet_search"]},
            {"name": "Chosen One", "confidence": 0.85, "sources": ["llm_knowledge"]},
        ],
    }

    json_str = json.dumps(response)
    parsed_response = json.loads(json_str)

    assert parsed_response == response

    print("\n✓ JSON serialization/deserialization works correctly")

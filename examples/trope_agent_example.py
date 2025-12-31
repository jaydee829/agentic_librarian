#!/usr/bin/env python3
"""
Example usage of the TropeAgent with A2A protocol.

This script demonstrates how to use the TropeAgent to identify literary tropes
in a book using JSON-based A2A (Agent-to-Agent) communication.
"""

import json
import os
import sys

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentic_librarian.agents import TropeAgent


def main():
    """Example usage of TropeAgent with A2A protocol."""

    # Example 1: Basic usage with A2A request
    print("=" * 60)
    print("Example 1: Identifying tropes with A2A protocol")
    print("=" * 60)

    # Create A2A protocol request
    request = {"title": "The Way of Kings", "author": "Brandon Sanderson", "top_n": 5}

    print("\nA2A Request:")
    print(json.dumps(request, indent=2))

    try:
        # Initialize agent
        agent = TropeAgent()

        # Process A2A request
        response = agent.process_request(request)

        print("\nA2A Response:")
        print(json.dumps(response, indent=2))

        if response["status"] == "success":
            print("\nIdentified Tropes:")
            for i, trope in enumerate(response["tropes"], 1):
                print(f"\n{i}. {trope['name']}")
                print(f"   Confidence: {trope['confidence']:.2%}")
                print(f"   Sources: {', '.join(trope['sources'])}")

    except ValueError as e:
        print(f"\nError: {e}")
        print("\nPlease ensure the following environment variables are set:")
        print("  - GOOGLE_SEARCH_API_KEY")
        print("  - SEARCH_ENGINE_ID")
        print("  - MCP_SERVER_URL")
        return

    # Example 2: Error handling with missing fields
    print("\n" + "=" * 60)
    print("Example 2: Error handling - missing author")
    print("=" * 60)

    invalid_request = {"title": "The Way of Kings", "top_n": 3}

    print("\nA2A Request:")
    print(json.dumps(invalid_request, indent=2))

    try:
        agent = TropeAgent()
        response = agent.process_request(invalid_request)

        print("\nA2A Response:")
        print(json.dumps(response, indent=2))

    except ValueError:
        pass  # Already handled above

    # Example 3: Different book
    print("\n" + "=" * 60)
    print("Example 3: Analyzing another book")
    print("=" * 60)

    another_request = {"title": "1984", "author": "George Orwell", "top_n": 3}

    print("\nA2A Request:")
    print(json.dumps(another_request, indent=2))

    try:
        agent = TropeAgent()
        response = agent.process_request(another_request)

        print("\nA2A Response:")
        print(json.dumps(response, indent=2))

    except ValueError:
        pass


if __name__ == "__main__":
    main()

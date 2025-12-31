"""
Trope identification agent for books.

This agent identifies literary tropes in books using multiple data sources:
- LLM knowledge base
- Internet search
- PostgreSQL database via MCP server

The agent complies with A2A (Agent-to-Agent) protocol for JSON-based communication.
"""

import json
import os
from typing import Any

import requests
from google import genai
from googleapiclient.discovery import build

# Load environment variables from .env if present (for local dev)
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


class TropeAgent:
    """
    Agent for identifying literary tropes in books.

    This agent uses a multi-source approach to identify tropes:
    1. LLM knowledge (via Gemini)
    2. Internet search (Google Custom Search)
    3. PostgreSQL database (via MCP server)

    The agent follows A2A protocol with JSON input/output.
    """

    def __init__(self, mcp_server_url: str = None):
        """
        Initialize the TropeAgent.

        Args:
            mcp_server_url (str, optional): URL of the PostgreSQL MCP server.
                Defaults to environment variable MCP_SERVER_URL.
        """
        # Initialize Gemini client for LLM knowledge
        self._gemini_api_key = os.environ.get("GOOGLE_SEARCH_API_KEY")
        if not self._gemini_api_key:
            raise ValueError("GOOGLE_SEARCH_API_KEY environment variable not set")

        self._gemini_client = genai.Client(api_key=self._gemini_api_key)

        # Initialize Google Custom Search for internet search
        self._search_engine_id = os.environ.get("SEARCH_ENGINE_ID")
        if not self._search_engine_id:
            raise ValueError("SEARCH_ENGINE_ID environment variable not set")

        # Initialize MCP server connection
        self._mcp_server_url = mcp_server_url or os.environ.get("MCP_SERVER_URL")
        if not self._mcp_server_url:
            raise ValueError("MCP_SERVER_URL environment variable not set")

    def process_request(self, request_data: dict) -> dict:
        """
        Process an A2A protocol request to identify book tropes.

        This is the main entry point for A2A communication.

        Args:
            request_data (dict): A2A protocol request with structure:
                {
                    "title": str,
                    "author": str,
                    "top_n": int (optional, defaults to 5)
                }

        Returns:
            dict: A2A protocol response with structure:
                {
                    "status": "success" | "error",
                    "tropes": [
                        {
                            "name": str,
                            "confidence": float,
                            "sources": [str]
                        }
                    ],
                    "message": str (optional, for errors)
                }
        """
        try:
            # Extract parameters
            title = request_data.get("title")
            author = request_data.get("author")
            top_n = request_data.get("top_n", 5)

            # Validate input
            if not title or not author:
                return {
                    "status": "error",
                    "message": "Both 'title' and 'author' are required fields",
                }

            # Identify tropes
            tropes = self.identify_tropes(title, author, top_n)

            return {"status": "success", "tropes": tropes}

        except Exception as e:
            return {"status": "error", "message": f"Error processing request: {str(e)}"}

    def identify_tropes(self, title: str, author: str, top_n: int = 5) -> list[dict[str, Any]]:
        """
        Identify the top n tropes in a book using multiple data sources.

        Args:
            title (str): Book title
            author (str): Book author
            top_n (int): Number of top tropes to return (default: 5)

        Returns:
            list[dict]: List of trope dictionaries with name, confidence, and sources
        """
        # Gather tropes from all sources
        llm_tropes = self._get_tropes_from_llm(title, author)
        search_tropes = self._get_tropes_from_search(title, author)
        db_tropes = self._get_tropes_from_database(title, author)

        # Combine and rank tropes
        combined_tropes = self._combine_and_rank_tropes(llm_tropes, search_tropes, db_tropes, top_n)

        return combined_tropes

    def _get_tropes_from_llm(self, title: str, author: str) -> dict[str, dict]:
        """
        Get tropes from LLM knowledge base.

        Args:
            title (str): Book title
            author (str): Book author

        Returns:
            dict: Dictionary of tropes with confidence scores and sources
        """
        prompt = f"""
        Analyze the book "{title}" by {author} and identify the major literary tropes present.
        A trope is a common or recurring literary device, theme, motif, or cliché.

        Return ONLY a raw JSON object with this structure:
        {{
            "tropes": [
                {{
                    "name": "trope name",
                    "confidence": 0.0-1.0,
                    "description": "brief description"
                }}
            ]
        }}

        Focus on well-known tropes like:
        - Hero's Journey
        - Chosen One
        - Enemies to Lovers
        - Dark Lord
        - Prophecy
        - Coming of Age
        - Redemption Arc
        - Found Family
        - MacGuffin
        - Love Triangle
        etc.
        """

        try:
            response = self._gemini_client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)

            text = response.text.strip()
            if text.startswith("```json"):
                text = text.replace("```json", "").replace("```", "").strip()

            data = json.loads(text)
            tropes = {}
            for trope in data.get("tropes", []):
                tropes[trope["name"]] = {"confidence": trope["confidence"], "source": "llm_knowledge"}
            return tropes
        except Exception as e:
            print(f"Error getting tropes from LLM: {e}")
            return {}

    def _get_tropes_from_search(self, title: str, author: str) -> dict[str, dict]:
        """
        Get tropes from internet search results.

        Args:
            title (str): Book title
            author (str): Book author

        Returns:
            dict: Dictionary of tropes with confidence scores and sources
        """
        try:
            # Search for tropes mentioned in web results
            service = build("customsearch", "v1", developerKey=self._gemini_api_key)
            search_results = (
                service.cse()
                .list(
                    q=f'"{title}" "{author}" tropes themes literary devices',
                    cx=self._search_engine_id,
                    num=5,
                )
                .execute()
            )

            # Extract snippets and analyze for tropes
            snippets = []
            if "items" in search_results:
                for item in search_results["items"]:
                    snippet = item.get("snippet", "")
                    snippets.append(snippet)

            # Use LLM to extract tropes from search snippets
            if snippets:
                combined_text = "\n\n".join(snippets)
                prompt = f"""
                From the following search results about "{title}" by {author}, extract literary tropes mentioned.
                Return ONLY a raw JSON object:
                {{
                    "tropes": [
                        {{
                            "name": "trope name",
                            "confidence": 0.0-1.0
                        }}
                    ]
                }}

                Search results:
                {combined_text[:5000]}
                """

                response = self._gemini_client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)

                text = response.text.strip()
                if text.startswith("```json"):
                    text = text.replace("```json", "").replace("```", "").strip()

                data = json.loads(text)
                tropes = {}
                for trope in data.get("tropes", []):
                    tropes[trope["name"]] = {"confidence": trope["confidence"], "source": "internet_search"}
                return tropes

            return {}
        except Exception as e:
            print(f"Error getting tropes from search: {e}")
            return {}

    def _get_tropes_from_database(self, title: str, author: str) -> dict[str, dict]:
        """
        Get tropes from PostgreSQL database via MCP server.

        Args:
            title (str): Book title
            author (str): Book author

        Returns:
            dict: Dictionary of tropes with confidence scores and sources
        """
        try:
            # Query MCP server for tropes associated with this book
            response = requests.post(
                f"{self._mcp_server_url}/query",
                json={"title": title, "author": author, "query_type": "tropes"},
                timeout=10,
            )

            if response.status_code == 200:
                data = response.json()
                tropes = {}
                for trope in data.get("tropes", []):
                    tropes[trope["name"]] = {"confidence": trope.get("confidence", 0.7), "source": "database"}
                return tropes

            return {}
        except Exception as e:
            print(f"Error getting tropes from database: {e}")
            return {}

    def _combine_and_rank_tropes(
        self,
        llm_tropes: dict[str, dict],
        search_tropes: dict[str, dict],
        db_tropes: dict[str, dict],
        top_n: int,
    ) -> list[dict[str, Any]]:
        """
        Combine tropes from all sources and rank them.

        Args:
            llm_tropes (dict): Tropes from LLM
            search_tropes (dict): Tropes from internet search
            db_tropes (dict): Tropes from database
            top_n (int): Number of top tropes to return

        Returns:
            list[dict]: Ranked list of top n tropes
        """
        # Combine all tropes
        all_tropes = {}

        # Process each source
        for trope_dict, weight in [
            (llm_tropes, 1.0),  # LLM knowledge gets base weight
            (search_tropes, 0.8),  # Search results slightly lower
            (db_tropes, 1.2),  # Database gets higher weight (previously identified)
        ]:
            for trope_name, trope_data in trope_dict.items():
                if trope_name not in all_tropes:
                    all_tropes[trope_name] = {
                        "name": trope_name,
                        "confidence": 0.0,
                        "sources": [],
                        "count": 0,
                    }

                # Weighted confidence accumulation
                all_tropes[trope_name]["confidence"] += trope_data["confidence"] * weight
                all_tropes[trope_name]["sources"].append(trope_data["source"])
                all_tropes[trope_name]["count"] += 1

        # Normalize confidence by number of sources and calculate final score
        for _trope_name, trope_data in all_tropes.items():
            # Average confidence, with bonus for multiple sources
            base_confidence = trope_data["confidence"] / trope_data["count"]
            source_bonus = min(0.2, (trope_data["count"] - 1) * 0.1)  # Up to 0.2 bonus
            trope_data["confidence"] = min(1.0, base_confidence + source_bonus)

            # Clean up temporary fields
            del trope_data["count"]

        # Sort by confidence and return top n
        ranked_tropes = sorted(all_tropes.values(), key=lambda x: x["confidence"], reverse=True)

        return ranked_tropes[:top_n]

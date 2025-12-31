# Examples

This directory contains example usage scripts for the agentic_librarian package.

## TropeAgent Example

**File:** `trope_agent_example.py`

Demonstrates how to use the TropeAgent with A2A protocol to identify literary tropes in books.

### Requirements

Set the following environment variables:

```bash
export GOOGLE_SEARCH_API_KEY="your-google-api-key"
export MCP_SERVER_URL="http://your-mcp-server:port"
```

### Running the Example

```bash
python examples/trope_agent_example.py
```

### What It Does

1. Creates an A2A protocol request with book title and author
2. Processes the request using the TropeAgent
3. Displays identified tropes with confidence scores and sources
4. Demonstrates error handling for invalid requests
5. Shows multiple book analyses

### Expected Output

```
============================================================
Example 1: Identifying tropes with A2A protocol
============================================================

A2A Request:
{
  "title": "The Way of Kings",
  "author": "Brandon Sanderson",
  "top_n": 5
}

A2A Response:
{
  "status": "success",
  "tropes": [
    {
      "name": "Hero's Journey",
      "confidence": 0.95,
      "sources": ["llm_knowledge", "internet_search", "database"]
    },
    ...
  ]
}

Identified Tropes:

1. Hero's Journey
   Confidence: 95.00%
   Sources: llm_knowledge, internet_search, database
...
```

## Adding More Examples

When adding new examples to this directory:

1. Follow the naming convention: `<feature>_example.py`
2. Include clear documentation at the top of the file
3. Add error handling and user-friendly output
4. Document required environment variables
5. Add an entry to this README

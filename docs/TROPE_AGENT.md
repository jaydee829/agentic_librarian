# TropeAgent Documentation

## Overview

The `TropeAgent` is an A2A (Agent-to-Agent) protocol-compliant agent that identifies literary tropes in books. It uses a multi-source approach combining:

1. **LLM Knowledge**: Gemini AI's knowledge base for common literary tropes
2. **Internet Search**: Google Custom Search to find trope mentions in reviews and analyses
3. **PostgreSQL Database**: Previously identified tropes via MCP (Model Context Protocol) server

## Features

- **A2A Protocol Compliance**: JSON-based request/response format
- **Multi-source Intelligence**: Combines information from three different sources
- **Confidence Scoring**: Each trope includes a confidence score (0.0-1.0)
- **Source Attribution**: Tracks which sources identified each trope
- **Weighted Ranking**: Prioritizes tropes found in multiple sources

## Installation

```bash
# Install the agentic-librarian package
uv pip install -e .

# Or with development dependencies
uv pip install -e ".[dev]"
```

## Configuration

The TropeAgent requires the following environment variables:

```bash
# Google API credentials for search and Gemini
export GOOGLE_SEARCH_API_KEY="your-google-api-key"
export SEARCH_ENGINE_ID="your-custom-search-engine-id"

# MCP server URL for PostgreSQL access
export MCP_SERVER_URL="http://your-mcp-server:port"
```

### Setting up Google Custom Search

1. Create a Google Cloud project
2. Enable the Custom Search API
3. Create API credentials
4. Set up a Custom Search Engine at https://cse.google.com
5. Note your Search Engine ID (cx parameter)

### Setting up MCP Server

The agent expects an MCP server that provides a `/query` endpoint with the following interface:

**Request:**
```json
{
  "title": "Book Title",
  "author": "Author Name",
  "query_type": "tropes"
}
```

**Response:**
```json
{
  "tropes": [
    {
      "name": "Trope Name",
      "confidence": 0.85
    }
  ]
}
```

## Usage

### Basic Usage

```python
from agentic_librarian.agents import TropeAgent

# Initialize the agent
agent = TropeAgent()

# Create an A2A protocol request
request = {
    "title": "The Way of Kings",
    "author": "Brandon Sanderson",
    "top_n": 5  # Optional, defaults to 5
}

# Process the request
response = agent.process_request(request)

# Handle the response
if response["status"] == "success":
    for trope in response["tropes"]:
        print(f"{trope['name']}: {trope['confidence']:.2%}")
        print(f"Sources: {', '.join(trope['sources'])}")
else:
    print(f"Error: {response['message']}")
```

### A2A Protocol Request Format

```json
{
  "title": "string (required)",
  "author": "string (required)",
  "top_n": "integer (optional, default: 5)"
}
```

### A2A Protocol Response Format

**Success Response:**
```json
{
  "status": "success",
  "tropes": [
    {
      "name": "Hero's Journey",
      "confidence": 0.95,
      "sources": ["llm_knowledge", "internet_search", "database"]
    },
    {
      "name": "Chosen One",
      "confidence": 0.87,
      "sources": ["llm_knowledge", "database"]
    }
  ]
}
```

**Error Response:**
```json
{
  "status": "error",
  "message": "Error description"
}
```

## API Reference

### TropeAgent

The main agent class for trope identification.

#### `__init__(mcp_server_url: str = None)`

Initialize the TropeAgent.

**Parameters:**
- `mcp_server_url` (str, optional): URL of the PostgreSQL MCP server. Defaults to `MCP_SERVER_URL` environment variable.

**Raises:**
- `ValueError`: If required environment variables are not set.

#### `process_request(request_data: dict) -> dict`

Process an A2A protocol request to identify book tropes.

**Parameters:**
- `request_data` (dict): A2A protocol request containing `title`, `author`, and optionally `top_n`.

**Returns:**
- dict: A2A protocol response with status and tropes or error message.

#### `identify_tropes(title: str, author: str, top_n: int = 5) -> list[dict]`

Identify the top n tropes in a book using multiple data sources.

**Parameters:**
- `title` (str): Book title
- `author` (str): Book author
- `top_n` (int): Number of top tropes to return (default: 5)

**Returns:**
- list[dict]: List of trope dictionaries with name, confidence, and sources.

## Common Tropes

The agent can identify various literary tropes, including but not limited to:

- **Hero's Journey**: Classic monomyth narrative structure
- **Chosen One**: Protagonist with special destiny
- **Enemies to Lovers**: Antagonistic relationship that becomes romantic
- **Dark Lord**: Primary antagonist with significant power
- **Prophecy**: Prediction that drives the narrative
- **Coming of Age**: Character's transition to maturity
- **Redemption Arc**: Character seeking to atone for past actions
- **Found Family**: Non-biological family bonds
- **MacGuffin**: Object that drives the plot
- **Love Triangle**: Romantic tension between three characters
- **Magic System**: Rules and mechanics of supernatural powers
- **Mentor's Death**: Loss of guiding figure
- **Reluctant Hero**: Protagonist who resists their role
- **Fish Out of Water**: Character in unfamiliar setting
- **Secret Identity**: Hidden nature of character

## How It Works

### Multi-Source Trope Identification

1. **LLM Knowledge**: Uses Gemini AI to analyze the book based on its training data
2. **Internet Search**: Searches for reviews, analyses, and discussions mentioning tropes
3. **Database Query**: Retrieves previously identified tropes from PostgreSQL

### Confidence Scoring

The agent uses a weighted confidence system:

- **LLM Knowledge**: 1.0x weight (base confidence)
- **Internet Search**: 0.8x weight (slightly lower due to potential noise)
- **Database**: 1.2x weight (higher due to verified previous identifications)

Tropes found in multiple sources receive a bonus to their confidence score (up to 0.2 additional).

### Ranking Algorithm

1. Aggregate confidence scores from all sources with weights
2. Average by number of sources
3. Add bonus for multi-source confirmation
4. Sort by final confidence score
5. Return top N tropes

## Examples

See `examples/trope_agent_example.py` for complete examples including:
- Basic usage with A2A protocol
- Error handling
- Multiple book analyses

Run the example:
```bash
python examples/trope_agent_example.py
```

## Testing

Run the test suite:

```bash
# Run all trope agent tests
pytest test/test_trope_agent.py

# Run with verbose output
pytest test/test_trope_agent.py -v

# Run specific test
pytest test/test_trope_agent.py::test_process_request_success
```

## Limitations

- Requires active internet connection for search functionality
- API rate limits apply to Google services
- MCP server must be running and accessible
- Accuracy depends on book's presence in training data and online discussions
- Some tropes may be subjective or context-dependent

## Future Enhancements

Potential improvements:
- Support for additional trope databases (TVTropes, etc.)
- Caching of frequently requested books
- Batch processing for multiple books
- Trope relationship mapping
- Custom trope definitions
- Support for other media types (movies, TV shows)

## Troubleshooting

### "GOOGLE_SEARCH_API_KEY environment variable not set"

Ensure you have set the `GOOGLE_SEARCH_API_KEY` environment variable with a valid Google API key.

### "SEARCH_ENGINE_ID environment variable not set"

Set up a Google Custom Search Engine and configure the `SEARCH_ENGINE_ID` environment variable.

### "MCP_SERVER_URL environment variable not set"

Configure the `MCP_SERVER_URL` to point to your running MCP server instance.

### Low confidence scores

This may indicate:
- The book is not well-known or lacks online discussion
- The tropes are subtle or unconventional
- The MCP database doesn't have entries for this book

### Empty results

Check that:
- All API credentials are valid
- The MCP server is running and accessible
- The book title and author are spelled correctly
- The book exists in available data sources

## Contributing

To contribute to the TropeAgent:

1. Follow the existing code style
2. Add tests for new features
3. Update documentation
4. Run linting: `ruff check .`
5. Run tests: `pytest test/test_trope_agent.py`

## License

This is part of the agentic-librarian project. See the main project license for details.

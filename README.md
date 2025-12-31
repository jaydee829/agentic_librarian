# agentic_librarian
An agentic book recommender system

## Features

### TropeAgent

An A2A (Agent-to-Agent) protocol-compliant agent for identifying literary tropes in books. The agent uses multiple data sources:

- **LLM Knowledge**: Gemini AI for common literary tropes
- **Internet Search**: Google Custom Search for trope mentions
- **PostgreSQL Database**: Previously identified tropes via MCP server

See [TropeAgent Documentation](docs/TROPE_AGENT.md) for detailed usage instructions.

#### Quick Start

```python
from agentic_librarian.agents import TropeAgent

# Initialize the agent
agent = TropeAgent()

# Create an A2A protocol request
request = {
    "title": "The Way of Kings",
    "author": "Brandon Sanderson",
    "top_n": 5
}

# Process the request
response = agent.process_request(request)

# Handle the response
if response["status"] == "success":
    for trope in response["tropes"]:
        print(f"{trope['name']}: {trope['confidence']:.2%}")
```

## Development Environment

### Docker Setup

Build the Docker image:

```bash
docker build -t agentic-librarian .
```

Run the container:

```bash
docker run -it agentic-librarian
```

### Dependencies

This project uses [uv](https://github.com/astral-sh/uv) for fast dependency management. Dependencies are defined in `pyproject.toml`.

To install dependencies:

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install project dependencies
uv pip install -e .

# Install development dependencies
uv pip install -e ".[dev]"
```

The development environment includes the following packages:

- **ML/Data Science**: mlflow, pandas, scikit-learn, PyTorch
- **API/Web**: requests, google-api-python-client, firecrawl-py
- **Audio/Books**: audible
- **LLM**: openai, langchain, langchain-openai

### Code Quality

This project uses [ruff](https://github.com/astral-sh/ruff) for fast Python linting and formatting. Linting is automatically run on every commit and pull request via GitHub Actions.

To run linting locally:

```bash
# Install uv (fast Python package installer)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install ruff
uv pip install ruff

# Run linting
ruff check .

# Run formatting check
ruff format --check .

# Auto-fix issues
ruff check --fix .

# Auto-format code
ruff format .
```

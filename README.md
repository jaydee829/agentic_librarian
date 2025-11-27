# agentic_librarian
An agentic book recommender system

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

The development environment includes the following packages:

- **ML/Data Science**: mlflow, pandas, scikit-learn, PyTorch
- **API/Web**: requests, google-api-python-client, firecrawl-py
- **Audio/Books**: audible
- **LLM**: openai, langchain, langchain-openai

### Code Quality

This project uses flake8 and pylint for PEP8 style checking. Linting is automatically run on every commit and pull request via GitHub Actions.

To run linting locally:

```bash
pip install flake8 pylint
flake8 .
find . -name '*.py' -not -path "./.git/*" -exec pylint {} +
```

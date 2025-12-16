# Python development environment for book recommendation system
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies and uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

# Add uv to PATH
ENV PATH="/root/.cargo/bin:$PATH"

# Copy pyproject.toml first for better caching
COPY pyproject.toml .

# Install Python dependencies with uv
RUN uv pip install --system -e ".[dev]"

# Copy the rest of the application
COPY . .

# Default command
CMD ["python", "--version"]

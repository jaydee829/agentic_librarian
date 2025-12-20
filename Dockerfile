# Python development environment for book recommendation system
FROM python:3.11

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create a non-root user and set up home directory
RUN useradd --create-home appuser

# Set working directory
WORKDIR /app

# Install system dependencies and uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && if [ -f /root/.cargo/bin/uv ]; then ln -sf /root/.cargo/bin/uv /usr/local/bin/uv; fi \
    && if [ -f /root/.local/bin/uv ]; then ln -sf /root/.local/bin/uv /usr/local/bin/uv; fi

# Add both possible uv install locations to PATH
ENV PATH="/root/.cargo/bin:/root/.local/bin:$PATH"

# Copy pyproject.toml first for better caching
COPY pyproject.toml .

# Install Python dependencies with uv
RUN uv pip install --system -e ".[dev]"

# Copy the rest of the application
COPY . .

# Install and configure en_US.UTF-8 locale
RUN apt-get update && apt-get install -y locales \
    && echo "en_US.UTF-8 UTF-8" > /etc/locale.gen \
    && locale-gen
ENV LANG=en_US.UTF-8
ENV LANGUAGE=en_US:en
ENV LC_ALL=en_US.UTF-8

# add appuser to sudoers with no password
RUN apt-get update && apt-get install -y sudo \
    && usermod -aG sudo appuser \
    && echo "appuser ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Switch to non-root user
USER appuser

# Default command
CMD ["python", "--version"]

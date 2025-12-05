# Python development environment for book recommendation system
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1


# Create a non-root user and set up home directory
RUN useradd --create-home appuser

# Set working directory
WORKDIR /app


# Install system dependencies and locales
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    git \
    locales \
    && rm -rf /var/lib/apt/lists/*

# Generate en_US.UTF-8 locale
RUN echo 'en_US.UTF-8 UTF-8' >> /etc/locale.gen && \
    locale-gen

# Set locale environment variables
ENV LANG en_US.UTF-8
ENV LANGUAGE en_US:en
ENV LC_ALL en_US.UTF-8

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt


# Copy the rest of the application
COPY . .

# Change ownership of the app directory to the non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Default command
CMD ["python", "--version"]

# Single image for every Python service in the stack — MCP servers, API, eval harness.
# Entrypoint is parameterised by docker-compose `command`.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps for psycopg + pgvector compilation.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip setuptools wheel \
    && pip install -e .

COPY . .

# Default command is overridden per-service in docker-compose.
CMD ["python", "-c", "print('Override me in docker-compose command:')"]

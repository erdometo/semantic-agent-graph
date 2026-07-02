# Stage 1: Build the React static frontend bundle
FROM node:18-alpine AS frontend-builder
WORKDIR /app/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

# Stage 2: Create a production Python runtime environment
FROM python:3.10-slim AS backend-builder

# Configure Python and Poetry environments
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

# Copy python dependencies spec
COPY pyproject.toml poetry.lock ./

# Install main production dependencies
RUN poetry install --no-root --only main

# Copy backend application source
COPY semantic_agent_graph ./semantic_agent_graph

# Copy built frontend assets from Stage 1
COPY --from=frontend-builder /app/dashboard/dist ./dashboard/dist

# Expose server port
EXPOSE 8000

# Start server serving both API and static frontend
CMD ["uvicorn", "semantic_agent_graph.api:app", "--host", "0.0.0.0", "--port", "8000"]

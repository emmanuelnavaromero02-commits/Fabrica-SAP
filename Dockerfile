# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS app
RUN apt-get update && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 fabrica
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src
RUN uv pip install --system --no-cache .

COPY config /app/config
COPY --from=web /web/dist /app/web
RUN mkdir -p /data && chown fabrica /data
ENV FABRICA_CONFIG_DIR=/app/config \
    FABRICA_DATA_DIR=/data \
    FABRICA_WEB_DIR=/app/web \
    PYTHONUNBUFFERED=1

USER fabrica
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=12 CMD curl -fsS http://localhost:8000/health || exit 1
CMD ["fabrica-api"]

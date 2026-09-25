#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export FABRICA_DATABASE_URL="${FABRICA_DATABASE_URL:-postgresql+asyncpg://fabrica:fabrica@localhost:5432/fabrica}"
export FABRICA_AUTO_SCHEMA=false
export FABRICA_RUNNER=temporal
export FABRICA_TEMPORAL_HOST=localhost:7233
export FABRICA_AUTH_MODE=oidc
export FABRICA_OIDC_ISSUER=http://localhost:8080/realms/fabrica
export FABRICA_KEYCLOAK_URL=http://localhost:8080
export FABRICA_KEYCLOAK_ADMIN="${KEYCLOAK_ADMIN:-admin}"
export FABRICA_KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-admin}"

docker compose up -d --wait postgres temporal keycloak

(cd backend && uv sync --quiet --extra dev && uv run fabrica-migrate upgrade)
(cd web && [ -d node_modules ] || npm install --no-audit --no-fund)

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

(cd backend && uv run uvicorn fabrica.api.app:app --reload --port 8000) & pids+=($!)
(cd backend && uv run fabrica-worker) & pids+=($!)
(cd web && npm run dev) & pids+=($!)

wait -n "${pids[@]}"

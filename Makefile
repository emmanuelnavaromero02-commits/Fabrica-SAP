.PHONY: install api web worker test lint check up down mcp-fabrica mcp-sap

BACKEND := cd backend &&

install:            ## Instala backend (uv) y frontend (npm)
	$(BACKEND) uv sync --extra dev
	cd web && npm install

api:                ## API en http://localhost:8000 (modo inline + mock por defecto)
	$(BACKEND) uv run uvicorn fabrica.api.app:app --reload --port 8000

web:                ## Web en http://localhost:5173
	cd web && npm run dev

worker:             ## Worker de Temporal (requiere FABRICA_RUNNER=temporal)
	$(BACKEND) uv run fabrica-worker

mcp-fabrica:        ## MCP del tablero por stdio
	$(BACKEND) uv run fabrica-mcp-fabrica

mcp-sap:            ## MCP del Puente SAP por stdio
	$(BACKEND) uv run fabrica-mcp-sap

test:               ## Pruebas del backend
	$(BACKEND) uv run pytest -q

lint:               ## Lint, formato y tipos (backend y frontend)
	$(BACKEND) uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
	cd web && npm run typecheck

check: lint test    ## Todo lo que corre CI

up:                 ## Entorno completo con Docker (Postgres, Temporal, API, worker, web)
	docker compose up --build

down:
	docker compose down

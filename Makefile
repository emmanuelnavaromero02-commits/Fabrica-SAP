.PHONY: install api web worker test lint check up down mcp-fabrica mcp-sap mcp-conocimiento

BACKEND := cd backend &&

install:
	$(BACKEND) uv sync --extra dev
	cd web && npm install

api:
	$(BACKEND) uv run uvicorn fabrica.api.app:app --reload --port 8000

web:
	cd web && npm run dev

worker:
	$(BACKEND) uv run fabrica-worker

mcp-fabrica:
	$(BACKEND) uv run fabrica-mcp-fabrica

mcp-sap:
	$(BACKEND) uv run fabrica-mcp-sap

mcp-conocimiento:
	$(BACKEND) uv run fabrica-mcp-conocimiento

test:
	$(BACKEND) uv run pytest -q

lint:
	$(BACKEND) uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
	cd web && npm run typecheck

check: lint test

up:
	docker compose up --build

down:
	docker compose down

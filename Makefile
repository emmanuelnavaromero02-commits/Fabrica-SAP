.PHONY: install dev infra up down logs ps usuario replay test lint check

BACKEND := cd backend &&
USUARIO ?=
EMAIL ?=
ROL ?= funcional
CASOS ?=

install:
	$(BACKEND) uv sync --extra dev
	cd web && npm install

dev:
	./scripts/dev.sh

infra:
	docker compose up -d --wait postgres temporal keycloak

up:
	docker compose up -d --build --wait
	@docker compose ps

down:
	docker compose down

logs:
	docker compose logs -f api worker

ps:
	docker compose ps

usuario:
	docker compose exec api fabrica-usuario --usuario $(USUARIO) --email $(EMAIL) $(foreach r,$(ROL),--rol $(r))

replay:
	docker compose exec -T api fabrica-replay /dev/stdin < $(CASOS)

test:
	$(BACKEND) uv run pytest -q

lint:
	$(BACKEND) uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
	cd web && npm run typecheck

check: lint test

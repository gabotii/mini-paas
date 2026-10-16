# Mini PaaS — developer commands. Run `make help` for the list.

COMPOSE ?= docker compose
PYTHON  ?= python3
VENV    := backend/.venv
VBIN    := $(VENV)/bin

.DEFAULT_GOAL := help
.PHONY: help up down logs ps build migrate seed test test-backend test-frontend e2e lint format \
        typecheck dev-setup clean secrets-show

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- stack
up: ## Build and start the whole platform (http://localhost)
	@test -f .env || cp .env.example .env
	$(COMPOSE) up --build -d
	@echo "Dashboard: http://localhost  ·  API docs: http://localhost/api/docs"

down: ## Stop the platform (data volumes are kept)
	$(COMPOSE) down

logs: ## Follow logs of all services
	$(COMPOSE) logs -f --tail=100

ps: ## Service status
	$(COMPOSE) ps

build: ## Build all images
	$(COMPOSE) build

migrate: ## Apply database migrations
	$(COMPOSE) run --rm migrate

seed: ## Create the demo user and project (prints the demo password)
	$(COMPOSE) exec backend python -m app.cli.seed

secrets-show: ## Print where the generated secrets live (never their values)
	$(COMPOSE) run --rm --entrypoint ls secrets-init -l /run/paas-secrets

# ---------------------------------------------------------------- quality (local toolchain)
dev-setup: ## Create the backend venv and install frontend/e2e dependencies
	$(PYTHON) -m venv $(VENV)
	$(VBIN)/pip install -r backend/requirements-dev.txt
	cd frontend && npm ci
	cd e2e && npm ci

test: test-backend test-frontend ## Run backend (needs PostgreSQL + Redis) and frontend tests

test-backend: ## Backend unit + integration tests (TEST_DATABASE_URL / TEST_REDIS_URL)
	cd backend && .venv/bin/pytest

test-frontend: ## Frontend unit tests
	cd frontend && npm test

e2e: ## Browser end-to-end tests against the running stack (make up first)
	cd e2e && npx playwright test

lint: ## Lint and type-check everything
	cd backend && .venv/bin/ruff check app tests alembic && .venv/bin/black --check app tests alembic
	cd backend && .venv/bin/mypy app tests
	cd frontend && npm run lint && npm run typecheck

format: ## Auto-format backend and frontend code
	cd backend && .venv/bin/ruff check app tests alembic --fix && .venv/bin/black app tests alembic
	cd frontend && npm run format

clean: ## Stop the stack and DELETE all data volumes (asks first)
	@read -p "This deletes databases, uploads and secrets. Continue? [y/N] " ans && [ "$$ans" = y ]
	$(COMPOSE) down -v

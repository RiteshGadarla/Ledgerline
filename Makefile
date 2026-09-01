.DEFAULT_GOAL := help

export PATH := $(HOME)/.local/bin:$(PATH)

BACKEND := backend
FRONTEND := frontend
DOCKER_COMPOSE := docker compose -f docker/compose.yaml

DATABASE_URL ?= postgresql+asyncpg://ledgerline:ledgerline@localhost:5432/ledgerline
REDIS_URL ?= redis://localhost:6379

.PHONY: help install install-backend install-frontend \
	infra-up infra-down migrate \
	backend worker frontend gen-api dev clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: install-backend install-frontend ## Install backend and frontend dependencies

install-backend: ## Install backend dependencies (uv sync)
	cd $(BACKEND) && uv sync

install-frontend: ## Install frontend dependencies (pnpm install)
	cd $(FRONTEND) && pnpm install

infra-up: ## Start redis + postgres (docker compose)
	$(DOCKER_COMPOSE) up -d

infra-down: ## Stop redis + postgres
	$(DOCKER_COMPOSE) down

migrate: ## Run alembic migrations against the dev database
	cd $(BACKEND) && DATABASE_URL=$(DATABASE_URL) uv run alembic upgrade head

backend: ## Run the FastAPI backend with autoreload
	cd $(BACKEND) && DATABASE_URL=$(DATABASE_URL) REDIS_URL=$(REDIS_URL) uv run uvicorn app.main:app --reload

worker: ## Run the arq background worker
	cd $(BACKEND) && DATABASE_URL=$(DATABASE_URL) REDIS_URL=$(REDIS_URL) uv run arq workers.main.WorkerSettings

gen-api: ## Regenerate the frontend's typed API client from the live backend
	cd $(FRONTEND) && pnpm run gen:api

frontend: ## Run the Next.js dev server
	cd $(FRONTEND) && pnpm run dev

dev: infra-up migrate ## Run infra, backend, worker, and frontend together (Ctrl+C stops all)
	@trap 'kill 0' INT TERM EXIT; \
	( $(MAKE) backend ) & \
	( $(MAKE) worker ) & \
	( $(MAKE) frontend ) & \
	wait

clean: infra-down ## Stop infra and remove local caches/build output
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache $(BACKEND)/.import_linter_cache $(BACKEND)/.hypothesis
	rm -rf $(FRONTEND)/.next

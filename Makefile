.DEFAULT_GOAL := help

export PATH := $(HOME)/.local/bin:$(PATH)

BACKEND := backend
FRONTEND := frontend
DOCKER_COMPOSE := docker compose -f docker/compose.yaml

DATABASE_URL ?= postgresql+asyncpg://ledgerline:ledgerline@localhost:5432/ledgerline
REDIS_URL ?= redis://localhost:6379

# Production knobs. The prod targets run the same code as the dev ones without
# autoreload, bound to every interface so a reverse proxy can reach them. The
# DATABASE_URL/REDIS_URL defaults above point at docker/compose.yaml, i.e. dev
# credentials -- override both (and GEMINI_API_KEY, via backend/.env) before
# using these against anything real:
#
#   DATABASE_URL=... REDIS_URL=... make prod
#
# ENV=prod is what puts the `secure` flag back on the session cookie
# (app/routers/auth.py), so logging in needs TLS terminated in front of the
# app. Over plain http://localhost the cookie is dropped -- use `make dev` for
# that, or ENV=dev make prod to smoke-test a production build locally.
ENV ?= prod
API_HOST ?= 0.0.0.0
API_PORT ?= 8000
WEB_CONCURRENCY ?= 2
FRONTEND_PORT ?= 3000
DEPLOY_SCRIPT := ./deploy_ec2.sh

.PHONY: help install install-backend install-frontend install-prod \
	infra-up infra-down migrate build \
	backend worker frontend gen-api dev \
	backend-prod worker-prod frontend-prod prod deploy clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: install-backend install-frontend ## Install backend and frontend dependencies

install-backend: ## Install backend dependencies (uv sync)
	cd $(BACKEND) && uv sync

install-frontend: ## Install frontend dependencies (pnpm install)
	cd $(FRONTEND) && pnpm install

install-prod: ## Install runtime dependencies only (no dev tooling, locked versions)
	cd $(BACKEND) && uv sync --no-dev
	cd $(FRONTEND) && pnpm install --frozen-lockfile

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

build: ## Build the Next.js production bundle (the backend needs no build step)
	cd $(FRONTEND) && pnpm run build

backend-prod: ## Run the FastAPI backend without autoreload, across WEB_CONCURRENCY workers
	cd $(BACKEND) && ENV=$(ENV) DATABASE_URL=$(DATABASE_URL) REDIS_URL=$(REDIS_URL) \
		uv run uvicorn app.main:app \
		--host $(API_HOST) --port $(API_PORT) --workers $(WEB_CONCURRENCY)

worker-prod: ## Run the arq background worker with production settings
	cd $(BACKEND) && ENV=$(ENV) DATABASE_URL=$(DATABASE_URL) REDIS_URL=$(REDIS_URL) \
		uv run arq workers.main.WorkerSettings

frontend-prod: ## Serve the built Next.js bundle (run `make build` first)
	cd $(FRONTEND) && PORT=$(FRONTEND_PORT) pnpm run start

prod: build migrate ## Build, migrate, and run backend + worker + frontend in production mode (Ctrl+C stops all)
	@trap 'kill 0' INT TERM EXIT; \
	( $(MAKE) backend-prod ) & \
	( $(MAKE) worker-prod ) & \
	( $(MAKE) frontend-prod ) & \
	wait

deploy: ## Deploy backend/ to the EC2 instance (needs the local, gitignored deploy_ec2.sh)
	@test -x $(DEPLOY_SCRIPT) || { \
		echo "$(DEPLOY_SCRIPT) not found or not executable -- it is gitignored and lives only on the deploying machine."; \
		exit 1; \
	}
	$(DEPLOY_SCRIPT)

clean: infra-down ## Stop infra and remove local caches/build output
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache $(BACKEND)/.import_linter_cache $(BACKEND)/.hypothesis
	rm -rf $(FRONTEND)/.next

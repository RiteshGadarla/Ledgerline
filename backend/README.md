# Ledgerline API

FastAPI backend for the Ledgerline reconciliation engine. See `personal/Plan.md` (repo root) for the full product/build spec.

## Running locally

```
uv sync
docker compose -f ../docker/compose.yaml up -d   # redis + postgres
DATABASE_URL=postgresql+asyncpg://ledgerline:ledgerline@localhost:5432/ledgerline uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Set `DATABASE_URL` and `REDIS_URL` in `backend/.env` (see `app/settings.py`) for the app to enable auth and rate limiting; without them it still starts and serves `/health`, but any route needing the database or Redis returns a `503`.

## Auth cookie / cross-origin decision

Sessions are httpOnly, `SameSite=Lax` cookies (`db/tenancy.py`, `app/routers/auth.py`). The chosen approach for cross-origin cookie handling (Phase 9 of the plan) is the **Next.js route-handler proxy**: the web app's server-side route handlers forward requests to the FastAPI backend, so the browser only ever talks to one origin (the Next.js app's own domain) and never sees the API's origin directly. This avoids `SameSite`/CORS credential complications entirely and needs no shared parent-domain cookie configuration. `secure` is set on the cookie whenever `settings.env != "dev"` (plain HTTP is expected in local dev; TLS termination is expected in front of the app otherwise).

## Validation

```
uv run ruff check .
uv run mypy .
uv run lint-imports
uv run pytest -q
```

Postgres- and Redis-backed tests (`tests/test_db_tenancy.py`, `tests/test_auth_api.py`, `tests/test_llm_*`) skip themselves with an actionable message if those services aren't running.

## Database migrations

Alembic migrations live in `migrations/`. `env.py` reads `DATABASE_URL` from `app/settings.py` when set, falling back to the `alembic.ini` default (the docker-compose Postgres). Generate a new migration after changing `db/models.py`:

```
DATABASE_URL=postgresql+asyncpg://ledgerline:ledgerline@localhost:5432/ledgerline uv run alembic revision --autogenerate -m "description"
```

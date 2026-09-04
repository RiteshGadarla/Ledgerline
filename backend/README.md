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

### Gemini API keys

`GEMINI_API_KEY` takes **one key or a comma-separated list of any length**:

```
GEMINI_API_KEY=AIzaSy...one,AIzaSy...two,AIzaSy...three
```

Free-tier quota is charged per key, so the pool is not cosmetic: the governor
keeps its RPM and RPD counters per `(model, key)` (`llm/keys.py`,
`llm/governor.py`), and every call takes the next key in rotation. Three keys
are three times the daily ceiling rather than one ceiling reached three times
as fast, and a key that runs out is stepped over -- the request moves to the
next key instead of degrading. Keys are counted under a digest of the key
itself, so reordering the list or restarting mid-day does not hand a spent key
a fresh allowance, and the credential never appears in a log line or traceback.

`GEMMA_PRIMARY_RPD` and friends (`llm/limits.py`) stay the ceiling for **one**
key -- the pool multiplies them; don't also multiply them by hand.

With no key set at all, the app still runs: every LLM path degrades honestly
(`assist_rate=0`, `llm_degraded=True`) rather than failing.

## Auth cookie / cross-origin decision

Sessions are httpOnly, `SameSite=Lax` cookies (`db/tenancy.py`, `app/routers/auth.py`). The chosen approach for cross-origin cookie handling (Phase 9 of the plan) is the **Next.js route-handler proxy**: the web app's server-side route handlers forward requests to the FastAPI backend, so the browser only ever talks to one origin (the Next.js app's own domain) and never sees the API's origin directly. This avoids `SameSite`/CORS credential complications entirely and needs no shared parent-domain cookie configuration. `secure` is set on the cookie whenever `settings.env != "dev"` (plain HTTP is expected in local dev; TLS termination is expected in front of the app otherwise).

## Validation

```
uv run ruff check .
uv run mypy .
uv run lint-imports
uv run pytest -q
```

Postgres- and Redis-backed tests (`tests/test_db_tenancy.py`, `tests/test_auth_api.py`, `tests/test_llm_*`) skip themselves with an actionable message if those services aren't running. They run against a separate `ledgerline_test` database (`DATABASE_TEST_URL`, created by `docker/postgres-init/001-create-test-db.sql` on a fresh volume), created and dropped per test via `Base.metadata` -- so running the suite never touches the `ledgerline` dev database `alembic upgrade` targets.

## Running a worker

Runs execute in a separate arq worker process, not in the request handler:

```
DATABASE_URL=postgresql+asyncpg://ledgerline:ledgerline@localhost:5432/ledgerline REDIS_URL=redis://localhost:6379 uv run arq workers.main.WorkerSettings
```

`POST /runs` enqueues a job under the run's own id (so arq's own id-based dedup backs up the DB-level idempotency-key check), and the worker persists each state-machine transition (`queued -> normalising -> matching -> triaging -> explaining -> scoring -> complete|failed`) to the run's row and publishes it on the `run:{id}` Redis pub/sub channel. `GET /runs/{id}/stream` (SSE) reads the row first and only then subscribes -- a client that connects after the run has already finished sees the terminal state immediately, and any API replica can serve any run's stream since nothing is held in worker or API process memory.

Both run sources work end to end. `source: "demo"` generates a seeded corpus with its answer key; `source: "dataset"` rebuilds the corpus from a stored dataset's validated records, so an uploaded four-file batch runs through the same engine as a generated one. The `mutations` field takes any of the seven typed corruptions in `datagen/mutations.py`, applied to a copy of the corpus with the truth corrupted in lockstep and recorded on the run, so a run's URL reproduces exactly what was tested.

## Database migrations

Alembic migrations live in `migrations/`. `env.py` reads `DATABASE_URL` from `app/settings.py` when set, falling back to the `alembic.ini` default (the docker-compose Postgres). Generate a new migration after changing `db/models.py`:

```
DATABASE_URL=postgresql+asyncpg://ledgerline:ledgerline@localhost:5432/ledgerline uv run alembic revision --autogenerate -m "description"
```

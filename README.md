# Ledgerline

Multi-source financial reconciliation with a verifier-gated LLM assist, an honest exception list, and bring-your-own-data.

Built for the Razorpay AI Buildathon, **Track 04: AI Finance Controller** ("run the books and the cash position"). The brief asks for an agent that closes one finance-ops loop across a 50+ record batch of synthetic data, reporting its match rate and the exceptions it could not resolve, judged on throughput, measured accuracy, and an honest exception list rather than a cherry-picked match. Ledgerline's answer combines the track's two suggested directions (multi-source reconciliation and a forward cash forecaster) with a settlement Q&A agent (`backend/llm/ask.py`, `frontend/components/AskPanel.tsx`) on top of the same verified data.

## What it does

Ledgerline closes one finance-ops loop end to end:

```
INVOICE (ledger) -> PAYMENT (gateway) -> SETTLEMENT BATCH (net payout, UTR) -> BANK CREDIT (statement line)
```

It ingests a batch of 50–5,000 records across those sources, reconciles them, reports match rate and measured accuracy, emits a typed exception list for everything it can't close, and projects the forward cash position from unsettled payments.

Two data modes:
- **Demo mode**: seeded synthetic corpus shipped with a ground-truth answer key, so precision/recall are measured, not guessed.
- **My data mode**: upload CSV, XLSX or PDF; columns are auto-mapped to the canonical schema (LLM-assisted, user-confirmed), and the same engine runs.

Key engineering property: **the LLM never does arithmetic and never writes a match**. It proposes `{links, evidence_spans, confidence}`; a deterministic verifier recomputes the tie-out in integer paise. Failed proposals surface as exceptions (`LLM_PROPOSAL_FAILED_VERIFY`), never as silent matches.

## Architecture

```
Next.js (frontend)              FastAPI (backend)                Worker (arq)
--------------------            --------------------             ---------------
Client components   --HTTP-->   routers + deps        --enqueue-> run pipeline
SSE-driven run log   <--SSE---  run stream (DB row     <-pub/sub-  progress events
Generated TS client              + redis pub/sub)                    |
                                 auth, tenancy                       v
                                                          engine (pure) -> verifier -> metrics
                                                                   ^
                                                          llm gateway (governed, cached)
```

- **`backend/engine`** is pure Python (no I/O, no clock, no randomness, no DB), a deterministic function from corpus to result.
- **Runs execute in a separate arq worker process**, not the request handler, so reconciliation never blocks the API event loop.
- **The frontend is presentation only.** No matching, no money math, no metric computation happens client-side; everything rendered comes from the API already computed. An ESLint rule enforces this (see `frontend/README.md`).
- Cross-origin cookies are avoided entirely: the Next.js app proxies API requests through its own origin (`frontend/app/api/[...path]/route.ts`), so the browser only ever talks to one domain.

### Stack

| Layer | Choice |
|---|---|
| API | FastAPI, Pydantic v2, uvicorn |
| Jobs | arq + Redis (separate worker) |
| DB | PostgreSQL, SQLAlchemy 2.0 async, Alembic |
| Engine | Pure Python, frozen dataclasses |
| Parsing | polars (CSV/XLSX), pdfplumber (PDF) |
| LLM | `google-genai` (Gemini), server side only |
| Auth | argon2-cffi (argon2id), server-side sessions in Postgres |
| Frontend | Next.js App Router, TypeScript strict |
| API client | `openapi-typescript`, generated from the live OpenAPI schema |

## Repo layout

```
backend/    FastAPI app, engine, ingestion, LLM gateway, workers, tests   (see backend/README.md)
frontend/   Next.js app: the seven surfaces (Run, Scoreboard, Chain,
            Exceptions, Data, Cash position, Method)                     (see frontend/README.md)
docker/     docker-compose for local Postgres + Redis
fixtures/   sample data
Makefile    convenience targets for install / run / test
```

## Running locally

```
make install     # backend (uv sync) + frontend (pnpm install)
make dev         # infra (postgres+redis) + backend + worker + frontend, Ctrl+C stops all
```

Or step by step; `make help` lists every target:

```
make infra-up    # redis + postgres via docker compose
make migrate     # alembic upgrade head
make backend     # FastAPI with autoreload  (localhost:8000)
make worker      # arq background worker
make gen-api     # regenerate frontend/lib/api/schema.d.ts from the live backend
make frontend    # Next.js dev server        (localhost:3000)
```

Without `DATABASE_URL`/`REDIS_URL` configured, the backend still starts and serves `/health`; any route needing the database or Redis returns `503`. See [`backend/README.md`](backend/README.md) and [`frontend/README.md`](frontend/README.md) for details, environment variables, and validation commands (`ruff`, `mypy`, `pytest`, `lint-imports`).

## Status

Runs work end to end against both a generated corpus and an uploaded dataset. The adversarial mutation engine is in (`backend/datagen/mutations.py`): seven typed corruptions, each applied to a copy of the corpus with the ground truth corrupted in lockstep, so precision, recall and false matches stay measurable after the sabotage. They are opt-in from the Run console and recorded on the run, so a run's URL reproduces exactly what was tested.

Still outstanding against the plan: no CI, no frontend test suite (Playwright/vitest/axe), no Dockerfiles or full-topology compose, no throughput benchmark baseline, and `/health` is a stub. See the "Known, documented gaps" section in [`frontend/README.md`](frontend/README.md).

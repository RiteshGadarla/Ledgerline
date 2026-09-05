# Ledgerline

Built for the **Razorpay AI Buildathon, Track 04: AI Finance Controller**.

> **The live deployment link is in the uploaded submission video** 

## What it is

Ledgerline closes the books on a batch of finance data. It walks every rupee
along the whole chain, invoice to payment to settlement batch to the line on a
bank statement, ties out what genuinely belongs together, and files everything
else as a typed exception carrying the check it failed and the evidence behind
it.


## The core of it

> **The model proposes. A deterministic verifier decides. The model has never
> written a match, and it never does arithmetic.**

That one rule is what the whole system is built around, and it is what makes
this different from an LLM asked to reconcile a spreadsheet:

- The model returns `{links, evidence_spans, confidence}` and nothing else. A
  verifier recomputes the tie-out in **integer paise** and is the only code
  path allowed to write a match. The deterministic passes go through the same
  verifier; there is no second path.
- Nothing is ever matched at "lower confidence". A proposal that will not tie
  out becomes an exception, not a guess.
- Every evidence span must be a **verbatim substring** of narration the run
  actually showed the model, so an invented quote is thrown out before
  verification even runs.
- Accuracy is measured, not asserted. Synthetic corpora ship with a
  ground-truth answer key the engine never sees, and the numbers below come
  from held-out seeds the engine was never developed against.
- It degrades loudly. With no Gemini key, or an unreachable one, the run still
  completes, reports `assist_rate = 0`, and flags itself rather than quietly
  producing worse output.
- **Lyra**, the Q&A agent, answers questions about a finished run through 11
  tools and cites the record ids it actually read, so an invented id gets no
  citation.

## Features

**Matching**
- Two deterministic linking passes plus a verifier, over the full chain: invoice, payment, settlement batch, bank credit.
- Every sum recomputed in **integer paise**. No floating point touches money, anywhere.
- Payout re-derived from its own batch (gross, less fees, less tax, less refunds, plus adjustments) and compared to what the bank actually credited.
- Date-window checks, so a credit that posted six weeks late is a break rather than a silent tie.

**Honesty**
- **13 typed exception codes**, each carrying the check it failed and the evidence behind it.
- Nothing is ever matched at "lower confidence": a proposal that will not tie out becomes an exception, not a guess.
- Accuracy figures are hidden when there is no answer key to score against, rather than shown as zeroes.
- Degrades loudly: if the model is unreachable, the run still completes, reports `assist_rate = 0`, and flags itself.

**The AI layer**
- Model proposes `{links, evidence_spans, confidence}`, never an amount, never a match.
- Evidence spans must be **verbatim substrings** of narration the run actually showed it; ungrounded quotes are rejected before verification even runs.
- A rejected proposal surfaces as `LLM_PROPOSAL_FAILED_VERIFY`: visible, not swallowed.
- Column mapping resolves known headers deterministically and asks the model **only** about genuinely ambiguous ones, with many-shot worked examples.

**Lyra, the Q&A agent**
- **11 tools** over the run's stored result: metrics, chains, exceptions, records, forecast, dataset, prior decisions, cross-run comparison and free-text search.
- Multi-turn, so follow-ups resolve against what was just said.
- Shows each lookup as it happens, then **cites the record ids it read**, collected from tool results so an invented id gets no citation.
- Numbers are checked against tool payloads before you see them; an ungrounded answer is replaced wholesale.
- Tenancy is re-verified per tool call at the repository layer, whatever run id the model asks for.

**Measurement**
- Synthetic corpora ship with a **ground-truth answer key** the engine never sees, so accuracy is measured rather than asserted.
- **11 difficulty classes**: fee/GST deltas, refunds and chargebacks in-batch, splits, duplicates, missing UTRs, payer mismatches, unrelated credits, and genuinely unmatchable records, each scaling with corpus size.
- **7 sabotage modes** switchable from the console, applied to a copy with the truth corrupted in lockstep.
- Every run reproducible from its seed, with an output hash.

**Product**
- Six surfaces: Run, Data, Scoreboard, Chain, Exceptions, Cash position.
- **Impact readout**: payments closed without a human, time returned, rupees cleared, and what still needs someone.
- 14-day cash position projected from unsettled payments.
- Branded PDF report per run, and CSV export.
- Guided tour that builds a first-time user's corpus with them: Run to Data, seed and size, then back to the console to close the books on it. A new account starts empty; nothing is generated on anyone's behalf.

**Operations**
- Runs execute in a separate **arq worker**, never in the request handler.
- Live run progress over SSE.
- **Multi-key Gemini pool**: quota is charged per key, so three keys are three times the ceiling, not one ceiling reached three times as fast.
- Governor enforces per-key RPM/RPD and per-user daily quota in Redis; a spent key is stepped over, not failed on.
- Response cache keyed by model + prompt + schema version, storing what each answer cost.

---

## The result

**300 runs. 267,172 records. Zero false matches.**

Measured on 30 held-out seeds the engine was never developed against
(5001 to 5030, asserted disjoint from the golden seeds in `scripts/eval.py`),
at three corpus sizes, then repeated once per corruption the mutation engine
can apply. Reproduce with `cd backend && uv run python -m scripts.benchmark`.

| Corpus                             | Runs | Records | Precision | Recall | False matches | Auto | Exceptions | Throughput |
|------------------------------------|-----:|--------:|----------:|-------:|--------------:|-----:|-----------:|-----------:|
| clean · 150 records                |   30 |   9,545 |     1.000 |  0.781 |             0 | 0.764 |       76.3 | 433,407/s |
| clean · 600 records                |   30 |  38,158 |     1.000 |  0.779 |             0 | 0.749 |      324.7 | 433,795/s |
| clean · 2400 records               |   30 | 152,594 |     1.000 |  0.776 |             0 | 0.746 |     1312.0 | 349,110/s |
| corrupted · duplicate_payment      |   30 |   9,575 |     1.000 |  0.781 |             0 | 0.759 |       77.3 | 402,919/s |
| corrupted · shift_date:45          |   30 |   9,545 |     1.000 |  0.679 |             0 | 0.690 |      100.5 | 386,222/s |
| corrupted · alter_amount:-150000   |   30 |   9,545 |     1.000 |  0.678 |             0 | 0.685 |      102.0 | 373,932/s |
| corrupted · delete_bank_line       |   30 |   9,515 |     1.000 |  0.679 |             0 | 0.690 |       99.5 | 382,316/s |
| corrupted · inject_unrelated_credit |   30 |   9,575 |     1.000 |  0.781 |             0 | 0.764 |       77.3 | 403,042/s |
| corrupted · scramble_narration     |   30 |   9,545 |     1.000 |  0.671 |             0 | 0.684 |      102.4 | 393,623/s |
| corrupted · split_payment          |   30 |   9,575 |     1.000 |  0.678 |             0 | 0.680 |      103.0 | 379,432/s |

Read the two halves together. The second one is the point:

- **Precision is 1.000 in every row.** Nothing was tied out that the truth file says does not belong together. That is the verifier: every proposal, whoever made it, recomputed in integer paise before it is written.
- **Recall falls under sabotage, and that is the honest outcome.** Corrupting a date, an amount or a narration genuinely destroys the evidence a match needs. The engine answers by filing exceptions, roughly 76 on a clean 150-record corpus and roughly 100 once corrupted, rather than guessing. An engine whose recall held steady under sabotage would be inventing matches, and its precision would say so.
- **Recall is flat across sizes** (0.781 at 150 records, 0.776 at 2,400) because difficulty is held constant by construction at 15%. A bigger run is more evidence for the same claim, not an easier corpus flattering the number.
- **Throughput times `engine.match()` alone**: no ingestion, no LLM, no database. A throughput figure that quietly includes or excludes the slow parts is advertising, not measurement. End-to-end wall clock is reported per run on the Scoreboard.

### On real files, not just generated ones

A four-file upload of genuine exports (55 invoices, 56 payments, 53
settlements, 60 bank lines) reconciles to **50 matched groups against 53
settlements, and 26 typed exceptions**:

- 10 unidentified credits
- 8 settlements with no bank line
- 5 payments with no invoice
- 2 unexplained amount mismatches
- 1 duplicate candidate

That list is the deliverable. A reconciliation that reports no exceptions on
real books is not finished. It is lying.

---

## How it works

One finance-ops loop, closed end to end:

INVOICE (ledger) -> PAYMENT (gateway) -> SETTLEMENT BATCH (net payout, UTR) -> BANK CREDIT (statement line)


### The rule the whole system is built around

> **The model never does arithmetic and never writes a match.**

It proposes `{links, evidence_spans, confidence}`; a deterministic verifier
recomputes the tie-out in integer paise and decides. The same discipline governs
schema mapping: known columns resolve deterministically, the model is asked only
about ambiguous headers, and any answer colliding with an already-resolved field
is dropped rather than merged.

**Propose freely; a deterministic check decides.**

### The reconciliation path

Where a record goes, and who is allowed to say it matched:

```mermaid
flowchart TD
    IN(["Batch · invoices · payments · settlements · bank lines"])
    IN --> P1

    P1{"P1 · link<br/>bank credit to settlement<br/>UTR + exact amount + date window"}
    P1 -->|"unique hit"| P3
    P1 -->|"no credit / several candidates"| EX

    P3["P3 · link<br/>invoice to payment<br/>exact ref, else unique amount + window"]
    P3 --> PROP["Proposal<br/>invoices · payments · settlement · bank line"]
    PROP --> V

    TRIAGE["Assisted triage · residue only<br/>model proposes links + evidence spans"]
    TRIAGE --> V

    V{"THE VERIFIER<br/>the only path that writes a match"}
    V -->|"ties out exactly"| OK(["Matched group · auto or assisted"])
    V -->|"residual, or evidence fails"| EX

    EX(["Typed exception · the check it failed + its evidence"])
    EX -.->|"amount-equal candidate exists"| TRIAGE

    classDef gate fill:#0b3d3a,stroke:#00a294,stroke-width:2px,color:#ffffff
    classDef good fill:#e3f2ea,stroke:#146a47,color:#0b2e1e
    classDef bad fill:#fdecec,stroke:#b3261e,color:#5c1512
    class V gate
    class OK good
    class EX bad
```

- **P1** links a bank credit to a settlement on UTR plus an exact amount, and requires the credit to have landed near the settlement date. UTR and an exact amount are strong evidence, but not a licence to silently tie a credit that posted six weeks late.
- **P3** links an invoice to a payment on an exact reference token, falling back to a unique amount inside a date window.
- **The verifier** is the only code path that creates a match. It recomputes the payout from the settlement's own batch (gross, less fees, less tax, less refunds, plus adjustments: the P2 algebra) in integer paise, requires every evidence span to be a verbatim substring of narration the run actually showed, and refuses any record already claimed by another group. Both the deterministic passes and the model go through it; there is no second path.

`p4_subset_sum` (one credit covering many invoices) is implemented and unit
tested in `engine/passes.py`, but is **not currently wired into `match()`**.

### System architecture

```mermaid
flowchart LR
    UI["Next.js on Vercel<br/>six surfaces"] --> PROXY["same-origin<br/>API proxy"]

    subgraph aws["AWS EC2 · Nginx + TLS"]
        subgraph api["API process · FastAPI"]
            ROUTES["routers<br/>auth · tenancy · SSE"]
            INGEST["ingest/<br/>CSV · XLSX · PDF"]
        end

        subgraph worker["Worker process · arq"]
            PIPE["run_pipeline<br/>five stages"]
            ENGINE["engine/<br/>pure · deterministic"]
            VERIFY["verifier<br/>integer paise"]
        end

        REDIS[("Redis<br/>queue · pub-sub · cache")]
        PG[("PostgreSQL<br/>runs · datasets · sessions")]
    end

    GEMINI["Gemini<br/>key pool · governed · cached"]

    PROXY --> ROUTES
    ROUTES --> INGEST
    ROUTES <-->|"enqueue · pub-sub"| REDIS
    REDIS -->|"job"| PIPE
    PIPE --> ENGINE --> VERIFY -->|"result"| PG
    ROUTES --> PG
    INGEST --> PG
    PIPE -->|"progress"| REDIS
    ROUTES -->|"SSE"| UI

    INGEST -->|"ambiguous columns"| GEMINI
    PIPE -->|"triage residue"| GEMINI
    GEMINI -.->|"proposals only<br/>never arithmetic"| VERIFY

    classDef gate fill:#0b3d3a,stroke:#00a294,stroke-width:2px,color:#ffffff
    classDef ext fill:#fff6e5,stroke:#b7791f,color:#5c3d0a
    class VERIFY gate
    class GEMINI ext
```

### Design decisions

- **`backend/engine` is pure Python**: no I/O, no clock, no randomness, no database. A deterministic function from corpus to result, which is what makes a run reproducible from its seed and hash.
- **Runs execute in a separate arq worker**, never in the request handler, so reconciliation cannot block the API event loop.
- **The frontend is presentation only.** No matching, no money math, no metric computed client-side. An ESLint rule enforces it.
- **Cross-origin cookies are avoided entirely**: the Next.js app on Vercel proxies the API through its own origin, so the browser only ever talks to one domain.
- **Impact figures are derived at render time, never stored.** The per-match time assumption is a reading of a run, not a fact about it; baking it in would make every past run silently change meaning the day it is revised.

### Stack

| Layer | Choice |
|---|---|
| API | FastAPI, Pydantic v2, uvicorn |
| Jobs | arq + Redis, in a separate worker process |
| DB | PostgreSQL, SQLAlchemy 2.0 async, Alembic |
| Engine | Pure Python, frozen dataclasses |
| Parsing | polars (CSV/XLSX), pdfplumber (PDF) |
| LLM | `google-genai` (Gemini), server side only |
| Auth | argon2-cffi (argon2id), server-side sessions in Postgres |
| Frontend | Next.js App Router, TypeScript strict |
| API client | `openapi-typescript`, generated from the live OpenAPI schema |
| Hosting | Vercel (frontend), AWS EC2 behind Nginx with TLS (API, worker, Postgres, Redis) |

---

## Running it

Prerequisites either way: Python with [`uv`](https://docs.astral.sh/uv/),
Node with `pnpm`, and Docker for local Postgres and Redis.

### macOS and Linux

```
make install     # backend (uv sync) + frontend (pnpm install)
make dev         # infra + backend + worker + frontend, Ctrl+C stops all
```

Step by step; `make help` lists every target:

```
make infra-up    # redis + postgres via docker compose
make migrate     # alembic upgrade head
make backend     # FastAPI with autoreload  (localhost:8000)
make worker      # arq background worker
make gen-api     # regenerate frontend/lib/api/schema.d.ts from the live backend
make frontend    # Next.js dev server        (localhost:3000)
```

### Windows

The `make` targets are a convenience, not a requirement: nothing in the app
needs them. Two ways to run on Windows.

**With WSL2 (closest to the above).** Install Ubuntu from the Microsoft Store,
clone the repo inside the WSL filesystem, install `uv`, `pnpm` and Docker
Desktop with the WSL2 backend enabled, then use exactly the `make` commands
above.

**Native PowerShell.** Start Docker Desktop, then use four terminals, each
opened at the repo root:

```powershell
# once
cd backend;  uv sync
cd ..\frontend;  pnpm install
cd ..

# terminal 1 - infra
docker compose -f docker/compose.yaml up -d

# terminal 2 - migrations, then the API on localhost:8000
cd backend
$env:DATABASE_URL = "postgresql+asyncpg://ledgerline:ledgerline@localhost:5432/ledgerline"
$env:REDIS_URL    = "redis://localhost:6379"
uv run alembic upgrade head
uv run uvicorn app.main:app --reload

# terminal 3 - the arq worker
cd backend
$env:DATABASE_URL = "postgresql+asyncpg://ledgerline:ledgerline@localhost:5432/ledgerline"
$env:REDIS_URL    = "redis://localhost:6379"
uv run arq workers.main.WorkerSettings

# terminal 4 - the Next.js dev server on localhost:3000
cd frontend
pnpm run dev
```

`$env:` assignments last only for the terminal that set them, so each backend
terminal needs both lines. In `cmd.exe` use `set VAR=value` instead. Put
`GEMINI_API_KEY` in `backend\.env` rather than in the shell.

### Notes

- Without `DATABASE_URL` / `REDIS_URL`, the backend still starts and serves `/health`; any route needing Postgres or Redis returns `503`.
- `GEMINI_API_KEY` takes one key or a comma-separated list of any length.
- With no key at all, every LLM path degrades honestly rather than failing.

### Repo layout

```
backend/    FastAPI app, engine, ingestion, LLM gateway, workers, tests
frontend/   Next.js app: Run, Data, Scoreboard, Chain, Exceptions, Cash position
docker/     docker-compose for local Postgres + Redis
fixtures/   golden metrics, pinned per seed
Makefile    install / run / test
```

See [`backend/README.md`](backend/README.md) and
[`frontend/README.md`](frontend/README.md) for environment variables and the
validation commands (`ruff`, `mypy`, `pytest`, `lint-imports`).

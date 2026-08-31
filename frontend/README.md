# Ledgerline web

Next.js App Router frontend. Presentation only: every number rendered comes from the API already computed (see the amount-arithmetic-ban ESLint rule in `eslint.config.mjs`, exempted only for `lib/money.ts`).

## Running locally

```
pnpm install
pnpm run gen:api          # regenerates lib/api/schema.d.ts from the live backend's OpenAPI schema
pnpm run dev
```

Requires the backend running at `LEDGERLINE_API_URL` (`.env.local`, defaults to `http://localhost:8000`) — see `backend/README.md` for bringing up Postgres/Redis/the API/the worker. `gen:api` needs the backend reachable; re-run it after any backend contract change.

## Architecture

- **`app/api/[...path]/route.ts`** is a transparent reverse proxy to the FastAPI backend. The browser only ever talks to this app's own origin; the proxy forwards the `Cookie` header in and `Set-Cookie` out, which is what makes the httpOnly session cookie work without CORS (the cross-origin decision documented in `backend/README.md`).
- **`lib/api/client.ts`** wraps `openapi-fetch`, typed against the generated `lib/api/schema.d.ts`, scoped to `baseUrl: "/api"`. All browser-side data fetching goes through this client (or, for the two multipart file-upload endpoints, a thin `fetch` wrapper in `app/data/page.tsx` -- see its comment for why).
- Every page is a Client Component fetching after mount (no server-side data fetching / SSR data). This is a deliberate scope reduction: it keeps auth handling to "the browser has a cookie or it doesn't" instead of manually forwarding cookies through Server Component fetches.
- **`lib/money.ts`** is the one file allowed to do arithmetic on a paise amount (converting an already-final integer into a display string, mirroring `backend/money/parse.py::format_paise`). Nowhere else may sum, multiply, or divide an amount-shaped field -- `eslint.config.mjs`'s `no-restricted-syntax` rule fails the build if it finds one.

## The seven surfaces

Run, Scoreboard, Chain, Exceptions, Data, Cash position, and Method, per the plan. Run/Scoreboard/Chain/Exceptions/Cash position live under `/runs/[id]/*`, sharing `components/RunShell.tsx` (the SSE-driven live log and status banner -- the app's one animated element, per the design constraints; everything else is static and respects `prefers-reduced-motion`).

Known, documented gaps against the plan's Phase 11 verify list:
- **No Playwright/axe/screenshot test suite yet.** Scoped down deliberately to prioritize a working, real-backend-integrated UI over test infrastructure in this pass.
- **Bring-your-own-dataset runs aren't wired up**: `/data` previews and validates an upload but nothing is persisted as a reusable dataset (no `dataset_id` storage exists on the backend yet), so `POST /runs` with `source: "dataset"` still fails with a typed error.
- **Difficulty-class filtering on the Chain surface is not implemented**: that's a synthetic-corpus-truth-file concept with no equivalent for a live run's result, so only status and pass are filterable.
- **Mutations are not exposed in the Run form**: the backend rejects any non-empty `mutations` list outright (Phase 13 doesn't exist yet), so the control is omitted rather than shown broken.

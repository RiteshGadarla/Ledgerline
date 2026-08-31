import createClient from "openapi-fetch";
import type { paths } from "./schema";

// Every call from browser code goes through this client, which is scoped to
// our own origin's /api/* route handlers (see app/api/[...path]/route.ts).
// The browser attaches the session cookie automatically since it's a
// same-origin request; the proxy is the only place that ever talks to the
// separate FastAPI origin.
export const api = createClient<paths>({ baseUrl: "/api" });

export type { components } from "./schema";

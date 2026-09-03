import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema";

// Every call from browser code goes through this client, which is scoped to
// our own origin's /api/* route handlers (see app/api/[...path]/route.ts).
// The browser attaches the session cookie automatically since it's a
// same-origin request; the proxy is the only place that ever talks to the
// separate FastAPI origin.

/**
 * Conditional GETs, client side.
 *
 * The backend tags every JSON response with an ETag over its exact bytes
 * (backend/app/etag.py). This keeps the last body seen for each GET URL and
 * echoes its tag in `If-None-Match`, so a poll that finds nothing changed --
 * which is what most of them find -- comes back as a bodyless 304 and is
 * answered from memory instead of off the wire. On a slow link that is the
 * difference between a run surface that ticks and one that stutters.
 *
 * Kept here rather than left to the browser's HTTP cache on purpose: a
 * Next.js route handler is free to rewrite the caching headers it proxies,
 * and this layer has to behave the same in `next dev` and in production.
 * Hence `cache: "no-store"` below -- the browser cache is told to stay out of
 * the way so there is exactly one cache in the path, this one.
 */
type CacheEntry = { etag: string; body: string; contentType: string };

// Keyed by absolute request URL, so two surfaces reading the same run share
// an entry. Bounded because a long session touches many run ids; the oldest
// entry goes first, which is also the one least likely to be polled next.
const MAX_ENTRIES = 32;
const cache = new Map<string, CacheEntry>();

function remember(url: string, entry: CacheEntry) {
  // Delete-then-set so a refreshed entry moves to the young end of the Map's
  // insertion order rather than staying where it first landed.
  cache.delete(url);
  cache.set(url, entry);
  while (cache.size > MAX_ENTRIES) {
    const oldest = cache.keys().next();
    if (oldest.done) break;
    cache.delete(oldest.value);
  }
}

/** Drop every cached body. Called on sign-out, so nothing survives into the
 *  next session in memory -- the ETags themselves are already tenant-safe,
 *  since a different user's row hashes to a different tag. */
export function clearApiCache() {
  cache.clear();
}

const conditionalGet: Middleware = {
  onRequest({ request }) {
    if (request.method !== "GET") return undefined;
    const hit = cache.get(request.url);
    if (hit) request.headers.set("If-None-Match", hit.etag);
    return request;
  },

  async onResponse({ request, response }) {
    if (request.method !== "GET") return undefined;

    if (response.status === 304) {
      const hit = cache.get(request.url);
      // The entry was evicted between the two halves of this request. Rare,
      // and recoverable: ask again without the validator.
      if (!hit) return fetch(request.url, { cache: "no-store" });
      return new Response(hit.body, {
        status: 200,
        headers: { "Content-Type": hit.contentType },
      });
    }

    const etag = response.headers.get("ETag");
    if (response.status !== 200 || !etag) return undefined;

    // Clone: the original response still has to be read by openapi-fetch.
    const body = await response.clone().text();
    remember(request.url, {
      etag,
      body,
      contentType: response.headers.get("Content-Type") ?? "application/json",
    });
    return undefined;
  },
};

export const api = createClient<paths>({ baseUrl: "/api", cache: "no-store" });
api.use(conditionalGet);

export type { components } from "./schema";

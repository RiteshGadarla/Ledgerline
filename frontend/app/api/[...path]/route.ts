import type { NextRequest } from "next/server";

const BACKEND_URL = process.env.LEDGERLINE_API_URL ?? "http://localhost:8000";

// A transparent reverse proxy to the FastAPI backend: the browser only ever
// talks to this origin, so the session cookie is set for (and read back
// from) this app's own domain -- the "Next.js route-handler proxy" decision
// documented in backend/README.md. Every method and every response shape
// (JSON, CSV download, SSE stream) passes through unchanged; this file has
// no per-route knowledge of the API at all.
async function proxy(request: NextRequest): Promise<Response> {
  const path = request.nextUrl.pathname.replace(/^\/api/, "");
  const target = `${BACKEND_URL}${path}${request.nextUrl.search}`;

  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("content-length");

  const hasBody = !["GET", "HEAD"].includes(request.method);
  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    // Required by the fetch spec when streaming a request body.
    duplex: hasBody ? "half" : undefined,
    redirect: "manual",
  } as RequestInit & { duplex?: "half" });

  const responseHeaders = new Headers(upstream.headers);
  // Let Next.js/the browser recompute framing for the proxied response.
  responseHeaders.delete("content-length");
  responseHeaders.delete("content-encoding");

  // 304 (and 204) carry no representation at all, and the fetch spec refuses
  // to build a Response with a body for either. The conditional-GET path in
  // lib/api/client.ts depends on those statuses reaching the browser intact.
  const bodyless = upstream.status === 304 || upstream.status === 204;

  return new Response(bodyless ? null : upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export {
  proxy as DELETE,
  proxy as GET,
  proxy as PATCH,
  proxy as POST,
  proxy as PUT,
};

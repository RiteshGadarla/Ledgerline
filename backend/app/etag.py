"""Conditional GETs for the JSON API.

The console polls. The run surface re-reads ``/runs/{id}`` every two seconds
while a run is in flight, and every tab of a finished run re-reads the same
row and the same result blob. Those bodies are not small -- a ``RunOut``
carries the metrics and a fourteen-day forecast, a ``RunResultOut`` carries
every match group and every exception -- and between two polls they are
almost always byte-identical to the copy the browser is already holding.

So each JSON body is hashed and handed back as a strong ETag. A client that
echoes the tag in ``If-None-Match`` gets a 304 with no body at all. The query
still runs; what stops is the transfer, which is the part the operator feels
as lag on a slow connection.

Deliberately a raw ASGI middleware rather than a ``BaseHTTPMiddleware``: the
run's SSE stream and the CSV export must keep streaming a chunk at a time,
and that decision is made here from the content type on
``http.response.start``, before a single body chunk has been buffered.
"""

import hashlib

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

JSON_CONTENT_TYPE = "application/json"

# "no-cache" is not "don't store": it means store it, then revalidate before
# every reuse. That is exactly the contract this middleware implements, and
# "private" keeps a shared proxy from ever holding one tenant's body.
CACHE_CONTROL = "private, no-cache"


def compute_etag(body: bytes) -> str:
    """A strong validator over the exact bytes sent. blake2b rather than
    sha256 because this runs on every JSON response and only needs to be
    collision-resistant, not cryptographically committing."""
    return f'"{hashlib.blake2b(body, digest_size=16).hexdigest()}"'


def etag_matches(if_none_match: str, etag: str) -> bool:
    """RFC 9110 If-None-Match: a comma-separated list, possibly ``*``,
    compared with the weak comparison function (so ``W/"x"`` and ``"x"``
    are the same entity for the purposes of a 304)."""
    stripped = etag.removeprefix("W/")
    for raw in if_none_match.split(","):
        candidate = raw.strip()
        if candidate == "*":
            return True
        if candidate and candidate.removeprefix("W/") == stripped:
            return True
    return False


class _ETagResponder:
    """Buffers a JSON 200 so its body can be hashed, and passes anything
    else straight through untouched."""

    def __init__(self, send: Send, if_none_match: str | None) -> None:
        self._send = send
        self._if_none_match = if_none_match
        self._start: Message | None = None
        self._buffering = False
        self._chunks: list[bytes] = []

    async def __call__(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = Headers(raw=message["headers"])
            self._start = message
            self._buffering = (
                message["status"] == 200
                and headers.get("content-type", "").startswith(JSON_CONTENT_TYPE)
                # A route that computed its own validator knows better than
                # a body hash does; never second-guess it.
                and "etag" not in headers
            )
            if not self._buffering:
                await self._send(message)
            return

        if message["type"] != "http.response.body" or not self._buffering:
            await self._send(message)
            return

        self._chunks.append(message.get("body", b""))
        if message.get("more_body", False):
            return
        await self._finish()

    async def _finish(self) -> None:
        start = self._start
        if start is None:  # pragma: no cover -- ASGI servers never do this
            return

        body = b"".join(self._chunks)
        etag = compute_etag(body)

        # MutableHeaders edits start["headers"] in place, so `start` carries
        # every change below without being rebuilt.
        headers = MutableHeaders(raw=start["headers"])
        headers["etag"] = etag
        headers.setdefault("cache-control", CACHE_CONTROL)
        # Every body here is scoped to the session cookie; a cache that keyed
        # on the URL alone would serve one tenant's run to another.
        headers.add_vary_header("Cookie")

        if self._if_none_match is not None and etag_matches(self._if_none_match, etag):
            # A 304 carries no representation, so the framing headers that
            # described one have to go with it.
            del headers["content-length"]
            del headers["content-type"]
            await self._send({**start, "status": 304})
            await self._send({"type": "http.response.body", "body": b"", "more_body": False})
            return

        await self._send(start)
        await self._send({"type": "http.response.body", "body": body, "more_body": False})


class ETagMiddleware:
    """Tags JSON GET responses and answers a matching If-None-Match with 304.

    GET only: a HEAD response body is empty by the time it reaches here, so
    hashing it would mint a tag for the wrong entity.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "GET":
            await self.app(scope, receive, send)
            return
        if_none_match = Headers(scope=scope).get("if-none-match")
        await self.app(scope, receive, _ETagResponder(send, if_none_match))

"""The conditional-GET layer: JSON responses carry a validator, a matching
If-None-Match comes back as a bodyless 304, and streaming responses are left
alone entirely."""

from fastapi.testclient import TestClient

from app.etag import compute_etag, etag_matches
from app.main import app


def test_json_get_carries_an_etag_and_revalidation_headers() -> None:
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["etag"] == compute_etag(response.content)
    assert response.headers["cache-control"] == "private, no-cache"
    # Every body is session-scoped; a cache keyed on the URL alone would
    # hand one tenant's response to another.
    assert "cookie" in response.headers["vary"].lower()


def test_matching_if_none_match_returns_a_bodyless_304() -> None:
    client = TestClient(app)
    etag = client.get("/health").headers["etag"]

    response = client.get("/health", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag
    # A 304 describes no representation, so it must not claim to frame one.
    assert "content-length" not in response.headers
    assert "content-type" not in response.headers


def test_stale_if_none_match_returns_the_full_body() -> None:
    client = TestClient(app)
    response = client.get("/health", headers={"If-None-Match": '"not-the-current-body"'})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_etag_matches_handles_lists_wildcards_and_weak_tags() -> None:
    assert etag_matches('"a", "b"', '"b"')
    assert etag_matches("*", '"b"')
    assert etag_matches('W/"b"', '"b"')
    assert etag_matches('"b"', 'W/"b"')
    assert not etag_matches('"a"', '"b"')
    assert not etag_matches("", '"b"')


def test_non_get_methods_are_never_tagged() -> None:
    client = TestClient(app)
    # A 405 still proves the middleware let the request through untouched.
    response = client.post("/health")
    assert "etag" not in response.headers

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from llm.backoff import LlmTransientError
from llm.keys import ApiKey, KeyPool


class LlmUnavailable(Exception):
    """Raised when a client cannot produce a response at all (no fixture, no key, exhausted retries)."""


class LlmResponse(BaseModel):
    raw_json: str
    input_tokens: int
    output_tokens: int


class LlmClient(Protocol):
    async def generate(
        self, *, model: str, prompt: str, response_schema: type[BaseModel], api_key: ApiKey | None = None
    ) -> LlmResponse:
        """Serve one prompt. `api_key` is the credential the governor reserved
        quota against for this call; a client with no credentials to spend
        (the offline fake) ignores it."""
        ...


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class FakeClient:
    """Deterministic fixtures for offline testing. No network, no API key, ever."""

    def __init__(self, fixtures: dict[str, str]) -> None:
        self._fixtures = fixtures
        self.calls: list[tuple[str, str]] = []

    async def generate(
        self, *, model: str, prompt: str, response_schema: type[BaseModel], api_key: ApiKey | None = None
    ) -> LlmResponse:
        self.calls.append((model, prompt))
        raw = self._fixtures.get(prompt)
        if raw is None:
            raise LlmUnavailable(f"no fixture recorded for prompt: {prompt[:80]!r}")
        response_schema.model_validate_json(raw)
        return LlmResponse(raw_json=raw, input_tokens=_estimate_tokens(prompt), output_tokens=_estimate_tokens(raw))


class RecordingClient:
    """Wraps a real client and writes its responses to fixtures/llm/ for later replay."""

    def __init__(self, inner: LlmClient, fixtures_dir: Path) -> None:
        self._inner = inner
        self._fixtures_dir = fixtures_dir

    async def generate(
        self, *, model: str, prompt: str, response_schema: type[BaseModel], api_key: ApiKey | None = None
    ) -> LlmResponse:
        response = await self._inner.generate(
            model=model, prompt=prompt, response_schema=response_schema, api_key=api_key
        )
        self._fixtures_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(f"{model}:{prompt}".encode()).hexdigest()
        (self._fixtures_dir / f"{digest}.json").write_text(response.raw_json)
        return response


# One attempt's ceiling. Comfortably above a healthy call (measured at about
# 1.5s) and well below the minute a failing one was taking.
REQUEST_TIMEOUT_SECONDS = 20.0


class GeminiClient:
    """Talks to the real Gemini API. Only ever constructed when GEMINI_API_KEY is present.

    It holds the whole pool rather than one credential: the governor reserves
    quota against a specific key and passes it in, and the pool's own rotation
    is the fallback for a caller that has no governor in front of it.
    """

    def __init__(self, keys: KeyPool) -> None:
        self._keys = keys

    async def generate(
        self, *, model: str, prompt: str, response_schema: type[BaseModel], api_key: ApiKey | None = None
    ) -> LlmResponse:
        from google import genai  # lazy: keeps the SDK off the import path when no key is configured
        from google.genai import types

        key = api_key or self._keys.next_key()
        if key is None or not key.value:
            raise LlmUnavailable("no Gemini API key configured")

        client = genai.Client(api_key=key.value)
        try:
            # A ceiling on one attempt. Without it a provider that is failing
            # slowly -- a 503 that takes most of a minute to arrive -- is worse
            # than one that is failing fast: the retry chain multiplies the
            # wait, and a run measured here spent a hundred seconds on two
            # calls. A timeout is raised as transient, so the backoff layer
            # treats a hung request the same as a refused one and moves on.
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0,
                        response_mime_type="application/json",
                        response_schema=response_schema,
                    ),
                ),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise LlmTransientError(f"{model}: no answer within {REQUEST_TIMEOUT_SECONDS:.0f}s") from exc
        except Exception as exc:
            # The SDK's exception hierarchy isn't something we can verify against a
            # live key here; treat any transport/provider failure as retryable and
            # let the backoff layer's attempt cap turn a genuine outage into a
            # degraded run instead of retrying forever.
            raise LlmTransientError(str(exc)) from exc

        raw = response.text
        if raw is None:
            raise LlmUnavailable("Gemini returned an empty response")
        usage = response.usage_metadata
        input_tokens = usage.prompt_token_count if usage and usage.prompt_token_count else _estimate_tokens(prompt)
        output_tokens = (
            usage.candidates_token_count if usage and usage.candidates_token_count else _estimate_tokens(raw)
        )
        return LlmResponse(raw_json=raw, input_tokens=input_tokens, output_tokens=output_tokens)


def load_fixture_file(path: Path) -> dict[str, str]:
    """Load a {prompt: raw_json} fixture map for FakeClient from disk."""
    return json.loads(path.read_text()) if path.exists() else {}

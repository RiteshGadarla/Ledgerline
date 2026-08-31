import hashlib
import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from llm.backoff import LlmTransientError


class LlmUnavailable(Exception):
    """Raised when a client cannot produce a response at all (no fixture, no key, exhausted retries)."""


class LlmResponse(BaseModel):
    raw_json: str
    input_tokens: int
    output_tokens: int


class LlmClient(Protocol):
    async def generate(self, *, model: str, prompt: str, response_schema: type[BaseModel]) -> LlmResponse: ...


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class FakeClient:
    """Deterministic fixtures for offline testing. No network, no API key, ever."""

    def __init__(self, fixtures: dict[str, str]) -> None:
        self._fixtures = fixtures
        self.calls: list[tuple[str, str]] = []

    async def generate(self, *, model: str, prompt: str, response_schema: type[BaseModel]) -> LlmResponse:
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

    async def generate(self, *, model: str, prompt: str, response_schema: type[BaseModel]) -> LlmResponse:
        response = await self._inner.generate(model=model, prompt=prompt, response_schema=response_schema)
        self._fixtures_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(f"{model}:{prompt}".encode()).hexdigest()
        (self._fixtures_dir / f"{digest}.json").write_text(response.raw_json)
        return response


class GeminiClient:
    """Talks to the real Gemini API. Only ever constructed when GEMINI_API_KEY is present."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def generate(self, *, model: str, prompt: str, response_schema: type[BaseModel]) -> LlmResponse:
        from google import genai  # lazy: keeps the SDK off the import path when no key is configured
        from google.genai import types

        client = genai.Client(api_key=self._api_key)
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=response_schema,
                ),
            )
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

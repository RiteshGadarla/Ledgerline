import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from llm.client import FakeClient, LlmUnavailable, RecordingClient
from llm.models import PRIMARY_MODEL


class Proposal(BaseModel):
    invoice_id: str
    confidence: float


async def test_fake_client_returns_fixture_and_records_calls() -> None:
    fixture = json.dumps({"invoice_id": "INV1", "confidence": 0.95})
    client = FakeClient({"match these": fixture})

    response = await client.generate(model=PRIMARY_MODEL, prompt="match these", response_schema=Proposal)

    assert response.raw_json == fixture
    assert client.calls == [(PRIMARY_MODEL, "match these")]


async def test_fake_client_raises_on_missing_fixture() -> None:
    client = FakeClient({})
    with pytest.raises(LlmUnavailable):
        await client.generate(model=PRIMARY_MODEL, prompt="unrecorded prompt", response_schema=Proposal)


async def test_fake_client_rejects_malformed_fixture() -> None:
    client = FakeClient({"bad": "{not json"})
    with pytest.raises(Exception):  # noqa: B017 - pydantic's ValidationError, deliberately broad here
        await client.generate(model=PRIMARY_MODEL, prompt="bad", response_schema=Proposal)


async def test_recording_client_writes_fixture_to_disk(tmp_path: Path) -> None:
    fixture = json.dumps({"invoice_id": "INV1", "confidence": 0.95})
    inner = FakeClient({"match these": fixture})
    recorder = RecordingClient(inner, tmp_path)

    response = await recorder.generate(model=PRIMARY_MODEL, prompt="match these", response_schema=Proposal)

    assert response.raw_json == fixture
    written = list(tmp_path.glob("*.json"))
    assert len(written) == 1
    assert written[0].read_text() == fixture

import json

import redis.asyncio as redis

from ingest.mapper import CANONICAL_FIELDS, MappingCache, build_prompt, map_schema
from ingest.tabular import ParsedTable
from llm.cache import ResponseCache
from llm.client import FakeClient
from llm.gateway import LlmGateway
from llm.governor import Governor
from llm.models import BACKUP_MODEL, PRIMARY_MODEL
from money.result import Err, Ok


def _gateway(redis_client: redis.Redis, client: FakeClient) -> LlmGateway:
    governor = Governor(
        redis_client=redis_client,
        rpm_limits={PRIMARY_MODEL: 1000, BACKUP_MODEL: 1000},
        rpd_limits={PRIMARY_MODEL: 1000, BACKUP_MODEL: 1000},
        user_daily_quota=1000,
    )
    return LlmGateway(client=client, governor=governor, cache=ResponseCache(redis_client), schema_version="mapper-v1")


def _bank_table(rows: list[dict[str, str]]) -> ParsedTable:
    return ParsedTable(headers=["Txn Date", "Description", "Amount", "Type"], rows=rows)


def _fixture_response() -> str:
    return json.dumps(
        {
            "fields": [
                {"source_header": "Txn Date", "canonical_field": "value_date", "confidence": 0.95},
                {"source_header": "Description", "canonical_field": "narration", "confidence": 0.95},
                {"source_header": "Amount", "canonical_field": "credit", "confidence": 0.7},
                {"source_header": "Type", "canonical_field": None, "confidence": 0.4},
            ]
        }
    )


async def test_map_schema_returns_field_mapping(redis_client: redis.Redis) -> None:
    table = _bank_table([{"Txn Date": "01-Jan-24", "Description": "NEFT", "Amount": "100.00", "Type": "CR"}])
    prompt = build_prompt("bank", table)
    client = FakeClient(fixtures={prompt: _fixture_response()})
    gateway = _gateway(redis_client, client)
    cache = MappingCache(redis_client)

    result = await map_schema("bank", table, gateway, cache, user_id="user_1")

    assert isinstance(result, Ok)
    mapping = result.value
    assert mapping.canonical_for("Txn Date") == "value_date"
    assert mapping.canonical_for("Description") == "narration"
    assert mapping.canonical_for("Type") is None
    assert len(client.calls) == 1


async def test_same_header_signature_hits_cache_on_second_upload(redis_client: redis.Redis) -> None:
    """Two uploads with the same columns but different sample *values* must
    issue exactly one LLM call between them -- the cache key is the header
    signature alone, per the mapper's caching contract."""
    table_1 = _bank_table([{"Txn Date": "01-Jan-24", "Description": "NEFT", "Amount": "100.00", "Type": "CR"}])
    table_2 = _bank_table([{"Txn Date": "02-Feb-24", "Description": "UPI", "Amount": "500.00", "Type": "CR"}])
    prompt_1 = build_prompt("bank", table_1)
    client = FakeClient(fixtures={prompt_1: _fixture_response()})
    gateway = _gateway(redis_client, client)
    cache = MappingCache(redis_client)

    first = await map_schema("bank", table_1, gateway, cache, user_id="user_1")
    second = await map_schema("bank", table_2, gateway, cache, user_id="user_2")

    assert isinstance(first, Ok)
    assert isinstance(second, Ok)
    assert second.value.canonical_for("Description") == "narration"
    assert len(client.calls) == 1


async def test_different_header_order_is_a_different_signature(redis_client: redis.Redis) -> None:
    table_1 = _bank_table([{"Txn Date": "01-Jan-24", "Description": "NEFT", "Amount": "100.00", "Type": "CR"}])
    table_2 = ParsedTable(
        headers=["Description", "Txn Date", "Amount", "Type"],
        rows=[{"Description": "NEFT", "Txn Date": "01-Jan-24", "Amount": "100.00", "Type": "CR"}],
    )
    prompt_1 = build_prompt("bank", table_1)
    prompt_2 = build_prompt("bank", table_2)
    client = FakeClient(fixtures={prompt_1: _fixture_response(), prompt_2: _fixture_response()})
    gateway = _gateway(redis_client, client)
    cache = MappingCache(redis_client)

    await map_schema("bank", table_1, gateway, cache, user_id="user_1")
    await map_schema("bank", table_2, gateway, cache, user_id="user_1")

    assert len(client.calls) == 2


async def test_missing_fixture_degrades_to_err(redis_client: redis.Redis) -> None:
    table = _bank_table([{"Txn Date": "01-Jan-24", "Description": "NEFT", "Amount": "100.00", "Type": "CR"}])
    client = FakeClient(fixtures={})
    gateway = _gateway(redis_client, client)
    cache = MappingCache(redis_client)

    result = await map_schema("bank", table, gateway, cache, user_id="user_1")

    assert isinstance(result, Err)


def test_canonical_fields_cover_all_roles() -> None:
    assert set(CANONICAL_FIELDS.keys()) == {"ledger", "gateway", "settlement", "bank"}
    assert "id" in CANONICAL_FIELDS["bank"]

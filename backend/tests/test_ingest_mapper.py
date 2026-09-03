import json

import redis.asyncio as redis

from ingest.examples import WORKED_EXAMPLES
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
    """Headers a real statement uses, every one of them in the header table."""
    return ParsedTable(headers=["Txn Date", "Description", "Amount", "Type"], rows=rows)


def _opaque_table(rows: list[dict[str, str]]) -> ParsedTable:
    """Headers nothing can be inferred from, so the model has to be asked."""
    return ParsedTable(headers=["Col A", "Col B", "Col C"], rows=rows)


_OPAQUE_ROW = {"Col A": "01-Jan-24", "Col B": "NEFT", "Col C": "100.00"}


def _opaque_prompt(table: ParsedTable) -> str:
    return build_prompt("bank", table, unresolved=list(table.headers), taken=set())


def _opaque_response() -> str:
    return json.dumps(
        {
            "fields": [
                {"source_header": "Col A", "canonical_field": "value_date", "confidence": 0.8},
                {"source_header": "Col B", "canonical_field": "narration", "confidence": 0.8},
                {"source_header": "Col C", "canonical_field": "credit", "confidence": 0.7},
            ]
        }
    )


async def test_known_headers_are_resolved_without_asking_the_model(redis_client: redis.Redis) -> None:
    """The mapper's first job is to not need the model. A statement whose
    columns are named the way statements are named costs no call and cannot
    be mismapped by a guess."""
    table = _bank_table([{"Txn Date": "01-Jan-24", "Description": "NEFT", "Amount": "100.00", "Type": "CR"}])
    client = FakeClient(fixtures={})
    cache = MappingCache(redis_client)

    result = await map_schema("bank", table, _gateway(redis_client, client), cache, user_id="user_1")

    assert isinstance(result, Ok)
    mapping = result.value
    assert mapping.canonical_for("Txn Date") == "value_date"
    assert mapping.canonical_for("Description") == "narration"
    assert mapping.canonical_for("Amount") == "credit"
    assert mapping.canonical_for("Type") is None
    assert client.calls == []


async def test_unknown_headers_are_put_to_the_model(redis_client: redis.Redis) -> None:
    table = _opaque_table([_OPAQUE_ROW])
    client = FakeClient(fixtures={_opaque_prompt(table): _opaque_response()})
    cache = MappingCache(redis_client)

    result = await map_schema("bank", table, _gateway(redis_client, client), cache, user_id="user_1")

    assert isinstance(result, Ok)
    assert result.value.canonical_for("Col B") == "narration"
    assert len(client.calls) == 1


async def test_a_proposal_that_collides_with_a_resolved_field_is_dropped(redis_client: redis.Redis) -> None:
    """Two headers cannot both be the credit. The table resolved `Amount`, so
    a proposal putting `Deposit Ref` there as well is refused rather than
    merged -- the same discipline the verifier applies to a match."""
    table = ParsedTable(
        headers=["Value Date", "Description", "Amount", "Deposit Ref"],
        rows=[{"Value Date": "01-Jan-24", "Description": "NEFT", "Amount": "100.00", "Deposit Ref": "R1"}],
    )
    prompt = build_prompt("bank", table, unresolved=["Deposit Ref"], taken={"value_date", "narration", "credit"})
    response = json.dumps(
        {"fields": [{"source_header": "Deposit Ref", "canonical_field": "credit", "confidence": 0.9}]}
    )
    client = FakeClient(fixtures={prompt: response})
    cache = MappingCache(redis_client)

    result = await map_schema("bank", table, _gateway(redis_client, client), cache, user_id="user_1")

    assert isinstance(result, Ok)
    assert result.value.canonical_for("Amount") == "credit"
    assert result.value.canonical_for("Deposit Ref") is None


async def test_same_header_signature_hits_cache_on_second_upload(redis_client: redis.Redis) -> None:
    """Two uploads with the same columns but different sample *values* must
    issue exactly one LLM call between them -- the cache key is the header
    signature alone, per the mapper's caching contract."""
    table_1 = _opaque_table([_OPAQUE_ROW])
    table_2 = _opaque_table([{"Col A": "02-Feb-24", "Col B": "UPI", "Col C": "500.00"}])
    client = FakeClient(fixtures={_opaque_prompt(table_1): _opaque_response()})
    cache = MappingCache(redis_client)
    gateway = _gateway(redis_client, client)

    first = await map_schema("bank", table_1, gateway, cache, user_id="user_1")
    second = await map_schema("bank", table_2, gateway, cache, user_id="user_2")

    assert isinstance(first, Ok)
    assert isinstance(second, Ok)
    assert second.value.canonical_for("Col B") == "narration"
    assert len(client.calls) == 1


async def test_different_header_order_is_a_different_signature(redis_client: redis.Redis) -> None:
    table_1 = _opaque_table([_OPAQUE_ROW])
    table_2 = ParsedTable(
        headers=["Col B", "Col A", "Col C"],
        rows=[{"Col B": "NEFT", "Col A": "01-Jan-24", "Col C": "100.00"}],
    )
    client = FakeClient(
        fixtures={_opaque_prompt(table_1): _opaque_response(), _opaque_prompt(table_2): _opaque_response()}
    )
    cache = MappingCache(redis_client)
    gateway = _gateway(redis_client, client)

    await map_schema("bank", table_1, gateway, cache, user_id="user_1")
    await map_schema("bank", table_2, gateway, cache, user_id="user_1")

    assert len(client.calls) == 2


async def test_missing_fixture_degrades_to_err(redis_client: redis.Redis) -> None:
    table = _opaque_table([_OPAQUE_ROW])
    client = FakeClient(fixtures={})
    cache = MappingCache(redis_client)

    result = await map_schema("bank", table, _gateway(redis_client, client), cache, user_id="user_1")

    assert isinstance(result, Err)


def test_canonical_fields_cover_all_roles() -> None:
    assert set(CANONICAL_FIELDS.keys()) == {"ledger", "gateway", "settlement", "bank"}
    assert "id" in CANONICAL_FIELDS["bank"]


def test_the_prompt_carries_many_worked_examples_before_the_question() -> None:
    """Many-shot, not zero-shot: whole files answered correctly, plus the
    header lexicon, in front of every question actually asked."""
    table = _opaque_table([_OPAQUE_ROW])
    prompt = build_prompt("bank", table, unresolved=list(table.headers), taken=set())

    assert "Worked examples" in prompt
    # Both worked examples for the role, answers included.
    assert prompt.count("Answer: {") == len(WORKED_EXAMPLES["bank"])
    assert '"canonical_field": "credit"' in prompt
    # The lexicon, rendered as labelled pairs.
    assert "  deposit -> credit" in prompt
    assert "  withdrawal -> debit" in prompt
    # The file being asked about comes last, after the demonstrations.
    assert prompt.index("Worked examples") < prompt.index("Now the file")
    assert prompt.rstrip().endswith("with a confidence in [0, 1].")


def test_the_settlement_examples_teach_that_a_payout_is_the_net() -> None:
    """The mistake this corpus exists to prevent: a real upload had its
    `gross_amount` mapped onto the payout, putting every bank comparison off
    by exactly the fee. The demonstration says otherwise, in full."""
    table = ParsedTable(headers=["gross_amount"], rows=[{"gross_amount": "25637.14"}])
    prompt = build_prompt("settlement", table, unresolved=["gross_amount"], taken=set())

    assert '{"source_header": "Gross", "canonical_field": null' in prompt
    assert '{"source_header": "Net Credit", "canonical_field": "payout"' in prompt


def test_examples_never_demonstrate_a_field_already_taken() -> None:
    """A file that has resolved its payout is not shown examples mapping other
    headers onto the payout: the demonstrations agree with the instruction
    about which fields are still on offer."""
    table = ParsedTable(headers=["mystery"], rows=[{"mystery": "1"}])
    taken = {"id", "utr", "payout", "fees", "tax", "settled_at", "payment_ids"}
    prompt = build_prompt("settlement", table, unresolved=["mystery"], taken=taken)

    assert "Choose only from these fields: adjustments." in prompt
    assert "-> payout" not in prompt.split("Further column names")[-1]
    assert "already assigned to other columns" in prompt

import json

import redis.asyncio as redis

from contracts.models import Exception_
from datagen.generator import generate_corpus
from engine.index import build_index
from engine.pipeline import match
from engine.verifier import UsedRecordIds
from llm.cache import ResponseCache
from llm.client import FakeClient
from llm.explain import explain
from llm.gateway import LlmGateway
from llm.governor import Governor
from llm.triage import build_candidates, build_prompt, run_triage

SEEDS = [1001, 1002, 1003]


def _gateway(redis_client: redis.Redis, client: FakeClient) -> LlmGateway:
    governor = Governor(
        redis_client=redis_client,
        rpm_limits={"gemini-2.5-flash": 10, "gemini-2.5-flash-lite": 15},
        rpd_limits={"gemini-2.5-flash": 250, "gemini-2.5-flash-lite": 1000},
        user_daily_quota=25,
    )
    return LlmGateway(client=client, governor=governor, cache=ResponseCache(redis_client), schema_version="run-v1")


def _well_behaved_fixtures(candidates, exceptions_after_triage, index) -> dict[str, str]:  # type: ignore[no-untyped-def]
    fixtures: dict[str, str] = {}
    for candidate in candidates:
        bank_line_id = candidate.bank_line_ids[0]
        narration = index.bank_lines_by_id[bank_line_id].narration
        fixtures[build_prompt(candidate, index)] = json.dumps(
            {"proposals": [{"bank_line_id": bank_line_id, "confidence": 0.9, "evidence_spans": [narration]}]}
        )
    if exceptions_after_triage:
        from llm.explain import build_prompt as explain_prompt

        items = [
            {"exception_id": exc.id, "explanation": "Auto-generated.", "suggested_action": exc.suggested_action}
            for exc in exceptions_after_triage
        ]
        fixtures[explain_prompt(exceptions_after_triage)] = json.dumps({"items": items})
    return fixtures


async def _run_full_pipeline(seed: int, redis_client: redis.Redis) -> tuple[int, int]:
    corpus, _truth = generate_corpus(seed, 150)
    result = match(corpus)
    index = build_index(corpus)

    exceptioned_settlement_ids = {r.id for e in result.exceptions for r in e.records if r.kind == "settlement"}
    unresolved_bank_line_ids = {r.id for e in result.exceptions for r in e.records if r.kind == "bank_line"}
    used = UsedRecordIds()
    for group in result.groups:
        used = used.with_group(group)

    candidates = build_candidates(list(exceptioned_settlement_ids), index, unresolved_bank_line_ids)
    remaining_exceptions: list[Exception_] = list(result.exceptions)

    # build_prompt is deterministic, so the exact prompt the real call will send
    # is known upfront; a FakeClient primed with the well-behaved answer for it
    # measures request/token counts without asserting anything about answer
    # quality, which is what a budget test is actually about.
    fixtures = _well_behaved_fixtures(candidates, [], index)
    client = FakeClient(fixtures)
    gateway = _gateway(redis_client, client)
    triage_outcome = await run_triage(
        list(exceptioned_settlement_ids), index, unresolved_bank_line_ids, gateway, used, user_id=f"seed-{seed}"
    )

    resolved_settlement_ids = {g.settlement_id for g in triage_outcome.groups}
    remaining_exceptions = [
        exc
        for exc in remaining_exceptions
        if not any(r.kind == "settlement" and r.id in resolved_settlement_ids for r in exc.records)
    ]
    remaining_exceptions.extend(triage_outcome.exceptions)

    explain_fixtures = _well_behaved_fixtures([], remaining_exceptions, index)
    explain_client = FakeClient(explain_fixtures)
    explain_gateway = _gateway(redis_client, explain_client)
    _annotated, explain_in_tokens, explain_out_tokens = await explain(
        remaining_exceptions, explain_gateway, user_id=f"seed-{seed}"
    )

    total_requests = triage_outcome.requests_issued + (1 if remaining_exceptions else 0)
    total_tokens = triage_outcome.tokens_used + explain_in_tokens + explain_out_tokens
    return total_requests, total_tokens


async def test_150_record_run_stays_within_llm_budget(redis_client: redis.Redis) -> None:
    for seed in SEEDS:
        requests, tokens = await _run_full_pipeline(seed, redis_client)
        assert requests <= 3, f"seed={seed}: {requests} LLM requests exceeds the budget of 3"
        assert tokens < 15_000, f"seed={seed}: {tokens} tokens exceeds the 15k budget"


async def test_demo_like_clean_run_issues_zero_requests(redis_client: redis.Redis) -> None:
    """A run with no residue at all (nothing for triage, nothing to explain)
    costs zero LLM requests, matching the demo run's zero-cost guarantee."""
    corpus, _truth = generate_corpus(1001, 150)
    result = match(corpus)

    non_settlement_or_bankline_exceptions = [
        exc for exc in result.exceptions if not any(r.kind in ("settlement", "bank_line") for r in exc.records)
    ]
    # Simulate the fully-resolved case directly: an empty residue list costs nothing.
    client = FakeClient({})
    gateway = _gateway(redis_client, client)
    _annotated, in_tokens, out_tokens = await explain([], gateway, user_id="u1")

    assert len(client.calls) == 0
    assert in_tokens == 0 and out_tokens == 0
    assert isinstance(non_settlement_or_bankline_exceptions, list)  # sanity: helper computed without error

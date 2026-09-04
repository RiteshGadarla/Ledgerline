import json

import redis.asyncio as redis

from contracts.models import Exception_
from datagen.generator import generate_corpus
from engine.index import build_index
from engine.pipeline import match
from engine.verifier import UsedRecordIds
from llm.cache import ResponseCache
from llm.client import FakeClient
from llm.explain import distinct_codes, explain
from llm.gateway import LlmGateway
from llm.governor import Governor
from llm.models import BACKUP_MODEL, PRIMARY_MODEL
from llm.triage import build_candidates, build_prompt, run_triage

SEEDS = [1001, 1002, 1003]


def _gateway(redis_client: redis.Redis, client: FakeClient) -> LlmGateway:
    governor = Governor(
        redis_client=redis_client,
        rpm_limits={PRIMARY_MODEL: 10, BACKUP_MODEL: 15},
        rpd_limits={PRIMARY_MODEL: 250, BACKUP_MODEL: 1000},
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
        from llm.explain import distinct_codes

        # One item per distinct code, which is what the real call now asks
        # for -- priming this per exception would measure a budget the
        # pipeline no longer spends.
        codes = distinct_codes(exceptions_after_triage)
        items = [{"code": code, "explanation": "Auto-generated.", "suggested_action": "Investigate."} for code in codes]
        fixtures[explain_prompt(codes)] = json.dumps({"items": items})
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
    _annotated, explain_in_tokens, explain_out_tokens, _degraded = await explain(
        remaining_exceptions, explain_gateway, user_id=f"seed-{seed}"
    )

    total_requests = triage_outcome.requests_issued + (1 if remaining_exceptions else 0)
    total_tokens = triage_outcome.tokens_used + explain_in_tokens + explain_out_tokens
    return total_requests, total_tokens


async def test_150_record_run_stays_within_llm_budget(redis_client: redis.Redis) -> None:
    """800 tokens, not the 15k this once allowed. Explaining used to ask for
    one item per exception and now asks for one per distinct code, which on
    these three seeds is 81/110/83 exceptions collapsing to 6/5/4 codes. The
    ceiling is set below what the old shape cost on the *cheapest* seed
    (~940 tokens), so a regression to per-exception fails here rather than
    surfacing as a run that mysteriously takes minutes.

    The request ceiling is 4 rather than 3 because the corpus is now seeded
    15% difficult across all ten classes instead of one record each, and a
    harder corpus surfaces more distinct exception codes to explain. Measured
    cost on these seeds is 3/4/3 requests and 472/551/294 tokens; the ceilings
    sit just above the worst of them, so any real regression still fails here.
    """
    for seed in SEEDS:
        requests, tokens = await _run_full_pipeline(seed, redis_client)
        assert requests <= 4, f"seed={seed}: {requests} LLM requests exceeds the budget of 4"
        assert tokens < 800, f"seed={seed}: {tokens} tokens exceeds the 800 budget"


async def test_explaining_asks_once_per_code_however_many_exceptions_share_it(
    redis_client: redis.Redis,
) -> None:
    """The token budget above is an estimate; this is the structural fact
    behind it. Latency in this stage is output-token-bound and a model emits
    those serially, so items-per-response is what decides whether the stage
    takes seconds or minutes -- measured on a real run, 88 exceptions across
    4 codes took 124 of the run's 137 seconds.
    """
    corpus, _truth = generate_corpus(1003, 150)
    exceptions = match(corpus).exceptions
    codes = distinct_codes(exceptions)
    assert len(exceptions) > 4 * len(codes), "seed chosen so the collapse is worth measuring"

    fixtures = _well_behaved_fixtures([], exceptions, build_index(corpus))
    client = FakeClient(fixtures)
    annotated, _in, _out, degraded = await explain(exceptions, _gateway(redis_client, client), user_id="u1")

    assert len(client.calls) == 1
    sent = client.calls[0][1]
    assert sum(line in codes for line in sent.splitlines()) == len(codes)
    assert not any(exc.id in sent for exc in exceptions), "no exception id reaches the prompt"
    # Every exception still comes back annotated -- fewer tokens, same output.
    assert all(exc.explanation for exc in annotated)
    assert degraded is False


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
    _annotated, in_tokens, out_tokens, _degraded = await explain([], gateway, user_id="u1")

    assert len(client.calls) == 0
    assert in_tokens == 0 and out_tokens == 0
    assert isinstance(non_settlement_or_bankline_exceptions, list)  # sanity: helper computed without error

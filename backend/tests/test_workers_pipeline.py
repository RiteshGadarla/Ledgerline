import json

import redis.asyncio as redis

from datagen.generator import generate_corpus
from llm.cache import ResponseCache
from llm.client import FakeClient
from llm.gateway import LlmGateway
from llm.governor import Governor
from llm.models import BACKUP_MODEL, PRIMARY_MODEL
from llm.triage import build_candidates
from llm.triage import build_prompt as triage_prompt
from workers.pipeline import run_pipeline


def _gateway(redis_client: redis.Redis, client: FakeClient) -> LlmGateway:
    governor = Governor(
        redis_client=redis_client,
        rpm_limits={PRIMARY_MODEL: 1000, BACKUP_MODEL: 1000},
        rpd_limits={PRIMARY_MODEL: 1000, BACKUP_MODEL: 1000},
        user_daily_quota=1000,
    )
    return LlmGateway(client=client, governor=governor, cache=ResponseCache(redis_client), schema_version="run-v1")


async def test_run_pipeline_scores_against_truth_and_reports_llm_usage(redis_client: redis.Redis) -> None:
    corpus, truth = generate_corpus(1001, 150)

    from engine.index import build_index
    from engine.pipeline import match

    result = match(corpus)
    index = build_index(corpus)
    exceptioned_settlement_ids = {r.id for e in result.exceptions for r in e.records if r.kind == "settlement"}
    unresolved_bank_line_ids = {r.id for e in result.exceptions for r in e.records if r.kind == "bank_line"}
    candidates = build_candidates(list(exceptioned_settlement_ids), index, unresolved_bank_line_ids)

    fixtures: dict[str, str] = {}
    for candidate in candidates:
        bank_line_id = candidate.bank_line_ids[0]
        narration = index.bank_lines_by_id[bank_line_id].narration
        fixtures[triage_prompt(candidate, index)] = json.dumps(
            {"proposals": [{"bank_line_id": bank_line_id, "confidence": 0.9, "evidence_spans": [narration]}]}
        )

    client = FakeClient(fixtures)
    gateway = _gateway(redis_client, client)

    states: list[str] = []

    async def _record_state(state: str) -> None:
        states.append(state)

    outcome = await run_pipeline(corpus, truth, gateway, user_id="u1", publish_state=_record_state)

    assert states == ["normalising", "matching", "triaging", "explaining", "scoring"]
    assert outcome.metrics.false_matches == 0
    assert outcome.metrics.output_hash == outcome.result.output_hash
    assert outcome.metrics.llm_requests >= 1


async def test_assisted_group_closes_every_exception_it_resolved(redis_client: redis.Redis) -> None:
    """A record triage tied out is not also an open exception.

    The settlement sent to triage arrives with its payments, its invoices and
    its bank line, each already filed as its own exception by the deterministic
    pass. Dropping only the settlement's exception left the rest of the group
    open while the same records were counted as assisted -- the exception
    count, the rupees at risk and the open rate were all inflated by whatever
    the assist had just resolved, and auto + assist + open summed past 100%.
    """
    corpus, truth = generate_corpus(1001, 150)

    from engine.index import build_index
    from engine.pipeline import match

    result = match(corpus)
    index = build_index(corpus)
    exceptioned_settlement_ids = {r.id for e in result.exceptions for r in e.records if r.kind == "settlement"}
    unresolved_bank_line_ids = {r.id for e in result.exceptions for r in e.records if r.kind == "bank_line"}

    fixtures: dict[str, str] = {}
    for candidate in build_candidates(list(exceptioned_settlement_ids), index, unresolved_bank_line_ids):
        bank_line_id = candidate.bank_line_ids[0]
        narration = index.bank_lines_by_id[bank_line_id].narration
        fixtures[triage_prompt(candidate, index)] = json.dumps(
            {"proposals": [{"bank_line_id": bank_line_id, "confidence": 0.9, "evidence_spans": [narration]}]}
        )

    gateway = _gateway(redis_client, FakeClient(fixtures))
    outcome = await run_pipeline(corpus, truth, gateway, user_id="u1")

    assert outcome.metrics.assist_rate > 0, "fixture should have let triage resolve at least one settlement"

    for kind, matched in (
        ("payment", {pid for g in outcome.result.groups for pid in g.payment_ids}),
        ("invoice", {iid for g in outcome.result.groups for iid in g.invoice_ids}),
        ("bank_line", {g.bank_line_id for g in outcome.result.groups if g.bank_line_id}),
        ("settlement", {g.settlement_id for g in outcome.result.groups if g.settlement_id}),
    ):
        still_open = {r.id for e in outcome.result.exceptions for r in e.records if r.kind == kind}
        assert not (matched & still_open), f"{kind} counted as both matched and open: {sorted(matched & still_open)}"

    m = outcome.metrics
    assert m.auto_rate + m.assist_rate + m.open_rate <= 1.0 + 1e-9


async def test_run_pipeline_degrades_cleanly_when_llm_unavailable(redis_client: redis.Redis) -> None:
    corpus, truth = generate_corpus(1001, 150)
    client = FakeClient({})  # no fixtures recorded -> every call degrades
    gateway = _gateway(redis_client, client)

    outcome = await run_pipeline(corpus, truth, gateway, user_id="u1")

    assert outcome.metrics.false_matches == 0
    assert outcome.metrics.assist_rate == 0.0
    assert outcome.metrics.llm_degraded is True

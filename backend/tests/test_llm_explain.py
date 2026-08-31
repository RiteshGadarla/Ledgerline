import json

import redis.asyncio as redis

from contracts.enums import ExceptionCode
from contracts.models import Exception_, RecordRef
from llm.cache import ResponseCache
from llm.client import FakeClient
from llm.explain import build_prompt, explain
from llm.gateway import LlmGateway
from llm.governor import Governor


def _gateway(redis_client: redis.Redis, client: FakeClient) -> LlmGateway:
    governor = Governor(
        redis_client=redis_client,
        rpm_limits={"gemini-3.5-flash-lite": 1000},
        rpd_limits={"gemini-3.5-flash-lite": 1000},
        user_daily_quota=1000,
    )
    return LlmGateway(client=client, governor=governor, cache=ResponseCache(redis_client), schema_version="explain-v1")


def _exception(id_: str, code: ExceptionCode) -> Exception_:
    return Exception_(
        id=id_,
        code=code,
        severity=2,
        amount_at_risk=10000,  # type: ignore[arg-type]
        records=[RecordRef(kind="settlement", id="STL1")],
        attempted=["P1"],
        explanation=None,
        suggested_action="template action",
        rejected_proposal=None,
    )


async def test_explain_annotates_matching_exceptions_by_id(redis_client: redis.Redis) -> None:
    exceptions = [_exception("EXC-1", ExceptionCode.MISSING_IN_BANK)]
    prompt = build_prompt(exceptions)
    item = {"exception_id": "EXC-1", "explanation": "No bank credit found.", "suggested_action": "Check payout status."}
    fixture = json.dumps({"items": [item]})
    client = FakeClient({prompt: fixture})
    gateway = _gateway(redis_client, client)

    annotated, input_tokens, output_tokens, degraded = await explain(exceptions, gateway, user_id="u1")

    assert annotated[0].explanation == "No bank credit found."
    assert annotated[0].suggested_action == "Check payout status."
    assert input_tokens > 0 and output_tokens > 0
    assert degraded is False


async def test_explain_degrades_gracefully_leaving_template_action(redis_client: redis.Redis) -> None:
    exceptions = [_exception("EXC-1", ExceptionCode.MISSING_IN_BANK)]
    client = FakeClient({})  # no fixture recorded -> LlmUnavailable inside the gateway's client call

    gateway = _gateway(redis_client, client)
    annotated, input_tokens, output_tokens, degraded = await explain(exceptions, gateway, user_id="u1")

    assert annotated == exceptions
    assert annotated[0].suggested_action == "template action"
    assert input_tokens == 0 and output_tokens == 0
    assert degraded is True


async def test_explain_with_empty_list_issues_no_request(redis_client: redis.Redis) -> None:
    client = FakeClient({})
    gateway = _gateway(redis_client, client)

    annotated, input_tokens, output_tokens, degraded = await explain([], gateway, user_id="u1")

    assert annotated == []
    assert input_tokens == 0 and output_tokens == 0
    assert degraded is False

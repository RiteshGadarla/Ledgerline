import json

import redis.asyncio as redis

from contracts.enums import ExceptionCode
from contracts.models import Exception_, RecordRef
from llm.cache import ResponseCache
from llm.client import FakeClient
from llm.explain import build_prompt, distinct_codes, explain
from llm.gateway import LlmGateway
from llm.governor import Governor
from llm.models import BACKUP_MODEL, PRIMARY_MODEL


def _gateway(redis_client: redis.Redis, client: FakeClient) -> LlmGateway:
    governor = Governor(
        redis_client=redis_client,
        rpm_limits={PRIMARY_MODEL: 1000, BACKUP_MODEL: 1000},
        rpd_limits={PRIMARY_MODEL: 1000, BACKUP_MODEL: 1000},
        user_daily_quota=1000,
    )
    return LlmGateway(client=client, governor=governor, cache=ResponseCache(redis_client), schema_version="explain-v2")


def _exception(id_: str, code: ExceptionCode, amount: int = 10000) -> Exception_:
    return Exception_(
        id=id_,
        code=code,
        severity=2,
        amount_at_risk=amount,  # type: ignore[arg-type]
        records=[RecordRef(kind="settlement", id="STL1")],
        attempted=["P1"],
        explanation=None,
        suggested_action="template action",
        rejected_proposal=None,
    )


def _fixture(*items: tuple[str, str, str]) -> str:
    return json.dumps({"items": [{"code": c, "explanation": e, "suggested_action": a} for c, e, a in items]})


async def test_explain_annotates_every_exception_from_its_code_s_answer(redis_client: redis.Redis) -> None:
    """The whole point of asking per code: one answer, fanned out over every
    exception carrying that code, however many there are."""
    exceptions = [
        _exception("EXC-1", ExceptionCode.MISSING_IN_BANK, amount=10000),
        _exception("EXC-2", ExceptionCode.MISSING_IN_BANK, amount=99999),
        _exception("EXC-3", ExceptionCode.MISSING_IN_LEDGER),
    ]
    prompt = build_prompt(distinct_codes(exceptions))
    client = FakeClient(
        {
            prompt: _fixture(
                ("MISSING_IN_BANK", "No bank credit found.", "Check payout status."),
                ("MISSING_IN_LEDGER", "Not booked.", "Post the entry."),
            )
        }
    )
    gateway = _gateway(redis_client, client)

    annotated, input_tokens, output_tokens, degraded = await explain(exceptions, gateway, user_id="u1")

    assert [e.explanation for e in annotated] == ["No bank credit found.", "No bank credit found.", "Not booked."]
    actions = [e.suggested_action for e in annotated]
    assert actions == ["Check payout status.", "Check payout status.", "Post the entry."]
    # Three exceptions, two codes, still exactly one request.
    assert len(client.calls) == 1
    assert input_tokens > 0 and output_tokens > 0
    assert degraded is False


async def test_the_prompt_lists_each_code_once_however_many_exceptions_carry_it(
    redis_client: redis.Redis,
) -> None:
    """Latency here is output-token-bound, and the model emits one item per
    line of this prompt -- so the prompt's length, not the exception count,
    is what the stage costs."""
    many = [_exception(f"EXC-{i}", ExceptionCode.MISSING_IN_BANK, amount=i * 100) for i in range(50)]
    many.append(_exception("EXC-X", ExceptionCode.UNIDENTIFIED_CREDIT))

    prompt = build_prompt(distinct_codes(many))

    assert prompt.count("MISSING_IN_BANK") == 1
    assert prompt.count("UNIDENTIFIED_CREDIT") == 1
    assert not any("EXC-" in line for line in prompt.splitlines()), "no exception id reaches the prompt"


async def test_two_runs_breaking_the_same_ways_build_the_same_prompt(redis_client: redis.Redis) -> None:
    """The prompt is the cache key. Ids and amounts differ between runs and
    codes do not, so keeping them out is what lets the second run answer from
    Redis without an API call at all."""
    monday = [_exception("EXC-1", ExceptionCode.MISSING_IN_BANK, amount=10000)]
    tuesday = [_exception("EXC-9", ExceptionCode.MISSING_IN_BANK, amount=77777)]

    assert build_prompt(distinct_codes(monday)) == build_prompt(distinct_codes(tuesday))


async def test_a_code_the_model_skipped_keeps_its_template_action(redis_client: redis.Redis) -> None:
    """A partial answer annotates what it covered and leaves the rest alone,
    rather than discarding the whole batch."""
    exceptions = [
        _exception("EXC-1", ExceptionCode.MISSING_IN_BANK),
        _exception("EXC-2", ExceptionCode.UNIDENTIFIED_CREDIT),
    ]
    prompt = build_prompt(distinct_codes(exceptions))
    client = FakeClient({prompt: _fixture(("MISSING_IN_BANK", "No bank credit found.", "Check payout status."))})
    gateway = _gateway(redis_client, client)

    annotated, _, _, degraded = await explain(exceptions, gateway, user_id="u1")

    assert annotated[0].explanation == "No bank credit found."
    assert annotated[1].explanation is None
    assert annotated[1].suggested_action == "template action"
    assert degraded is False


async def test_a_code_answered_in_the_wrong_case_still_matches(redis_client: redis.Redis) -> None:
    exceptions = [_exception("EXC-1", ExceptionCode.MISSING_IN_BANK)]
    prompt = build_prompt(distinct_codes(exceptions))
    client = FakeClient({prompt: _fixture((" missing_in_bank ", "No bank credit found.", "Check payout."))})
    gateway = _gateway(redis_client, client)

    annotated, _, _, degraded = await explain(exceptions, gateway, user_id="u1")

    assert annotated[0].explanation == "No bank credit found."
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

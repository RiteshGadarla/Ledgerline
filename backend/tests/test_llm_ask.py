import json

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.passwords import hash_password
from db.tenancy import complete_run, create_run, create_user
from llm.ask import AskToolCall, AskTurn, ScriptedAskClient, _is_grounded, ask
from llm.governor import Governor

RESULT_JSON = json.dumps(
    {
        "groups": [
            {
                "id": "STL1",
                "invoice_ids": ["INV1"],
                "payment_ids": ["PAY1"],
                "settlement_id": "STL1",
                "bank_line_id": "BNK1",
                "status": "auto",
                "pass_id": "P3",
                "confidence": 0.95,
                "residual": 0,
                "evidence": [],
            }
        ],
        "exceptions": [
            {
                "id": "EXC-1",
                "code": "MISSING_IN_BANK",
                "severity": 1,
                "amount_at_risk": 5000,
                "records": [{"kind": "settlement", "id": "STL2"}],
                "attempted": ["P1"],
            }
        ],
        "output_hash": "deadbeef",
    }
)
METRICS_JSON = json.dumps(
    {
        "auto_rate": 0.7333333333333333,
        "assist_rate": 0.0,
        "open_rate": 0.26666666666666666,
        "records": 10,
        "open_exceptions": 1,
        "amount_at_risk": 5000,
        "throughput_rps": 100.0,
        "p50_ms": 5,
        "p95_ms": 5,
        "llm_requests": 0,
        "llm_tokens": 0,
        "llm_degraded": False,
        "output_hash": "deadbeef",
    }
)


def _governor(redis_client: redis.Redis, quota: int = 1000) -> Governor:
    return Governor(
        redis_client=redis_client,
        rpm_limits={"gemini-3.6-flash": quota},
        rpd_limits={"gemini-3.6-flash": quota},
        user_daily_quota=quota,
    )


async def _completed_run(db_session_factory: async_sessionmaker[AsyncSession], username: str) -> tuple[str, str]:
    async with db_session_factory() as db:
        user = await create_user(db, username, hash_password("x"))
        run, _created = await create_run(db, user.id, "demo")
        await complete_run(db, run.id, RESULT_JSON, METRICS_JSON, None)
    return run.id, user.id


async def test_grounded_answer_restating_a_tool_number_is_accepted(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: redis.Redis
) -> None:
    run_id, user_id = await _completed_run(db_session_factory, "ask-grounded")
    client = ScriptedAskClient(
        turns=[
            AskTurn(tool_call=AskToolCall(name="get_metrics", args={"run_id": run_id})),
            AskTurn(text="The auto rate is 0.7333333333333333, with 1 open exception."),
        ]
    )
    async with db_session_factory() as db:
        answer = await ask("What's the auto rate?", run_id, user_id, db, client, _governor(redis_client))

    assert answer.degraded is False
    assert "0.7333333333333333" in answer.text
    assert answer.requests_issued == 2


async def test_grounded_answer_stated_as_a_percentage_is_still_accepted(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: redis.Redis
) -> None:
    """0.7333... restated as "73.3%" is a faithful unit conversion, not a
    fabrication -- the grounding check must not punish it."""
    run_id, user_id = await _completed_run(db_session_factory, "ask-percentage")
    client = ScriptedAskClient(
        turns=[
            AskTurn(tool_call=AskToolCall(name="get_metrics", args={"run_id": run_id})),
            AskTurn(text="The auto rate for this run is 73.3%."),
        ]
    )
    async with db_session_factory() as db:
        answer = await ask("What's the auto rate?", run_id, user_id, db, client, _governor(redis_client))

    assert answer.degraded is False
    assert "73.3%" in answer.text


async def test_answer_containing_a_fabricated_number_is_rejected(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: redis.Redis
) -> None:
    """The plan's grounding test: a number absent from every tool result
    must fail, even though the model's tool call and reasoning look fine."""
    run_id, user_id = await _completed_run(db_session_factory, "ask-fabricated")
    client = ScriptedAskClient(
        turns=[
            AskTurn(tool_call=AskToolCall(name="get_metrics", args={"run_id": run_id})),
            AskTurn(text="This run has 42 open exceptions."),  # 42 appears nowhere in the tool result
        ]
    )
    async with db_session_factory() as db:
        answer = await ask("How many open exceptions?", run_id, user_id, db, client, _governor(redis_client))

    assert answer.text == "I do not have that grounded in this run's data."


async def test_tool_error_is_relayed_without_the_model_inventing_data(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: redis.Redis
) -> None:
    run_id, user_id = await _completed_run(db_session_factory, "ask-unknown")
    client = ScriptedAskClient(
        turns=[
            AskTurn(tool_call=AskToolCall(name="get_forecast", args={"run_id": run_id})),
            AskTurn(text="I do not have that: this run has no forecast yet."),
        ]
    )
    async with db_session_factory() as db:
        answer = await ask("What's the cash forecast?", run_id, user_id, db, client, _governor(redis_client))

    assert "do not have" in answer.text.lower()


async def test_a_question_never_costs_more_than_three_requests(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: redis.Redis
) -> None:
    run_id, user_id = await _completed_run(db_session_factory, "ask-budget")
    client = ScriptedAskClient(
        turns=[
            AskTurn(tool_call=AskToolCall(name="get_metrics", args={"run_id": run_id})),
            AskTurn(tool_call=AskToolCall(name="get_metrics", args={"run_id": run_id})),
            AskTurn(tool_call=AskToolCall(name="get_metrics", args={"run_id": run_id})),
            AskTurn(text="This should never be reached."),
        ]
    )
    async with db_session_factory() as db:
        answer = await ask("Loop forever?", run_id, user_id, db, client, _governor(redis_client))

    assert answer.requests_issued == 3
    assert "allotted lookups" in answer.text


async def test_asking_about_another_users_run_returns_not_found_from_the_tool_layer_not_the_model(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: redis.Redis
) -> None:
    run_id, _owner_id = await _completed_run(db_session_factory, "ask-tenancy-owner")
    async with db_session_factory() as db:
        attacker = await create_user(db, "ask-tenancy-attacker", hash_password("y"))

    client = ScriptedAskClient(
        turns=[
            AskTurn(tool_call=AskToolCall(name="get_metrics", args={"run_id": run_id})),
            AskTurn(text="I do not have that: no run was found."),
        ]
    )
    question = "What's the auto rate for that run?"
    async with db_session_factory() as db:
        answer = await ask(question, run_id, attacker.id, db, client, _governor(redis_client))

    # No numeric metric leaked through even though the model asked about it --
    # the tool call itself came back Err before any real metric ever entered
    # the conversation, so there was nothing ungrounded to catch either.
    assert "0.73" not in answer.text
    assert "do not have" in answer.text.lower()


async def test_governor_denial_degrades_the_answer(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: redis.Redis
) -> None:
    run_id, user_id = await _completed_run(db_session_factory, "ask-governor")
    client = ScriptedAskClient(turns=[AskTurn(text="This should never be reached.")])
    exhausted_governor = _governor(redis_client, quota=0)

    async with db_session_factory() as db:
        answer = await ask("Anything?", run_id, user_id, db, client, exhausted_governor)

    assert answer.degraded is True
    assert answer.requests_issued == 0


def test_grounding_ignores_digits_embedded_in_record_ids_and_list_markers() -> None:
    """A real run against the live Gemini API surfaced this exact failure
    mode: a run_id UUID segment ("...9525...") and markdown list markers
    ("1. ", "16. ") both look like standalone numbers under a naive digit
    regex, and neither is a claim that needs to trace to a tool result."""
    answer = (
        "For run `d772cfb5-e980-42a0-9525-254f5f700a41`:\n\n"
        "* **Auto Rate:** `0.7666666666666667` (76.67%)\n"
        "* **Open Exceptions:** `17`\n\n"
        "1. `EXC-settlement-STL000003`\n"
        "16. `EXC-bank_line-BNK000003`\n"
    )
    payloads = [{"metrics": {"auto_rate": 0.7666666666666667, "open_exceptions": 17}}]

    assert _is_grounded(answer, payloads) is True
